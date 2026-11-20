from __future__ import annotations

import asyncio
import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest


class _JsonRequest:
    def __init__(self, body: dict, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    async def json(self):
        return self._body


def test_chunk_upload_import_does_not_validate_title_as_filename(monkeypatch):
    from platform_app.api import scripts as scripts_api

    calls: list[dict] = []

    def fake_import_script(user_id, file_item, **kwargs):
        calls.append({"user_id": user_id, "file_item": file_item, **kwargs})
        return {"script_id": 123, "chapter_count": 1}

    monkeypatch.setattr(scripts_api.script_import, "import_script", fake_import_script)

    response = asyncio.run(scripts_api.api_import_script(
        _JsonRequest({
            "upload_id": "upload_smoke_id",
            "title": "剧本标题没有后缀",
            "split_rule": "auto",
            "custom_pattern": "",
        }, headers={"Idempotency-Key": "legacy-client-key"}),
        user={"id": 7},
    ))

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["script_id"] == 123
    assert "idempotent_replay" not in payload
    assert calls == [{
        "user_id": 7,
        "file_item": {},
        "split_rule": "auto",
        "custom_pattern": "",
        "title": "剧本标题没有后缀",
        "upload_id": "upload_smoke_id",
    }]


def test_moneyai_headers_forward_stable_source_path(monkeypatch):
    from platform_app.api import scripts as scripts_api

    calls = []

    def fake_import_script(user_id, file_item, **kwargs):
        calls.append(kwargs)
        return {"script": {"id": 321}, "idempotent_replay": False}

    monkeypatch.setattr(scripts_api.script_import, "import_script", fake_import_script)
    response = asyncio.run(scripts_api.api_import_script(
        _JsonRequest(
            {"file": {"name": "book.txt", "base64": "eA=="}},
            headers={
                "X-MoneyAI-Release-Id": "release_01ABC-def",
                "Idempotency-Key": "publish-attempt-1",
            },
        ),
        user={"id": 7},
    ))

    assert response.status_code == 200
    assert json.loads(response.body)["idempotent_replay"] is False
    assert calls[0]["source_path"] == "moneyai://releases/release_01ABC-def"

    for headers in (
        {"X-MoneyAI-Release-Id": "bad", "Idempotency-Key": "attempt"},
        {"X-MoneyAI-Release-Id": "release_missing_key"},
    ):
        rejected = asyncio.run(scripts_api.api_import_script(
            _JsonRequest({"file": {"name": "book.txt", "base64": "eA=="}}, headers=headers),
            user={"id": 7},
        ))
        assert rejected.status_code == 400
    assert len(calls) == 1


def test_worldline_seed_field_is_forwarded_only_when_present(monkeypatch):
    from platform_app.api import scripts as scripts_api

    calls = []
    monkeypatch.setattr(
        scripts_api.script_import, "import_script",
        lambda user_id, file_item, **kwargs: calls.append(kwargs) or {"script": {"id": 1}},
    )
    seed = {"characters": [{"name": "沈梧"}]}
    response = asyncio.run(scripts_api.api_import_script(
        _JsonRequest({"file": {"name": "book.txt", "base64": "eA=="}, "worldline_seed": seed}),
        user={"id": 7},
    ))
    assert response.status_code == 200
    assert calls == [{
        "split_rule": "auto", "custom_pattern": "", "title": "", "upload_id": "",
        "worldline_seed": seed,
    }]


class _Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _State:
    def __init__(self):
        self.scripts = []
        self.chapters = []
        self.jobs = []
        self.assets = []
        self.lock_keys = []
        self.fallback = False
        self.data_lock = threading.RLock()
        self.locks = {}
        self.locks_guard = threading.Lock()

    def advisory_lock(self, key):
        with self.locks_guard:
            return self.locks.setdefault(key, threading.Lock())

    def schedule(self, user_id, script_id, kind):
        with self.data_lock:
            job = next(
                (j for j in self.jobs if j["user_id"] == user_id and j["script_id"] == script_id),
                None,
            )
            if job is None:
                job = {
                    "id": len(self.jobs) + 1,
                    "user_id": user_id,
                    "script_id": script_id,
                    "job_id": f"{'ks' if kind == 'knowledge_sync' else 'imp'}_{script_id}_1",
                    "kind": kind,
                    "status": "pending",
                }
                self.jobs.append(job)
            return job


class _ChapterCursor:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def executemany(self, sql, rows):
        with self.state.data_lock:
            for p in rows:
                self.state.chapters.append({
                    "script_id": p[0], "chapter_index": p[1], "title": p[2],
                    "content": p[3], "volume_title": p[5],
                })


class _DB:
    def __init__(self, state):
        self.state = state
        self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.lock is not None:
            self.lock.release()
        return False

    def cursor(self):
        return _ChapterCursor(self.state)

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        if flat.startswith("select pg_advisory_xact_lock"):
            self.lock = self.state.advisory_lock(params)
            self.lock.acquire()
            self.state.lock_keys.append(params)
            return _Result({})
        if flat.startswith("select * from scripts where owner_id"):
            with self.state.data_lock:
                row = next((s for s in self.state.scripts if s["owner_id"] == params[0]
                            and s["source_path"] == params[1]), None)
                return _Result(dict(row) if row else None)
        if flat.startswith("insert into scripts"):
            with self.state.data_lock:
                row = {
                    "id": len(self.state.scripts) + 1,
                    "owner_id": params[0], "title": params[1], "description": params[2],
                    "source_path": params[3], "chapter_count": params[4],
                    "word_count": params[5], "import_report": getattr(params[6], "obj", params[6]),
                    "review_status": "unreviewed",
                    "worldline_seed": (getattr(params[8], "obj", params[8]) if len(params) > 8 else None),
                }
                self.state.scripts.append(row)
                return _Result(dict(row))
        if flat.startswith("select chapter_index, title, volume_title, content"):
            with self.state.data_lock:
                rows = [dict(c) for c in self.state.chapters if c["script_id"] == params[0]]
                return _Result(rows=rows)
        if flat.startswith("select job_id, kind, status from import_jobs"):
            with self.state.data_lock:
                rows = [j for j in self.state.jobs
                        if j["user_id"] == params[0] and j["script_id"] == params[1]]
                return _Result(dict(max(rows, key=lambda j: j["id"])) if rows else None)
        raise AssertionError(f"unexpected SQL: {flat}")


@pytest.fixture
def import_harness(monkeypatch, tmp_path):
    from platform_app import assets_registry, import_pipeline, storage
    from platform_app.script_import import imports as imports_module

    state = _State()
    monkeypatch.setattr(imports_module, "init_db", lambda: None)
    monkeypatch.setattr(imports_module, "connect", lambda: _DB(state))
    monkeypatch.setattr(imports_module, "BASE", tmp_path)
    monkeypatch.setattr(imports_module, "SCRIPT_ROOT", tmp_path)
    monkeypatch.setattr(storage, "PLATFORM_DATA_ROOT", tmp_path)
    monkeypatch.setattr(assets_registry, "register_asset", lambda **kw: state.assets.append(kw))
    monkeypatch.setattr(
        imports_module.chapter_splitter,
        "split_chapters_with_report",
        lambda text, **kwargs: (
            [{"title": "第一章", "content": "正文"}],
            {"mode": "auto", "mode_label": "自动", "confidence": 1.0},
        ),
    )

    def schedule_full(user_id, script_id):
        if state.fallback:
            raise ValueError("missing test credentials")
        return {"job_id": state.schedule(user_id, script_id, "full_pipeline")["job_id"]}

    def schedule_sync(user_id, script_id):
        return state.schedule(user_id, script_id, "knowledge_sync")["job_id"]

    monkeypatch.setattr(import_pipeline, "schedule_full_import", schedule_full)
    monkeypatch.setattr(imports_module, "_schedule_knowledge_sync", schedule_sync)
    return imports_module, state


def _file_item():
    return {
        "name": "book.txt",
        "base64": base64.b64encode("第一章\n正文".encode()).decode(),
    }


def test_moneyai_import_retry_replays_script_and_real_knowledge(import_harness, tmp_path):
    imports_module, state = import_harness
    state.fallback = True
    source_path = "moneyai://releases/release_retry_1"

    first = imports_module.import_script(
        7, _file_item(), title="书", source_path=source_path,
    )
    state.jobs[0]["status"] = "running"
    second = imports_module.import_script(
        7, title="书", source_path=source_path,
    )

    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["script"]["id"] == first["script"]["id"]
    assert second["script"]["source_path"] == source_path
    assert first["knowledge"]["kind"] == "knowledge_sync"
    assert second["knowledge"] == {
        "ok": True, "job_id": first["knowledge"]["job_id"], "status": "running",
        "async": True, "kind": "knowledge_sync",
    }
    assert len(state.scripts) == len(state.chapters) == len(state.jobs) == len(state.assets) == 1
    assert len(state.lock_keys) == 1
    assert len(list(tmp_path.rglob("*.txt"))) == 1


def test_worldline_seed_persists_once_and_replay_does_not_rematerialize(import_harness, monkeypatch):
    imports_module, state = import_harness
    from extract import author_seed

    materialize = mock.Mock()
    monkeypatch.setattr(author_seed, "materialize_author_seed", materialize)
    source_path = "moneyai://releases/release_seed_1"
    seed = {"characters": [{"name": "沈梧", "relationships": []}]}
    first = imports_module.import_script(
        7, _file_item(), title="书", source_path=source_path, worldline_seed=seed,
    )
    second = imports_module.import_script(
        7, title="书", source_path=source_path,
        worldline_seed={"characters": [{"name": "不应覆盖"}]},
    )

    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["script"]["id"] == first["script"]["id"]
    assert second["script"]["worldline_seed"] == seed
    assert len(state.scripts) == len(state.jobs) == 1
    assert state.scripts[0]["worldline_seed"] == seed
    assert materialize.call_count == 0

    with pytest.raises(ValueError, match="未知顶层键"):
        imports_module.import_script(
            7, _file_item(), source_path="moneyai://releases/release_bad_seed",
            worldline_seed={"not_allowed": []},
        )
    assert len(state.scripts) == len(state.jobs) == 1


def test_moneyai_import_concurrent_release_creates_once(import_harness, monkeypatch):
    imports_module, state = import_harness
    source_path = "moneyai://releases/release_concurrent_1"
    split = imports_module.chapter_splitter.split_chapters_with_report
    barrier = threading.Barrier(8)

    def synchronized_split(text, **kwargs):
        barrier.wait(timeout=5)
        return split(text, **kwargs)

    monkeypatch.setattr(imports_module.chapter_splitter, "split_chapters_with_report", synchronized_split)

    def call(_):
        return imports_module.import_script(
            7, _file_item(), title="书", source_path=source_path,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(call, range(8)))

    assert {r["script"]["id"] for r in results} == {1}
    assert sum(not r["idempotent_replay"] for r in results) == 1
    assert {r["knowledge"]["job_id"] for r in results} == {"imp_1_1"}
    assert all(r["knowledge"]["kind"] == "full_pipeline" for r in results)
    assert len(state.scripts) == len(state.chapters) == len(state.jobs) == len(state.assets) == 1
    assert len(state.lock_keys) == 8


def test_plain_import_has_no_moneyai_lock_or_response_field(import_harness):
    imports_module, state = import_harness

    result = imports_module.import_script(7, _file_item(), title="书")

    assert "idempotent_replay" not in result
    assert "worldline_seed" not in result["script"]
    assert state.lock_keys == []
    assert result["script"]["source_path"] == str(Path("user_7") / "book.txt")
