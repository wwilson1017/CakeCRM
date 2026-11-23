"""Admin-only settings and streamed downloads. No assistant tool surface."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from backups import BackupError, service
from backups.dump import cleanup, make_backup
from core.auth import require_admin

router = APIRouter()


class DestinationBody(BaseModel):
    endpoint: str = Field(max_length=2048)
    bucket: str = Field(min_length=1, max_length=255)
    region: str = Field(default="us-east-1", min_length=1, max_length=128)
    prefix: str = Field(default="", max_length=512)
    access_key_id: str = Field(min_length=1, max_length=512)
    secret_key: str | None = Field(default=None, max_length=4096)
    retention: int = Field(default=30, ge=1, le=365)


class PassphraseBody(BaseModel):
    passphrase: str | None = Field(min_length=8, max_length=1024)


@router.get("/status")
def status(_user: dict = Depends(require_admin)):
    return service.public_status()


@router.get("/download")
def download(_user: dict = Depends(require_admin)):
    if not service.backup_lock.acquire(blocking=False):
        raise HTTPException(409, "A backup is already being made; try again in a minute")
    try:
        path, name = make_backup(service.passphrase(service.get_settings()))
        return FileResponse(path, filename=name, media_type="application/octet-stream",
                            background=BackgroundTask(cleanup, path))
    except BackupError as exc:
        raise HTTPException(500, str(exc)) from None
    finally:
        service.backup_lock.release()


@router.put("/destination")
def save_destination(body: DestinationBody, _user: dict = Depends(require_admin)):
    try:
        service.save_destination(**body.model_dump())
    except BackupError as exc:
        raise HTTPException(400, str(exc)) from None
    return service.public_status()


@router.delete("/destination")
def clear_destination(_user: dict = Depends(require_admin)):
    service.clear_destination()
    return service.public_status()


@router.post("/test")
def test_connection(_user: dict = Depends(require_admin)):
    try:
        service.test_connection()
    except BackupError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True}


@router.put("/passphrase")
def set_passphrase(body: PassphraseBody, _user: dict = Depends(require_admin)):
    try:
        service.set_passphrase(body.passphrase)
    except BackupError as exc:
        raise HTTPException(400, str(exc)) from None
    return service.public_status()
