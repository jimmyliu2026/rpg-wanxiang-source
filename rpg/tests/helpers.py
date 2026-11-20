"""
tests/helpers.py — 集成测试公共工具

约定：
- 使用同一个真实数据库（DATABASE_URL），但用户名前缀 `integtest_` 隔离
- 每个 TestCase setUp/tearDown 清理本测试创建的用户和级联数据
- 不依赖外部 LLM，不触发 /api/chat
"""
from __future__ import annotations

import os
import random
import string
import sys
from pathlib import Path
from typing import Any

# 让测试能 import 顶层模块
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) != sys.path[0]:
    sys.path.insert(0, str(REPO_ROOT))

# 强制服务器鉴权模式，避免 local 模式的隐式登录干扰
os.environ.setdefault("RPG_REQUIRE_AUTH", "1")


def random_suffix(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def integtest_username() -> str:
    return f"integtest_{random_suffix()}"


def make_client():
    """构造一个 FastAPI TestClient（lazy import 防止 conftest 阶段就加载 ui）"""
    from fastapi.testclient import TestClient

    import app  # noqa: F401 触发路由注册
    return TestClient(app.app)


def _clear_reg_rate_locks() -> None:
    """F4 存量清偿: 注册风暴防护的 reg:ip / reg:email Redis 锁跨用例/跨整轮
    套件残留(单 IP 10 次/10min 预算被多轮 suite 跑光)→ 清理 integtest 相关
    键与 IP 预算,保证隔离。Redis 不可用时静默(本地语义不变)。"""
    try:
        import redis_bus as _rb
        client = _rb.get_sync_client()
        if client is None:
            return
        keys = [k for k in client.scan_iter("rpg:rl:reg:ip:*")]  # rate_incr 前缀 rpg:rl:
        keys += [k for k in client.scan_iter("reg:email:integtest_*")]
        if keys:
            client.delete(*keys)
    except Exception:
        pass


def cleanup_test_users() -> int:
    """删除所有 integtest_ 前缀用户（级联清掉 sessions/saves 等）。
    返回删除条数。生产环境如果用了正经测试 DB 也安全。
    """
    _clear_reg_rate_locks()
    from platform_app.db import connect
    with connect() as db:
        row = db.execute(
            "delete from users where username like 'integtest_%' returning id"
        ).fetchall()
    return len(row)


def register_user(client, username: str | None = None, password: str = "Test12345!") -> dict[str, Any]:
    """注册并返回 (username, password, body)。"""
    uname = username or integtest_username()
    email = f"{uname}@example.test"
    code = "123456"
    from platform_app import auth as _auth

    old_generate_email_code = _auth.generate_email_code
    _auth.generate_email_code = lambda n_digits=6: code
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "username": uname,
            "password": password,
            "display_name": "integ",
            "email": email,
            "birthday": "1990-01-01",
            "terms_accepted": True,
            "age_confirmed": True,
        },
    )
    _auth.generate_email_code = old_generate_email_code
    if resp.status_code == 200:
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if body.get("pending_verify"):
            verify_resp = client.post(
                "/api/v1/auth/verify-email",
                json={"email": email, "code": code},
            )
            return {
                "username": uname,
                "password": password,
                "status": verify_resp.status_code,
                "body": verify_resp.json() if verify_resp.headers.get("content-type", "").startswith("application/json") else {},
                "cookies": dict(verify_resp.cookies),
            }
    return {
        "username": uname,
        "password": password,
        "status": resp.status_code,
        "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {},
        "cookies": dict(resp.cookies),
    }


def login_user(client, username: str, password: str = "Test12345!") -> dict[str, Any]:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    return {
        "status": resp.status_code,
        "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {},
        "cookies": dict(resp.cookies),
    }
