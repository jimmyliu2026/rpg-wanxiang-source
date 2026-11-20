# 质量攻坚 · 收官证据 · 2026-11-20

指令来源：Jimmy 2026-11-20「质量和支付都要宗师级完美解决……质量充分借鉴 github 项目」。
落点：RPG 仓 `feat/moneyai-curator-integration`（commit 链见下），全部复刻/复用上游既有机制，零平行实现。

## 四项治理（R5 定案转产品改进清单 #2/#3/#5/#6）

| # | 项 | commit | 治理 | 借鉴的上游机制 |
|---|---|---|---|---|
| 1 | 元话术拦截（#2） | 27a49cc | 行动类输入(词库复用 _risk 权威表)强制缓冲→detect_meta_menu 命中→确定性 directive 重生成一次→复检仍菜单→ContinuityRejected fail-closed；独立开关 RPG_META_MENU_GATE 默认开(生产生效，不依赖 P1 门)；persist 层 F2c 审计保留兜底 | A1 硬门"一次修正+二次复检→拒绝"先例；_rewrite_candidate_text 历史快照+文本直调模式 |
| 2 | 退化循环（#6） | 51f22b5 | 核心=周期 8..64 字紧邻重复≥4 次判退化(文学复沓 3 连不中，零误伤)；流式 StreamDegenerationGuard 第 4 次重复起截流、新内容恢复；终态 compress_degeneration 复读段压成 1 份(确定性，不用 LLM)；开关 RPG_DEGENERATION_GATE | StreamFenceGuard/StreamLeakGuard 同族跨 chunk 状态机；确定性代码缝铁律 |
| 3 | 空回合（#5） | 验证闭环 | 9 类确定性分诊(f045f6a 已建)+35 项测试全过；R5 的 3 例剥后空形态已覆盖且属修复前样本 | 泄露修复时建的空回合分诊接管机制 |
| 4 | 身份吞并/口径翻转（#3） | 14c3fed | CONTINUITY_CLAUSES 拆【身份一致】专用条款(称谓/辈分/亲属/性别一致+NPC 不顶替)→LLM 验收器辅助信号→既有审计+acceptance_alt 改写候选链；master 全局【身份不变式】铁律(所有局)；协议标记条款保持末位零索引影响 | A1 分层纪律(LLM 辅助信号不拦截, R2 校准实证)；身份独立铁律先例(群反馈#87)；改写候选跨 worker 推送链 |

## 测试证据

- 本地(Windows, SQLite): 质量管线 116 项全绿(F2c 8 + F2c-2 8 + F2d 9 + F2e 4 + 空回合 35 + P1/管线 52)
- 本地全量 72 失败=环境噪音(本地无 PG, 连接池超时坐实), 与代码无关
- 服务器容器(r1-verify 镜像+真 PG/Redis): 见 §容器回归

## 容器回归（权威环境）

第一轮 29 失败三组根因（非代码回归）：
1. dev_sh_check_db ×20：F4 已裁定的环境类（镜像跑法下本就 skipif；挂载工作树使其假跑）
2. E2E/tavern 族 ×9：挂载卷权限（容器用户写不进 platform_data）→ 第二轮 --user root + 预建目录重跑
3. 第二轮结果：见下表（跑法：docker run --user root --network r1-verify_default 挂载最新树, deselect dev_sh 组按 F4 口径）

| 轮次 | 结果 |
|---|---|
| 第一轮 | 3205 passed / 29 failed(20 dev_sh 环境类假跑 + 9 挂载卷权限) |
| **第二轮** | **3220 passed / 20 failed(全部 dev_sh 组) / 9 skipped** —— 权威结论: 非环境类 **0 失败**, 四项质量代码全绿; dev_sh 20 项为 F4 已裁定环境类(镜像跑法下 skipif 跳过, 挂载工作树使其假跑, 跑法差异非代码回归), 上轮 9 项权限失败已全部转绿 |

## 验证口径边界（如实申报）

- 元话术 8 形态/R3 崩坏段/复沓零误伤：确定性测试已锁
- 身份吞并的铁律与条款：注入已锚定；实际拦截效果须封测(R7)真实回合验证——验收器是 LLM 行为，无法离线测
- 开关矩阵：RPG_META_MENU_GATE / RPG_DEGENERATION_GATE 默认开；关闭回退纯观测/仅审计
