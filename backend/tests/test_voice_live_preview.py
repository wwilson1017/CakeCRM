import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from voice import router as mod, service


@pytest.mark.parametrize('row', [None, {}, {'voice_live_preview': False}, {'voice_live_preview': 'true'}])
def test_defaults_off(monkeypatch, row):
    monkeypatch.setattr(service, 'pg_fetchone', lambda *args: row)
    assert service.get_live_preview() is False


def test_unreadable_defaults_off(monkeypatch):
    def fail(*args):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(service, 'pg_fetchone', fail)
    assert service.get_live_preview() is False


def test_set_get(monkeypatch):
    row = {'voice_live_preview': False}

    def execute(sql, params):
        assert 'WHERE id = 1' in sql and 'updated_at' in sql
        row['voice_live_preview'] = params[0]
    monkeypatch.setattr(service, 'pg_execute', execute)
    monkeypatch.setattr(service, 'pg_fetchone', lambda *args: row)
    for enabled in (True, False):
        service.set_live_preview(enabled)
        assert service.get_live_preview() is enabled


@pytest.mark.parametrize('ai,transcriber', [(True, True), (True, False), (False, True)])
def test_admin_routes(monkeypatch, ai, transcriber):
    app = FastAPI()
    app.include_router(mod.router, prefix='/api/voice')
    app.dependency_overrides[get_current_user] = lambda: {'id': 1, 'role': 'admin'}
    monkeypatch.setattr(mod, 'get_ai_provider', lambda: object() if ai else None)
    monkeypatch.setattr(mod, 'get_transcriber', lambda: object() if transcriber else None)
    monkeypatch.setattr(service, 'get_live_preview', lambda: True)
    writes = []
    monkeypatch.setattr(service, 'set_live_preview', writes.append)
    client = TestClient(app)
    assert client.get('/api/voice/live-preview').json() == {'enabled': True, 'voice_ready': ai and transcriber}
    response = client.post('/api/voice/live-preview', json={'enabled': True})
    assert response.status_code == (200 if ai and transcriber else 400)
    assert writes == ([True] if ai and transcriber else [])
    assert client.post('/api/voice/live-preview', json={'enabled': False}).json() == {'enabled': False}
    assert writes[-1] is False
