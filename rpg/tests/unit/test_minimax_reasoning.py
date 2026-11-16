"""MiniMax reasoning must stay out of narrative and respect the user's effort setting."""
import agents.gm.backends._effort as effort_module
from agents.gm.backends.openai_compat import _OpenAICompatBackend


def _stub_backend(monkeypatch, effort):
    backend = object.__new__(_OpenAICompatBackend)
    backend.api_id = "minimax"
    backend.model_name = "MiniMax-M3"
    backend.user_id = 1
    monkeypatch.setattr(effort_module, "resolve_effort", lambda *args: effort)
    return backend


def test_minimax_off_disables_thinking(monkeypatch):
    assert _stub_backend(monkeypatch, "off")._reasoning_param() == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }


def test_minimax_enabled_splits_reasoning(monkeypatch):
    assert _stub_backend(monkeypatch, "high")._reasoning_param() == {
        "extra_body": {"reasoning_split": True}
    }
