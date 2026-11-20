import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agents.gm.backends.openai_compat import _OpenAICompatBackend
from core.moneyai import managed_headers, set_grant
from core.outbound import _SsrfGuardTransport


@pytest.fixture(autouse=True)
def moneyai_env(monkeypatch):
    monkeypatch.setenv(
        "RPG_MONEYAI_MODEL_BASE_URL", "http://platform-api:8000/v1/internal/model-access"
    )
    monkeypatch.setenv("RPG_MONEYAI_INTERNAL_SECRET", "test-only-rpg-service-secret-32-bytes")
    set_grant("")
    yield
    set_grant("")


def test_moneyai_catalog_uses_the_existing_openai_compatible_backend():
    catalog = json.loads(
        (Path(__file__).parents[2] / "config/model_catalog.json").read_text()
    )
    provider = next(api for api in catalog["apis"] if api["id"] == "moneyai")
    assert provider["kind"] == "openai_compat"
    assert provider["models"][0]["real_name"] == "wanxiang-main"


def test_managed_grant_is_request_scoped_and_not_cached():
    set_grant("grant-turn-a")
    first = managed_headers("moneyai")
    set_grant("grant-turn-b")
    second = managed_headers("moneyai")
    assert first["Authorization"] == "Bearer grant-turn-a"
    assert second["Authorization"] == "Bearer grant-turn-b"
    assert first["X-Wanxiang-Nonce"] != second["X-Wanxiang-Nonce"]
    assert managed_headers("openai") == {}


def test_managed_call_without_grant_fails_closed():
    with pytest.raises(RuntimeError, match="turn grant"):
        managed_headers("moneyai")


def test_env_grant_fallback_warns_but_still_works(monkeypatch, caplog):
    """R4切片2: env 兜底是迁移期通道——命中必须告警(根治后应归零),但不阻断既有任务。"""
    import logging

    monkeypatch.setenv("RPG_MONEYAI_IMPORT_GRANT", "grant-env-fallback")
    with caplog.at_level(logging.WARNING, logger="core.moneyai"):
        headers = managed_headers("moneyai")
    assert headers["Authorization"] == "Bearer grant-env-fallback"
    assert any("moneyai_env_grant_fallback_used" in r.message for r in caplog.records)


def test_main_gm_create_injects_grant_at_call_time():
    class Completions:
        def create(self, **kwargs):
            return kwargs

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    backend = _OpenAICompatBackend.__new__(_OpenAICompatBackend)
    backend.api_id = "moneyai"
    backend.model_name = "wanxiang-main"
    backend.user_id = 1
    backend.client = Client()
    set_grant("grant-current-turn")
    result = backend._create(model="wanxiang-main", messages=[])
    assert result["extra_headers"]["Authorization"] == "Bearer grant-current-turn"


def test_managed_provider_never_falls_back_to_byok(monkeypatch):
    from core.llm_backend import guard_byok_usable

    monkeypatch.setattr(
        "core.llm_backend.first_user_model", lambda _user: ("openai", "gpt-test")
    )
    assert guard_byok_usable(1, "moneyai", "wanxiang-main") == ("moneyai", "wanxiang-main")


def test_moneyai_narrative_never_uses_reasoning_budget(monkeypatch):
    backend = _OpenAICompatBackend.__new__(_OpenAICompatBackend)
    backend.api_id = "moneyai"
    backend.model_name = "wanxiang-main"
    backend.user_id = 1
    assert backend._reasoning_param() == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_m31_narrative_uses_medium_adaptive_thinking():
    backend = _OpenAICompatBackend.__new__(_OpenAICompatBackend)
    backend.api_id = "moneyai"
    backend.model_name = "wanxiang-m31-flash"
    backend.user_id = 1
    assert backend._reasoning_param() == {"extra_body": {"reasoning_effort": "medium"}}


def test_m31_narrative_stream_leaves_room_for_prose(monkeypatch):
    backend = _OpenAICompatBackend.__new__(_OpenAICompatBackend)
    backend.api_id = "moneyai"
    backend.model_name = "wanxiang-m31-flash"
    backend.user_id = 1
    backend.last_usage = {}
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return iter(())

    monkeypatch.setattr(backend, "_create", fake_create)
    assert list(backend.stream("sys", [], 4096)) == []
    assert captured["max_tokens"] == 8000
    assert captured["extra_body"] == {"reasoning_effort": "medium"}


def test_m31_call_structured_uses_low_without_disabling_thinking(monkeypatch):
    backend = _OpenAICompatBackend.__new__(_OpenAICompatBackend)
    backend.api_id = "moneyai"
    backend.model_name = "wanxiang-m31-flash"
    backend.user_id = 1
    backend.last_usage = {}
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")],
            usage=None,
        )

    monkeypatch.setattr(backend, "_create", fake_create)
    assert backend.call_structured("sys", [], 8000) == "{}"
    assert captured["extra_body"] == {"reasoning_effort": "low"}
    assert "thinking" not in captured["extra_body"]


def test_m31_harness_microtasks_use_low_instead_of_disabled(monkeypatch):
    from agents import _harness

    captured = {}

    def fake_json_mode(*args, extra_body=None, **kwargs):
        captured["extra_body"] = extra_body
        return "{}", {}

    monkeypatch.setattr(_harness, "_openai_compat_json_mode", fake_json_mode)
    result = _harness.call_agent_json(
        "moneyai", "wanxiang-m31-flash", "sys", "user", None, no_think=True
    )
    assert result == ("{}", {})
    assert captured["extra_body"] == {"reasoning_effort": "low"}


def test_moneyai_legacy_extractor_uses_managed_harness(monkeypatch):
    from agents import _harness
    from agents import extractor

    captured = {}

    def fake_managed(*args, extra_body=None, **kwargs):
        captured["args"] = args
        captured["extra_body"] = extra_body
        return '{"unmet":[]}', {}

    monkeypatch.setattr(_harness, "_openai_compat_json_mode", fake_managed)
    result = extractor._call_openai_compat_json_mode(
        "moneyai", "wanxiang-m31-flash", "sys", "user", 1, 15,
        json_hint='{"unmet":[...]}',
    )
    assert result == '{"unmet":[]}'
    assert captured["extra_body"] == {"reasoning_effort": "low"}
    assert "unmet" in captured["args"][2]
    extractor._call_openai_compat_json_mode(
        "moneyai", "wanxiang-m31-flash", "sys", "user", 1, 15,
        json_hint='{"unmet":[...]}', effort_override="medium",
    )
    assert captured["extra_body"] == {"reasoning_effort": "medium"}


def test_verifier_effort_override_is_scoped_to_m31(monkeypatch):
    from agents import acceptance_verifier

    captured = {}

    def fake_backend(**kwargs):
        captured.update(kwargs)
        return '{"unmet":[]}', None

    monkeypatch.setattr(acceptance_verifier, "_call_verifier_backend", fake_backend)
    result = acceptance_verifier.verify_acceptance_llm(
        ["不得回退"], "正文", [], model_override="wanxiang-main",
        api_id_override="moneyai", effort_override="medium",
    )
    assert result == []
    assert captured["effort_override"] is None


def test_m31_structured_400_never_retries_without_thinking_policy(monkeypatch):
    from agents import _harness

    calls = []

    def raise_400(req, *, timeout):
        calls.append(json.loads(req.data))
        raise urllib.error.HTTPError("u", 400, "bad request", None, None)

    monkeypatch.setattr(_harness, "_no_redirect_urlopen", raise_400)
    set_grant("grant-m31-structured")
    with pytest.raises(urllib.error.HTTPError):
        _harness._openai_compat_json_mode(
            "moneyai", "wanxiang-m31-flash", "sys", "user", 1, 30, 8000,
            extra_body={"reasoning_effort": "low"},
        )
    assert len(calls) == 1
    assert calls[0]["reasoning_effort"] == "low"


def test_authoritative_scene_projection_is_shared_and_private_safe():
    import copy
    from agents.context_agent import _curator_task_prompt
    from agents.extractor import _build_user_prompt as extractor_prompt
    from agents.recorder import _build_user_prompt as recorder_prompt
    from state import DEFAULT_STATE, GameState
    from state.utils import format_current_scene_projection

    state = GameState(copy.deepcopy(DEFAULT_STATE))
    state.data["player"]["current_location"] = "雾钟驿站"
    state.data["world"]["time"] = "九月十四·雨夜"
    state.data["memory"]["resources"] = ["半枚铜牌", "匿名信"]
    state.data["player_character"]["inventory"] = []
    state.data["active_entities"] = [
        {"name": "沈梧", "source": "recorder", "status": "present", "location": "雾钟驿站"},
        {"name": "远方人物", "source": "recorder", "status": "present", "location": "北岸"},
        {"name": "被谈论者", "source": "context_inject", "status": "present"},
    ]
    state.data["player_private"]["secrets"] = ["绝密身份"]
    projection = format_current_scene_projection(state.data)
    assert "半枚铜牌" in projection and "匿名信" in projection
    assert "沈梧" in projection
    assert "远方人物" not in projection and "被谈论者" not in projection
    assert "绝密身份" not in projection
    assert "绝密身份" not in state.short_summary()
    for text in (
        state.short_summary(),
        _curator_task_prompt(state, "查看铜牌", []),
        recorder_prompt("查看铜牌", state.data, frozenset({"ops"}), None, None, None),
        extractor_prompt("查看铜牌", state.data),
    ):
        assert "【权威场景投影】" in text
        assert "匿名信" in text and "沈梧" in text


def test_player_action_guard_covers_plain_and_tiered_turns(monkeypatch):
    from agents.gm.master import GameMaster

    gm = GameMaster.__new__(GameMaster)
    gm._backend = object()
    state = SimpleNamespace(short_summary=lambda: "STATE")
    monkeypatch.setattr(gm, "_dynamic_context", lambda *args: "CONTEXT")
    monkeypatch.setattr(gm, "_tiered_enabled", lambda: False)
    plain = gm._turn_message(
        "我把两半铜牌压进凹槽", state, "", include_action_guard=True
    )
    assert plain.count("【本轮玩家授权边界】") == 1
    assert "不得重新发放或重置相关物品" in plain
    assert "不得让人物瞬移" in plain
    assert "【本轮玩家授权边界】" not in gm._turn_message(
        "开场", state, "", include_action_guard=False
    )
    assert "【本轮玩家授权边界】" not in gm._turn_message(
        "/set 时间=夜晚", state, "", include_action_guard=True
    )

    monkeypatch.setattr(gm, "_tiered_enabled", lambda: True)
    tiered = gm._turn_content(
        "我停下听门后的声音", state, "",
        prompt_segments=[{"tier": "A", "text": "STABLE"}],
    )
    assert tiered.count("【本轮玩家授权边界】") == 1
    assert tiered.index("【本轮玩家授权边界】") < tiered.index("【玩家本轮输入】")


def test_deepseek_candidate_reasoning_is_enabled_but_separate(monkeypatch):
    backend = _OpenAICompatBackend.__new__(_OpenAICompatBackend)
    backend.api_id = "moneyai"
    backend.model_name = "wanxiang-deepseek-flash"
    backend.user_id = 1
    assert backend._reasoning_param() == {"extra_body": {"thinking": {"type": "enabled"}}}


def test_curator_thread_pool_preserves_request_grant(monkeypatch):
    from agents import _harness
    from agents.context_agent import (
        _CURATOR_TOOL_SCHEMA,
        _MONEYAI_CURATOR_PROMPT,
        _call_curator_via_harness,
        _parse_curator_json,
    )

    seen = []
    monkeypatch.setattr(
        _harness, "resolve_api_and_model", lambda *args, **kwargs: ("moneyai", "wanxiang-main")
    )

    def fake_call_agent_json(**kwargs):
        seen.append((managed_headers("moneyai")["Authorization"], kwargs))
        return '{"intent":"ok"}', {}

    monkeypatch.setattr(_harness, "call_agent_json", fake_call_agent_json)
    set_grant("grant-curator-turn")
    result = _call_curator_via_harness(
        user_id=1,
        api_id_override="moneyai",
        model_override="wanxiang-main",
        system_prompt="system",
        user_prompt="user",
        stop_requested=lambda: False,
    )
    assert result == '{"intent":"ok"}'
    assert seen[0][0] == "Bearer grant-curator-turn"
    call = seen[0][1]
    assert call["max_tokens"] == 800
    assert call["no_think"] is True
    assert call["tool_schema"] is None
    assert call["system_prompt"] == _MONEYAI_CURATOR_PROMPT
    properties = list(_CURATOR_TOOL_SCHEMA["input_schema"]["properties"])
    assert properties[:6] == [
        "intent", "hard_constraints", "candidate_actions",
        "acceptance", "retrieval_query", "confidence",
    ]
    valid = _parse_curator_json(json.dumps({
        "intent": "ok",
        "hard_constraints": [],
        "candidate_actions": ["叙事：继续观察"],
        "acceptance": ["回应观察请求"],
        "retrieval_query": "渡口",
        "confidence": 0.9,
    }, ensure_ascii=False))
    assert valid["candidate_actions"] == ["叙事：继续观察"]
    assert valid["acceptance"] == ["回应观察请求"]
    missing = _parse_curator_json('{"intent":"missing required fields"}')
    assert "curator_json_parse_failed" in missing["risk_flags"]


def test_ssrf_exception_is_exact_origin_only(monkeypatch):
    class Inner:
        def handle_request(self, request):
            return request

    transport = _SsrfGuardTransport(Inner())
    monkeypatch.setattr("core.outbound._ssrf_enforced", lambda: True)

    def fail_resolution(_host, _port):
        raise AssertionError("trusted origin must not resolve as public IP")

    monkeypatch.setattr("core.outbound._resolve_external_ip", fail_resolution)
    request = httpx.Request("POST", "http://platform-api:8000/v1/internal/model-access/chat/completions")
    assert transport.handle_request(request) is request
    other = httpx.Request("POST", "http://other-internal:8000/v1/internal/model-access/chat/completions")
    with pytest.raises(Exception, match="trusted origin"):
        transport.handle_request(other)
