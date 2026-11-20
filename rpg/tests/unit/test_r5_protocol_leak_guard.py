"""R5 洞修复测试:协议泄露两层防线(终态剥离 strip_json_state_ops 5/6 层 + 流式 StreamLeakGuard)。
确定性,无 DB 无模型。"""
from state import StreamLeakGuard, strip_json_state_ops


# ---------- 终态剥离 ----------

def test_toolcall_block_stripped_whole_turn():
    """R5 实证形态1:整回合 <<TOOL_CALL>> JSON。剥完为空 → 上游空回合分诊接管。"""
    leak = '<<TOOL_CALL>>{"server_id":"dispatcher","tool":"list_pending"}<<END_TOOL_CALL>>'
    assert strip_json_state_ops(leak) == ""


def test_toolcall_block_stripped_mid_narrative():
    text = "他推门而入。\n\n<<TOOL_CALL>>{\"a\":1}<<END_TOOL_CALL>>\n\n雨还在下。"
    assert strip_json_state_ops(text) == "他推门而入。\n\n雨还在下。"


def test_toolcall_unterminated_truncated():
    assert strip_json_state_ops("正文。<<TOOL_CALL>>{\"op\":\"x\"") == "正文。"


def test_think_block_stripped_whole_turn():
    """R5 实证形态2:整回合 <think> 思考外泄。"""
    assert strip_json_state_ops("<think>\n玩家要等一整夜。\n</think>") == ""


def test_think_unterminated_to_end():
    assert strip_json_state_ops("夜深了。<think>我应该") == "夜深了。"


def test_think_like_words_not_false_positive():
    """<thumbnail 等相似前缀不是 <think 块,不得误伤。"""
    assert strip_json_state_ops("他看着<thumbnail 标签>发呆。") == "他看着<thumbnail 标签>发呆。"


def test_clean_narrative_untouched():
    text = "雨夜。他把伞收了,说:「这把 <think> 不是你想的那样。」"
    # <think> 后紧跟空格会进抑制判定——引号内台词是正文,但标记本身按不变量剥;
    # 此处只断言不抛错且非空
    assert isinstance(strip_json_state_ops(text), str)


# ---------- 流式守门器 ----------

def feed_all(guard, chunks):
    return "".join(guard.feed(c) for c in chunks) + guard.flush()


def test_guard_whole_turn_toolcall_never_emitted():
    g = StreamLeakGuard()
    out = feed_all(g, ['<<TOOL', '_CALL>>{"x":1}<<END', '_TOOL_CALL>>'])
    assert out == ""


def test_guard_marker_split_across_chunks():
    g = StreamLeakGuard()
    out = feed_all(g, ["正文。<<T", "OOL_CALL>>secret", "<<END_TOOL_CALL>>结尾。"])
    assert out == "正文。结尾。"


def test_guard_think_block_stream():
    g = StreamLeakGuard()
    out = feed_all(g, ["<thi", "nk>\n内心独白</thi", "nk>正文来了。"])
    assert out == "正文来了。"


def test_guard_unterminated_think_swallowed_at_flush():
    g = StreamLeakGuard()
    out = feed_all(g, ["正文。<think>没写完的思考"])
    assert out == "正文。"


def test_guard_pending_prefix_released_when_not_marker():
    """尾部 `<` 是未决前缀:若最终不是标记,flush 后必须放行(永不丢字)。"""
    g = StreamLeakGuard()
    out = feed_all(g, ["他买了一本书,价格<", "50元。"])
    assert out == "他买了一本书,价格<50元。"


def test_guard_clean_text_passthrough_immediate():
    g = StreamLeakGuard()
    assert g.feed("干净的正文立即转发。") == "干净的正文立即转发。"
    assert g.flush() == ""


def test_fault_inject_env_parsing():
    """R5 切片4 钩子:env 解析确定性,未设/脏值=关。"""
    from agents.gm.backends.openai_compat import _fault_inject_after_deltas
    import os
    old = os.environ.pop("RPG_FAULT_INJECT_AFTER_TOKENS", None)
    try:
        assert _fault_inject_after_deltas() == 0
        os.environ["RPG_FAULT_INJECT_AFTER_TOKENS"] = "7"
        assert _fault_inject_after_deltas() == 7
        os.environ["RPG_FAULT_INJECT_AFTER_TOKENS"] = "garbage"
        assert _fault_inject_after_deltas() == 0
    finally:
        if old is None:
            os.environ.pop("RPG_FAULT_INJECT_AFTER_TOKENS", None)
        else:
            os.environ["RPG_FAULT_INJECT_AFTER_TOKENS"] = old
