"""T10 认证层：口令、会话、CSRF、Host/Origin 精确匹配与限流。

这里只测 :class:`AuthManager` 本身（不起 HTTP），因为这几条边界的失败模式是
「悄悄放行」——用真时钟和真 HTTP 反而看不清。时钟通过 ``monotonic`` 注入，
冷却与过期都能在常数时间内验证。
"""

from __future__ import annotations

import pytest

from nanobot.evaluation.auth import (
    LOGIN_FAILURE_LIMIT,
    SESSION_COOKIE,
    SESSION_TTL_S,
    WRITE_RATE_LIMIT,
    WRITE_RATE_WINDOW_S,
    AuthConfig,
    AuthError,
    AuthManager,
    Session,
)

PASSPHRASE = "correct-horse-battery-staple"


class FakeClock:
    """单调时钟替身：测试里推进时间，不真的等 60 秒。"""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _manager(clock: FakeClock | None = None) -> AuthManager:
    return AuthManager(AuthConfig(passphrase=PASSPHRASE), monotonic=clock or FakeClock())


def _code(exc: BaseException) -> str:
    assert isinstance(exc, AuthError)
    return exc.code


# ---------------------------------------------------------------------- 配置


def test_empty_passphrase_config_is_rejected() -> None:
    with pytest.raises(ValueError, match="PASSPHRASE_REQUIRED"):
        AuthConfig(passphrase="")


@pytest.mark.parametrize(
    "origin",
    ["https://127.0.0.1:5173", "http://evil.example:5173", "http://[::]", "*"],
)
def test_dev_origin_must_be_explicit_loopback_http(origin: str) -> None:
    """dev-origin 只认显式 loopback：通配或远端等于把 CSRF 防线直接拆掉。"""
    with pytest.raises(ValueError, match="DEV_ORIGIN_NOT_LOOPBACK"):
        AuthConfig(passphrase=PASSPHRASE, dev_origin=origin)


def test_dev_origin_accepts_loopback() -> None:
    config = AuthConfig(passphrase=PASSPHRASE, dev_origin="http://localhost:5173")
    manager = AuthManager(config)
    manager.check_origin("http://localhost:5173")


# ------------------------------------------------------------------ 口令与会话


def test_wrong_passphrase_raises_auth_failed_and_issues_no_session() -> None:
    manager = _manager()
    with pytest.raises(AuthError) as exc:
        manager.verify_passphrase("wrong")
    assert _code(exc.value) == "AUTH_FAILED"
    # 前缀正确与整体错误必须是同一个码：否则错误响应成了口令探测面。
    with pytest.raises(AuthError) as prefix_exc:
        manager.verify_passphrase(PASSPHRASE[:-1])
    assert _code(prefix_exc.value) == "AUTH_FAILED"


def test_login_issues_distinct_session_and_csrf_tokens() -> None:
    manager = _manager()
    first = manager.verify_passphrase(PASSPHRASE)
    second = manager.verify_passphrase(PASSPHRASE)
    assert first.session_id != second.session_id
    assert first.csrf_token != second.csrf_token
    assert len(first.csrf_token) >= 32
    assert first.user == "local-user"


def test_success_clears_previous_failures() -> None:
    clock = FakeClock()
    manager = _manager(clock)
    for _ in range(LOGIN_FAILURE_LIMIT - 1):
        with pytest.raises(AuthError):
            manager.verify_passphrase("wrong")
    manager.verify_passphrase(PASSPHRASE)
    # 成功一次就清零：否则攻击者可以用「先失败 4 次再成功」把用户锁在外面。
    for _ in range(LOGIN_FAILURE_LIMIT - 1):
        with pytest.raises(AuthError):
            manager.verify_passphrase("wrong")
    with pytest.raises(AuthError) as exc:
        manager.verify_passphrase("wrong")
    assert _code(exc.value) == "AUTH_FAILED"


def test_login_locks_out_after_repeated_failures() -> None:
    clock = FakeClock()
    manager = _manager(clock)
    for _ in range(LOGIN_FAILURE_LIMIT):
        with pytest.raises(AuthError) as exc:
            manager.verify_passphrase("wrong")
        assert _code(exc.value) == "AUTH_FAILED"
    # 达上限后连正确口令也拒绝：限流要挡的是脚本，不是打字慢的人。
    with pytest.raises(AuthError) as locked:
        manager.verify_passphrase(PASSPHRASE)
    assert _code(locked.value) == "RATE_LIMITED"


def test_login_cooldown_expires() -> None:
    clock = FakeClock()
    manager = _manager(clock)
    for _ in range(LOGIN_FAILURE_LIMIT):
        with pytest.raises(AuthError):
            manager.verify_passphrase("wrong")
    clock.advance(61.0)
    manager.verify_passphrase(PASSPHRASE)


def test_session_lookup_rejects_missing_unknown_and_expired() -> None:
    clock = FakeClock()
    manager = _manager(clock)
    session = manager.verify_passphrase(PASSPHRASE)
    with pytest.raises(AuthError) as missing:
        manager.session_for(None)
    assert _code(missing.value) == "AUTH_REQUIRED"
    with pytest.raises(AuthError):
        manager.session_for("no-such-session")
    clock.advance(SESSION_TTL_S + 1.0)
    with pytest.raises(AuthError) as expired:
        manager.session_for(session.session_id)
    assert _code(expired.value) == "AUTH_REQUIRED"
    # 过期会话必须从内存里删掉，不能只靠返回值。
    assert session.session_id not in manager._sessions  # noqa: SLF001


def test_logout_invalidates_the_session() -> None:
    manager = _manager()
    session = manager.verify_passphrase(PASSPHRASE)
    manager.logout(session.session_id)
    with pytest.raises(AuthError):
        manager.session_for(session.session_id)
    manager.logout(None)


# ---------------------------------------------------------------------- CSRF


def test_csrf_requires_matching_token() -> None:
    manager = _manager()
    session = manager.verify_passphrase(PASSPHRASE)
    manager.check_csrf(session, session.csrf_token)
    for bad in (None, "", "x" * 32, session.csrf_token + "0"):
        with pytest.raises(AuthError) as exc:
            manager.check_csrf(session, bad)
        assert _code(exc.value) == "CSRF_INVALID"


# -------------------------------------------------------------- Host 与 Origin


def test_host_must_match_allow_list_exactly() -> None:
    manager = _manager()
    manager.check_host("127.0.0.1:8765")
    manager.check_host("localhost")
    for bad in (None, "", "evil.example", "127.0.0.1.evil.example", "127.0.0.2"):
        with pytest.raises(AuthError) as exc:
            manager.check_host(bad)
        assert _code(exc.value) == "HOST_NOT_ALLOWED"


def test_missing_origin_passes_but_any_unlisted_origin_is_rejected() -> None:
    """同源读取不带 Origin；一旦带了就必须精确命中允许表。"""
    manager = AuthManager(
        AuthConfig(
            passphrase=PASSPHRASE,
            allowed_origins=frozenset({"http://127.0.0.1:5173"}),
        )
    )
    manager.check_origin(None)
    manager.check_origin("")
    manager.check_origin("http://127.0.0.1:5173")
    for bad in ("http://127.0.0.1:5174", "http://evil.example", "null"):
        with pytest.raises(AuthError) as exc:
            manager.check_origin(bad)
        assert _code(exc.value) == "ORIGIN_NOT_ALLOWED"


# -------------------------------------------------------------------- 限流


def test_write_rate_limit_is_per_endpoint_and_resets_with_window() -> None:
    clock = FakeClock()
    manager = _manager(clock)
    for _ in range(WRITE_RATE_LIMIT):
        manager.check_write_rate("local-user", "/api/eval/v1/runs")
    with pytest.raises(AuthError) as exc:
        manager.check_write_rate("local-user", "/api/eval/v1/runs")
    assert _code(exc.value) == "RATE_LIMITED"
    # 别的端点不受这次超限影响：限流的键是「账户 + 端点」。
    manager.check_write_rate("local-user", "/api/eval/v1/suites")
    clock.advance(WRITE_RATE_WINDOW_S + 1.0)
    manager.check_write_rate("local-user", "/api/eval/v1/runs")


def test_write_rate_limit_is_per_user() -> None:
    manager = _manager()
    for _ in range(WRITE_RATE_LIMIT):
        manager.check_write_rate("alice", "/api/eval/v1/runs")
    manager.check_write_rate("bob", "/api/eval/v1/runs")


# ------------------------------------------------------------------- cookie


def test_session_cookie_is_httponly_strict_and_scoped_to_api() -> None:
    manager = _manager()
    session = manager.verify_passphrase(PASSPHRASE)
    header = manager.set_cookie(session)["Set-Cookie"]
    assert header.startswith(f"{SESSION_COOKIE}={session.session_id}")
    assert "HttpOnly" in header and "SameSite=Strict" in header
    assert "Path=/api" in header
    assert "Max-Age=0" not in header


def test_clear_cookie_expires_immediately() -> None:
    header = _manager().clear_cookie()["Set-Cookie"]
    assert header.startswith(f"{SESSION_COOKIE}=;")
    assert "Max-Age=0" in header and "HttpOnly" in header


def test_session_exposes_expiry_for_cookie_max_age() -> None:
    clock = FakeClock()
    manager = _manager(clock)
    session: Session = manager.verify_passphrase(PASSPHRASE)
    clock.advance(60.0)
    max_age = int(manager.set_cookie(session)["Set-Cookie"].split("Max-Age=")[1])
    assert 0 < max_age <= SESSION_TTL_S
