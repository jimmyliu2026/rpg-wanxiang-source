"""Request-scoped opaque MoneyAI model grants for the managed OpenAI provider."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from contextvars import ContextVar
from urllib.parse import urlparse

_grant: ContextVar[str] = ContextVar("moneyai_model_grant", default="")
_COMPLETIONS_PATH = "/v1/internal/model-access/chat/completions"


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
    grant = _grant.get() or os.environ.get("RPG_MONEYAI_IMPORT_GRANT", "").strip()
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
