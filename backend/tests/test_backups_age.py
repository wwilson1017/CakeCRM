"""Independent age interoperability, including exact chunk boundaries."""

import os

import pyrage
import pytest

from backups.age import encrypt_file


@pytest.mark.parametrize("size", [0, 1, 65536, 65537, 200000])
def test_age_interoperability(tmp_path, size):
    data = os.urandom(size)
    src, dst = tmp_path / "plain", tmp_path / "cipher"
    src.write_bytes(data)
    encrypt_file(src, dst, "a long test passphrase")
    assert pyrage.passphrase.decrypt(dst.read_bytes(), "a long test passphrase") == data


def test_wrong_passphrase_and_tampering_fail(tmp_path):
    src, dst = tmp_path / "plain", tmp_path / "cipher"
    src.write_bytes(b"attachment bytes")
    encrypt_file(src, dst, "a long test passphrase")
    data = dst.read_bytes()
    with pytest.raises(pyrage.DecryptError):
        pyrage.passphrase.decrypt(data, "wrong passphrase")
    with pytest.raises(pyrage.DecryptError):
        pyrage.passphrase.decrypt(data[:-1], "a long test passphrase")
