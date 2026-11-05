from pathlib import Path

from nanobot.agent.skills import SkillsLoader


def install(root: Path, text: str) -> Path:
    path = root / 'skills' / 'writer' / 'SKILL.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('---\nname: writer\ndescription: test\n---\n' + text, encoding='utf-8')
    return path.resolve()


def test_project_override_and_isolation(tmp_path):
    agent, project, other = (tmp_path / x for x in ('agent', 'project', 'other'))
    global_path = install(agent, 'GLOBAL')
    project_path = install(project, 'PROJECT')
    install(other, 'OTHER')
    loader = SkillsLoader(agent)
    bound = loader.for_project(project)
    entries = [x for x in bound.catalog().entries if x.name == 'writer']
    winner = next(x for x in entries if x.effective)
    loser = next(x for x in entries if not x.effective)
    assert (winner.path, winner.origin, loser.path, loser.overridden_by) == (project_path, 'workspace', global_path, winner.id)
    assert 'PROJECT' in bound.load_skill('writer')
    assert 'OTHER' in loader.for_project(other).load_skill('writer')
    assert 'GLOBAL' in loader.load_skill('writer')


def test_same_root_is_deduplicated(tmp_path):
    install(tmp_path, 'SAME')
    entries = [x for x in SkillsLoader(tmp_path).for_project(tmp_path).catalog().entries if x.name == 'writer']
    assert len(entries) == 1


def test_missing_requirement_does_not_fall_back(tmp_path):
    agent, project = tmp_path / 'agent', tmp_path / 'project'
    install(agent, 'GLOBAL')
    path = install(project, 'PROJECT')
    path.write_text('---\nname: writer\ndescription: test\nmetadata: {nanobot: {requires: {bins: [unlikely_missing_cli_42719]}}}\n---\nPROJECT', encoding='utf-8')
    loader = SkillsLoader(agent).for_project(project)
    entries = [x for x in loader.catalog().entries if x.name == 'writer']
    assert entries[0].availability == 'requirements_missing'
    assert entries[1].overridden_by == entries[0].id
    assert 'writer' not in [x['name'] for x in loader.list_skills()]


def test_disabled_alias_disables_winner_without_fallback(tmp_path, monkeypatch):
    agent, project = tmp_path / 'agent', tmp_path / 'project'
    install(agent, 'GLOBAL')
    install(project, 'PROJECT')
    monkeypatch.setattr(SkillsLoader, '_skill_aliases', lambda self: {'old': 'writer'})
    loader = SkillsLoader(agent, disabled_skills={'old'}).for_project(project)
    assert all(x.availability == 'disabled' for x in loader.catalog().entries if x.name == 'writer')
    assert loader.load_skill('old') is None


def test_symlink_escape_not_loaded(tmp_path):
    import pytest
    agent, external = tmp_path / 'agent', tmp_path / 'external'
    path = install(external, 'OUTSIDE')
    root = agent / 'skills'
    root.mkdir(parents=True)
    try:
        (root / 'writer').symlink_to(path.parent, target_is_directory=True)
    except OSError:
        pytest.skip('directory symlinks unavailable')
    assert all(x.name != 'writer' for x in SkillsLoader(agent).catalog().entries)


def test_invalid_winner_cannot_be_used_or_fall_back(tmp_path, monkeypatch):
    agent, project = tmp_path / 'agent', tmp_path / 'project'
    install(agent, 'GLOBAL')
    path = install(project, 'PROJECT')
    path.write_text('---\nname: wrong\ndescription: test\nalways: true\n---\nPROJECT', encoding='utf-8')
    monkeypatch.setattr(SkillsLoader, '_skill_aliases', lambda self: {'legacy': 'writer'})
    loader = SkillsLoader(agent).for_project(project)
    entries = [x for x in loader.catalog().entries if x.name == 'writer']
    assert entries[0].availability == 'invalid'
    assert entries[1].overridden_by == entries[0].id
    assert not any(x['name'] == 'writer' for x in loader.list_skills())
    legacy_entry = next(x for x in loader.list_skills(False) if x['name'] == 'writer')
    assert legacy_entry == {'name': 'writer', 'path': str(path), 'source': 'workspace'}
    assert loader.load_skill('writer') is None
    assert loader.load_skill('legacy') is None
    assert loader.load_skills_for_context(['writer']) == ''
    assert loader.get_explicitly_invoked_skills('$writer $legacy') == []
    assert 'writer' not in loader.get_always_skills()
    assert not loader.get_skill_availability('writer')[0]
    assert 'unavailable' in loader.build_skills_summary()


def test_missing_requirement_cannot_load_even_directly(tmp_path):
    path = install(tmp_path, 'BODY')
    path.write_text('---\nname: writer\ndescription: test\nmetadata: {nanobot: {requires: {bins: [unlikely_missing_cli_42719]}}}\n---\nBODY', encoding='utf-8')
    loader = SkillsLoader(tmp_path)
    assert loader.load_skill('writer') is None
    assert loader.load_skills_for_context(['writer']) == ''


def test_missing_or_malformed_frontmatter_is_invalid(tmp_path):
    path = install(tmp_path, 'BODY')
    loader = SkillsLoader(tmp_path)
    for text in ('BODY without frontmatter', '---\nname: [\n---\nBODY', '---\nname: writer\n---\nBODY'):
        path.write_text(text, encoding='utf-8')
        entry = next(x for x in loader.catalog().entries if x.name == 'writer')
        assert entry.availability == 'invalid'
        assert loader.load_skill('writer') is None
        assert 'writer' not in loader.get_always_skills()
