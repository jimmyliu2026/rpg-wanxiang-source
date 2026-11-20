from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_RPG = Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

from platform_app import branches  # noqa: E402
from platform_app.api import saves as api_saves  # noqa: E402
from platform_app.branches import activation, commits, deletion  # noqa: E402
from platform_app.perms import PermissionError as SavePermissionError  # noqa: E402
from platform_app.workspace import listing  # noqa: E402


class _Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _DB:
    def __init__(self, *, owner_id=1):
        self.save = {"id": 7, "user_id": owner_id, "title": "save"}
        self.commit = {"id": 100, "save_id": 7, "kind": "round"}
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        self.queries.append(flat)
        if flat == "select * from branch_commits where id = %s":
            return _Result(dict(self.commit) if params[0] == 100 else None)
        if flat == "select 1 from game_saves where id = %s and user_id = %s":
            owned = params == (7, self.save["user_id"])
            return _Result({"1": 1} if owned else None)
        if flat == "select * from game_saves where id = %s":
            return _Result(dict(self.save) if params[0] == 7 else None)
        raise AssertionError(f"permission check escaped its expected path: {flat}")


def _client(user_id=2):
    app = FastAPI()
    app.include_router(api_saves.router)
    app.dependency_overrides[api_saves.require_user] = lambda: {"id": user_id, "role": "user"}
    return TestClient(app)


def test_commit_lookup_uses_authoritative_save_permission():
    db = _DB(owner_id=2)
    own = commits._commit_for_user(db, 2, 100)
    assert own["id"] == 100 and own["user_id"] == 2
    assert commits._commit_for_user(db, 2, 999) is None
    with pytest.raises(SavePermissionError):
        commits._commit_for_user(db, 3, 100)


@pytest.mark.parametrize("path,body", [
    ("/api/branches/continue", {"node_id": 100}),
    ("/api/branches/continue", {"save_id": 7, "message_index": 1}),
    ("/api/branches/activate", {"node_id": 100}),
    ("/api/branches/delete", {"node_id": 100}),
    ("/api/branches/rollback", {"save_id": 7, "message_index": 1}),
])
def test_cross_user_branch_operations_return_exact_403(monkeypatch, path, body):
    db = _DB(owner_id=1)
    monkeypatch.setattr(api_saves, "connect", lambda: db)
    for module in (activation, deletion):
        monkeypatch.setattr(module, "connect", lambda: db)
        monkeypatch.setattr(module, "init_db", lambda: None)
    response = _client(user_id=2).post(path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["ok"] is False
    assert not any("pg_advisory" in query for query in db.queries)


def test_owner_branch_operations_still_return_200(monkeypatch):
    db = _DB(owner_id=2)
    monkeypatch.setattr(api_saves, "connect", lambda: db)
    monkeypatch.setitem(sys.modules, "app", SimpleNamespace(_invalidate_user_cache=lambda user: None))
    monkeypatch.setattr(branches, "resolve_commit_id_by_message", lambda uid, sid, idx: 100)
    for name in ("continue_from", "activate_node", "delete_subtree", "rollback_to_message"):
        monkeypatch.setattr(branches, name, lambda user_id, *args: {"ok": True})
    client = _client(user_id=2)
    for path, body in (
        ("/api/branches/continue", {"node_id": 100}),
        ("/api/branches/continue", {"save_id": 7, "message_index": 1}),
        ("/api/branches/activate", {"node_id": 100}),
        ("/api/branches/delete", {"node_id": 100}),
        ("/api/branches/rollback", {"save_id": 7, "message_index": 1}),
    ):
        response = client.post(path, json=body)
        assert response.status_code == 200, (path, response.text)
        assert response.json()["ok"] is True


def test_save_detail_uses_owns_save_and_preserves_owner_read(monkeypatch):
    db = _DB(owner_id=2)
    monkeypatch.setattr(listing, "connect", lambda: db)
    assert listing.save_detail(2, 7)["title"] == "save"
    with pytest.raises(ValueError, match="无权访问该存档"):
        listing.save_detail(3, 7)
