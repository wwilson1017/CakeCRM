"""Small path-style S3 client: SigV4, paginated lists and bounded multipart uploads."""

import hashlib
import hmac
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx

from backups import BackupError

PART_BYTES = 64 * 1024 * 1024


def validate_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    try:
        url = urlsplit(endpoint)
        port = url.port
    except ValueError:
        raise BackupError("Endpoint URL or port is invalid") from None
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username
            or url.password or url.query or url.fragment
            or any(part in {".", ".."} for part in unquote(url.path).split("/"))
            or any(ord(c) < 32 for c in endpoint)):
        raise BackupError("Endpoint must be an http(s) URL with a host, no credentials or query, and no dot path segments")
    # Canonicalize the origin (including IDNA hosts) and encode any proxy base path once.
    host = url.hostname.encode("idna").decode().lower()
    if ":" in host:
        host = f"[{host}]"
    # httpx drops a default port from the Host header, so the signed host must too.
    default = {"http": 80, "https": 443}[url.scheme]
    origin = host + (f":{port}" if port not in (None, default) else "")
    return urlunsplit((url.scheme, origin, quote(unquote(url.path), safe="/-_.~"), "", ""))


def signed_headers(method: str, host: str, path: str, query: str, digest: str,
                   region: str, access_key_id: str, secret_key: str,
                   now: datetime | None = None) -> dict[str, str]:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    date = stamp[:8]
    headers = {"host": host, "x-amz-content-sha256": digest, "x-amz-date": stamp}
    names = ";".join(headers)
    canonical = "\n".join([method, path, query,
                           "".join(f"{k}:{v}\n" for k, v in headers.items()), names, digest])
    scope = f"{date}/{region}/s3/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    signing_key = ("AWS4" + secret_key).encode()
    for value in (date, region, "s3", "aws4_request"):
        signing_key = hmac.digest(signing_key, value.encode(), "sha256")
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, "
                                f"SignedHeaders={names}, Signature={signature}")
    return headers


def _xml(data: bytes) -> ET.Element:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise BackupError("S3 returned invalid XML") from None
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    return root


class S3Destination:
    def __init__(self, endpoint: str, bucket: str, region: str, access_key_id: str,
                 secret_key: str, prefix: str):
        self.endpoint = validate_endpoint(endpoint)
        self.bucket, self.region = bucket, region
        self.access_key_id, self.secret_key, self.prefix = access_key_id, secret_key, prefix

    def _request(self, method: str, key: str = "", params: dict | None = None,
                 data: bytes = b"") -> httpx.Response:
        endpoint = urlsplit(self.endpoint)
        path = endpoint.path + "/" + quote(self.bucket, safe="-_.~") + "/" + quote(key, safe="/-_.~")
        query = "&".join(f"{k}={v}" for k, v in sorted(
            (quote(str(k), safe="-_.~"), quote(str(v), safe="-_.~"))
            for k, v in (params or {}).items()))
        url = urlunsplit((endpoint.scheme, endpoint.netloc, path, query, ""))
        headers = signed_headers(method, urlsplit(self.endpoint).netloc, path, query,
                                 hashlib.sha256(data).hexdigest(), self.region,
                                 self.access_key_id, self.secret_key)
        headers["Content-Length"] = str(len(data))
        try:
            with httpx.Client(timeout=httpx.Timeout(300, connect=20), follow_redirects=False) as client:
                response = client.request(method, url, headers=headers, content=data)
        except httpx.HTTPError:
            raise BackupError("Could not reach S3; check the endpoint and network connection") from None
        # CompleteMultipartUpload may return HTTP 200 containing an Error document.
        if not response.is_success or (response.content and _is_error(response.content)):
            message = f"S3 returned HTTP {response.status_code}"
            try:
                root = _xml(response.content)
                message += ": " + " — ".join(filter(None, [root.findtext("Code"), root.findtext("Message")]))
            except BackupError:
                pass
            for secret in (self.secret_key, self.access_key_id):
                if secret:
                    message = message.replace(secret, "[redacted]")
            raise BackupError(message[:1000])
        return response

    def put_bytes(self, key: str, data: bytes) -> None:
        self._request("PUT", key, data=data)

    def put_file(self, key: str, path: Path) -> None:
        # Grow parts for huge files to stay below S3's 10,000-part limit.
        part_size = max(PART_BYTES, math.ceil(path.stat().st_size / 10000))
        if part_size > 5 * 1024**3:
            raise BackupError("Backup exceeds S3's multipart size limit")
        result = self._request("POST", key, {"uploads": ""})
        upload_id = _xml(result.content).findtext("UploadId")
        if not upload_id:
            raise BackupError("S3 did not return a multipart upload ID")
        complete = False
        try:
            parts = ET.Element("CompleteMultipartUpload")
            with path.open("rb") as source:
                number = 1
                while True:
                    data = source.read(part_size)
                    if not data and number > 1:
                        break
                    response = self._request("PUT", key, {"partNumber": number, "uploadId": upload_id}, data)
                    etag = response.headers.get("etag")
                    if not etag:
                        raise BackupError("S3 did not return a part ETag")
                    part = ET.SubElement(parts, "Part")
                    ET.SubElement(part, "PartNumber").text = str(number)
                    ET.SubElement(part, "ETag").text = etag
                    number += 1
                    if not data:
                        break
            response = self._request("POST", key, {"uploadId": upload_id}, ET.tostring(parts))
            if _xml(response.content).tag != "CompleteMultipartUploadResult":
                raise BackupError("S3 did not confirm the completed multipart upload")
            complete = True
        finally:
            if not complete:
                try:
                    self._request("DELETE", key, {"uploadId": upload_id})
                except BackupError:
                    pass  # Preserve the original failure; provider lifecycle rules can reap abandoned parts.

    def delete(self, key: str) -> None:
        self._request("DELETE", key)

    def list_keys(self, prefix: str) -> list[str]:
        keys = []
        params = {"list-type": "2", "prefix": prefix}
        seen = set()
        while True:
            root = _xml(self._request("GET", params=params).content)
            keys.extend(node.text for node in root.findall("Contents/Key") if node.text is not None)
            if root.findtext("IsTruncated", "false").lower() != "true":
                return keys
            token = root.findtext("NextContinuationToken")
            if not token or token in seen:
                raise BackupError("S3 returned an invalid continuation token")
            seen.add(token)
            params["continuation-token"] = token


def _is_error(data: bytes) -> bool:
    try:
        return _xml(data).tag == "Error"
    except BackupError:
        return False
