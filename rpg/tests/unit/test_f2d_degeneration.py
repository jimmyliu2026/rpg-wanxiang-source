"""F2d 退化循环治理测试: 紧邻重复检测/确定性压缩/流式截流/零误伤。"""
import pytest

from chat_pipeline._degeneration import (
    ALLOWED_REPS,
    MIN_REPS,
    StreamDegenerationGuard,
    compress_degeneration,
    degeneration_gate_enabled,
    find_degenerate_repeat,
)


def test_r3_collapse_form_detected_and_compressed():
    """R3 实证形态: 万字符级机械复读——检测命中并压成 1 份。

    周期代表元存在等价类(单元起点可滑动), 断言锁语义不锁字面起点。"""
    unit = "他推开门，雨声灌进来，灯灭了。"
    collapse = "开头正常。" + unit * 40 + "结尾正常。"
    found_unit, reps = find_degenerate_repeat(collapse)
    assert found_unit and reps >= 39  # 命中且重复次数达 39+(边界首尾差 1)
    assert found_unit in collapse
    out, rounds, sample = compress_degeneration(collapse)
    assert rounds >= 1 and sample
    # 语义: 压缩后不再有紧邻重复, 且体量回到"开头+一份单元+结尾"量级
    assert find_degenerate_repeat(out) == ("", 0)
    assert len(out) < len(collapse) // 10
    assert "开头正常" in out and "结尾正常" in out


def test_short_period_repeats_detected():
    """短单元(8字)连排 5 次 → 命中。"""
    unit = "雨还在下，灯还亮"
    assert find_degenerate_repeat(unit * 5) == (unit, 5)


def test_three_reps_not_flagged_zero_false_positive():
    """3 次紧邻重复(文学复沓上限口径) → 不判退化(零误伤)。"""
    unit = "他想起那年的雨，想起渡口的灯。"
    assert find_degenerate_repeat(unit * 3) == ("", 0)


def test_normal_narrative_clean():
    text = (
        "周明把车停进巷口，雨刷三下一停。乘客还没来。他数着挡风玻璃上的水痕，"
        "想起十年前也是这样的夜。那年他刚入行，师父坐在副驾说：夜路慢开，人稳话少。"
        "后来师父走了，车换了三台，这句话他还记得。"
    )
    assert find_degenerate_repeat(text) == ("", 0)
    out, rounds, _ = compress_degeneration(text)
    assert out == text and rounds == 0


def test_gate_off_means_audit_only(monkeypatch):
    monkeypatch.setenv("RPG_DEGENERATION_GATE", "0")
    assert degeneration_gate_enabled() is False
    unit = "风把门吹开又关上。"
    text = unit * 6
    out, rounds, sample = compress_degeneration(text)
    assert out == text and rounds == 0 and sample == unit  # 不压缩但审计样本返回


def test_gate_default_on():
    assert degeneration_gate_enabled() is True


def test_stream_guard_truncates_at_allowed_reps():
    """流式: 前 3 份重复照常放行, 第 4 份起截; 新内容恢复放行。"""
    guard = StreamDegenerationGuard()
    unit = "他沿着河岸走，雾越来越浓。"  # 13字 ≥ MIN_PERIOD
    seen = []
    for i in range(8):
        seen.append(guard.feed(unit))
    emitted = "".join(seen)
    assert emitted.count(unit) <= ALLOWED_REPS
    assert guard.truncated is True
    # 新内容(非单元延续)恢复放行
    fresh = guard.feed("天亮了，雾散了，渡口的人排起队。")
    assert fresh == "天亮了，雾散了，渡口的人排起队。"


def test_stream_guard_normal_chunks_pass_through():
    guard = StreamDegenerationGuard()
    chunks = ["夜色压下来。", "他把灯芯拨亮一点。", "远处有狗吠。"]
    assert [guard.feed(c) for c in chunks] == chunks
    assert guard.truncated is False


def test_multi_round_compression_nested_repeats():
    """两段独立复读分别压缩。"""
    u1, u2 = "第一句话重复着说。", "另一段也在重复说着。"
    text = u1 * 5 + "分隔。" + u2 * 5
    out, rounds, _ = compress_degeneration(text)
    assert out == u1 + "分隔。" + u2
    assert rounds >= 2
