"""Internal MoneyAI user provisioning. Not a public registration endpoint."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import APIRouter, Request

from .. import auth
from ..db import connect, init_db
from ..security import hash_password
from ._deps import _set_session_cookie, json_response

router = APIRouter()

_PATH = "/api/internal/moneyai/provision"
_MAX_BODY_BYTES = 16 * 1024
_NONCE_TTL_MS = 10 * 60 * 1000


def _error(status_code: int, message: str):
    return json_response(
        {"ok": False, "error": message},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _verify_signature(request: Request, raw_body: bytes) -> bool:
    secret = os.environ.get("RPG_MONEYAI_INTERNAL_SECRET", "")
    if len(secret.encode()) < 32:
        return False
    if request.headers.get("X-Wanxiang-Service") != "moneyai":
        return False
    timestamp = request.headers.get("X-Wanxiang-Timestamp", "").strip()
    nonce = request.headers.get("X-Wanxiang-Nonce", "").strip()
    signature = request.headers.get("X-Wanxiang-Signature", "").strip()
    if (
        not timestamp
        or len(timestamp) > 20
        or not timestamp.isascii()
        or not timestamp.isdigit()
        or abs(int(time.time()) - int(timestamp)) > 300
    ):
        return False
    if (
        not 16 <= len(nonce) <= 128
        or not nonce.isascii()
        or not all(ch.isalnum() or ch in "_-" for ch in nonce)
    ):
        return False
    if len(signature) != 64 or any(ch not in "0123456789abcdef" for ch in signature):
        return False
    canonical = "\n".join(
        (
            timestamp,
            nonce,
            request.method.upper(),
            request.url.path,
            hashlib.sha256(raw_body).hexdigest(),
        )
    )
    expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _claim_nonce(nonce: str) -> str:
    import redis_bus

    client = redis_bus.get_sync_client()
    if client is None:
        return "unavailable"
    key = "rpg:moneyai:provision:nonce:" + hashlib.sha256(nonce.encode()).hexdigest()
    try:
        claimed = client.set(key, "1", nx=True, px=_NONCE_TTL_MS)
    except Exception:
        return "unavailable"
    return "claimed" if claimed is True else "replayed"


def _parse_payload(raw_body: bytes) -> tuple[str, str] | None:
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict) or set(body) != {"user_id", "display_name"}:
        return None
    user_id = body["user_id"]
    if isinstance(user_id, bool) or not isinstance(user_id, (str, int)):
        return None
    user_id = str(user_id).strip()
    display_name = body["display_name"]
    if not isinstance(display_name, str):
        return None
    display_name = display_name.strip()
    if (
        not user_id
        or len(user_id) > 128
        or not display_name
        or len(display_name) > 80
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in display_name)
    ):
        return None
    return user_id, display_name


def _provision_user(user_id: str, display_name: str) -> tuple[int, str] | None:
    username = "wx_" + hashlib.sha256(user_id.encode()).hexdigest()[:40]
    init_db()
    with connect() as db:
        db.execute(
            "select pg_advisory_xact_lock(hashtext(%s)::bigint)",
            ("moneyai_provision:" + username,),
        )
        row = db.execute("select * from users where username = %s", (username,)).fetchone()
        if row:
            if row.get("role") != "user" or row.get("deactivated_at") is not None:
                return None
            rpg_user_id = int(row["id"])
        else:
            password_hash = hash_password(secrets.token_urlsafe(32))
            row = db.execute(
                "insert into users(username, password_hash, display_name, role) "
                "values (%s, %s, %s, 'user') returning *",
                (username, password_hash, display_name),
            ).fetchone()
            rpg_user_id = int(row["id"])
        token = auth._issue_session(db, rpg_user_id)
    return rpg_user_id, token


@router.post(_PATH)
async def api_moneyai_provision(request: Request):
    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_BYTES:
        return _error(400, "invalid request")
    if not os.environ.get("RPG_MONEYAI_INTERNAL_SECRET", ""):
        return _error(503, "service unavailable")
    if not _verify_signature(request, raw_body):
        return _error(401, "unauthorized")
    nonce = request.headers.get("X-Wanxiang-Nonce", "").strip()
    nonce_status = await asyncio.to_thread(_claim_nonce, nonce)
    if nonce_status == "unavailable":
        return _error(503, "service unavailable")
    if nonce_status != "claimed":
        return _error(401, "unauthorized")
    payload = _parse_payload(raw_body)
    if payload is None:
        return _error(400, "invalid request")
    try:
        result = await asyncio.to_thread(_provision_user, *payload)
    except Exception:
        return _error(500, "provisioning failed")
    if result is None:
        return _error(409, "provisioning rejected")
    rpg_user_id, token = result
    response = json_response(
        {"ok": True, "rpg_user_id": rpg_user_id, "session_token": token},
        headers={"Cache-Control": "no-store"},
    )
    _set_session_cookie(response, request, token)
    return response
