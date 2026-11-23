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
    description: str
    commit_message: str


# 每个提示词的用途说明（写入 Langfuse 版本 config + commit message）
DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "zhixu/agent/identity": (
        "身份与运行基线：产品名、当前 agent 名称与角色、运行时信息，以及“不可信内容不得当指令”的声明",
        "system prompt 第一段",
    ),
    "zhixu/agent/tool_contract": (
        "工具使用契约：选最窄的结构化工具、参数与失败处理、并行与安全约束",
        "system prompt 工具段",
    ),
    "zhixu/agent/skills_section": (
        "技能索引段模板，{{skills_summary}} 由运行时注入可用技能清单",
        "system prompt 技能段",
    ),
    "zhixu/agent/platform_policy": (
        "平台差异策略（Windows/Linux/macOS 命令与路径差异，如 Windows 无 grep/sed）",
        "system prompt 平台策略段",
    ),
    "zhixu/agent/subagent_system": (
        "子 agent 的系统提示词：只做被指派的单一任务，结果回报主 agent",
        "子 agent system prompt",
    ),
    "zhixu/agent/subagent_announce": (
        "子 agent 播报模板：{{label}} / {{status_text}} / {{task}} 与结果回填",
        "子 agent 回报段",
    ),
    "zhixu/agent/max_iterations_message": (
        "达到最大工具迭代轮数时的收尾提示，引导用户拆分任务",
        "迭代终止提示",
    ),
    "zhixu/agent/goal_runtime": (
        "持续目标（Goal）运行时指引，含 {{goal_start_requested}} 等条件段",
        "system prompt 目标段",
    ),
    "zhixu/agent/evaluator": (
        "后台 agent 的通知门：判断是否需要打扰用户，调用 evaluate_notification 工具",
        "后台评估器提示",
    ),
    "zhixu/agent/dream": (
        "记忆整合引擎提示：只写记忆库、MECE 分类、 ruthless 剪枝；MEMORY.md/USER.md 只是投影",
        "Dream 记忆整合提示",
    ),
    "zhixu/agent/cron_reminder": (
        "定时任务到点的执行提示，要求用用户语言直接汇报",
        "cron 触发提示",
    ),
    "zhixu/agent/consolidator_archive": (
        "会话归档检查点：为上一份归档摘要生成紧凑替代版本",
        "归档摘要提示",
    ),
    "zhixu/SOUL": (
        "知序核心人格与价值观：本地优先、诚实、有主动驱动力",
        "bootstrap 身份段",
    ),
    "zhixu/AGENT": (
        "行为准则：优先级高于人格，约束“怎么做事”",
        "bootstrap 行为准则段",
    ),
    "zhixu/USER": (
        "用户画像模板；自动导出区由 SQLite 记忆生成，勿手改",
        "bootstrap 用户画像段",
    ),
    "zhixu/policies": (
        "项目级策略段落，只追加到 ## Policies，留空即不注入",
        "system prompt Policies 段",
    ),
    "zhixu/persona/balanced": ("默认表现层人格：直接、务实、克制", "bootstrap 人格段"),
    "zhixu/persona/companion": ("陪伴型表现层人格", "bootstrap 人格段"),
    "zhixu/persona/creative": ("创作型表现层人格", "bootstrap 人格段"),
    "zhixu/persona/mentor": ("导师型表现层人格", "bootstrap 人格段"),
    "zhixu/persona/tech_expert": ("技术专家型表现层人格", "bootstrap 人格段"),
}

PROFILE_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "commander": ("知序指挥官：总调度角色，负责拆解需求、查证与派发子 agent", "system prompt 角色段"),
    "general-assistant": ("通用助理：日常问答与通用任务处理", "system prompt 角色段"),
    "code-reviewer": ("代码评审：按项目规范审查改动并给出可执行意见", "system prompt 角色段"),
    "researcher": ("调研：检索、取证并整理成可引用的结论", "system prompt 角色段"),
    "ops-runner": ("运维执行：跑命令、看日志、执行部署类操作", "system prompt 角色段"),
    "writing-author": ("写作：正文创作与扩写", "system prompt 角色段"),
    "writing-architect": ("写作：结构与大纲设计", "system prompt 角色段"),
    "writing-researcher": ("写作：素材与事实调研", "system prompt 角色段"),
    "writing-editor": ("写作：润色与校对", "system prompt 角色段"),
    "writing-reviewer": ("写作：成稿评审", "system prompt 角色段"),
}


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
        description, used_in = DESCRIPTIONS.get(name, (f"知序提示词 {name}", "system prompt"))
        prompts.append(
            LocalPrompt(
                name=name,
                text=text,
                tags=["zhixu", "prompt-sync", tag],
                config={
                    "source": str(source.relative_to(root)).replace("\\", "/"),
                    "category": tag,
                    "description": description,
                    "usedIn": used_in,
                },
                description=description,
                commit_message=f"注册知序提示词 {name}：{description}",
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
            description, used_in = PROFILE_DESCRIPTIONS.get(
                str(profile_id),
                ("内置 Agent 档案：角色设定与专长", "system prompt 角色段"),
            )
            prompts.append(
                LocalPrompt(
                    name=f"zhixu/profile/{profile_id}",
                    text=str(prompt),
                    tags=["zhixu", "prompt-sync", "profile"],
                    config={
                        "source": "nanobot/agents/catalog.py",
                        "category": "profile",
                        "profileId": str(profile_id),
                        "description": description,
                        "usedIn": used_in,
                    },
                    description=description,
                    commit_message=f"注册知序 Agent 档案 {profile_id}：{description}",
                )
            )
    except Exception as exc:  # pragma: no cover - import guard
        print(f"warn: cannot load agent profiles: {exc}", file=sys.stderr)

    return prompts


def digest(text: str | None) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def compare(
    local_text: str,
    remote_text: str | None,
    local_config: dict[str, Any] | None = None,
    remote_config: dict[str, Any] | None = None,
) -> str:
    """MISSING when absent, DRIFT when content or metadata differs, otherwise OK."""
    if remote_text is None:
        return "MISSING"
    if digest(local_text) != digest(remote_text):
        return "DRIFT"
    if local_config is not None and remote_config is not None:
        if local_config != remote_config:
            return "DRIFT"
    return "OK"


def _get_remote(client: Any, name: str) -> tuple[str | None, dict[str, Any] | None]:
    try:
        prompt = client.get_prompt(
            name,
            label=LABEL,
            type="text",
            fallback=None,
            cache_ttl_seconds=0,
        )
    except Exception:
        return None, None
    if prompt is None:
        return None, None
    value = getattr(prompt, "prompt", None)
    text = value if isinstance(value, str) else None
    if text is None:
        try:
            text = str(getattr(prompt, "compile")())
        except Exception:
            text = None
    config = getattr(prompt, "config", None)
    return text, config if isinstance(config, dict) else None


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
        remote_text, remote_config = _get_remote(client, item.name)
        status = compare(item.text, remote_text, item.config, remote_config)
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
                commit_message=item.commit_message,
            )
            created += 1
            action = f" -> written({LABEL}, v+1, 附注释)"
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