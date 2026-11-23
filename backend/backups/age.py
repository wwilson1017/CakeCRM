"""Streaming age v1 scrypt writer: https://c2sp.org/age (age-encryption.org/v1).

The CLI requires a TTY for passphrases and pyrage's bytes API buffers entire files.
This writer keeps only two 64 KiB chunks in memory; tests decrypt with pyrage.
"""

import base64
import hashlib
import hmac
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _b64(data: bytes) -> bytes:
    return base64.b64encode(data).rstrip(b"=")


def _derive(key: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(key)


def encrypt_file(src: Path, dst: Path, passphrase: str) -> None:
    file_key, salt = os.urandom(16), os.urandom(16)
    wrap_key = hashlib.scrypt(
        passphrase.encode(), salt=b"age-encryption.org/v1/scrypt" + salt,
        n=2**18, r=8, p=1, maxmem=2**29, dklen=32,
    )
    body = ChaCha20Poly1305(wrap_key).encrypt(bytes(12), file_key, None)
    header = b"age-encryption.org/v1\n-> scrypt " + _b64(salt) + b" 18\n" + _b64(body) + b"\n---"
    mac = hmac.digest(_derive(file_key, b"", b"header"), header, "sha256")
    nonce = os.urandom(16)
    cipher = ChaCha20Poly1305(_derive(file_key, nonce, b"payload"))
    with src.open("rb") as source, dst.open("wb") as target:
        target.write(header + b" " + _b64(mac) + b"\n" + nonce)
        chunk = source.read(65536)
        counter = 0
        while True:
            following = source.read(65536)
            chunk_nonce = counter.to_bytes(11, "big") + bytes([not following])
            target.write(cipher.encrypt(chunk_nonce, chunk, None))
            if not following:
                break
            chunk = following
            counter += 1
