import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "langfuse_prompt_sync",
    ROOT / "scripts" / "langfuse_prompt_sync.py",
)
assert SPEC and SPEC.loader
prompt_sync = importlib.util.module_from_spec(SPEC)
sys.modules["langfuse_prompt_sync"] = prompt_sync
SPEC.loader.exec_module(prompt_sync)


def test_collects_named_prompt_sources() -> None:
    prompts = prompt_sync.collect_local_prompts(ROOT)
    names = {prompt.name for prompt in prompts}

    assert "zhixu/agent/identity" in names
    assert "zhixu/agent/tool_contract" in names
    assert "zhixu/SOUL" in names
    assert "zhixu/policies" in names
    assert "zhixu/persona/balanced" in names
    assert "zhixu/profile/commander" in names
    assert all(prompt.text.strip() for prompt in prompts)


def test_compare_detects_drift() -> None:
    assert prompt_sync.compare("same", "same\n") == "OK"
    assert prompt_sync.compare("same", None) == "MISSING"
    assert prompt_sync.compare("same", "different") == "DRIFT"