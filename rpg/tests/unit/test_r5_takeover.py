"""R5 切片4b 主备接管编排测试:注入故障→平台接管RPC→备用模型续写→审计落 state。
确定性,无DB无模型(fake backend + monkeypatched takeover_grant)。"""
import asyncio
import os
import threading

import pytest

from chat_pipeline import gm as pipeline
from agents.gm.backends.openai_compat import _UpstreamMidStreamFault


class Backend:
    last_usage = {}
    model_name = "wanxiang-main"

    def __init__(self):
        self.emitted = 0

    def stream(self, system, messages, max_tokens):
        # 首次调用:发3段后注入中途死亡;续写调用:正常续完
        if not getattr(self, "_continued", False):
            self._continued = True
            yield "雨夜。"
            yield "他推门,"
            self.emitted = 3
            raise _UpstreamMidStreamFault("fault-inject: simulated upstream mid-stream death after 3 deltas")
        # 续写: 校验续写上下文含半截正文与指令,然后续写
        assert any(m.get("role") == "assistant" and "他推门," in (m.get("content") or "") for m in messages)
        assert any("续写" in (m.get("content") or "") for m in messages)
        assert self.model_name == "wanxiang-m31-flash"  # 接管后已换备用模型
        yield "伞上的水滴在门槛上砸出一圈深色。"
        yield "屋里的人抬起头。"


class GM:
    api_id = "moneyai"

    def __init__(self):
        self._backend = Backend()

    def _build_system(self):
        return "SYS"

    def _turn_content(self, message, state, prompt, **kw):
        return message  # graded 缓冲工厂用的纯叙事稿入口

    def respond_stream_with_tools(self, *a, **k):
        for chunk in self._backend.stream("SYS", [], 800):
            yield {"type": "text", "text": chunk}


def _run(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    captured = {}

    def fake_takeover(model, price):
        captured["model"] = model
        captured["price"] = price
        return {"status": "generating", "model_token": "tok2"}

    import core.moneyai as moneyai
    monkeypatch.setattr(moneyai, "takeover_grant", fake_takeover)
    monkeypatch.setattr(pipeline, "P1_NARRATIVE_RULE", "RULE")

    from chat_pipeline._common import PipelineContext
    from state import GameState

    ctx = PipelineContext(
        api_user={"id": 1}, state=GameState.new(), gm=GM(), sub_gm=None,
        message_for_model="我进门", run_id=1, stop_event=threading.Event(),
        chat_start_time=0.0,
    )
    ctx.bundle = {"prompt": "", "debug": {"layers": []}}
    ctx.agent_result = {"curator_plan": {}}

    events = []

    async def collect():
        async for e in pipeline.run_gm_phase(
            ctx,
            payload_fn=lambda u: {},
            persist_chat_turn=lambda *a, **k: None,
            mark_context_run=lambda *a, **k: None, current_run_id_fn=lambda u: 1,
            is_stop_requested_global=lambda u, r: False,
            is_extractor_enabled=lambda u: False, is_black_swan_enabled=lambda u: False,
            acceptance_verifier_mode=lambda u: "rule", verify_acceptance=lambda *a, **k: [],
            active_script_id=lambda u: None,
        ):
            events.append((e[0], e[1]))

    asyncio.run(collect())
    return ctx, events, captured


def test_takeover_continues_after_midstream_fault(monkeypatch):
    ctx, events, captured = _run(monkeypatch, {
        "RPG_FAULT_INJECT_AFTER_TOKENS": "0",  # fake backend 自带注入,不走env钩子
        "RPG_GM_TAKEOVER_MODEL": "wanxiang-m31-flash",
        "RPG_GM_TAKEOVER_PRICE": "minimax-m31-flash-x",
    })
    assert captured["model"] == "wanxiang-m31-flash"
    # 模式无关断言:最终正文=半截+续写(gated 模式 live token 被缓冲,看 ctx.response)
    assert (ctx.response or "").endswith("伞上的水滴在门槛上砸出一圈深色。屋里的人抬起头。")
    assert "雨夜。他推门," in (ctx.response or "")
    tk = ctx.state.data["_takeover"]
    assert tk["to_model"] == "wanxiang-m31-flash"
    assert tk["chars_before_takeover"] == len("雨夜。他推门,")
    assert tk["boundary_sha256"]
    phases = [d.get("phase") for e, d in events if e == "agent"]
    assert "gm_takeover" in phases


def test_no_takeover_config_propagates_fault(monkeypatch):
    monkeypatch.delenv("RPG_GM_TAKEOVER_MODEL", raising=False)
    monkeypatch.delenv("RPG_GM_TAKEOVER_PRICE", raising=False)
    with pytest.raises(_UpstreamMidStreamFault):
        _run(monkeypatch, {})
