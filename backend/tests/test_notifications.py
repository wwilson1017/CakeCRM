"""Notification delivery fan-out (notifications/delivery.py).

Hermetic: pywebpush is injected as a fake module (the lazy import makes this
trivial), the notification service + subscriptions + vapid are mocked. Covers
create-row-before-send (R12), web_push success/expiry-prune, empty-subs, the
Telegram stub, and per-channel error isolation.
"""

import sys
import types

import pytest

from notifications import delivery, subscriptions, vapid


class FakeWebPushException(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.response = types.SimpleNamespace(status_code=status) if status else None


def _install_pywebpush(monkeypatch, *, fail_status=None):
    mod = types.ModuleType("pywebpush")
    calls = []

    def webpush(**kwargs):
        calls.append(kwargs)
        if fail_status:
            raise FakeWebPushException("gone", status=fail_status)

    mod.webpush = webpush
    mod.WebPushException = FakeWebPushException
    monkeypatch.setitem(sys.modules, "pywebpush", mod)
    return calls


@pytest.fixture
def wiring(monkeypatch):
    order = []
    created = {}

    def create_notification(title, message, channels_sent=None, notification_id=None):
        order.append("create")
        created["id"] = "nid-1"
        created["channels_at_create"] = list(channels_sent or [])
        return "nid-1"

    def update_channels(nid, channels):
        order.append("update")
        created["final_channels"] = list(channels)

    monkeypatch.setattr(delivery.service, "create_notification", create_notification)
    monkeypatch.setattr(delivery.service, "update_channels", update_channels)
    monkeypatch.setattr(vapid, "get_vapid_keys", lambda: ("pub", "priv"))
    monkeypatch.setattr(vapid, "get_vapid_claims", lambda: {"sub": "mailto:a@b.c"})
    return order, created


_SUB = {"endpoint": "https://push.example/abc", "p256dh": "p", "auth": "a", "user_agent": ""}


def test_creates_row_before_sending(wiring, monkeypatch):
    order, created = wiring
    _install_pywebpush(monkeypatch)
    monkeypatch.setattr(subscriptions, "list_subscriptions", lambda: [_SUB])
    result = delivery.deliver_notification("Hi", "there")
    assert order[0] == "create" and order[-1] == "update"   # row first (R12)
    assert result["web_push"] is True
    assert "web_push" in result["channels_sent"]
    assert created["final_channels"] == ["web_push"]


def test_no_subscriptions_still_logs(wiring, monkeypatch):
    order, created = wiring
    monkeypatch.setattr(subscriptions, "list_subscriptions", lambda: [])
    result = delivery.deliver_notification("Hi", "there")
    assert result["channels_sent"] == []   # no push channel
    assert result["web_push"] is False
    assert "create" in order                # but the in-app row was written


def test_expired_subscription_pruned(wiring, monkeypatch):
    _wiring = wiring
    _install_pywebpush(monkeypatch, fail_status=410)
    monkeypatch.setattr(subscriptions, "list_subscriptions", lambda: [_SUB])
    removed = []
    monkeypatch.setattr(subscriptions, "remove_subscription", lambda ep: removed.append(ep))
    result = delivery.deliver_notification("Hi", "there")
    assert removed == [_SUB["endpoint"]]         # 410 → pruned via status_code, not string
    assert result["web_push"] is False


def test_invalid_endpoint_skipped(wiring, monkeypatch):
    _install_pywebpush(monkeypatch)
    monkeypatch.setattr(subscriptions, "list_subscriptions",
                        lambda: [{"endpoint": "http://insecure/x", "p256dh": "p", "auth": "a"}])
    result = delivery.deliver_notification("Hi", "there")
    assert result["web_push"] is False           # non-https endpoint never sent


def test_telegram_stub_absent_is_false(wiring, monkeypatch):
    # No telegram module present (#7 not landed) → _send_telegram returns False,
    # never raises, and telegram is absent from channels_sent.
    monkeypatch.setattr(subscriptions, "list_subscriptions", lambda: [])
    monkeypatch.setitem(sys.modules, "telegram", None)  # force ImportError on submodule import
    result = delivery.deliver_notification("Hi", "there")
    assert "telegram" not in result["channels_sent"]


def test_pywebpush_missing_degrades(wiring, monkeypatch):
    # Simulate pywebpush not installed → web push channel unavailable, no crash.
    monkeypatch.setitem(sys.modules, "pywebpush", None)
    monkeypatch.setattr(subscriptions, "list_subscriptions", lambda: [_SUB])
    result = delivery.deliver_notification("Hi", "there")
    assert result["web_push"] is False


def test_create_notification_failure_never_raises(monkeypatch):
    # Even if the audit row can't be written, deliver_notification must not raise.
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(delivery.service, "create_notification", boom)
    monkeypatch.setattr(delivery.service, "update_channels", lambda *a, **k: None)
    monkeypatch.setattr(subscriptions, "list_subscriptions", lambda: [])
    result = delivery.deliver_notification("Hi", "there")   # must not raise
    assert result["ok"] is True and result["notification_id"]


@pytest.mark.parametrize("endpoint,ok", [
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://web.push.apple.com/xyz", True),
    ("http://fcm.googleapis.com/x", False),        # not https
    ("https://localhost/x", False),                 # localhost
    ("https://127.0.0.1/x", False),                 # loopback IP literal
    ("https://10.0.0.5/x", False),                   # private IP literal
    ("https://169.254.169.254/latest", False),       # link-local (cloud metadata)
    ("https://[::1]/x", False),                       # IPv6 loopback
    ("", False),
    ("https://" + "a" * 3000, False),                # too long
])
def test_is_safe_push_endpoint(endpoint, ok):
    assert delivery.is_safe_push_endpoint(endpoint) is ok
