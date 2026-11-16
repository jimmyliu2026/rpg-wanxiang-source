import json
from pathlib import Path

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


def test_deepseek_candidate_reasoning_is_enabled_but_separate(monkeypatch):
    backend = _OpenAICompatBackend.__new__(_OpenAICompatBackend)
    backend.api_id = "moneyai"
    backend.model_name = "wanxiang-deepseek-flash"
    backend.user_id = 1
    assert backend._reasoning_param() == {"extra_body": {"thinking": {"type": "enabled"}}}


def test_curator_thread_pool_preserves_request_grant(monkeypatch):
    from agents import _harness
    from agents.context_agent import _call_curator_via_harness

    seen = []
    monkeypatch.setattr(
        _harness, "resolve_api_and_model", lambda *args, **kwargs: ("moneyai", "wanxiang-main")
    )

    def fake_call_agent_json(**kwargs):
        seen.append(managed_headers("moneyai")["Authorization"])
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
    assert seen == ["Bearer grant-curator-turn"]


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
