"""Identity file catalog: single source of truth for whitelist, grouping, constants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nanobot.memory.lifecycle import MEMORY_MD_MAX_CHARS

IDENTITY_DIR_NAME = "identity"
PERSONAS_SUBDIR = "personas"
PROMPTS_SUBDIR = "prompts"
RUNTIME_SUBDIR = "runtime"

# Which persona is activated, as a bare stem in a one-line file. The WebUI
# writes it (``identity_api.identity_set_active_persona``) and ContextBuilder
# reads it (``ContextBuilder._load_persona_section``) — one constant so the
# writer and the reader can never silently disagree.
PERSONA_STATE_FILE = "active_persona"

# 策略段落文件：内容作为 system prompt 的 Policy 段追加（见 context.py）。
# 出厂模板是空壳（只有「在下方书写…」的引导语），所以模板态一律不注入。
POLICIES_MD_NAME = f"{PROMPTS_SUBDIR}/policies.md"

# Rule-compiled injection artifacts live under identity/runtime/. Bumping this
# string invalidates every existing compiled set (see compiler.compiled_status),
# so it changes only when the compile pipeline's output shape changes.
COMPILED_SCHEMA_VERSION = "1"

# Frontend IdentityView charMax derives from here (via /api/settings/identity/files).
# Shares the same constant with MEMORY.md truncation logic.
CHAR_LIMIT = MEMORY_MD_MAX_CHARS

# Badge labelKey directly reuses frontend i18n keys; backend only passes them through.
BADGE_AUTO_REGEN = "settings.identity.badgeAutoRegen"
BADGE_SYSTEM_SECTION = "settings.identity.badgeSystemSection"


@dataclass(frozen=True)
class IdentityFileSpec:
    """Static description of a single identity file."""

    name: str
    group: str  # "core" | "personas"
    restricted: bool = False
    badge_tone: str | None = None  # "amber" | "sage" | "clay"
    badge_label_key: str | None = None
    # Relative path within identity/. Defaults to same as name.
    # Personas use bare filenames but land under personas/.
    logical_path: str = ""

    @property
    def path(self) -> str:
        return self.logical_path or self.name


CORE_FILES: tuple[IdentityFileSpec, ...] = (
    IdentityFileSpec(name="SOUL.md", group="core"),
    IdentityFileSpec(name="AGENT.md", group="core", restricted=True),
    IdentityFileSpec(
        name="AGENTS.md",
        group="core",
    ),
    IdentityFileSpec(
        name="USER.md",
        group="core",
        restricted=True,
        badge_tone="amber",
        badge_label_key=BADGE_AUTO_REGEN,
    ),
    IdentityFileSpec(
        name="MEMORY.md",
        group="core",
        restricted=True,
        badge_tone="amber",
        badge_label_key=BADGE_AUTO_REGEN,
    ),
    IdentityFileSpec(
        name=POLICIES_MD_NAME,
        group="core",
        restricted=True,
        badge_tone="amber",
        badge_label_key=BADGE_SYSTEM_SECTION,
    ),
)

# Files owned by the memory lifecycle: MEMORY.md / USER.md are derived artifacts
# regenerated from SQLite, so their manual-edit path must go through
# MemoryLifecycle's backup semantics. IdentityStore refuses them outright, so a
# caller that forgets to branch gets a loud error instead of a silent unbacked
# write. Defined once here so the store, the WebUI API layer and any future entry
# point cannot drift apart.
LIFECYCLE_OWNED_FILES: frozenset[str] = frozenset({"MEMORY.md", "USER.md"})

# Subset of the above whose on-disk home is ``memory/`` rather than ``identity/``.
# USER.md deliberately is NOT here: the injection path (``ContextBuilder``) reads
# ``identity/USER.md``, so writing it anywhere else would make WebUI, compiler and
# prompt disagree about which file is authoritative.
MEMORY_DIR_DERIVED_FILES: frozenset[str] = frozenset({"MEMORY.md"})

# Files whose truth is the workspace root, not ``identity/``. ``AGENTS.md`` is the
# workspace instruction file: the injection path (``ContextBuilder``) reads it from
# the project root and explicitly refuses to move it, so the store must not offer
# an ``identity/AGENTS.md`` home for it — the two would drift apart.
WORKSPACE_ROOT_FILES: frozenset[str] = frozenset({"AGENTS.md"})


def resolve_identity_dir(workspace: Path) -> Path:
    """Return identity directory under *workspace* (existence not guaranteed)."""
    return workspace / IDENTITY_DIR_NAME


def is_safe_persona_stem(stem: str) -> bool:
    """persona stem 是否能安全地落在 ``personas/`` 内。

    禁路径分隔符、NUL 与点号名。**不**用 ``str.isidentifier()``：它会误伤
    ``foo.bar.md`` 这类合法文件名（stem 含点），让 persona 出现在选择列表里
    却永远无法激活。

    这是纵深防御，不是唯一防线——真正的闸是随后的「必须命中磁盘上真实的
    persona」成员检查，它天然挡死穿越。
    """
    return bool(stem) and not (set(stem) & {"/", "\\", "\x00"}) and stem not in {".", ".."}


def discover_personas(workspace: Path) -> list[Path]:
    """Return all *.md files under personas/, sorted by filename.

    Returns empty list when directory does not exist — "personas not created yet"
    is a valid state, not an error.
    """
    persona_dir = resolve_identity_dir(workspace) / PERSONAS_SUBDIR
    if not persona_dir.is_dir():
        return []
    return sorted(
        (p for p in persona_dir.iterdir() if p.is_file() and p.suffix == ".md"),
        key=lambda p: p.name,
    )


def build_persona_specs(workspace: Path) -> list[IdentityFileSpec]:
    """Convert real persona files under personas/ into spec list."""
    specs: list[IdentityFileSpec] = []
    for path in discover_personas(workspace):
        specs.append(
            IdentityFileSpec(
                name=path.name,
                group="personas",
                logical_path=f"{PERSONAS_SUBDIR}/{path.name}",
            )
        )
    return specs
