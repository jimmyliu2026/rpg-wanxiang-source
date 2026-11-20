"""Request-scoped opaque MoneyAI model grants for the managed OpenAI provider."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from contextvars import ContextVar
from urllib.parse import urlparse

_grant: ContextVar[str] = ContextVar("moneyai_model_grant", default="")
_COMPLETIONS_PATH = "/v1/internal/model-access/chat/completions"
_TAKEOVER_PATH = "/v1/internal/model-access/takeover"


def set_grant(value: str | None) -> None:
    _grant.set(value or "")


def managed_base_url(api_id: str | None) -> str:
    if api_id != "moneyai":
        return ""
    return os.environ.get("RPG_MONEYAI_MODEL_BASE_URL", "").strip().rstrip("/")


def managed_headers(api_id: str | None) -> dict[str, str]:
    base_url = managed_base_url(api_id)
    if not base_url:
        return {}
    grant = _grant.get()
    if not grant:
        grant = os.environ.get("RPG_MONEYAI_IMPORT_GRANT", "").strip()
        if grant:
            # R4切片2: env 兜底是迁移期通道(人工注入长效 grant)——每次命中都告警,
            # 根治后(scheduler copy_context + 导入 turn 长 TTL)此路径应归零。
            import logging
            logging.getLogger(__name__).warning(
                "moneyai_env_grant_fallback_used: request-scoped grant missing; "
                "background job scheduler should copy_context (R4 slice-2)"
            )
    secret = os.environ.get("RPG_MONEYAI_INTERNAL_SECRET", "")
    # env grant fallback is reserved for one-off asset-import jobs that run in
    # background threads without an HTTP request context; chat turns always use
    # the request-scoped grant.
    if not grant or len(secret.encode()) < 32:
        raise RuntimeError("MoneyAI managed model call requires a turn grant and service secret")
    timestamp = int(time.time())
    nonce = uuid.uuid4().hex
    path = urlparse(base_url).path + "/chat/completions"
    if path != _COMPLETIONS_PATH:
        raise RuntimeError("RPG_MONEYAI_MODEL_BASE_URL has an unexpected path")
    canonical = "\n".join(
        (
            str(timestamp),
            nonce,
            "POST",
            path,
            hashlib.sha256(b"").hexdigest(),
        )
    )
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "Authorization": f"Bearer {grant}",
        "X-Wanxiang-Service": "rpg",
        "X-Wanxiang-Timestamp": str(timestamp),
        "X-Wanxiang-Nonce": nonce,
        "X-Wanxiang-Signature": signature,
    }


def takeover_grant(model: str, price_version: str) -> dict:
    """R5 切片4b: 向平台请求主备接管——换绑备用模型并换取新独立 grant。

    用当前(旧)grant 认证;成功后 ContextVar 已更新为新 token,后续
    chat/completions 直接以备用模型身份调用。失败抛 RuntimeError(带平台错误体)。
    """
    base_url = managed_base_url("moneyai")
    grant = _grant.get() or os.environ.get("RPG_MONEYAI_IMPORT_GRANT", "").strip()
    secret = os.environ.get("RPG_MONEYAI_INTERNAL_SECRET", "")
    if not base_url or not grant or len(secret.encode()) < 32:
        raise RuntimeError("takeover requires managed channel, grant and service secret")
    timestamp = int(time.time())
    nonce = uuid.uuid4().hex
    canonical = "\n".join(
        (str(timestamp), nonce, "POST", _TAKEOVER_PATH, hashlib.sha256(b"").hexdigest())
    )
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    import urllib.request

    req = urllib.request.Request(
        base_url + "/takeover",
        data=json.dumps({"model": model, "price_version": price_version}).encode(),
        headers={
            "Authorization": f"Bearer {grant}",
            "Content-Type": "application/json",
            "X-Wanxiang-Service": "rpg",
            "X-Wanxiang-Timestamp": str(timestamp),
            "X-Wanxiang-Nonce": nonce,
            "X-Wanxiang-Signature": signature,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"takeover rejected: {exc.code} {exc.read()[:200].decode(errors='replace')}")
    token = (body.get("data") or body).get("model_token")
    if not token:
        raise RuntimeError(f"takeover response missing model_token: {str(body)[:200]}")
    _grant.set(token)
    return body.get("data") or body
