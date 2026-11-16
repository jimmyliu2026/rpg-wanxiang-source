from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RPG = Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

from platform_app.api import moneyai_release
from platform_app.api.script_edit._shared import _require_owner


class _Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value

    def fetchall(self):
        return self.value


class _DB:
    def __init__(self, script):
        self.script = script

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split()).lower()
        if "from scripts where id" in flat or "from scripts where source_path" in flat:
            return _Result(self.script)
        for table in (
            "script_chapters",
            "character_cards",
            "worldbook_entries",
            "kb_canon_entities",
            "script_timeline_anchors",
            "phase_digests",
            "chapter_facts",
        ):
            if f"from {table}" in flat:
                if table == "chapter_facts":
                    assert "order by chapter,id" in flat
                return _Result([])
        raise AssertionError(flat)


def test_moneyai_published_release_is_immutable():
    script = {
        "id": 77,
        "owner_id": 7,
        "source_path": "moneyai://releases/release_frozen",
        "moneyai_visibility_version": 1,
    }
    with pytest.raises(ValueError, match="不可变"):
        _require_owner(_DB(script), 77, 7)
    script["moneyai_visibility_version"] = 0
    assert _require_owner(_DB(script), 77, 7)["id"] == 77
    script["moneyai_visibility_version"] = 1
    script["source_path"] = "legacy/story.md"
    assert _require_owner(_DB(script), 77, 7)["id"] == 77


def test_manifest_binds_source_and_module_content(monkeypatch):
    script = {
        "id": 77,
        "title": "测试",
        "source_path": "moneyai://releases/release_frozen",
        "moneyai_source_sha256": "a" * 64,
        "chapter_count": 1,
        "word_count": 10,
    }
    monkeypatch.setattr(moneyai_release, "connect", lambda: _DB(script))
    first = moneyai_release._release_manifest("release_frozen")
    assert first["source_sha256"] == "a" * 64
    assert len(first["module_sha256"]) == 64
    assert first["counts"]["chapters"] == 0
