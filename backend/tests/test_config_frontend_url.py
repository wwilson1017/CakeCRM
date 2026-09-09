"""Which base URL this install considers its own (core.config._configured_base_url).

`Settings.frontend_url` always holds a string — its last fallback is a dev default — so
"was a public address actually configured?" is a question the fallback chain erases.
`crm.links.deal_url` asks it to decide between an absolute and a relative deep link, and
getting it wrong means pasting `http://localhost:5173` into a message that leaves the app
(issue #145).

Tested through the helper rather than through `Settings`: the settings object is built at
import and shared by reference across the app, so reloading the module to re-evaluate its
class body would hand other modules a second, stale singleton.
"""

from core import config


def test_no_configuration_is_reported_as_default(monkeypatch):
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.setattr(config, "RAILWAY_PUBLIC_URL", "")
    assert config._configured_base_url() == ""


def test_an_explicit_frontend_url_wins(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://crm.example.com")
    monkeypatch.setattr(config, "RAILWAY_PUBLIC_URL", "https://ignored.up.railway.app")
    assert config._configured_base_url() == "https://crm.example.com"


def test_a_railway_domain_alone_counts_as_configured(monkeypatch):
    """The deploy target injects RAILWAY_PUBLIC_DOMAIN and nothing else, so this is the
    path that decides whether a real deployment hands out working links."""
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.setattr(config, "RAILWAY_PUBLIC_URL", "https://cakecrm.up.railway.app")
    assert config._configured_base_url() == "https://cakecrm.up.railway.app"


def test_an_empty_frontend_url_falls_through_rather_than_counting(monkeypatch):
    """`FRONTEND_URL=` in a .env file reads as set-but-empty. An `os.getenv(..) is not
    None` check would call that configured and then build links against an empty base."""
    monkeypatch.setenv("FRONTEND_URL", "")
    monkeypatch.setattr(config, "RAILWAY_PUBLIC_URL", "https://cakecrm.up.railway.app")
    assert config._configured_base_url() == "https://cakecrm.up.railway.app"
