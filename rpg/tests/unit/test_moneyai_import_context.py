from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_RPG = Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

from core import moneyai  # noqa: E402
from platform_app.import_pipeline import rebuild_scheduler, runner  # noqa: E402


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _DB:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        if "select job_id from import_jobs" in flat:
            return _Result(None)
        if "select count(*) as n from import_jobs" in flat:
            return _Result({"n": 0})
        if "insert into import_jobs" in flat:
            return _Result(None)
        raise AssertionError(flat)


def test_import_worker_captures_request_grant_context(monkeypatch):
    monkeypatch.setattr(runner, "init_db", lambda: None)
    monkeypatch.setattr(runner, "require_user_llm_credential", lambda user_id: None)
    monkeypatch.setattr(runner, "connect", lambda: _DB())
    created = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon
            created.append(self)

        def start(self):
            return None

    monkeypatch.setattr(runner.threading, "Thread", FakeThread)
    moneyai.set_grant("grant-import-test")
    result = runner.schedule_full_import(7, 77)
    assert result["ok"] is True
    thread = created[0]
    assert thread.args[0] is runner._run_pipeline
    assert thread.target.__self__.get(moneyai._grant) == "grant-import-test"
    moneyai.set_grant("")


def test_module_rebuild_captures_request_grant_context(monkeypatch):
    monkeypatch.setattr(rebuild_scheduler, "init_db", lambda: None)
    monkeypatch.setattr(rebuild_scheduler, "require_user_llm_credential", lambda user_id: None)
    monkeypatch.setattr(rebuild_scheduler, "script_owned", lambda db, script_id, user_id: True)
    monkeypatch.setattr(rebuild_scheduler, "connect", lambda: _DB())
    created = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args
            created.append(self)

        def start(self):
            return None

    monkeypatch.setattr(rebuild_scheduler.threading, "Thread", FakeThread)
    moneyai.set_grant("grant-rebuild-test")
    result = rebuild_scheduler.schedule_module_rebuild(7, 77, "worldbook", body={"source": "llm"})
    assert result["ok"] is True
    thread = created[0]
    assert thread.target.__self__.get(moneyai._grant) == "grant-rebuild-test"
    moneyai.set_grant("")


def _chapters(count):
    return [
        {"chapter_index": index, "title": f"第{index}章", "content": "正文", "content_descriptor": ""}
        for index in range(1, count + 1)
    ]


def _fake_connect(chapters):
    cursor = mock.MagicMock()
    cursor.execute.return_value.fetchall.return_value = chapters
    manager = mock.MagicMock()
    manager.__enter__.return_value = cursor
    return mock.MagicMock(return_value=manager)


def test_per_chapter_pool_uses_independent_contexts():
    from extract import pipeline
    from extract.per_chapter import ChapterExtract

    calls = []

    def extract(*args, **kwargs):
        calls.append(1)
        time.sleep(0.05)
        return ChapterExtract(chapter=1, raw_ok=False, error="stop after concurrency check")

    seed = SimpleNamespace(era="", power_system=[], entity_vocab=[])
    with mock.patch("platform_app.db.connect", _fake_connect(_chapters(2))), \
         mock.patch.object(pipeline, "build_seed", return_value=seed), \
         mock.patch.object(pipeline, "extract_chapter", side_effect=extract):
        result = pipeline.run_extraction(12, 7, user_id=1, concurrency=2)
    assert result["ok"] is False
    assert len(calls) == 2


def test_arc_pool_uses_independent_contexts():
    from extract import arc_pipeline
    from extract.per_chapter import ChapterExtract

    calls = []

    def extract(*args, **kwargs):
        calls.append(1)
        time.sleep(0.05)
        return ChapterExtract(chapter=1, raw_ok=False, error="stop after concurrency check")

    seed = SimpleNamespace(era="", power_system=[], entity_vocab=[])
    with mock.patch("platform_app.db.connect", _fake_connect(_chapters(10))), \
         mock.patch.object(arc_pipeline, "build_seed", return_value=seed), \
         mock.patch.object(arc_pipeline, "extract_arc", side_effect=extract):
        result = arc_pipeline.run_arc_extraction(
            12, 7, user_id=1, target_arcs=2, concurrency=2
        )
    assert result["ok"] is False
    assert len(calls) == 2
