from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_RPG = Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

import redis_bus  # noqa: E402
from platform_app.api import moneyai_provision as provision  # noqa: E402

_SECRET = "test-only-moneyai-service-secret-32-bytes"
_PATH = "/api/internal/moneyai/provision"


class _Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _DB:
    def __init__(self):
        self.users = []
        self.sessions = []
        self.locks = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        if flat.startswith("select pg_advisory_xact_lock"):
            self.locks.append(params[0])
            return _Result({})
        if flat.startswith("select * from users where username"):
            row = next((u for u in self.users if u["username"] == params[0]), None)
            return _Result(dict(row) if row else None)
        if flat.startswith("insert into users"):
            row = {
                "id": max((u["id"] for u in self.users), default=0) + 1,
                "username": params[0],
                "password_hash": params[1],
                "display_name": params[2],
                "role": "user",
                "deactivated_at": None,
            }
            self.users.append(row)
            return _Result(dict(row))
        if flat.startswith("select count(*) as n from sessions"):
            return _Result({"n": sum(s["user_id"] == params[0] for s in self.sessions)})
        if flat.startswith("insert into sessions"):
            self.sessions.append({
                "token": params[0], "token_hash": params[1],
                "user_id": params[2], "expires_at": params[3],
            })
            return _Result({})
        raise AssertionError(f"unexpected SQL: {flat}")


class _Redis:
    def __init__(self):
        self.keys = set()

    def set(self, key, value, *, nx, px):
        assert nx is True and px == 600_000 and value == "1"
        if key in self.keys:
            return False
        self.keys.add(key)
        return True


@pytest.fixture
def harness(monkeypatch):
    db = _DB()
    redis = _Redis()
    monkeypatch.setenv("RPG_MONEYAI_INTERNAL_SECRET", _SECRET)
    monkeypatch.setattr(provision, "connect", lambda: db)
    monkeypatch.setattr(provision, "init_db", lambda: None)
    monkeypatch.setattr(redis_bus, "get_sync_client", lambda: redis)
    app = FastAPI()
    app.include_router(provision.router)
    return db, redis, TestClient(app)


def _signed_request(client, body, *, nonce=None, timestamp=None, signature=None):
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    nonce = nonce or uuid.uuid4().hex
    canonical = "\n".join((timestamp, nonce, "POST", _PATH, hashlib.sha256(raw).hexdigest()))
    signature = signature or hmac.new(_SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return client.post(_PATH, content=raw, headers={
        "Content-Type": "application/json",
        "X-Wanxiang-Service": "moneyai",
        "X-Wanxiang-Timestamp": timestamp,
        "X-Wanxiang-Nonce": nonce,
        "X-Wanxiang-Signature": signature,
    })


def test_provisioning_is_idempotent_and_sessions_are_hashed(harness):
    db, _redis, client = harness
    body = {"user_id": "moneyai-user-1", "display_name": "玩家一"}
    first = _signed_request(client, body)
    second = _signed_request(client, body)
    assert first.status_code == second.status_code == 200
    one, two = first.json(), second.json()
    assert one["rpg_user_id"] == two["rpg_user_id"]
    assert one["session_token"] != two["session_token"]
    assert first.cookies.get("rpg_session") == one["session_token"]
    assert first.headers["cache-control"] == "no-store"
    assert len(db.users) == 1 and len(db.sessions) == 2
    assert db.users[0]["role"] == "user"
    assert db.users[0]["password_hash"] != ""
    assert db.users[0]["password_hash"] != "moneyai-user-1"
    assert len(db.locks) == 2
    for session, response in zip(db.sessions, (one, two), strict=True):
        token = response["session_token"]
        assert session["token"] == ""
        assert session["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in repr(db.sessions)


def test_bad_signature_and_replay_are_rejected(harness):
    db, _redis, client = harness
    body = {"user_id": 42, "display_name": "玩家"}
    bad = _signed_request(client, body, signature="0" * 64)
    assert bad.status_code == 401 and not db.users
    nonce = uuid.uuid4().hex
    good = _signed_request(client, body, nonce=nonce)
    replay = _signed_request(client, body, nonce=nonce)
    assert good.status_code == 200
    assert replay.status_code == 401
    assert len(db.users) == 1 and len(db.sessions) == 1


def test_existing_admin_is_never_reused(harness):
    db, _redis, client = harness
    user_id = "moneyai-admin-collision"
    username = "wx_" + hashlib.sha256(user_id.encode()).hexdigest()[:40]
    db.users.append({
        "id": 99, "username": username, "password_hash": "secret-hash",
        "display_name": "admin", "role": "admin", "deactivated_at": None,
    })
    response = _signed_request(client, {"user_id": user_id, "display_name": "new name"})
    assert response.status_code == 409
    assert response.json()["error"] == "provisioning rejected"
    assert db.users[0]["role"] == "admin" and not db.sessions


def test_redis_outage_fails_closed(harness, monkeypatch):
    db, _redis, client = harness
    monkeypatch.setattr(redis_bus, "get_sync_client", lambda: None)
    response = _signed_request(client, {"user_id": "u", "display_name": "玩家"})
    assert response.status_code == 503
    assert response.json()["error"] == "service unavailable"
    assert not db.users and not db.sessions


def test_stale_timestamp_and_extra_fields_are_rejected(harness):
    db, _redis, client = harness
    stale = _signed_request(
        client, {"user_id": "u", "display_name": "玩家"}, timestamp=int(time.time()) - 301,
    )
    extra = _signed_request(client, {"user_id": "u", "display_name": "玩家", "role": "admin"})
    assert stale.status_code == 401
    assert extra.status_code == 400
    assert not db.users and not db.sessions
