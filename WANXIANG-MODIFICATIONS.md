# Wanxiang Modifications Notice

This repository provides the **Corresponding Source** (AGPL-3.0-or-later, section 13) of the modified `rpg-roleplay-platform` deployed at <https://wanxiang.kaiwu1866.com>.

- Upstream: <https://github.com/felixchaos/rpg-roleplay-platform> @ commit `c67f25291df3cc7748e216ccc61576021206273e`
- Modifications: the cumulative diff contained in this tree (M0 + M1 + D1 + D2 + C-stage DeepSeek candidate), identical to `rpg-wanxiang-fixes.patch` (production version, 3099 lines, SHA-256 `43b1774397cb…` prefix; full hash in the release notes of this repository).
- License: AGPL-3.0-or-later (see `LICENSE` from upstream). The modifications above are released under the same license.

## Summary of modifications

1. MiniMax reasoning control in `rpg/agents/gm/backends/openai_compat.py` (`thinking.type=disabled` / `reasoning_split=true`).
2. Worldbook scope fix in `rpg/context_engine/formatters.py` (empty scoped results no longer fall back to unrelated built-in examples).
3. Optional build mirrors in `deploy/Dockerfile` (`DEBIAN_MIRROR`, `PIP_INDEX_URL`).
4. MoneyAI managed model access: request-scoped opaque grants and service signatures for the main GM and both subagent paths; no BYOK fallback; SSRF exception limited to the configured MoneyAI origin.
5. D1 restricted account provisioning (`platform_app/api/moneyai_provision.py`): HMAC signature + Redis nonce; deterministic external identity mapping with a PostgreSQL advisory lock; only `user` accounts.
6. D1 authoritative save/script permissions: `owns_save` / `require_save` convergence; cross-user operations return 403.
7. Curator managed-grant propagation via `contextvars` copy into thread-pool paths.
8. D2 idempotent creator import, worker context, and release visibility (`platform_app/script_import/imports.py`, `platform_app/api/moneyai_release.py`): stable release URI mapping, owner-scoped advisory locks, signed replay-protected publish/delist with monotonic visibility versions, source-byte SHA-256 and content-hash manifest endpoint.
9. C-stage DeepSeek V4.1 Flash model-catalog candidate (`wanxiang-deepseek-flash` alias; no credentials, no automatic fallback).

No secrets, credentials, user data, or user-generated content are included in this tree. Model credentials and MoneyAI service secrets are injected at runtime via environment configuration and are not part of this source.

## Build

```bash
# RPG API image (as deployed)
docker build -f deploy/Dockerfile -t rpg-api:wanxiang \
  [--build-arg DEBIAN_MIRROR=…] [--build-arg PIP_INDEX_URL=…] .
```

The service is run behind the Wanxiang platform (separate, non-AGPL codebase) which calls this API over HTTP; only this modified upstream is subject to AGPL source obligations.

## Updates

This tree is regenerated whenever the production patch changes. Each release tag records the patch's full SHA-256 so any deployed version can be reproduced from the upstream commit plus the tagged tree.
