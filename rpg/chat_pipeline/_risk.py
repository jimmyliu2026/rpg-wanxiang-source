"""R2 risk grading: deterministic signals + authoritative projection → low/medium/high.

蓝图 4.2 口径:
- 风险级别由【确定性信号与状态变化】共同决定,模型不得自报"安全"来降级。
- low(氛围/对话/观察)=流式;medium(NPC进退场/关系变化)=流式+事后审计候选;
  high(物品移交/丢弃、不可逆动作、时间跳转、关键机关/身份)=正文首外发前验收(一次修正)。

工程语义(因果链):
- 输入信号匹配【权威投影里的实体/物品名】才升级——凭空名词不放大风险(避免全量缓冲退化)。
- 投影不可用(空状态/新档) → 保守判 high:信息不足按高风险处理(fail-safe),绝不静默降级。
- 纯确定性:不调 LLM、无随机;同输入同投影必同级别(可单测、可回放)。
"""
from __future__ import annotations

import os
import re

from state.utils import format_current_scene_projection

# ── 确定性信号词(中文语义信号,词组级,不用单字符) ──────────────────────────
# 高风险:物品移交/丢弃(需命中投影持有物)、不可逆破坏/消耗、时间跳转意图、关键机关/身份。
_IRREVERSIBLE = (
    "烧毁", "撕毁", "砸碎", "摔碎", "销毁", "喝下", "吞下", "杀死", "杀掉", "处决",
    "签订", "签下", "抵押", "典当", "立誓", "发誓",
)
_TIME_JUMP = (
    "第二天", "次日", "隔日", "几天后", "数日后", "翌日", "次日凌晨", "次日清晨",
    "一周后", "半个月后", "一个月后", "数月后", "一年后",
)
_CRITICAL_MECHANISM = (
    "密码", "口令", "密道", "机关", "封印", "解开封", "钥匙打开", "验明正身",
    "身份验证", "对暗号", "婚书", "休书", "遗书", "遗嘱", "传位",
)
_TRANSFER = (
    "交出", "交给", "递给", "归还", "还给", "丢弃", "扔掉", "扔了", "丢掉",
    "塞给", "递给", "转赠", "赠予", "送给", "抵押出去", "卖掉", "出售", "用掉",
    "扔进", "丢进", "投入", "抛进", "掷入", "烧掉", "撕碎", "毁掉",
)
# 中风险:NPC/关系变化(需命中投影在场人物或已知名词)。
_PRESENCE_CHANGE = (
    "离开", "走了", "退下", "告辞", "进来", "闯进", "走进", "出现", "登场",
    "赶到", "闯入", "带进来", "带进来", "引见", "介绍",
)
_RELATION = ("结拜", "成婚", "拜堂", "定亲", "断绝", "绝交", "收徒", "拜师", "认亲", "翻脸")


def _projection_names(state_data: dict) -> tuple[set[str], set[str]]:
    """从权威投影文本提取可匹配名词:持有物品名 + 在场人物名。

    复用 format_current_scene_projection(P1-A 共享投影,唯一权威),按其真实输出行格式解析:
    "玩家持有（完整）：A、B" / "可信在场人物：名字（位置）"。
    返回 (item_names, person_names);解析不到任何名词 → 空集(调用方保守处理)。
    """
    text = ""
    try:
        text = format_current_scene_projection(state_data or {})
    except Exception:
        text = ""
    items: set[str] = set()
    persons: set[str] = set()
    if not text:
        return items, persons
    current: set[str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "玩家持有" in line:
            current = items
        elif "可信在场人物" in line:
            current = persons
        elif line.startswith("【") or "当前地点" in line or "当前时间" in line or "待确认" in line or "投影未记录" in line:
            current = None
            continue
        if current is None:
            continue
        rest = line.split("：", 1)[-1] if "：" in line else line.split(":", 1)[-1]
        # 投影行尾的规约说明("未记录；不得因被提及而自行添加")不是实体,整体跳过该行。
        rest = rest.split("；", 1)[0].split(";", 1)[0]
        # 剥掉括号内容(人物行的"名字（位置）"只留主词,位置不进人物集)。
        rest = re.sub(r"[（(][^）)]*[）)]", " ", rest)
        for token in re.split(r"[、,，;；/·()（）\s]+", rest):
            token = token.strip("·。.-—-* ")
            if 2 <= len(token) <= 12 and not token.startswith("未记录"):
                current.add(token)
    return items, persons


def grade_risk(player_input: str, state_data: dict) -> str:
    """确定性风险分级:返回 'low' | 'medium' | 'high'。"""
    text = (player_input or "").strip()
    item_names, person_names = _projection_names(state_data)
    projection_text = ""
    try:
        projection_text = format_current_scene_projection(state_data or {})
    except Exception:
        projection_text = ""

    # 保守默认:投影不可用(异常/空)或全部"未记录" → high(信息不足不降级;
    # 管线测试的空状态也走这条,行为可预期)。
    if not projection_text.strip() or projection_text.count("未记录") >= 4:
        return "high"

    # 高风险:不可逆动作 / 时间跳转意图 / 关键机关身份 —— 输入命中即升级(与实体无关的硬信号)。
    if any(w in text for w in _IRREVERSIBLE):
        return "high"
    if any(w in text for w in _TIME_JUMP):
        return "high"
    if any(w in text for w in _CRITICAL_MECHANISM):
        return "high"
    # 高风险:物品移交/丢弃 且 命中投影持有物(状态将变化)。
    # 匹配含尾段:玩家常以简称指物("铜牌" vs 投影"半枚铜牌")——物品名全串或尾2字命中均算。
    def _hits_item() -> bool:
        return any(
            name in text or (len(name) > 2 and name[-2:] in text)
            for name in item_names
        )
    # R4 fail-safe:玩家明确移交/丢弃物品,但权威投影连持有清单都"未记录"
    # (recorder 异步写回未落/权威层为空)——信息不足按高风险处理,不静默降级。
    # 根因锚点:R2真实链回合10"扔进铜牌"判 low、risk_gate 未复现即此窗口。
    def _items_unrecorded() -> bool:
        for line in projection_text.splitlines():
            if "玩家持有" in line:
                return "未记录" in line
        # 复核B6:投影无"玩家持有"行=格式漂移(投影函数改版/异常输出)——保守 high
        # 安全,但必须有度量:静默漂移会让全量转移词无差别判 high 而无人知晓。
        import logging
        logging.getLogger(__name__).warning(
            "risk_projection_format_drift: no holdings line in projection; "
            "transfer inputs conservatively graded high"
        )
        return True
    if any(w in text for w in _TRANSFER) and (_hits_item() or _items_unrecorded()):
        return "high"

    # 中风险:进退场/关系 且 命中投影人物或输入含人名。
    if any(w in text for w in _PRESENCE_CHANGE + _RELATION) and (
        any(name in text for name in person_names) or any(name in text for name in item_names)
    ):
        return "medium"

    return "low"


def p1_gate_mode() -> str:
    """RPG_P1_CONTINUITY_GATE 语义升级(R2):
    '0'/'off'/缺省 → 'off'(生产现状,零变化);
    '1'/'on'/'true' → 'graded'(R2 分级门:仅 high 缓冲验收);
    'all' → 全量门(P1 复测语义:所有回合缓冲,兼容历史回归)。"""
    raw = (os.environ.get("RPG_P1_CONTINUITY_GATE", "0") or "0").strip().lower()
    if raw in ("all",):
        return "all"
    if raw in ("1", "true", "on", "yes"):
        return "graded"
    return "off"
