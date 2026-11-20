"""F2c · 元话术治理(确定性检测,R5改进清单#2)。

R5 批次实证的"元话术替代正文"形态:玩家做了明确行动(尤其时间推进类),
GM 不执行、不写剧情,而是输出"待确认/请确认以下选择/时间跳跃待确认"式
系统菜单,玩家行动零执行——两候选均出现(R3评审 8 处 severe 命中)。

治理设计(借上游 persist 层确定性兜底手法,不靠 prompt 恳求):
- 确定性检测:命中元话术模式且正文短(菜单替代叙事的特征) → 识别为
  meta_menu_replaced 事件,入 state 审计 + 单独日志;
- 不拦截(A1分层纪律:确定性硬门只管协议泄露/连续性,这里玩家自主权类
  先观察度量),供后续 prompt 治理与 F 阶段复盘有真实数据。
"""
from __future__ import annotations

import os
import re

# R5 实证形态提炼(保守窄口径,零误伤优先):
_META_MENU_PATTERNS = (
    re.compile(r"时间跳跃?[仍尚]+.{0,6}待确认"),
    re.compile(r"【时间跳跃[^】]{0,12}待确认】"),
    re.compile(r"请确认以下选择"),
    re.compile(r"确认后，?场景才会推进"),
    re.compile(r"仍处于待确认状态，?尚未成为已发生事实"),
)
# 菜单替代叙事的篇幅特征:整段输出以系统裁定/菜单为主,没有展开的叙事
_MAX_NARRATIVE_CHARS = 400


def detect_meta_menu(text: str) -> str | None:
    """命中返回匹配片段(供审计),未命中返回 None。零误伤口径:
    ① 命中任一实证模式 ② 清洗后正文不足 _MAX_NARRATIVE_CHARS(菜单没替代
    长叙事的不算——那只是剧情里人物在填表,不是 GM 在推卸)。"""
    if not text:
        return None
    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) >= _MAX_NARRATIVE_CHARS:
        return None
    for pat in _META_MENU_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


# ── F2c-2 拦截层(2026-11-20,Jimmy「宗师级完美解决」指令;观察期结束) ──


def meta_menu_gate_enabled() -> bool:
    """拦截开关:默认开;RPG_META_MENU_GATE=0 回退 F2c 纯观测模式。"""
    return os.environ.get("RPG_META_MENU_GATE", "1").lower() not in {"0", "false", "off"}


def action_intent_signal(player_input: str) -> bool:
    """玩家输入含明确行动意图 → 该回合值得缓冲验菜单(元话术的最强预测因子,
    R5 实证 8 处 severe 全部是明确行动/时间推进被菜单挡)。

    词库共享缝:时间推进/移交/不可逆直接复用 _risk 权威词表(同面横扫——
    _risk 更新时此处自动跟随),另补移动动词。宁漏勿误:漏网由 persist 层
    F2c 观测兜底,误伤则所有行动回合都缓冲(流式体验退化)。
    """
    from ._risk import _IRREVERSIBLE, _TIME_JUMP, _TRANSFER

    if not player_input:
        return False
    text = player_input.strip()
    if len(text) > 200:  # 行动意图类输入通常简短;长篇输入(自带叙述)不按行动处理
        return False
    movement = ("前往", "走向", "跑去", "回到", "迈步", "动身", "出发", "跟上", "追上", "离开")
    vocab = _TIME_JUMP + _TRANSFER + _IRREVERSIBLE + movement
    return any(word in text for word in vocab)


META_MENU_REWRITE_DIRECTIVE = (
    "【系统:元话术修正 —— 是改写替换,不是续写】\n"
    "你上一版回应没有执行玩家的行动,而是输出了「待确认」式系统菜单。这违反规则:\n"
    "玩家的明确行动就是本回合【已发生的事实】,GM 必须直接书写其后果的叙事正文。\n"
    "请重写这一版:承接玩家本轮行动、直接推进剧情、输出完整叙事正文(≥200字)。\n"
    "禁止:待确认/请确认菜单、选项列表、系统裁定口吻、把玩家行动悬置或退回。\n\n"
    "【玩家本轮行动(已发生,直接写后果)】\n{player_action}\n\n"
    "【你的上一版回应(待整段替换的对象)】\n{draft}\n\n"
    "现在直接输出【改写后的完整正文】(不要解释、不要接着写之后发生的事):"
)


def enforce_meta_menu(
    response: str,
    *,
    regenerate: "callable",
) -> tuple[str, bool, str]:
    """F2c-2 拦截核心(缓冲已就位时调用)。返回 (定稿, 是否重写过, 命中片段)。

    链路: detect 命中 → regenerate(旧稿清洗版) 一次 → 复检仍命中/空稿 →
    ContinuityRejected("meta_menu_rewrite_rejected") fail-closed
    (复刻 A1 协议泄露"一次修正+二次复检"先例)。未命中原样返回。
    """
    from state import strip_json_state_ops

    from ._continuity import ContinuityRejected

    clean = strip_json_state_ops(response or "")
    hit = detect_meta_menu(clean)
    if not hit:
        return response, False, ""
    rewritten = (regenerate(clean) or "").strip()
    rclean = strip_json_state_ops(rewritten)
    if detect_meta_menu(rclean) or not rclean.strip():
        raise ContinuityRejected(
            "meta_menu_rewrite_rejected",
            draft=rewritten,
            unmet=[f"meta_menu:{hit[:60]}"],
        )
    return rewritten, True, hit
