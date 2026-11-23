import time

from nanobot.observability import langfuse_prompts as lp


class FakePrompt:
    def __init__(self, text: str = "remote template", version: int = 7) -> None:
        self.prompt = text
        self.version = version


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.delenv("LANGFUSE_PROMPTS_ENABLED", raising=False)
    monkeypatch.setattr(lp, "_ensure_refresher", lambda: None)


def _reset() -> None:
    with lp._cache_lock:
        lp._cache.clear()
        lp._wanted.clear()
    lp.reset_turn_state()


def test_remote_name_mapping() -> None:
    assert lp.remote_name_for("agent/identity.md") == "zhixu/agent/identity"
    assert lp.remote_name_for("agent/subagent_system.md") == "zhixu/agent/subagent_system"
    assert lp.remote_name_for("profile/commander") == "zhixu/profile/commander"
    assert lp.remote_name_for("agent/_snippets/untrusted_content.md") is None
    assert lp.remote_name_for("unknown/file.md") is None
    # Identity files, personas and policies are user-owned workspace content.
    assert lp.remote_name_for("personas/balanced.md") is None
    assert lp.remote_name_for("prompts/policies.md") is None
    assert lp.remote_name_for("SOUL.md") is None
    assert lp.remote_name_for("AGENT.md") is None
    assert lp.remote_name_for("USER.md") is None


def test_disabled_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    assert lp.enabled() is False
    assert lp.get_prompt("agent/identity.md") is None
    assert lp.prompt_text("agent/identity.md") is None


def test_disabled_by_flag(monkeypatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PROMPTS_ENABLED", "0")

    assert lp.enabled() is False
    assert lp.get_prompt("agent/identity.md") is None


def test_get_prompt_serves_cache_without_network(monkeypatch) -> None:
    _enable(monkeypatch)
    _reset()

    def boom(name: str) -> FakePrompt:
        raise AssertionError("render path must not fetch synchronously")

    monkeypatch.setattr(lp, "_fetch", boom)
    with lp._cache_lock:
        lp._cache["zhixu/agent/identity"] = (time.monotonic(), FakePrompt())

    assert lp.prompt_text("agent/identity.md") == "remote template"
    assert lp.prompt_version("agent/identity.md") == 7
    assert lp.prompt_snapshot("agent/identity.md") is not None


def test_refresh_respects_ttl(monkeypatch) -> None:
    _enable(monkeypatch)
    _reset()
    calls: list[str] = []

    def fake_fetch(name: str) -> FakePrompt:
        calls.append(name)
        return FakePrompt()

    monkeypatch.setattr(lp, "_fetch", fake_fetch)
    with lp._cache_lock:
        lp._wanted.add("zhixu/agent/identity")

    lp._refresh_once()
    lp._refresh_once()

    assert calls == ["zhixu/agent/identity"]
    assert lp.prompt_version("agent/identity.md") == 7


def test_refresh_refetches_after_ttl_expiry(monkeypatch) -> None:
    _enable(monkeypatch)
    _reset()
    monkeypatch.setenv("LANGFUSE_PROMPTS_TTL_S", "0")
    calls: list[str] = []

    monkeypatch.setattr(lp, "_fetch", lambda name: calls.append(name) or FakePrompt())
    with lp._cache_lock:
        lp._wanted.add("zhixu/agent/identity")

    lp._refresh_once()
    lp._refresh_once()

    assert calls == ["zhixu/agent/identity", "zhixu/agent/identity"]


def test_prompt_snapshot_ignores_blank_prompt(monkeypatch) -> None:
    _enable(monkeypatch)
    _reset()
    with lp._cache_lock:
        lp._cache["zhixu/agent/identity"] = (time.monotonic(), FakePrompt(text="  "))

    assert lp.prompt_snapshot("agent/identity.md") is None


def test_used_prompts_reset_per_turn() -> None:
    lp.reset_turn_state()
    lp.record_used("agent/identity.md", 3)
    lp.record_used("profile/commander", 2)
    assert lp.used_prompts() == {
        "zhixu/agent/identity": 3,
        "zhixu/profile/commander": 2,
    }

    lp.reset_turn_state()
    assert lp.used_prompts() == {}
    assert lp.linked_prompt() is None


def test_render_template_prefers_remote_override(monkeypatch) -> None:
    from nanobot.utils import prompt_templates as pt

    lp.reset_turn_state()
    monkeypatch.setattr(
        lp, "get_prompt", lambda key: FakePrompt("remote: {{system}}", 4)
    )

    text = pt.render_template("agent/platform_policy.md", system="Windows")

    assert text.strip() == "remote: Windows"
    assert lp.used_prompts() == {"zhixu/agent/platform_policy": 4}


def test_render_template_falls_back_to_bundled_file(monkeypatch) -> None:
    from nanobot.utils import prompt_templates as pt

    lp.reset_turn_state()
    monkeypatch.setattr(lp, "get_prompt", lambda key: None)

    text = pt.render_template("agent/platform_policy.md", system="Windows")

    assert text.strip()
    assert "Windows" in text
    assert lp.used_prompts() == {}


def test_wrapper_detection_reads_patched_module(monkeypatch) -> None:
    import sys

    from nanobot.providers import openai_compat_provider as provider

    monkeypatch.delitem(sys.modules, "langfuse.openai", raising=False)
    assert provider._langfuse_wrapper_active() is False

    monkeypatch.setitem(sys.modules, "langfuse.openai", object())
    assert provider._langfuse_wrapper_active() is True


def test_generation_kwargs_carry_linked_prompt() -> None:
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider

    lp.reset_turn_state()
    provider = OpenAICompatProvider(
        api_key="k",
        api_base="http://127.0.0.1:1/v1",
        default_model="mock-model",
        provider_name="custom",
    )
    prompt = FakePrompt(text="p", version=3)
    provider._langfuse_tracing = True
    lp.set_linked_prompt(prompt)
    try:
        kwargs = provider._build_kwargs(
            [{"role": "user", "content": "hi"}], None, None, 16, 0.0, None, None
        )
    finally:
        lp.reset_turn_state()

    assert kwargs["langfuse_prompt"] is prompt


def test_generation_kwargs_skip_prompt_without_wrapper() -> None:
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider

    lp.reset_turn_state()
    provider = OpenAICompatProvider(
        api_key="k",
        api_base="http://127.0.0.1:1/v1",
        default_model="mock-model",
        provider_name="custom",
    )
    provider._langfuse_tracing = False
    lp.set_linked_prompt(FakePrompt(text="p", version=3))
    try:
        kwargs = provider._build_kwargs(
            [{"role": "user", "content": "hi"}], None, None, 16, 0.0, None, None
        )
    finally:
        lp.reset_turn_state()

    assert "langfuse_prompt" not in kwargs
