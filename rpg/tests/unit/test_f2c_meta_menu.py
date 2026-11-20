"""F2c 元话术治理测试:确定性检测(R5实证8形态),零误伤,不拦截。"""
from chat_pipeline._meta_menu import detect_meta_menu


def test_r5_real_forms_detected():
    """R5 批次评审实锤的 6 种形态全部命中(短正文=菜单替代叙事)。"""
    assert detect_meta_menu("【时间跳跃裁定：待确认】\n请确认是否推进。")
    assert detect_meta_menu("你提出的时间跳跃仍处于待确认状态。")
    assert detect_meta_menu("请确认以下选择：\nA. 畼在原地\nB. 推进到天亮")
    assert detect_meta_menu("时间跳转仍待确认。确认后，场景才会推进。")
    assert detect_meta_menu("仍处于待确认状态，尚未成为已发生事实。")
    assert detect_meta_menu("【时间跳跃待确认】玩家请求从暴雨夜推进到天亮。")


def test_long_narrative_not_flagged():
    """命中词但正文是完整长叙事(≥400字清洗后)→ 不算菜单替代(零误伤)。"""
    narrative = "时间跳转仍待确认。" + "他沿着栈道走了很久，海雾漫上来。" * 40
    assert detect_meta_menu(narrative) is None


def test_normal_narrative_clean():
    assert detect_meta_menu("周明把车停进巷口，雨刷三下一停。乘客还没来。") is None
    assert detect_meta_menu("") is None


def test_in_story_character_filling_form_not_flagged():
    """剧情里人物填表/对话提及"确认"但非 GM 菜单 → 不命中(窄口径)。"""
    assert detect_meta_menu("她低头填表格,在\"是否确认\"一栏画了个勾,推回给他。") is None
