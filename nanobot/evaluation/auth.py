"""T10 认证与请求准入：本地口令、会话、CSRF、Origin/Host 与限流。

首版是**单机本地服务**（spec：serve 默认 127.0.0.1），但仍按多用户站点的标准做：

- 口令只做常量时间比较，错误响应不回显「哪一部分错了」。
- 会话 cookie 是 HttpOnly + SameSite=Strict；CSRF 令牌与会话绑定。
- Origin / Host **精确匹配**允许表，默认不放行任意 Origin。
- ``--dev-origin`` 只接受显式 loopback Origin，不接受通配。
- 限流按本地账户 + 端点计数，触发后一律 429。

会话在内存里：服务重启即失效，这与「本地口令 + 单实例锁」的口径一致。
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

SESSION_COOKIE = "nanobot_eval_session"
SESSION_TTL_S = 12 * 3600
CSRF_TTL_S = SESSION_TTL_S

#: 登录失败限流：同一账户累计失败达到该次数后进入冷却。
LOGIN_FAILURE_LIMIT = 5
LOGIN_COOLDOWN_S = 60.0
#: 写操作限流：滑动窗口内允许的次数。
WRITE_RATE_LIMIT = 120
WRITE_RATE_WINDOW_S = 60.0

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class AuthError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Session:
    session_id: str
    csrf_token: str
    created_at: float
    expires_at: float
    user: str = "local-user"


@dataclass
class AuthConfig:
    passphrase: str
    allowed_hosts: frozenset[str] = frozenset({"127.0.0.1", "localhost"})
    allowed_origins: frozenset[str] = frozenset()
    dev_origin: str | None = None

    def __post_init__(self) -> None:
        if not self.passphrase:
            raise ValueError("PASSPHRASE_REQUIRED")
        if self.dev_origin is not None:
            origin = urlsplit(self.dev_origin)
            # 只接受显式 loopback Origin：通配或远端会把 CSRF 防线直接拆掉。
            if origin.scheme != "http" or origin.hostname not in LOOPBACK_HOSTS:
                raise ValueError(f"DEV_ORIGIN_NOT_LOOPBACK: {self.dev_origin}")


class AuthManager:
    """会话、CSRF 与限流。线程无关（asyncio 单循环使用）。"""

    def __init__(self, config: AuthConfig, *, monotonic: Any = time.monotonic) -> None:
        self._config = config
        self._now = monotonic
        self._sessions: dict[str, Session] = {}
        self._login_failures: dict[str, list[float]] = {}
        self._write_calls: dict[tuple[str, str], list[float]] = {}

    # ------------------------------------------------------------------ 口令

    def verify_passphrase(self, candidate: str, *, user: str = "local-user") -> Session:
        now = self._now()
        self._reject_if_cooling(user)
        # 常量时间比较：让「前缀正确」和「整体正确」耗时不可区分。
        if not hmac.compare_digest(
            str(candidate).encode("utf-8"), self._config.passphrase.encode("utf-8")
        ):
            self._login_failures.setdefault(user, []).append(now)
            raise AuthError("AUTH_FAILED")
        self._login_failures.pop(user, None)
        return self._issue(user)

    def _reject_if_cooling(self, user: str) -> None:
        now = self._now()
        recent = [
            t for t in self._login_failures.get(user, []) if now - t < LOGIN_COOLDOWN_S
        ]
        if not recent:
            self._login_failures.pop(user, None)
            return
        self._login_failures[user] = recent
        if len(recent) >= LOGIN_FAILURE_LIMIT:
            raise AuthError("RATE_LIMITED")

    def _issue(self, user: str) -> Session:
        now = self._now()
        session = Session(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + SESSION_TTL_S,
            user=user,
        )
        self._sessions[session.session_id] = session
        return session

    # ------------------------------------------------------------------ 会话

    def session_for(self, session_id: str | None) -> Session:
        if not session_id:
            raise AuthError("AUTH_REQUIRED")
        session = self._sessions.get(session_id)
        if session is None or self._now() >= session.expires_at:
            self._sessions.pop(session_id, None)
            raise AuthError("AUTH_REQUIRED")
        return session

    def logout(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def check_csrf(self, session: Session, token: str | None) -> None:
        if not token or not hmac.compare_digest(token, session.csrf_token):
            raise AuthError("CSRF_INVALID")

    # ------------------------------------------------------- Host 与 Origin

    def check_host(self, host: str | None) -> None:
        """Host 精确匹配允许表；缺失 Host 一律拒绝（防 DNS rebinding）。"""
        hostname = urlsplit(f"//{host or ''}").hostname or ""
        if hostname not in self._config.allowed_hosts:
            raise AuthError("HOST_NOT_ALLOWED")

    def check_origin(self, origin: str | None) -> None:
        """带了 Origin 就必须精确命中允许表；**不放行任意来源**。

        不带 Origin 的请求（同源 GET/HEAD 在浏览器里就不带）交给已严格匹配的
        Host 判定——把「没带 Origin」当成拒绝会把正常的同源读取也一起挡掉。
        """
        if not origin:
            return
        allowed = set(self._config.allowed_origins)
        if self._config.dev_origin:
            allowed.add(self._config.dev_origin)
        if origin not in allowed:
            raise AuthError("ORIGIN_NOT_ALLOWED")

    # ------------------------------------------------------------------ 限流

    def check_write_rate(self, user: str, endpoint: str) -> None:
        now = self._now()
        key = (user, endpoint)
        calls = [t for t in self._write_calls.get(key, []) if now - t < WRITE_RATE_WINDOW_S]
        if len(calls) >= WRITE_RATE_LIMIT:
            self._write_calls[key] = calls
            raise AuthError("RATE_LIMITED")
        calls.append(now)
        self._write_calls[key] = calls

    # ------------------------------------------------------------------ 辅助

    def set_cookie(self, session: Session) -> dict[str, str]:
        """HttpOnly + SameSite=Strict：不写 Secure 是因为首版只跑 loopback HTTP。"""
        max_age = int(max(0, session.expires_at - self._now()))
        return {
            "Set-Cookie": (
                f"{SESSION_COOKIE}={session.session_id}; Path=/api; HttpOnly; "
                f"SameSite=Strict; Max-Age={max_age}"
            )
        }

    def clear_cookie(self) -> dict[str, str]:
        return {
            "Set-Cookie": (
                f"{SESSION_COOKIE}=; Path=/api; HttpOnly; SameSite=Strict; Max-Age=0"
            )
        }

    @property
    def cookie_name(self) -> str:
        return SESSION_COOKIE
