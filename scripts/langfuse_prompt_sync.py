"""Register Zhixu prompt sources in Langfuse and detect drift (read-only check).

Sources:
- nanobot/templates/agent/*.md        -> zhixu/agent/<stem>
- nanobot/templates/SOUL.md, AGENT.md, USER.md -> zhixu/<stem>
- nanobot/templates/prompts/policies.md -> zhixu/policies
- nanobot/templates/personas/*.md     -> zhixu/persona/<stem>
- built-in agent profiles (catalog)   -> zhixu/profile/<id>

Usage:
  python scripts/langfuse_prompt_sync.py            # check only (default)
  python scripts/langfuse_prompt_sync.py --sync     # create/update prompts
  python scripts/langfuse_prompt_sync.py --sync --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LABEL = os.environ.get("LANGFUSE_PROMPT_LABEL", "production")


@dataclass(slots=True)
class LocalPrompt:
    name: str
    text: str
    tags: list[str]
    config: dict[str, Any]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def collect_local_prompts(root: Path = ROOT) -> list[LocalPrompt]:
    prompts: list[LocalPrompt] = []
    templates = root / "nanobot" / "templates"

    def add(name: str, text: str | None, source: Path, tag: str) -> None:
        if not text or not text.strip():
            return
        prompts.append(
            LocalPrompt(
                name=name,
                text=text,
                tags=["zhixu", "prompt-sync", tag],
                config={"source": str(source.relative_to(root)).replace("\\", "/")},
            )
        )

    for path in sorted((templates / "agent").glob("*.md")):
        add(f"zhixu/agent/{path.stem}", _read(path), path, "agent")

    for stem in ("SOUL", "AGENT", "USER"):
        path = templates / f"{stem}.md"
        add(f"zhixu/{stem}", _read(path), path, "identity")

    policies = templates / "prompts" / "policies.md"
    add("zhixu/policies", _read(policies), policies, "policy")

    for path in sorted((templates / "personas").glob("*.md")):
        add(f"zhixu/persona/{path.stem}", _read(path), path, "persona")

    try:
        from nanobot.agents.catalog import FACTORY_PROFILES

        for profile in FACTORY_PROFILES:
            profile_id = getattr(profile, "id", None)
            prompt = getattr(profile, "prompt", None)
            if not profile_id or not prompt:
                continue
            prompts.append(
                LocalPrompt(
                    name=f"zhixu/profile/{profile_id}",
                    text=str(prompt),
                    tags=["zhixu", "prompt-sync", "profile"],
                    config={"source": "nanobot/agents/catalog.py", "profile_id": profile_id},
                )
            )
    except Exception as exc:  # pragma: no cover - import guard
        print(f"warn: cannot load agent profiles: {exc}", file=sys.stderr)

    return prompts


def digest(text: str | None) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def compare(local_text: str, remote_text: str | None) -> str:
    if remote_text is None:
        return "MISSING"
    return "OK" if digest(local_text) == digest(remote_text) else "DRIFT"


def _get_remote(client: Any, name: str) -> str | None:
    try:
        prompt = client.get_prompt(
            name,
            label=LABEL,
            type="text",
            fallback=None,
            cache_ttl_seconds=0,
        )
    except Exception:
        return None
    if prompt is None:
        return None
    value = getattr(prompt, "prompt", None)
    if isinstance(value, str):
        return value
    try:
        return str(getattr(prompt, "compile")())
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true", help="create/update prompts in Langfuse")
    parser.add_argument("--dry-run", action="store_true", help="show actions without writing")
    args = parser.parse_args()

    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        raise SystemExit("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are required")

    from langfuse import Langfuse

    client = Langfuse()
    local = collect_local_prompts()
    drift = 0
    created = 0
    for item in local:
        remote = _get_remote(client, item.name)
        status = compare(item.text, remote)
        if status != "OK":
            drift += 1
        action = ""
        if status != "OK" and args.sync and not args.dry_run:
            client.create_prompt(
                name=item.name,
                prompt=item.text,
                labels=[LABEL],
                type="text",
                tags=item.tags,
                config=item.config,
            )
            created += 1
            action = f" -> written({LABEL})"
        elif status != "OK" and args.sync and args.dry_run:
            action = f" -> would write({LABEL})"
        print(f"{status:7} {item.name}{action}")

    print(
        f"\ntotal={len(local)} drift={drift} written={created} "
        f"label={LABEL} mode={'sync' if args.sync else 'check'}"
    )
    if args.sync:
        client.flush()
        return 0
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())