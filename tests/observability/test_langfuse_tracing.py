from nanobot.observability import langfuse_tracing as lt


class FakeSpan:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def update(self, **kwargs: object) -> None:
        self.updates.append(dict(kwargs))


class FakeManager:
    def __init__(self) -> None:
        self.span = FakeSpan()
        self.exited: tuple[object, object, object] | None = None

    def __enter__(self) -> FakeSpan:
        return self.span

    def __exit__(self, *exc: object) -> bool:
        self.exited = exc
        return False


class FakeClient:
    def __init__(self) -> None:
        self.manager = FakeManager()
        self.started: dict[str, object] = {}
        self.trace_updates: dict[str, object] = {}

    def start_as_current_span(self, **kwargs: object) -> FakeManager:
        self.started = dict(kwargs)
        return self.manager

    def update_current_trace(self, **kwargs: object) -> None:
        self.trace_updates.update(kwargs)

    def get_current_trace_id(self) -> str:
        return "a" * 32


def test_noop_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    handle = lt.start_turn_trace(
        session_key="s",
        channel="cli",
        sender_id="u",
        turn_id="t",
        user_input="hi",
    )
    assert handle.trace_id is None
    lt.finish_turn_trace(handle, output="ok", status="succeeded")


def test_trace_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    fake = FakeClient()
    lt.set_client_factory(lambda: fake)
    try:
        handle = lt.start_turn_trace(
            session_key="sess",
            channel="websocket",
            sender_id="u1",
            turn_id="t1",
            user_input="hello",
        )
        assert handle.trace_id == "a" * 32
        assert fake.started["name"] == "zhixu.turn"
        assert fake.trace_updates["session_id"] == "sess"
        assert fake.trace_updates["user_id"] == lt.hashed_user_id("websocket", "u1")

        lt.finish_turn_trace(handle, output="answer", status="failed", error="boom")
        assert fake.manager.exited is not None
        update = fake.manager.span.updates[-1]
        assert update["level"] == "ERROR"
        assert update["output"] == {"message": "answer"}
        assert update["status_message"] == "boom"

        lt.finish_turn_trace(handle, output="again", status="succeeded")
        assert len(fake.manager.span.updates) == 1
    finally:
        lt.set_client_factory(None)


def test_turn_trace_registry() -> None:
    lt.register_turn_trace("turn-1", "b" * 32)
    assert lt.lookup_turn_trace("turn-1") == "b" * 32
    assert lt.lookup_turn_trace("missing") is None
    assert lt.lookup_turn_trace(None) is None


def test_turn_content_cache() -> None:
    lt.remember_turn_content("trace-1", "问题", "答案")
    assert lt.lookup_turn_content("trace-1") == ("问题", "答案")
    assert lt.lookup_turn_content("missing") is None
    assert lt.lookup_turn_content(None) is None


def test_client_factory_error_is_fail_open(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    def explode() -> None:
        raise RuntimeError("sdk unavailable")

    lt.set_client_factory(explode)
    try:
        handle = lt.start_turn_trace(
            session_key="s",
            channel="cli",
            sender_id="u",
            turn_id="t",
            user_input="hi",
        )
        assert handle.trace_id is None
    finally:
        lt.set_client_factory(None)
