"""Heartbeat interval env parsing (core/config._positive_int_env) — a bad value
must neither crash startup nor make the cadence fire every tick."""

from core.config import _positive_int_env


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("X_HB", raising=False)
    assert _positive_int_env("X_HB", 30) == 30


def test_valid_value(monkeypatch):
    monkeypatch.setenv("X_HB", "45")
    assert _positive_int_env("X_HB", 30) == 45


def test_non_integer_falls_back(monkeypatch):
    monkeypatch.setenv("X_HB", "abc")
    assert _positive_int_env("X_HB", 30) == 30


def test_zero_and_negative_fall_back(monkeypatch):
    monkeypatch.setenv("X_HB", "0")
    assert _positive_int_env("X_HB", 30) == 30
    monkeypatch.setenv("X_HB", "-5")
    assert _positive_int_env("X_HB", 30) == 30
