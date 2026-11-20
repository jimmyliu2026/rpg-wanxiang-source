"""R2 risk grading tests; deterministic, no DB, no model calls."""
import asyncio
import os
import threading

import pytest

from chat_pipeline import _risk as risk_mod
from chat_pipeline._risk import grade_risk, p1_gate_mode


STATE = {
    "player": {"current_location": "雾钟驿站"},
    "world": {"time": "九月十四 夜"},
    "memory": {"resources": ["半枚铜牌", "油灯"]},
    "active_entities": [{
        "name": "守夜人", "source": "room_data", "status": "present", "location": "雾钟驿站",
    }],
}


def test_mode_matrix(monkeypatch):
    for raw, expect in [("0", "off"), ("", "off"), ("off", "off"), ("false", "off"),
                        ("1", "graded"), ("on", "graded"), ("true", "graded"),
                        ("all", "all"), ("ALL", "all")]:
        monkeypatch.setenv("RPG_P1_CONTINUITY_GATE", raw)
        assert p1_gate_mode() == expect, raw
    monkeypatch.delenv("RPG_P1_CONTINUITY_GATE", raising=False)
    assert p1_gate_mode() == "off"


def test_empty_projection_is_conservative_high():
    """投影不可用 → 保守 high(信息不足不降级;管线测试的空状态走此路,行为可预期)。"""
    assert grade_risk("观察四周", {}) == "high"
    assert grade_risk("观察四周", None) == "high"


def test_irreversible_action_is_high():
    assert grade_risk("我烧毁那封信", STATE) == "high"
    assert grade_risk("我喝下瓶里的药水", STATE) == "high"


def test_time_jump_intent_is_high():
    assert grade_risk("第二天再出发", STATE) == "high"
    assert grade_risk("我们几天后再碰头", STATE) == "high"


def test_critical_mechanism_is_high():
    assert grade_risk("我试试密码是铜牌上的数字", STATE) == "high"
    assert grade_risk("用钥匙打开密道", STATE) == "high"


def test_transfer_of_held_item_is_high():
    assert grade_risk("我把半枚铜牌交出去", STATE) == "high"
    assert grade_risk("把油灯扔掉", STATE) == "high"


def test_transfer_word_without_held_item_stays_low():
    """移交动词但未命中投影持有物 → 不放大(凭空名词不升级,避免全量缓冲退化)。"""
    assert grade_risk("我想把皇帝玉玺交出去", STATE) == "low"


def test_transfer_with_unrecorded_items_is_conservative_high():
    """R4 fail-safe:移交/丢弃动词 + 持有清单"未记录"(recorder 异步写回未落/
    权威层为空)→ 保守 high,不静默降级。根因锚点:R2真实链"扔进铜牌"判 low、
    risk_gate 事件未复现即此窗口(玩家要动真物品,投影却无清单可核)。"""
    state = {
        "player": {"current_location": "雾钟驿站"},
        "world": {"time": "九月十四 夜"},
        "memory": {"resources": []},
        "active_entities": [{
            "name": "守夜人", "source": "room_data", "status": "present", "location": "雾钟驿站",
        }],
    }
    assert grade_risk("我把铜牌扔进水道", state) == "high"
    assert grade_risk("把油灯交出去", state) == "high"
    # 持有未记录时,非转移输入仍按原语义(观察=low)——只补 fail-safe,不扩大缓冲。
    assert grade_risk("观察四周", state) == "low"


def test_projection_format_drift_is_conservative_and_logged(caplog):
    """复核B6:投影无"玩家持有"行(格式漂移)→转移词保守 high 且必须留告警,
    不允许静默漂移让全量转移回合无差别判 high 而无人知晓。"""
    import logging

    def drifting_projection(_state):
        return "【权威场景投影】\n当前地点：雾钟驿站\n（格式改版后无持有行）"

    from state.utils import format_current_scene_projection as real_projection
    from chat_pipeline import _risk
    monkeypatch_target = _risk.format_current_scene_projection
    import pytest

    state = {
        "player": {"current_location": "雾钟驿站"},
        "world": {"time": "九月十四 夜"},
        "memory": {"resources": ["半枚铜牌"]},
        "active_entities": [],
    }
    original = monkeypatch_target
    _risk.format_current_scene_projection = drifting_projection
    try:
        with caplog.at_level(logging.WARNING, logger="chat_pipeline._risk"):
            assert grade_risk("我把铜牌扔进水道", state) == "high"
        assert any("risk_projection_format_drift" in r.message for r in caplog.records)
    finally:
        _risk.format_current_scene_projection = original


def test_presence_change_with_known_person_is_medium():
    assert grade_risk("等守夜人离开后我再动", STATE) == "medium"
    assert grade_risk("守夜人走进来时我装睡", STATE) == "medium"


def test_presence_word_without_entity_stays_low():
    assert grade_risk("随便聊聊天气", STATE) == "low"


def test_ordinary_observation_is_low():
    assert grade_risk("观察四周", STATE) == "low"
    assert grade_risk("和掌柜聊聊今晚的客人", STATE) == "low"


def test_grading_is_deterministic():
    assert grade_risk("我烧毁那封信", STATE) == grade_risk("我烧毁那封信", STATE)


def test_gate_mode_off_leaves_pipeline_unchanged(monkeypatch):
    """off 模式:工厂走正常 respond_stream_with_tools(不进 P1 纯文本工厂)——
    用管线级断言验证 graded 不误伤生产现状。"""
    monkeypatch.setenv("RPG_P1_CONTINUITY_GATE", "0")
    import chat_pipeline.gm as pipeline

    captured = {}

    class Backend:
        last_usage = {}

        def stream(self, *a, **k):
            return iter(["正文"])

    class GM:
        api_id = "moneyai"

        def __init__(self):
            self._backend = Backend()

        def respond_stream_with_tools(self, *a, **k):
            captured["tools_path"] = True
            return iter([{"type": "text", "text": "正文"}])

        def _build_system(self):
            return "SYS"

    from chat_pipeline._common import PipelineContext
    from state import GameState

    ctx = PipelineContext(
        api_user={"id": 1}, state=GameState.new(), gm=GM(), sub_gm=None,
        message_for_model="观察", run_id=1, stop_event=threading.Event(), chat_start_time=0.0,
    )
    ctx.bundle = {"prompt": "", "debug": {"layers": []}}
    ctx.agent_result = {"curator_plan": {}}

    async def collect():
        return [e async for e in pipeline.run_gm_phase(
            ctx,
            payload_fn=lambda u: {}, persist_chat_turn=lambda *a, **k: None,
            mark_context_run=lambda *a, **k: None, current_run_id_fn=lambda u: 1,
            is_stop_requested_global=lambda u, r: False,
            is_extractor_enabled=lambda u: False, is_black_swan_enabled=lambda u: False,
            acceptance_verifier_mode=lambda u: "rule", verify_acceptance=lambda *a, **k: [],
            active_script_id=lambda u: None,
        )]

    events = asyncio.run(collect())
    assert captured.get("tools_path") is True, "off 模式必须走正常工具流式工厂"
    tokens = [d.get("text") for e, d in events if e == "token"]
    assert tokens == ["正文"]


def test_graded_mode_buffers_only_high(monkeypatch):
    """graded 模式:high 缓冲走 P1 工厂;low 走正常流式。分级器 monkeypatch 控制级别。"""
    monkeypatch.setenv("RPG_P1_CONTINUITY_GATE", "1")
    import chat_pipeline.gm as pipeline
    monkeypatch.setattr(pipeline, "grade_risk", lambda inp, st: "low")

    captured = {}

    class Backend:
        last_usage = {}

        def stream(self, *a, **k):
            return iter(["低风险正文"])

    class GM:
        api_id = "moneyai"

        def __init__(self):
            self._backend = Backend()

        def respond_stream_with_tools(self, *a, **k):
            captured["tools_path"] = True
            return iter([{"type": "text", "text": "低风险正文"}])

        def _build_system(self):
            return "SYS"

    from chat_pipeline._common import PipelineContext
    from state import GameState

    ctx = PipelineContext(
        api_user={"id": 1}, state=GameState.new(), gm=GM(), sub_gm=None,
        message_for_model="观察", run_id=1, stop_event=threading.Event(), chat_start_time=0.0,
    )
    ctx.bundle = {"prompt": "", "debug": {"layers": []}}
    ctx.agent_result = {"curator_plan": {}}

    async def collect():
        return [e async for e in pipeline.run_gm_phase(
            ctx,
            payload_fn=lambda u: {}, persist_chat_turn=lambda *a, **k: None,
            mark_context_run=lambda *a, **k: None, current_run_id_fn=lambda u: 1,
            is_stop_requested_global=lambda u, r: False,
            is_extractor_enabled=lambda u: False, is_black_swan_enabled=lambda u: False,
            acceptance_verifier_mode=lambda u: "rule", verify_acceptance=lambda *a, **k: [],
            active_script_id=lambda u: None,
        )]

    events = asyncio.run(collect())
    assert captured.get("tools_path") is True, "graded+low 必须走正常流式工厂(不缓冲)"
    tokens = [d.get("text") for e, d in events if e == "token"]
    assert tokens == ["低风险正文"]
    assert ctx.state.data.get("_risk_level") == "low"
