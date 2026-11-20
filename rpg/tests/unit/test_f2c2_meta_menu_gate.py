"""F2c-2 元话术拦截层测试:行动意图缓冲升级 + 一次重生成 + 二次拒绝 fail-closed。"""
import pytest

from chat_pipeline._continuity import ContinuityRejected
from chat_pipeline._meta_menu import (
    META_MENU_REWRITE_DIRECTIVE,
    action_intent_signal,
    enforce_meta_menu,
    meta_menu_gate_enabled,
)


def test_gate_enabled_by_default_and_off_switch(monkeypatch):
    assert meta_menu_gate_enabled() is True
    monkeypatch.setenv("RPG_META_MENU_GATE", "0")
    assert meta_menu_gate_enabled() is False
    monkeypatch.setenv("RPG_META_MENU_GATE", "false")
    assert meta_menu_gate_enabled() is False


def test_action_intent_signal_uses_risk_vocab_and_movement():
    # 时间推进(R5 实证最强预测因子)/移动/移交类 → 行动意图
    assert action_intent_signal("我等到第二天一早再出发。")
    assert action_intent_signal("前往钟楼。")
    assert action_intent_signal("把钥匙交给守门人。")
    assert action_intent_signal("我烧毁那封信。")


def test_action_intent_signal_zero_false_positive():
    # 普通对话/观察/空/超长自带叙述 → 不缓冲
    assert action_intent_signal("") is False
    assert action_intent_signal("你觉得他会怎么想？") is False
    assert action_intent_signal("夜色很好。") is False
    long_input = "我回忆起很久以前的事情，" + "那些细节像潮水一样涌来，" * 30
    assert action_intent_signal(long_input) is False


def test_enforce_clean_draft_passes_through_without_regeneration():
    calls = []
    final, rewritten, hit = enforce_meta_menu(
        "周明推门进来，把伞收在门边，说雨还要下三天。",
        regenerate=lambda old: calls.append(old) or "should-not-run",
    )
    assert final.startswith("周明推门")
    assert rewritten is False and hit == "" and calls == []


def test_enforce_menu_hit_regenerates_once_and_accepts_clean():
    menu_draft = "【时间跳跃待确认】玩家请求从暴雨夜推进到天亮。"
    clean_rewrite = "天亮了。雨停在四更，檐角还在滴水。" + "他把湿透的外袍拧了拧，摊在窗边。" * 10
    seen = []

    def regen(old):
        seen.append(old)
        return clean_rewrite

    final, rewritten, hit = enforce_meta_menu(menu_draft, regenerate=regen)
    assert rewritten is True and "待确认" in hit
    assert final == clean_rewrite
    assert len(seen) == 1  # 恰一次重生成


def test_enforce_menu_hit_second_menu_rejected_fail_closed():
    menu_draft = "时间跳转仍待确认。确认后，场景才会推进。"

    def regen(old):
        return "请确认以下选择：\nA. 原地等待\nB. 推进到天亮"  # 重生成仍是菜单

    with pytest.raises(ContinuityRejected, match="meta_menu_rewrite_rejected"):
        enforce_meta_menu(menu_draft, regenerate=regen)


def test_enforce_menu_hit_empty_regeneration_rejected():
    menu_draft = "【时间跳跃裁定：待确认】"
    with pytest.raises(ContinuityRejected, match="meta_menu_rewrite_rejected"):
        enforce_meta_menu(menu_draft, regenerate=lambda old: "")


def test_directive_carries_player_action_and_draft():
    text = META_MENU_REWRITE_DIRECTIVE.format(player_action="我等到第二天。", draft="【时间跳跃待确认】")
    assert "我等到第二天。" in text
    assert "【时间跳跃待确认】" in text
    assert "待确认" in text and "禁止" in text
