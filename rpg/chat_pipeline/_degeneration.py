"""F2d · 退化循环治理(R5改进#6, R3 同族结论"输出预算+流式监控"的流式/终态落地)。

R3/R5 实证的崩坏形态: 万字符级机械复读——同一文本单元紧邻重复数十次。
治理设计(确定性代码缝, 不靠 prompt 恳求):
- 核心检测: 周期 8..64 字的紧邻重复单元出现 ≥ min_reps 次 → 退化。
  紧邻重复是机械复读的特征; 文学排比/复沓通常变体或短于 8 字单元×4 连排,
  零误伤口径(宁漏勿误——漏网只是长文本, 误伤是砍正常叙事)。
- 终态压缩: 紧邻重复段压成 1 份(确定性, 无需 LLM 重生成), 入审计。
- 流式止损: StreamDegenerationGuard 转发层放行至第 ALLOWED_REPS 次重复,
  其后不再转发该重复流(玩家最多看到 ALLOWED_REPS 次; 已发出不可收回,
  万字符崩坏被截在数百字内)。与 StreamFenceGuard/StreamLeakGuard 同族模式。
- 开关 RPG_DEGENERATION_GATE 默认开; =0 时检测仍入审计但不压缩不截流。
"""
from __future__ import annotations

import os
import re

MIN_PERIOD = 8   # 重复单元最小长度(字)
MAX_PERIOD = 64  # 最大长度
MIN_REPS = 4     # 判退化的紧邻重复次数(4 次起判; 排比按变体不中)
ALLOWED_REPS = 3  # 流式层最多放行次数(第 4 次起截)


def degeneration_gate_enabled() -> bool:
    """压缩/截流开关; 检测恒开(审计口径)。"""
    return os.environ.get("RPG_DEGENERATION_GATE", "1").lower() not in {"0", "false", "off"}


def find_degenerate_repeat(text: str) -> tuple[str, int]:
    """返回 (重复单元, 重复次数); 未命中返回 ("", 0)。

    扫描从最短周期开始, 命中首个(最左最短)退化点即返回——压缩会反复调用
    直到无命中(循环文本如 ABAB 需多轮)。
    """
    if not text:
        return "", 0
    length = len(text)
    for start in range(0, max(0, length - MIN_PERIOD * MIN_REPS) + 1):
        for period in range(MIN_PERIOD, MAX_PERIOD + 1):
            unit = text[start : start + period]
            if len(unit) < period:
                break
            reps = 1
            pos = start + period
            while text[pos : pos + period] == unit:
                reps += 1
                pos += period
            if reps >= MIN_REPS:
                return unit, reps
    return "", 0


def degeneration_clean(text: str) -> str:
    """剥离退化段前后的空白后做整体判断的入口; 实际压缩循环在此。"""
    if not text:
        return text
    out = text
    for _ in range(10):  # 嵌套/多轮复读上限 10 轮, 防病态输入死循环
        unit, reps = find_degenerate_repeat(out)
        if not unit:
            break
        # 压缩: 该重复段全部替换为单份单元
        target = unit * reps
        out = out.replace(target, unit, 1)
    return out


def compress_degeneration(text: str) -> tuple[str, int, str]:
    """终态确定性压缩。返回 (压缩后文本, 压缩次数, 首个重复单元样本)。

    开关关闭时原样返回(检测样本仍返回供审计)。
    """
    unit, reps = find_degenerate_repeat(text or "")
    if not unit:
        return text, 0, ""
    if not degeneration_gate_enabled():
        return text, 0, unit
    out = text
    rounds = 0
    for _ in range(10):
        u, r = find_degenerate_repeat(out)
        if not u:
            break
        out = out.replace(u * r, u, 1)
        rounds += 1
    return out, rounds, unit


class StreamDegenerationGuard:
    """流式转发层止损: 与 StreamFenceGuard 同族(调用方维护完整累积)。

    feed(chunk) 返回当前可转发文本; 检测到紧邻重复已达 ALLOWED_REPS 次
    后进入截流态——后续 chunk 仅在检测到"走出重复段"(新内容)时恢复放行。
    """

    def __init__(self) -> None:
        self._tail = ""       # 尾部窗口(最长 MAX_PERIOD*2, 检测紧邻重复用)
        self._last_unit = ""  # 当前正在截的重复单元
        self._truncated = False

    @property
    def truncated(self) -> bool:
        return self._truncated

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        if not degeneration_gate_enabled():
            return chunk
        self._tail = (self._tail + chunk)[-MAX_PERIOD * 4 :]
        unit, reps = find_degenerate_repeat(self._tail)
        if unit and reps >= ALLOWED_REPS and not self._truncated:
            # 首次达阈: 前 ALLOWED_REPS-1 份已在此前 feed 实时放行,
            # 本 chunk(第 ALLOWED_REPS 份)起整吞, 进入截流态。
            self._truncated = True
            self._last_unit = unit
            return ""
        if self._truncated:
            # 截流态: 纯重复延续吞掉; 出现新内容则恢复放行(剧情可能回到正轨)
            if self._is_unit_continuation(chunk):
                return ""
            self._truncated = False
            self._last_unit = ""
            return chunk
        return chunk

    def _is_unit_continuation(self, chunk: str) -> bool:
        u = self._last_unit
        if not u:
            return False
        for i in range(min(len(chunk), len(u))):
            if chunk[i] != u[i]:
                return False
        return True

    def flush(self) -> str:
        self._tail = ""
        return ""
