"""Scoped scripts must never inherit an unrelated built-in worldbook."""
from context_engine import formatters


def test_scoped_empty_worldbook_fails_closed(monkeypatch):
    monkeypatch.setattr(formatters, "_load_worldbook_db", lambda **kwargs: [])
    monkeypatch.setattr(
        formatters,
        "_worldbook_entries",
        lambda *args: (_ for _ in ()).throw(AssertionError("built-in fallback was used")),
    )
    assert formatters._active_worldbook("驿站", {}, None, script_id=11) == []


def test_unscoped_legacy_chat_keeps_builtin_fallback(monkeypatch):
    monkeypatch.setattr(formatters, "_worldbook_entries", lambda *args: [])
    assert formatters._active_worldbook("驿站", {}, None) == []
