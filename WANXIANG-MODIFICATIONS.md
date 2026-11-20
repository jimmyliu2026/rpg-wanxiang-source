# Wanxiang Modifications Notice

This repository provides the **Corresponding Source** (AGPL-3.0-or-later, section 13) of the modified `rpg-roleplay-platform` deployed at <https://wanxiang.kaiwu1866.com>.

- Upstream: <https://github.com/felixchaos/rpg-roleplay-platform> @ commit `c67f25291df3cc7748e216ccc61576021206273e`
- Modifications: the cumulative diff contained in this tree, identical to `rpg-wanxiang-fixes-f5.patch` (F5 production version, 9823 lines, SHA-256 `9ca509ab4b056b7fb93aeee346d31ebc2bd8ca8eafb46d2ffc7b52683c13735b`).
- License: AGPL-3.0-or-later (see `LICENSE` from upstream). The modifications above are released under the same license.

## Summary of modifications (F5, 2026-11-20)

Prior baseline: M0 + M1 + D1 + D2 + C-stage (see git history, tag `prod-2026-11-16`).

New in this F5 update:
1. **F2 质量四项**: 元话术拦截(行动输入缓冲+一次重生成+二次拒绝 fail-closed), 退化循环流式止损+终态确定性压缩, 空回合分诊接管验证, 身份吞并专用验收条款+全局身份不变式铁律。
2. **F4 测试清偿**: 存量 60 项失败分类修复(真缺陷/陈旧锚/环境跳过)。
3. **F5 worldline-seed**: `POST /api/scripts/import` 新增可选 `worldline_seed`(结构化作者设定: 人物关系/辈分/Canon/伏笔/大纲)——scripts.worldline_seed JSONB(v105 迁移), 提取期前置 truth(独立转义作者设定块+有界词表), editor 保护类物化+author_seed provenance, 作者关系按 reveal 章合入 facts(重建后重放), continuity 验收独立证据段(evidence_sections 通道, 无 200 字截断)。
4. **回滚并发闸**: 激活时重取存档 advisory lock+回滚目标权威性校验, 过期激活 STATE_CONFLICT(409)。
5. R1-R6 全部既有能力保持不变(回合/分支/存档/主备接管/支付沙盒/分润对账)。

No secrets, credentials, user data, or user-generated content are included in this tree. Model credentials and MoneyAI service secrets are injected at runtime via environment configuration and are not part of this source.

## Build

```bash
# RPG API image (as deployed)
docker build -f deploy/Dockerfile -t rpg-api:wanxiang \
  [--build-arg DEBIAN_MIRROR=…] [--build-arg PIP_INDEX_URL=…] .
```

After migrations, the service expects `schema_migrations` at version 105 (`scripts.worldline_seed`).
