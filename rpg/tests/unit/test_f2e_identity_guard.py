"""F2e 身份吞并治理测试: 验收专用条款 + 全局身份不变式铁律注入。

R5 实证形态(出租车线玩家被 NPC 替换 / 雾钟辈分翻转)发生在自由生成层;
按 A1 分层纪律走"提示层不变式 + 验收器辅助信号 + 既有改写候选链",
不建新的确定性拦截门(中文语义零误伤检测不可达, R2 校准实证)。
"""
from chat_pipeline._continuity import CONTINUITY_CLAUSES, deterministic_gate


def test_identity_clause_is_dedicated():
    """身份一致条款独立存在且点名 R5 实证形态(称谓/辈分/NPC 顶替)。"""
    identity = [c for c in CONTINUITY_CLAUSES if c.startswith("【身份一致】")]
    assert len(identity) == 1
    for keyword in ("称谓", "辈分", "亲属关系", "NPC 顶替"):
        assert keyword in identity[0]


def test_autonomy_clause_kept():
    """玩家自主权条款保留(原第4条语义, 措辞细化)。"""
    autonomy = [c for c in CONTINUITY_CLAUSES if c.startswith("【玩家自主权】")]
    assert len(autonomy) == 1
    assert "替玩家作新决定" in autonomy[0]


def test_protocol_marker_clause_still_last():
    """确定性硬门锚定最后一条(协议标记)——索引 [-1] 引用不受拆分影响。"""
    assert "协议" in CONTINUITY_CLAUSES[-1] or "内部状态指令" in CONTINUITY_CLAUSES[-1]
    assert deterministic_gate("正文正常, 无标记。") == []
    assert deterministic_gate("正文带 <|im_start|> 标记。") == [CONTINUITY_CLAUSES[-1]]


def test_master_identity_invariant_injected():
    """master 系统 prompt 注入全局身份不变式(所有局, 非仅穿越者)。"""
    from agents.gm.master import GameMaster

    master = GameMaster.__new__(GameMaster)  # 不跑 __init__(依赖重), 只测 _dynamic_context 纯文本段
    text = master._dynamic_context("玩家档案: 林川, 表字川哥, 男性", "")
    assert "身份不变式" in text
    assert "辈分" in text and "不得让 NPC 替玩家说话" in text
