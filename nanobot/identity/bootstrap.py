"""Identity directory initialization and legacy workspace migration."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from nanobot.identity.catalog import (
    IDENTITY_DIR_NAME,
    PERSONAS_SUBDIR,
    resolve_identity_dir,
)

logger = logging.getLogger(__name__)

# Files historically scattered at workspace root, now consolidated under identity/.
LEGACY_ROOT_FILES: tuple[str, ...] = ("SOUL.md", "USER.md")

# 历史出厂模板的位置（相对 nanobot/templates/）。
#
# 用途：模板改版后，把「用户一个字没改过」的旧副本升级成新出厂模板。判定依据
# 是内容逐字相等——用户改过的文件对不上，一律不碰。没有这一步的话，老
# workspace 会永远停在旧模板上，出厂行为基线的改进对他们不生效。
#
# 这些副本保留在包里而不是只留哈希：升级逻辑要能被人直接读懂，也要让测试
# 能直接拿它们当「旧出厂内容」用。
_LEGACY_FACTORY_SOURCES: dict[str, tuple[str, ...]] = {
    # logical name → 历史出厂版本所在的模板路径
    "SOUL.md": ("legacy/SOUL.md", "legacy/SOUL-cn.md"),
    "AGENT.md": ("legacy/AGENT.md",),
}

# Core identity files that live under identity/ (bundled template path == logical path
# relative to nanobot/templates/, except MEMORY.md which is lifecycle-owned).
_IDENTITY_CORE_SEEDS: tuple[tuple[str, str], ...] = (
    ("AGENT.md", "AGENT.md"),
    ("POLICIES.yaml", "POLICIES.yaml"),
    ("prompts/policies.md", "prompts/policies.md"),
)

# Factory persona presets under templates/personas/ → identity/personas/.
PERSONA_PRESET_STEMS: tuple[str, ...] = (
    "balanced",
    "mentor",
    "creative",
    "companion",
    "tech_expert",
)

# Whitelisted logical name → bundled template path (relative to nanobot/templates/).
_BUNDLED_TEMPLATES: dict[str, str] = {
    "SOUL.md": "SOUL.md",
    "USER.md": "USER.md",
    "AGENT.md": "AGENT.md",
    "POLICIES.yaml": "POLICIES.yaml",
    "prompts/policies.md": "prompts/policies.md",
    "MEMORY.md": "memory/MEMORY.md",
}


def load_identity_template(name: str) -> str | None:
    """Bundled factory template for a whitelisted identity file, if any."""
    from nanobot.utils.helpers import load_bundled_template  # local: avoid import cycle

    bundled = _BUNDLED_TEMPLATES.get(name)
    if bundled is not None:
        return load_bundled_template(bundled)
    # Personas are addressed by bare filename in the catalog.
    if "/" not in name and name.endswith(".md"):
        return load_bundled_template(f"{PERSONAS_SUBDIR}/{name}")
    return None


def ensure_identity_templates(workspace: Path) -> list[str]:
    """Seed missing identity files from bundled templates. Never overwrites.

    Covers the gap left by ``sync_workspace_templates`` (root SOUL/USER +
    memory/MEMORY.md only): AGENT.md / POLICIES.yaml / prompts/policies.md and
    the persona presets all land under ``identity/``. SOUL/USER/MEMORY are
    re-checked so this function is also correct when called standalone.

    Exception to "never overwrites": a file that still matches a **previous
    factory template** byte-for-byte is upgraded to the current one (see
    :func:`upgrade_legacy_factory_templates`). Files the user actually wrote
    are never touched.

    Returns workspace-relative paths actually created or upgraded.
    """
    from nanobot.utils.helpers import load_bundled_template  # local: avoid import cycle

    added: list[str] = []
    identity_dir = resolve_identity_dir(workspace)

    def _seed(dest: Path, bundled: str) -> None:
        if dest.exists():
            return
        content = load_bundled_template(bundled)
        if content is None:
            logger.warning("Bundled template missing: %s", bundled)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    for logical, bundled in _IDENTITY_CORE_SEEDS:
        _seed(identity_dir / logical, bundled)

    for stem in PERSONA_PRESET_STEMS:
        _seed(identity_dir / PERSONAS_SUBDIR / f"{stem}.md", f"{PERSONAS_SUBDIR}/{stem}.md")

    # Legacy root files: seed only when neither identity/ nor workspace root has them.
    for filename in LEGACY_ROOT_FILES:
        if (identity_dir / filename).exists() or (workspace / filename).exists():
            continue
        _seed(workspace / filename, filename)

    # MEMORY.md derived artifact: only when neither location has it.
    if not (identity_dir / "MEMORY.md").exists() and not (
        workspace / "memory" / "MEMORY.md"
    ).exists():
        _seed(workspace / "memory" / "MEMORY.md", "memory/MEMORY.md")

    added.extend(upgrade_legacy_factory_templates(workspace))

    if added:
        logger.info("Seeded identity templates: %s", ", ".join(added))
    return added


def upgrade_legacy_factory_templates(workspace: Path) -> list[str]:
    """Upgrade untouched copies of **previous** factory templates to the current one.

    A file qualifies only when its content is word-for-word equal to a known
    previous factory version (``nanobot/templates/legacy/``). Anything the user
    wrote differs, so it is left alone — upgrading a template must never
    overwrite a user's own words.

    Contents are compared as ``read_text`` returns them (universal newlines),
    so a CRLF checkout still matches the LF-authored legacy copy.

    Returns workspace-relative paths actually upgraded.
    """
    from nanobot.utils.helpers import load_bundled_template  # local: avoid import cycle

    identity_dir = resolve_identity_dir(workspace)
    upgraded: list[str] = []

    for logical, legacy_paths in _LEGACY_FACTORY_SOURCES.items():
        template = load_identity_template(logical)
        if template is None:
            continue
        legacy_contents = {
            content
            for content in (load_bundled_template(path) for path in legacy_paths)
            if content is not None
        }
        if not legacy_contents:
            continue
        for path in (identity_dir / logical, workspace / logical):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if content not in legacy_contents:
                continue
            path.write_text(template, encoding="utf-8")
            upgraded.append(str(path.relative_to(workspace)))
            logger.info("Upgraded untouched factory template: %s", path)

    return upgraded


def migrate_legacy_identity_files(workspace: Path) -> list[str]:
    """Move SOUL.md / USER.md from workspace root into identity/.

    Returns list of actually migrated filenames.

    Conflict policy: if a file already exists in identity/,
    **do not migrate and do not overwrite** — old file stays in place,
    with a WARNING logged. Silent overwrite would lose user edits.
    """
    identity_dir = resolve_identity_dir(workspace)
    moved: list[str] = []

    for filename in LEGACY_ROOT_FILES:
        legacy = workspace / filename
        if not legacy.is_file():
            continue
        target = identity_dir / filename
        if target.exists():
            logger.warning(
                "identity/%s already exists, skipping migration of workspace/%s; "
                "please manually confirm whether to merge",
                filename,
                filename,
            )
            continue
        identity_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(target))
        moved.append(filename)
        logger.info("Migrated %s -> %s/%s", filename, IDENTITY_DIR_NAME, filename)

    return moved
