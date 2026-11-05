"""Project-scoped, immutable skill catalog shared by runtime and workbench."""
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

SkillOrigin = Literal['workspace', 'global', 'plugin', 'builtin']


@dataclass(frozen=True, slots=True)
class SkillEntry:
    id: str
    name: str
    path: Path
    origin: SkillOrigin
    description: str
    availability: str
    effective: bool
    overridden_by: str | None


@dataclass(frozen=True, slots=True)
class SkillCatalog:
    entries: tuple[SkillEntry, ...]


def resolve_skill_catalog(agent_workspace: Path, project_workspace: Path | None, *, disabled_skills: set[str] | None = None, builtin_skills_dir: Path | None = None) -> SkillCatalog:
    from nanobot.agent.plugins import enabled_agent_plugin_skills
    from nanobot.agent.skills import (
        BUILTIN_SKILLS_DIR,
        SkillsLoader,
        parse_skill_metadata,
        valid_skill_metadata,
    )

    loader = SkillsLoader(agent_workspace, builtin_skills_dir=builtin_skills_dir, disabled_skills=disabled_skills)
    disabled = set(disabled_skills or ())
    for legacy, canonical in loader._skill_aliases().items():  # pyright: ignore[reportPrivateUsage]
        if legacy in disabled or canonical in disabled:
            disabled.update((legacy, canonical))
    candidates: list[tuple[str, Path, SkillOrigin]] = []

    def scan(root: Path, origin: SkillOrigin) -> None:
        try:
            resolved_root = root.expanduser().resolve(strict=True)
            for directory in root.iterdir():
                try:
                    path = (directory / 'SKILL.md').resolve(strict=True)
                    if path.is_file() and path.is_relative_to(resolved_root):
                        candidates.append((directory.name, path, origin))
                except (OSError, RuntimeError):
                    continue
        except (OSError, RuntimeError):
            return

    if project_workspace is not None:
        scan(project_workspace / 'skills', 'workspace')
    scan(agent_workspace / 'skills', 'global')
    for name, path in enabled_agent_plugin_skills(agent_workspace):
        try:
            resolved = path.resolve(strict=True)
            if resolved.is_file():
                candidates.append((name, resolved, 'plugin'))
        except (OSError, RuntimeError):
            continue
    scan(builtin_skills_dir or BUILTIN_SKILLS_DIR, 'builtin')
    seen_paths: set[str] = set()
    winners: dict[str, str] = {}
    entries: list[SkillEntry] = []
    for name, path, origin in candidates:
        key = os.path.normcase(str(path))
        if key in seen_paths:
            continue
        seen_paths.add(key)
        identity = origin + ':' + sha256(key.encode('utf-8')).hexdigest()
        try:
            meta = parse_skill_metadata(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError):
            meta = None
        requirements = loader._parse_nanobot_metadata((meta or {}).get('metadata'))  # pyright: ignore[reportPrivateUsage]
        description = (meta or {}).get('description')
        availability = ('disabled' if name in disabled else
                        'invalid' if not valid_skill_metadata(meta or {}, name) else
                        'requirements_missing' if not loader._check_requirements(requirements) else 'available')  # pyright: ignore[reportPrivateUsage]
        winner = winners.get(name)
        entries.append(SkillEntry(identity, name, path, origin, description if isinstance(description, str) else name, availability, winner is None, winner))
        if winner is None:
            winners[name] = identity
    return SkillCatalog(tuple(entries))
