"""P1 continuity gate: validate before narrative is emitted or persisted.

A1 分层（2026-11-17 Jimmy 裁决，落实 R2 裁定A）：
- 确定性硬门 = 唯一拦截权：空稿 / 协议标记泄露；协议标记经一次修正后二次确定性
  复检仍命中 → 拒绝（fail-closed）。
- LLM 验收 = 辅助信号：unmet 仅入审计并作为改写候选素材，不拦截、不换稿；
  验收器不可用 / 异常不构成回合故障，记录为信号（R2 校准实证 LLM 层误伤 3.2%，
  漏判↔误伤跷跷板不可同时满足双硬门，故降级——r2-verifier-calibration §9）。
"""
from __future__ import annotations

import asyncio
import os
from typing import Callable

from state import strip_json_state_ops
from state.utils import format_current_scene_projection

CONTINUITY_CLAUSES = [
    "物品归属、数量与位置不得与权威场景投影、实际前文或玩家本轮明确行动冲突。",
    "人物不得无正文过程改变地点或在场状态；新进场必须有可见过程。",
    "时间不得与已确立的耗时、顺序或期限冲突；不得无依据回退。",
    # F2e(2026-11-20, R5 改进#3 身份吞并/口径翻转): 原"玩家自主权"泛条款拆出
    # 专用身份一致条款——R5 实证形态: 玩家被 NPC 替换(出租车线)、辈分称谓翻转(雾钟)。
    "【身份一致】正文对玩家的称谓、辈分、亲属关系、性别必须与玩家档案和前文一致，"
    "不得翻转或混乱；不得把玩家与在场 NPC 混同、让 NPC 顶替玩家的位置或身份，"
    "也不得以 NPC 的名字/视角指称玩家。",
    "【玩家自主权】不得新增玩家未授权的独立选择、台词、内心决定或记忆；完成输入明确动作所需的"
    "合理过程细节可以写，但不得改变其意图或替玩家作新决定。输入中明确动作已发生，不得重复执行。",
    "【作者设定一致】人物身份、辈分与关系不得与作者设定权威证据冲突；该证据仅用于辅助验收，不新增硬拦截。",
    "不得输出思考过程、工具调用协议、工具参数或内部状态指令；只能给玩家真正的叙事正文。",
]

P1_NARRATIVE_RULE = (
    "【P1纯叙事实验】本回合不得调用工具，不得输出任何工具标记、参数、思考过程或状态指令；"
    "只输出玩家可读的完整叙事正文。"
)

# 协议标记=出现即拦截(确定性门唯一unmet类)。R4复核B1:R2校准版4标记扩展至8——
# 补 ChatML/Qwen 形态(R5 多模型批次即将遇到);均为协议保留字,正文合法出现概率≈0。
# 可确定性清洗挽救的形态(脚手架header/英文meta preamble)不进此清单,留展示层剥离
# (能清理干净的不拒整回合)。扩展部分的零漏判/零误伤由 R4 新测试锚定,非沿用R2校准。
_PROTOCOL_MARKERS = (
    "<<TOOL_CALL>>", "<<END_TOOL_CALL>>", "<think", "</think>",
    "<|im_start|>", "<|im_end|>", "<tool_call>", "</tool_call>",
)


class ContinuityRejected(RuntimeError):
    def __init__(self, message: str, *, draft: str = "", unmet: list[str] | None = None):
        super().__init__(message)
        self.draft = draft
        self.unmet = unmet or []


def p1_continuity_enabled() -> bool:
    return os.environ.get("RPG_P1_CONTINUITY_GATE", "0").lower() in {"1", "true", "on"}


def deterministic_gate(narrative: str) -> list[str]:
    """确定性硬门（唯一拦截权）。输入应为 strip_json_state_ops 后的正文。

    返回 unmet 条款（当前仅协议标记一类）；空列表 = 通过。空稿由 enforce_continuity
    直接拒绝，不在此判（区分错误类型）。与 R2 校准 deterministic_gate 同构
    （r2-evidence-2026-11-16/calibration-tools/r2_run_verifier.py：故障池 20/20
    零漏判 + 正常池 53/53 零误伤的判定层）。"""
    if any(marker in narrative for marker in _PROTOCOL_MARKERS):
        return [CONTINUITY_CLAUSES[-1]]
    return []


def _llm_signal(
    narrative: str, state_data: dict, player_input: str, user_id: int | None,
    script_id: int | None = None, save_id: int | None = None,
) -> list[str] | None:
    """LLM 辅助信号（不拦截）。返回 unmet 列表（可能为 []）；None = 验收器不可用。"""
    from agents.acceptance_verifier import verify_acceptance_llm

    evidence = [
        format_current_scene_projection(state_data),
        "玩家本轮输入（最高授权证据）：" + (player_input or ""),
    ]
    author_sections: list[str] = []
    if script_id:
        try:
            from extract.author_seed import load_author_seed_evidence
            author_evidence = load_author_seed_evidence(script_id, save_id)
            if author_evidence:
                # 独立有界 prompt 段，不走 updates 每项 200 字截断通道。
                author_sections.append(author_evidence)
        except Exception:
            # 作者证据不可用不遮蔽原有验收器；A1 仍只产生辅助信号。
            pass
    return verify_acceptance_llm(
        CONTINUITY_CLAUSES, narrative, evidence, user_id=user_id,
        effort_override="medium", evidence_sections=author_sections,
    )


async def enforce_continuity(
    draft: str,
    *,
    state_data: dict,
    player_input: str,
    user_id: int | None,
    rewrite: Callable[[str, list[str]], str],
    script_id: int | None = None,
    save_id: int | None = None,
) -> tuple[str, int, list[str]]:
    """A1 分层验收。返回 (accepted_draft, rewrite_count, llm_signal)。

    - accepted_draft：确定性硬门通过（或经一次确定性修正后通过）的稿；首稿权威。
    - rewrite_count：仅确定性协议标记修正计入（0 或 1）。
    - llm_signal：LLM 辅助信号（非拦截依据）：[] / unmet 条款 /
      ["verifier_unavailable"] / ["verifier_error:<类型>"]——由调用方记审计、
      作改写候选素材。
    拒绝（ContinuityRejected）仅由确定性硬门触发：空稿、协议标记修正后仍命中。"""
    narrative = strip_json_state_ops(draft or "").strip()
    if not narrative:
        raise ContinuityRejected("empty_continuity_draft", draft=draft)
    det_unmet = deterministic_gate(narrative)
    if det_unmet:
        rewritten = await asyncio.to_thread(rewrite, draft, det_unmet)
        rnarrative = strip_json_state_ops(rewritten or "").strip()
        if not rnarrative or deterministic_gate(rnarrative):
            raise ContinuityRejected(
                "protocol_marker_rewrite_rejected", draft=rewritten, unmet=det_unmet
            )
        # 复核D2:确定性修正成功是确定性事件(rewrites=1 已记录),不冒充 llm_signal——
        # 否则已修净的稿仍触发后台改写候选+审计语义污染。
        return rewritten, 1, []
    llm_signal: list[str] = []
    try:
        if script_id:
            signal = await asyncio.to_thread(
                _llm_signal, narrative, state_data, player_input, user_id, script_id, save_id
            )
        else:
            # 保留既有内部回调签名（测试/外部嵌入方可替换四参数 _llm_signal）。
            signal = await asyncio.to_thread(
                _llm_signal, narrative, state_data, player_input, user_id
            )
        llm_signal = ["verifier_unavailable"] if signal is None else list(signal)
    except Exception as exc:  # noqa: BLE001 —— 辅助信号故障不构成回合故障，原样记录
        llm_signal = [f"verifier_error:{type(exc).__name__}"]
    return draft, 0, llm_signal
