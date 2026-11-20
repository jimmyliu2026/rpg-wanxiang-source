"""Internal MoneyAI release visibility adapter; never a public review endpoint."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

from fastapi import APIRouter, Request

from ..db import connect, expose
from .moneyai_provision import _claim_nonce, _error, _verify_signature

router = APIRouter()
_PATH = "/api/internal/moneyai/release-visibility"
_RELEASE = re.compile(r"release_[A-Za-z0-9_-]{1,120}\Z")


def _set_visibility(release_id: str, visible: bool, version: int) -> dict:
    with connect() as db:
        script = db.execute(
            "select id from scripts where source_path = %s",
            (f"moneyai://releases/{release_id}",),
        ).fetchone()
        if not script:
            return {"found": False}
        if visible:
            result = db.execute(
                "update scripts set review_status='reviewed', reviewed_at=now(), "
                "is_public=true, published_at=coalesce(published_at,now()), updated_at=now(), "
                "moneyai_visibility_version=%s where id=%s "
                "and (moneyai_visibility_version < %s "
                "or (moneyai_visibility_version = %s and is_public = %s)) returning id",
                (version, script["id"], version, version, visible),
            ).fetchone()
        else:
            result = db.execute(
                "update scripts set is_public=false, updated_at=now(), "
                "moneyai_visibility_version=%s where id=%s "
                "and (moneyai_visibility_version < %s "
                "or (moneyai_visibility_version = %s and is_public = %s)) returning id",
                (version, script["id"], version, version, visible),
            ).fetchone()
        current = db.execute(
            "select id, is_public, moneyai_visibility_version from scripts where id=%s",
            (script["id"],),
        ).fetchone()
        return {
            "found": True,
            "script_id": int(script["id"]),
            "applied": result is not None,
            "visible": bool(current["is_public"]),
            "version": int(current["moneyai_visibility_version"]),
        }


def _set_checked(release_id: str) -> dict:
    with connect() as db:
        script = db.execute(
            "select id from scripts where source_path = %s",
            (f"moneyai://releases/{release_id}",),
        ).fetchone()
        if not script:
            return {"found": False}
        db.execute(
            "update scripts set review_status='reviewed', reviewed_at=now(), updated_at=now() "
            "where id=%s",
            (script["id"],),
        )
        return {"found": True, "script_id": int(script["id"])}


def _release_manifest(release_id: str) -> dict:
    def clean(row):
        data = expose(row) or {}
        for key in list(data):
            if "embedding" in key:
                data.pop(key, None)
        return data

    with connect() as db:
        script = db.execute(
            "select id,title,source_path,moneyai_source_sha256,chapter_count,word_count "
            "from scripts where source_path = %s",
            (f"moneyai://releases/{release_id}",),
        ).fetchone()
        if not script:
            return {"found": False}
        script_id = int(script["id"])
        chapters = db.execute(
            "select chapter_index,title,content,volume_title,source_marker,exclude_from_extraction "
            "from script_chapters where script_id = %s order by chapter_index,id",
            (script_id,),
        ).fetchall()
        cards = db.execute(
            "select * from character_cards where script_id = %s order by id", (script_id,)
        ).fetchall()
        worldbook = db.execute(
            "select * from worldbook_entries where script_id = %s order by id", (script_id,)
        ).fetchall()
        canon = db.execute(
            "select * from kb_canon_entities where script_id = %s order by id", (script_id,)
        ).fetchall()
        anchors = db.execute(
            "select * from script_timeline_anchors where script_id = %s order by chapter_min,id",
            (script_id,),
        ).fetchall()
        phases = db.execute(
            "select * from phase_digests where script_id = %s order by chapter_min,id",
            (script_id,),
        ).fetchall()
        facts = db.execute(
            "select * from chapter_facts where script_id = %s order by chapter,id",
            (script_id,),
        ).fetchall()
        manifest = {
            "script": clean(script),
            "chapters": [clean(row) for row in chapters],
            "cards": [clean(row) for row in cards],
            "worldbook": [clean(row) for row in worldbook],
            "canon": [clean(row) for row in canon],
            "anchors": [clean(row) for row in anchors],
            "phase_digests": [clean(row) for row in phases],
            "chapter_facts": [clean(row) for row in facts],
        }
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
        return {
            "found": True,
            "source_sha256": script["moneyai_source_sha256"],
            "module_sha256": hashlib.sha256(encoded).hexdigest(),
            "counts": {key: len(value) for key, value in manifest.items() if isinstance(value, list)},
        }


@router.post("/api/internal/moneyai/release-check")
async def api_moneyai_release_check(request: Request):
    raw_body = await request.body()
    if len(raw_body) > 4096 or not _verify_signature(request, raw_body):
        return _error(401, "unauthorized")
    nonce = request.headers.get("X-Wanxiang-Nonce", "").strip()
    nonce_status = await asyncio.to_thread(_claim_nonce, nonce)
    if nonce_status == "unavailable":
        return _error(503, "service unavailable")
    if nonce_status != "claimed":
        return _error(401, "unauthorized")
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error(400, "invalid request")
    if (
        not isinstance(body, dict)
        or set(body) != {"release_id"}
        or not isinstance(body["release_id"], str)
        or not _RELEASE.fullmatch(body["release_id"])
    ):
        return _error(400, "invalid request")
    result = await asyncio.to_thread(_set_checked, body["release_id"])
    if not result["found"]:
        return _error(404, "release not found")
    return {"ok": True, "release_id": body["release_id"], "script_id": result["script_id"], "review_status": "reviewed"}


@router.post("/api/internal/moneyai/release-manifest")
async def api_moneyai_release_manifest(request: Request):
    raw_body = await request.body()
    if len(raw_body) > 4096 or not _verify_signature(request, raw_body):
        return _error(401, "unauthorized")
    nonce = request.headers.get("X-Wanxiang-Nonce", "").strip()
    nonce_status = await asyncio.to_thread(_claim_nonce, nonce)
    if nonce_status == "unavailable":
        return _error(503, "service unavailable")
    if nonce_status != "claimed":
        return _error(401, "unauthorized")
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error(400, "invalid request")
    if (
        not isinstance(body, dict)
        or set(body) != {"release_id"}
        or not isinstance(body["release_id"], str)
        or not _RELEASE.fullmatch(body["release_id"])
    ):
        return _error(400, "invalid request")
    result = await asyncio.to_thread(_release_manifest, body["release_id"])
    if not result["found"]:
        return _error(404, "release not found")
    return {"ok": True, "release_id": body["release_id"], **result}


@router.post(_PATH)
async def api_moneyai_release_visibility(request: Request):
    raw_body = await request.body()
    if len(raw_body) > 4096 or not _verify_signature(request, raw_body):
        return _error(401, "unauthorized")
    nonce = request.headers.get("X-Wanxiang-Nonce", "").strip()
    nonce_status = await asyncio.to_thread(_claim_nonce, nonce)
    if nonce_status == "unavailable":
        return _error(503, "service unavailable")
    if nonce_status != "claimed":
        return _error(401, "unauthorized")
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error(400, "invalid request")
    if (
        not isinstance(body, dict)
        or set(body) != {"release_id", "visible", "version"}
        or not isinstance(body["release_id"], str)
        or not _RELEASE.fullmatch(body["release_id"])
        or not isinstance(body["visible"], bool)
        or not isinstance(body["version"], int)
        or isinstance(body["version"], bool)
        or body["version"] < 1
    ):
        return _error(400, "invalid request")
    result = await asyncio.to_thread(
        _set_visibility, body["release_id"], body["visible"], body["version"]
    )
    if not result["found"]:
        return _error(404, "release not found")
    return {
        "ok": True,
        "release_id": body["release_id"],
        "script_id": result["script_id"],
        "visible": result["visible"],
        "version": result["version"],
        "applied": result["applied"],
    }
