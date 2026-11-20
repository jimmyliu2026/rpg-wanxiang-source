"""P1 continuity gate unit tests; no model calls.

A1 分层语义（2026-11-17 Jimmy 裁决，落实 R2 裁定A）：
确定性硬门=唯一拦截权（空稿/协议标记）；LLM=辅助信号不拦截、不可用不故障。"""
import asyncio

import pytest

from chat_pipeline import _continuity as continuity


def test_first_draft_passes_without_rewrite(monkeypatch):
    monkeypatch.setattr(continuity, "_llm_signal", lambda *args: [])
    result = asyncio.run(continuity.enforce_continuity(
        "合格正文",
        state_data={}, player_input="观察", user_id=1,
        rewrite=lambda *_: (_ for _ in ()).throw(AssertionError("must not rewrite")),
    ))
    assert result == ("合格正文", 0, [])


def test_llm_unmet_is_signal_only_never_rejects_or_rewrites(monkeypatch):
    """A1 核心：LLM 层 unmet 只作辅助信号——首稿原样采纳，不重写、不拦截。"""
    monkeypatch.setattr(continuity, "_llm_signal", lambda *args: ["不得代写玩家"])
    result = asyncio.run(continuity.enforce_continuity(
        "疑似越权正文",
        state_data={}, player_input="观察", user_id=1,
        rewrite=lambda *_: (_ for _ in ()).throw(AssertionError("must not rewrite on llm signal")),
    ))
    assert result == ("疑似越权正文", 0, ["不得代写玩家"])


def test_verifier_unavailable_records_signal_does_not_fail(monkeypatch):
    """验收器不可用 → 辅助信号缺失记录，不构成回合故障（A1：LLM 不可用不拦截）。"""
    monkeypatch.setattr(continuity, "_llm_signal", lambda *args: None)
    result = asyncio.run(continuity.enforce_continuity(
        "正文", state_data={}, player_input="观察", user_id=1,
        rewrite=lambda *_: "不会使用",
    ))
    assert result == ("正文", 0, ["verifier_unavailable"])


def test_verifier_error_records_signal_does_not_fail(monkeypatch):
    def boom(*args):
        raise RuntimeError("backend down")
    monkeypatch.setattr(continuity, "_llm_signal", boom)
    result = asyncio.run(continuity.enforce_continuity(
        "正文", state_data={}, player_input="观察", user_id=1,
        rewrite=lambda *_: "不会使用",
    ))
    assert result == ("正文", 0, ["verifier_error:RuntimeError"])


def test_protocol_marker_is_rewritten_once_and_accepted(monkeypatch):
    """确定性硬门：协议标记 → 一次修正 → 二次确定性复检通过 → 采纳修正稿。
    复核D2锚定：修正成功是确定性事件(rewrites=1)，llm_signal 必须为空——
    不触发后台候选、不污染辅助信号审计。"""
    monkeypatch.setattr(continuity, "_llm_signal", lambda *args: [])
    result = asyncio.run(continuity.enforce_continuity(
        "正文\n<|im_start|>assistant",
        state_data={}, player_input="观察", user_id=1,
        rewrite=lambda draft, unmet: "干净修正稿",
    ))
    assert result == ("干净修正稿", 1, [])


def test_protocol_marker_rewrite_still_dirty_is_rejected(monkeypatch):
    """确定性硬门 fail-closed：修正稿仍含协议标记 → 拒绝。"""
    monkeypatch.setattr(continuity, "_llm_signal", lambda *args: [])
    with pytest.raises(continuity.ContinuityRejected, match="protocol_marker_rewrite_rejected"):
        asyncio.run(continuity.enforce_continuity(
            "正文\n<|im_start|>leak", state_data={}, player_input="观察", user_id=1,
            rewrite=lambda *_: "仍带 <|im_start|> 标记",
        ))


def test_protocol_marker_checked_without_calling_the_llm(monkeypatch):
    import agents.acceptance_verifier as verifier
    monkeypatch.setattr(
        verifier, "verify_acceptance_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call LLM")),
    )
    assert continuity.deterministic_gate(
        "正文\n<|im_start|>assistant"
    ) == [continuity.CONTINUITY_CLAUSES[-1]]
    assert continuity.deterministic_gate("干净正文") == []


def test_chatml_and_qwen_tool_markers_are_rejected(monkeypatch):
    """复核B1:跨模型协议形态(ChatML/Qwen)入确定性拒绝清单——R5 多模型批次前置。"""
    for marker in ("<|im_start|>assistant", "<|im_end|>", "<tool_call>{\"name\":\"x\"}", "</tool_call>"):
        assert continuity.deterministic_gate(f"正文\n{marker}") == [
            continuity.CONTINUITY_CLAUSES[-1]
        ], marker


def test_empty_draft_is_rejected_without_llm(monkeypatch):
    monkeypatch.setattr(continuity, "_llm_signal", lambda *args: (_ for _ in ()).throw(
        AssertionError("must not call LLM")))
    with pytest.raises(continuity.ContinuityRejected, match="empty_continuity_draft"):
        asyncio.run(continuity.enforce_continuity(
            "   \n\t  ", state_data={}, player_input="观察", user_id=1,
            rewrite=lambda *_: "不会使用",  # strip 后为空，先于 rewrite 拒绝
        ))


def test_llm_signal_receives_authoritative_projection_and_player_input(monkeypatch):
    captured = {}

    def fake_signal(narrative, state_data, player_input, user_id=None):
        from state.utils import format_current_scene_projection
        evidence = [
            format_current_scene_projection(state_data),
            "玩家本轮输入（最高授权证据）：" + player_input,
        ]
        captured.update(narrative=narrative, evidence=evidence, user_id=user_id)
        return []

    import agents.acceptance_verifier as verifier
    monkeypatch.setattr(verifier, "verify_acceptance_llm", lambda *a, **k: [])
    monkeypatch.setattr(continuity, "_llm_signal", fake_signal)
    state = {
        "player": {"current_location": "雾钟驿站"},
        "world": {"time": "九月十四"},
        "memory": {"resources": ["半枚铜牌"]},
        "active_entities": [],
    }
    result = asyncio.run(continuity.enforce_continuity(
        "正文", state_data=state, player_input="我扔出铜牌", user_id=7,
        rewrite=lambda *_: "不会使用",
    ))
    assert result == ("正文", 0, [])
    assert "雾钟驿站" in captured["evidence"][0]
    assert "半枚铜牌" in captured["evidence"][0]
    assert "我扔出铜牌" in captured["evidence"][1]
    assert captured["user_id"] == 7


class PipelineContinuityTests:
    class Backend:
        def __init__(self):
            self.last_usage = {}

        def stream(self, *args, **kwargs):
            return iter(["坏首稿"])

    class GM:
        api_id = "moneyai"

        def __init__(self):
            self._backend = PipelineContinuityTests.Backend()

        def _build_system(self):
            return "SYS"

        def _turn_content(self, *args, **kwargs):
            return "玩家输入"

    def context(self):
        import threading
        from chat_pipeline._common import PipelineContext
        from state import GameState

        ctx = PipelineContext(
            api_user={"id": 1}, state=GameState.new(), gm=self.GM(), sub_gm=None,
            message_for_model="观察", run_id=1, stop_event=threading.Event(), chat_start_time=0.0,
        )
        ctx.bundle = {"prompt": "", "debug": {"layers": []}}
        ctx.agent_result = {"curator_plan": {}}
        return ctx

    async def collect(self, ctx):
        from chat_pipeline.gm import run_gm_phase
        return [event async for event in run_gm_phase(
            ctx,
            payload_fn=lambda u: {}, persist_chat_turn=lambda *a, **k: None,
            mark_context_run=lambda *a, **k: None, current_run_id_fn=lambda u: 1,
            is_stop_requested_global=lambda u, r: False,
            is_extractor_enabled=lambda u: False, is_black_swan_enabled=lambda u: False,
            acceptance_verifier_mode=lambda u: "rule", verify_acceptance=lambda *a, **k: [],
            active_script_id=lambda u: None,
        )]


def test_pipeline_emits_only_the_accepted_rewrite(monkeypatch):
    import chat_pipeline.gm as pipeline
    import agents.gm.stream_retry as retry

    async def accepted(response, **kwargs):
        return "修正稿", 1, []

    monkeypatch.setattr(pipeline, "enforce_continuity", accepted)
    monkeypatch.setattr(retry, "stream_with_channel_fallback", lambda factory, **kwargs: factory())
    monkeypatch.setenv("RPG_P1_CONTINUITY_GATE", "1")
    helper = PipelineContinuityTests()
    ctx = helper.context()
    events = asyncio.run(helper.collect(ctx))
    tokens = [data["text"] for event, data in events if event == "token"]
    assert tokens == ["修正稿"]
    assert ctx.response == "修正稿"
    assert ctx.state.data["history"] == []


def test_pipeline_rejection_emits_no_narrative_or_history(monkeypatch):
    import chat_pipeline.gm as pipeline
    import agents.gm.stream_retry as retry

    async def rejected(response, **kwargs):
        raise continuity.ContinuityRejected("test_rejection")

    monkeypatch.setattr(pipeline, "enforce_continuity", rejected)
    monkeypatch.setattr(retry, "stream_with_channel_fallback", lambda factory, **kwargs: factory())
    monkeypatch.setenv("RPG_P1_CONTINUITY_GATE", "1")
    helper = PipelineContinuityTests()
    ctx = helper.context()
    events = asyncio.run(helper.collect(ctx))
    assert not [event for event in events if event[0] == "token"]
    assert ctx.early_return is True and ctx.response == ""
    assert ctx.state.data["history"] == []
