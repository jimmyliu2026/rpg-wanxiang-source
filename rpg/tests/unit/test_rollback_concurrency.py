from __future__ import annotations

import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from psycopg.types.json import Jsonb


def _save_with_three_rounds(uid: int) -> int:
    from platform_app import workspace
    from platform_app.db import connect

    with connect() as db:
        script_id = db.execute(
            "insert into scripts(owner_id,title) values (%s,%s) returning id",
            (uid, "rollback-concurrency-test"),
        ).fetchone()["id"]
    save_id = int(workspace.create_save(uid, script_id, "rollback concurrency")["id"])
    with connect() as db:
        root = db.execute(
            "select * from branch_commits where save_id=%s order by id asc limit 1",
            (save_id,),
        ).fetchone()
        parent_id = int(root["id"])
        state_path = str(root["state_path"] or "")
        history: list[dict] = []
        latest = None
        for turn in (1, 2, 3):
            history.extend([
                {"role": "user", "content": f"u{turn}"},
                {"role": "assistant", "content": f"a{turn}"},
            ])
            latest = {"history": list(history), "turn": turn}
            obj_hash = uuid.uuid4().hex
            row = db.execute(
                """
                insert into branch_commits(save_id,parent_id,turn_index,kind,
                  object_hash,tree_hash,title,message,summary,content_preview,state_path,state_snapshot)
                values (%s,%s,%s,'round',%s,%s,%s,%s,%s,%s,%s,%s) returning id
                """,
                (
                    save_id, parent_id, turn, obj_hash, obj_hash, f"turn {turn}",
                    f"round {turn}", f"summary {turn}", f"preview {turn}",
                    state_path, Jsonb(latest),
                ),
            ).fetchone()
            parent_id = int(row["id"])
        db.execute(
            "update game_saves set active_commit_id=%s,active_branch_node_id=%s,state_snapshot=%s where id=%s",
            (parent_id, parent_id, Jsonb(latest), save_id),
        )
        db.commit()
    return save_id


@pytest.fixture()
def save_env():
    from platform_app.db import connect, init_db

    init_db()
    username = f"rollback_test_{uuid.uuid4().hex[:10]}"
    with connect() as db:
        uid = db.execute(
            "insert into users(username,display_name) values (%s,'rollback-test') returning id",
            (username,),
        ).fetchone()["id"]
        db.commit()
    save_id = _save_with_three_rounds(uid)
    try:
        yield uid, save_id
    finally:
        with connect() as db:
            db.execute("delete from user_runtime where user_id=%s", (uid,))
            db.execute("delete from game_saves where id=%s", (save_id,))
            db.execute("delete from scripts where owner_id=%s", (uid,))
            db.execute("delete from users where id=%s", (uid,))
            db.commit()


def test_concurrent_rollbacks_cannot_restore_stale_runtime(save_env, monkeypatch):
    from platform_app.branches import deletion
    from platform_app.db import connect

    uid, save_id = save_env
    transaction_order: list[int] = []
    order_lock = threading.Lock()
    real_set_active = deletion._set_save_active

    def record_transaction(db, sid, commit_id, ref_id):
        with order_lock:
            transaction_order.append(int(commit_id))
        return real_set_active(db, sid, commit_id, ref_id)

    real_activate = deletion._runtime_module.activate_state_snapshot
    activation_count = 0

    def delayed_activation(*args, **kwargs):
        nonlocal activation_count
        with order_lock:
            activation_count += 1
            delayed = activation_count == 1
        if delayed:
            # 强制放大原实现的「先事务、后 runtime」交错窗口；新实现持锁等待，不会死锁。
            time.sleep(0.4)
        return real_activate(*args, **kwargs)

    monkeypatch.setattr(deletion, "_set_save_active", record_transaction)
    monkeypatch.setattr(deletion._runtime_module, "activate_state_snapshot", delayed_activation)
    start = threading.Barrier(2)

    def rollback(message_index):
        start.wait(timeout=10)
        return deletion.rollback_to_message(uid, save_id, message_index)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(rollback, (0, 4)))

    assert any(result.get("ok") for result in results)
    assert all(result.get("ok") or result.get("code") == "STATE_CONFLICT" for result in results)
    assert transaction_order
    with connect() as db:
        active = db.execute(
            "select coalesce(active_commit_id,active_branch_node_id) as cid from game_saves where id=%s",
            (save_id,),
        ).fetchone()["cid"]
    runtime = deletion._runtime_module.read_runtime(user_id=uid)
    assert int(active) == transaction_order[-1]
    assert runtime
    assert int(runtime["active_commit_id"]) == int(active)


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def test_rollback_conflict_maps_to_http_409(monkeypatch):
    from platform_app.api import saves as api_saves

    monkeypatch.setattr(
        api_saves.branches, "rollback_to_message",
        lambda *args: {
            "ok": False, "code": "STATE_CONFLICT", "conflict": True,
            "error": "存档已在别处回滚或推进，请刷新后重试", "active_commit_id": 9,
        },
    )
    response = asyncio.run(api_saves.api_rollback_to_message(
        _Request({"save_id": 7, "message_index": 0}), user={"id": 1},
    ))
    assert response.status_code == 409
    import json
    payload = json.loads(response.body)
    assert payload["code"] == "STATE_CONFLICT"
    assert payload["active_commit_id"] == 9
