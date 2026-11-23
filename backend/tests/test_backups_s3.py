import hashlib
from datetime import datetime, timezone
from unittest.mock import Mock

import httpx
import pytest

from backups import BackupError, s3, service


def dest():
    return s3.S3Destination("https://storage.example.com", "bucket", "us-east-1", "test-key", "test-secret", "own/")


def transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(s3.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))


def test_aws_published_list_objects_signature():
    # https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html
    headers = s3.signed_headers(
        "GET", "examplebucket.s3.amazonaws.com", "/", "max-keys=2&prefix=J",
        hashlib.sha256(b"").hexdigest(), "us-east-1", "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert headers["Authorization"].endswith("34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")


def test_escaped_keys_and_paginated_list(monkeypatch):
    requests = []

    def handle(req):
        requests.append(req)
        if req.method == "PUT":
            return httpx.Response(200)
        if "continuation-token" not in req.url.params:
            return httpx.Response(200, text='<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Contents><Key>own/one</Key></Contents><IsTruncated>true</IsTruncated><NextContinuationToken>a+/= é</NextContinuationToken></ListBucketResult>')
        return httpx.Response(200, text='<ListBucketResult><Contents><Key>own/two</Key></Contents><IsTruncated>false</IsTruncated></ListBucketResult>')
    transport(monkeypatch, handle)
    target = dest()
    target.put_bytes("own/a +é", b"bytes")
    assert requests[0].url.raw_path == b"/bucket/own/a%20%2B%C3%A9"
    assert requests[0].headers["x-amz-content-sha256"] == hashlib.sha256(b"bytes").hexdigest()
    assert target.list_keys("own/") == ["own/one", "own/two"]
    assert b"continuation-token=a%2B%2F%3D%20%C3%A9" in requests[-1].url.raw_path


@pytest.mark.parametrize("code", [403, 200])
def test_xml_errors_even_in_http_200(monkeypatch, code):
    transport(monkeypatch, lambda req: httpx.Response(code, text='<Error><Code>AccessDenied</Code><Message>Wrong permissions test-secret</Message></Error>'))
    with pytest.raises(BackupError, match="AccessDenied") as exc:
        dest().put_bytes("file", b"x")
    assert "test-secret" not in str(exc.value)


@pytest.mark.parametrize("fail", [False, True])
def test_multipart_completion_and_abort(monkeypatch, tmp_path, fail):
    calls = []
    monkeypatch.setattr(s3, "PART_BYTES", 3)

    def handle(req):
        calls.append(req)
        if "uploads" in req.url.params:
            return httpx.Response(200, text="<Result><UploadId>a+/id</UploadId></Result>")
        if req.method == "PUT":
            if fail:
                return httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>")
            return httpx.Response(200, headers={"etag": '"part-tag"'})
        return httpx.Response(200, text="<CompleteMultipartUploadResult/>")
    transport(monkeypatch, handle)
    path = tmp_path / "dump"
    path.write_bytes(b"1234567")
    if fail:
        with pytest.raises(BackupError):
            dest().put_file("own/dump", path)
        assert calls[-1].method == "DELETE"
    else:
        dest().put_file("own/dump", path)
        assert [c.content for c in calls if c.method == "PUT"] == [b"123", b"456", b"7"]
        assert calls[-1].method == "POST"
        assert calls[-1].content.count(b"<Part>") == 3
    assert calls[-1].url.params["uploadId"] == "a+/id"


def test_prune_only_own_direct_backup_names():
    target = Mock(prefix="own/")
    names = [f"own/cakecrm-202611{day:02}T010203Z.dump" for day in range(1, 5)]
    target.list_keys.return_value = [*reversed(names), "own/readme", "own/nested/" + names[0][4:],
                                     "foreign/cakecrm-20261101T010203Z.dump"]
    service.prune(target, 2)
    assert [c.args[0] for c in target.delete.call_args_list] == names[:2]


@pytest.mark.parametrize("endpoint", ["ftp://example.com", "https://user:pass@example.com", "https://example.com/?x=1", "https://example.com/../base", "https://[invalid"])
def test_endpoint_validation(endpoint):
    with pytest.raises(BackupError):
        s3.validate_endpoint(endpoint)


def test_endpoint_base_path_is_in_the_signed_request(monkeypatch):
    calls = []
    def handle(req):
        calls.append(req)
        return httpx.Response(200)
    transport(monkeypatch, handle)
    target = s3.S3Destination("https://storage.example.com/proxy%20path/", "bucket", "us-east-1", "id", "secret", "")
    target.put_bytes("a +é", b"x")
    assert calls[0].url.raw_path == b"/proxy%20path/bucket/a%20%2B%C3%A9"


@pytest.mark.parametrize("endpoint, expected", [
    ("https://s3.example.com:443/", "https://s3.example.com"),
    ("http://127.0.0.1:80", "http://127.0.0.1"),
    ("http://127.0.0.1:9000", "http://127.0.0.1:9000"),
])
def test_default_port_is_dropped_to_match_the_host_header(endpoint, expected):
    assert s3.validate_endpoint(endpoint) == expected
