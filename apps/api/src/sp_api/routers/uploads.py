"""Upload and import endpoints.

The API's entire involvement in a 10 GB upload is minting a presigned URL. Bytes go
**browser -> object store** directly (CLAUDE.md §8) — routing them through here
would tie up a worker, hit proxy body limits, and offer no resume.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sp_core.storage.objects import (
    delete_object,
    object_exists,
    object_size,
    presign_put,
    raw_upload_key,
)
from sp_db.models import IngestItem, Upload
from sqlalchemy import func, select

from sp_api.deps import CurrentUser, DbSession
from sp_api.enqueue import enqueue_import
from sp_api.schemas import FailedItem, ImportStatus, Message, UploadCreate, UploadCreated

log = structlog.get_logger(__name__)
router = APIRouter(tags=["uploads"])

#: Strava archives for a decade of riding run large, but a 20 GB request is a
#: mistake or an attack, not an export.
_MAX_UPLOAD_BYTES = 20 * 1024**3

# Starlette renamed HTTP_422_UNPROCESSABLE_ENTITY; the literal is version-agnostic.
HTTP_422_UNPROCESSABLE = 422


@router.post("/uploads", response_model=UploadCreated, status_code=status.HTTP_201_CREATED)
async def create_upload(
    payload: UploadCreate, session: DbSession, user: CurrentUser
) -> UploadCreated:
    if payload.size_bytes > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="That file is larger than we can accept. Is it definitely a Strava export?",
        )
    if not payload.filename.lower().endswith(".zip"):
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail="Upload the .zip archive Strava emailed you.",
        )

    upload = Upload(
        user_id=user.id,
        object_key="",
        filename=payload.filename[:255],
        size_bytes=payload.size_bytes,
        status="awaiting_file",
    )
    session.add(upload)
    await session.flush()

    key = raw_upload_key(user.id, upload.id, payload.filename)
    upload.object_key = key
    presigned = presign_put(key, content_type="application/zip", expires_s=6 * 3600)

    log.info("upload.created", upload_id=str(upload.id), user_id=str(user.id))
    return UploadCreated(upload_id=upload.id, upload_url=presigned.url, object_key=key)


@router.post("/uploads/{upload_id}/complete", response_model=ImportStatus)
async def complete_upload(upload_id: UUID, session: DbSession, user: CurrentUser) -> ImportStatus:
    """Called by the browser once the PUT finishes. Verifies and enqueues."""
    upload = (
        await session.execute(
            select(Upload).where(Upload.id == upload_id, Upload.user_id == user.id)
        )
    ).scalar_one_or_none()
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such upload.")

    if upload.status not in ("awaiting_file", "failed"):
        # Idempotent: a double-click must not start a second import.
        return ImportStatus.model_validate(upload)

    if not object_exists(upload.object_key):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="We can't find the uploaded file. Please try uploading again.",
        )

    upload.size_bytes = object_size(upload.object_key) or upload.size_bytes
    upload.status = "queued"
    upload.started_at = datetime.now(UTC)
    upload.error = None

    # Commit *before* enqueueing. A worker can pick the task up within
    # milliseconds, and it must not read a row this transaction hasn't written yet.
    await session.commit()

    enqueue_import(str(upload.id), str(user.id))
    log.info("upload.enqueued", upload_id=str(upload.id))
    return ImportStatus.model_validate(upload)


@router.get("/imports", response_model=list[ImportStatus])
async def list_imports(session: DbSession, user: CurrentUser) -> list[ImportStatus]:
    rows = (
        await session.execute(
            select(Upload).where(Upload.user_id == user.id).order_by(Upload.created_at.desc())
        )
    ).scalars()
    return [ImportStatus.model_validate(row) for row in rows]


@router.get("/imports/{upload_id}", response_model=ImportStatus)
async def get_import(upload_id: UUID, session: DbSession, user: CurrentUser) -> ImportStatus:
    upload = (
        await session.execute(
            select(Upload).where(Upload.id == upload_id, Upload.user_id == user.id)
        )
    ).scalar_one_or_none()
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such import.")
    return ImportStatus.model_validate(upload)


@router.get("/imports/{upload_id}/failures", response_model=list[FailedItem])
async def get_import_failures(
    upload_id: UUID, session: DbSession, user: CurrentUser, limit: int = 200
) -> list[FailedItem]:
    """The specific files that couldn't be read.

    Partial success is the normal outcome of a decade-long archive, and the UI
    shows this list rather than a red error page (CLAUDE.md §4.6).
    """
    rows = (
        await session.execute(
            select(IngestItem)
            .where(
                IngestItem.upload_id == upload_id,
                IngestItem.user_id == user.id,
                IngestItem.status == "failed",
            )
            .limit(min(limit, 1000))
        )
    ).scalars()
    return [FailedItem(member_path=row.member_path, error=row.error) for row in rows]


@router.get("/imports/{upload_id}/events")
async def import_events(
    upload_id: UUID, request: Request, session: DbSession, user: CurrentUser
) -> StreamingResponse:
    """Server-sent progress stream.

    Polls the row rather than subscribing to Redis pub/sub: at one client per
    import the query is trivial, and it means progress survives an API restart.
    """
    owned = (
        await session.execute(
            select(Upload.id).where(Upload.id == upload_id, Upload.user_id == user.id)
        )
    ).first()
    if owned is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such import.")

    user_id = user.id

    async def stream():
        from sp_db.session import async_session_factory, set_tenant

        last_payload: str | None = None
        for _ in range(1800):  # ~1 hour ceiling at 2 s cadence
            if await request.is_disconnected():
                break

            async with async_session_factory()() as poll_session:
                await set_tenant(poll_session, user_id)
                row = (
                    await poll_session.execute(select(Upload).where(Upload.id == upload_id))
                ).scalar_one_or_none()
                if row is None:
                    break
                payload = json.dumps(
                    {
                        "status": row.status,
                        "items_total": row.items_total,
                        "items_done": row.items_done,
                        "items_failed": row.items_failed,
                        "activities_found": row.activities_found,
                        "dashboard_ready": row.fast_path_done_at is not None,
                        "error": row.error,
                    }
                )
                terminal = row.status in ("complete", "failed")

            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if terminal:
                break
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/imports/{upload_id}", response_model=Message)
async def delete_import(upload_id: UUID, session: DbSession, user: CurrentUser) -> Message:
    """Delete an import record and its raw archive.

    A deliberate, user-initiated exception to "raw data is immutable and kept"
    (CLAUDE.md §4.3) — that principle is about *us* never discarding data behind
    the user's back so we can recompute from source; it was never meant to stop
    someone reclaiming storage or removing a multi-GB export of their own
    location history once it's served its purpose. Only the raw zip goes —
    already-imported activities, streams, and derived metrics are untouched
    (`ingest_items` rows cascade-delete with the row, but those are just
    per-file bookkeeping, not user data).
    """
    upload = (
        await session.execute(
            select(Upload).where(Upload.id == upload_id, Upload.user_id == user.id)
        )
    ).scalar_one_or_none()
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such import.")
    if upload.status not in ("awaiting_file", "complete", "failed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This import is still running. Wait for it to finish before deleting it.",
        )

    if upload.object_key:
        delete_object(upload.object_key)
    await session.delete(upload)
    log.info("upload.deleted", upload_id=str(upload_id), user_id=str(user.id))
    return Message(message="Import removed and the uploaded archive was deleted. Your activities were kept.")


@router.get("/imports/{upload_id}/summary")
async def import_summary(upload_id: UUID, session: DbSession, user: CurrentUser) -> dict[str, int]:
    counts = (
        await session.execute(
            select(IngestItem.status, func.count())
            .where(IngestItem.upload_id == upload_id, IngestItem.user_id == user.id)
            .group_by(IngestItem.status)
        )
    ).all()
    return {status_name: int(count) for status_name, count in counts}
