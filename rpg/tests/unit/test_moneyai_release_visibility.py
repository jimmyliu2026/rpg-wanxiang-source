from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_RPG = Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

import redis_bus  # noqa: E402
from platform_app.api import moneyai_release as release  # noqa: E402

_SECRET = "test-only-moneyai-release-secret-32"
_PATH = "/api/internal/moneyai/release-visibility"


class _Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _DB:
    def __init__(self):
        self.updates = []
        self.current = {"id": 77, "is_public": False, "moneyai_visibility_version": 0}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        if flat.startswith("select id") and "from scripts where source_path" in flat:
            return _Result({"id": 77, "review_status": "unreviewed"})
        if flat.startswith("select id, is_public, moneyai_visibility_version"):
            return _Result(self.current)
        if flat.startswith("update scripts") and "moneyai_visibility_version=%s" in flat:
            self.updates.append((flat, params))
            version, visible = params[0], params[4]
            if version < self.current["moneyai_visibility_version"] or (
                version == self.current["moneyai_visibility_version"]
                and visible != self.current["is_public"]
            ):
                return _Result()
            self.current["is_public"] = visible
            self.current["moneyai_visibility_version"] = version
            return _Result({"id": 77})
        if flat.startswith("update scripts"):
            self.updates.append((flat, params))
            return _Result()
        raise AssertionError(flat)


class _Redis:
    def __init__(self):
        self.keys = set()

    def set(self, key, value, *, nx, px):
        if key in self.keys:
            return False
        self.keys.add(key)
        return True


def _signed(client, body, path=_PATH):
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    canonical = "\n".join((timestamp, nonce, "POST", path, hashlib.sha256(raw).hexdigest()))
    signature = hmac.new(_SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return client.post(path, content=raw, headers={
        "Content-Type": "application/json",
        "X-Wanxiang-Service": "moneyai",
        "X-Wanxiang-Timestamp": timestamp,
        "X-Wanxiang-Nonce": nonce,
        "X-Wanxiang-Signature": signature,
    })


def test_release_visibility_uses_internal_signature_and_release_mapping(monkeypatch):
    db = _DB()
    monkeypatch.setenv("RPG_MONEYAI_INTERNAL_SECRET", _SECRET)
    monkeypatch.setattr(release, "connect", lambda: db)
    monkeypatch.setattr(redis_bus, "get_sync_client", lambda: _Redis())
    app = FastAPI()
    app.include_router(release.router)
    client = TestClient(app)
    release_id = "release_contract_test"
    checked = _signed(
        client,
        {"release_id": release_id},
        path="/api/internal/moneyai/release-check",
    )
    assert checked.status_code == 200
    assert "is_public" not in db.updates[0][0]
    published = _signed(client, {"release_id": release_id, "visible": True, "version": 1})
    delisted = _signed(client, {"release_id": release_id, "visible": False, "version": 2})
    assert published.status_code == delisted.status_code == 200
    assert published.json()["script_id"] == delisted.json()["script_id"] == 77
    assert published.json()["version"] == 1
    assert delisted.json()["version"] == 2
    stale = _signed(client, {"release_id": release_id, "visible": True, "version": 1})
    assert stale.status_code == 200
    assert stale.json()["applied"] is False
    assert stale.json()["visible"] is False
    assert stale.json()["version"] == 2
    assert any("review_status='reviewed'" in sql for sql, _ in db.updates)
    assert any("is_public=false" in sql for sql, _ in db.updates)


def test_release_visibility_rejects_bad_signature(monkeypatch):
    db = _DB()
    monkeypatch.setenv("RPG_MONEYAI_INTERNAL_SECRET", _SECRET)
    monkeypatch.setattr(release, "connect", lambda: db)
    app = FastAPI()
    app.include_router(release.router)
    response = TestClient(app).post(
        _PATH,
        content=b"{}",
        headers={"X-Wanxiang-Service": "moneyai"},
    )
    assert response.status_code == 401
    assert not db.updates
