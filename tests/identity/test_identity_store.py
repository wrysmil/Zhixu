"""IdentityStore：读写往返、校验、路径穿越防护。"""

from pathlib import Path

import pytest

from nanobot.identity.catalog import CHAR_LIMIT, IDENTITY_DIR_NAME, IdentityFileSpec
from nanobot.identity.store import IdentityStore, IdentityStoreError

# 各种形态的越界输入：相对穿越、Windows 分隔符、POSIX/Windows 绝对路径、
# 混在合法前缀后面的穿越、被编码的穿越片段、截断名。
TRAVERSAL_NAMES = [
    "../../etc/passwd",
    "..\\..\\windows\\system32\\cmd.exe",
    "/etc/passwd",
    "C:\\Windows\\System32\\drivers\\etc\\hosts",
    "prompts/../../x.md",
    "prompts/../SOUL.md",
    "personas/../SOUL.md",
    "....//....//etc/passwd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "SOUL.md\x00",
    "",
]


@pytest.fixture()
def store(tmp_path: Path) -> IdentityStore:
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    (identity_dir / "prompts").mkdir(parents=True)
    (identity_dir / "personas").mkdir()
    (identity_dir / "SOUL.md").write_text("# soul", encoding="utf-8")
    (identity_dir / "prompts" / "policies.md").write_text("# policies", encoding="utf-8")
    (identity_dir / "personas" / "default.md").write_text("# default", encoding="utf-8")
    return IdentityStore(tmp_path)


# -- 路径穿越 ---------------------------------------------------------------


@pytest.mark.parametrize("evil", TRAVERSAL_NAMES)
def test_rejects_path_traversal(store: IdentityStore, evil: str) -> None:
    with pytest.raises(IdentityStoreError):
        store.read_file(evil)
    with pytest.raises(IdentityStoreError):
        store.write_file(evil, "x")


def test_traversal_write_leaves_no_file_behind(store: IdentityStore, tmp_path: Path) -> None:
    """拒绝必须是「没写」，不能是「写完了才报错」。"""
    with pytest.raises(IdentityStoreError):
        store.write_file("../marker.md", "# pwned")

    assert not (tmp_path / "marker.md").exists()
    assert not (tmp_path.parent / "marker.md").exists()


def test_rejects_symlink_escaping_identity_dir(tmp_path: Path) -> None:
    """白名单能命中，但 resolve() 后落到 identity/ 之外——必须挡住。"""
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    identity_dir.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    try:
        (identity_dir / "SOUL.md").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")

    store = IdentityStore(tmp_path)
    with pytest.raises(IdentityStoreError):
        store.read_file("SOUL.md")


def test_resolve_blocks_escape_even_if_whitelisted(
    store: IdentityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前缀比对是独立的第二道闸。

    即便白名单里混进了一个越界的 logical_path（未来改 catalog 改错、或磁盘上
    出现指向外部的链接），也要在 resolve() 之后被前缀比对挡下。
    """
    evil = IdentityFileSpec(name="evil.md", group="core", logical_path="../escaped.md")
    monkeypatch.setattr(IdentityStore, "_specs", lambda self: [evil])

    with pytest.raises(IdentityStoreError) as exc:
        store.read_file("evil.md")
    assert exc.value.status == 400

    with pytest.raises(IdentityStoreError):
        store.write_file("evil.md", "x")


# -- 白名单 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["secret.env", ".env", "AGENTS.md", "SOUL.MD", "soul.md", "agent.md", "notes.md"],
)
def test_rejects_non_whitelisted_name(store: IdentityStore, name: str) -> None:
    with pytest.raises(IdentityStoreError) as exc:
        store.write_file(name, "x")
    assert exc.value.status == 403


def test_rejects_non_whitelisted_read(store: IdentityStore) -> None:
    with pytest.raises(IdentityStoreError) as exc:
        store.read_file("id_rsa")
    assert exc.value.status == 403


def test_accepts_whitelisted_nested_path(store: IdentityStore) -> None:
    """白名单里的带目录逻辑名要能正常解析（穿越防护不能误伤）。"""
    assert store.read_file("prompts/policies.md") == "# policies"


# -- MEMORY.md 特例 ---------------------------------------------------------


def test_write_rejects_memory_md(store: IdentityStore) -> None:
    with pytest.raises(IdentityStoreError) as exc:
        store.write_file("MEMORY.md", "# memory")

    assert "MEMORY.md" in exc.value.message
    assert not (store.identity_dir / "MEMORY.md").exists()


def test_write_rejects_memory_md_even_when_valid(store: IdentityStore) -> None:
    """就算内容完全合法也必须拒绝——MEMORY.md 只能走 MemoryLifecycle。"""
    with pytest.raises(IdentityStoreError) as exc:
        store.write_file("MEMORY.md", "# short and valid\n")
    assert "MemoryLifecycle" in exc.value.message


def test_read_allows_memory_md(store: IdentityStore) -> None:
    (store.identity_dir / "MEMORY.md").write_text("# m", encoding="utf-8")
    assert store.read_file("MEMORY.md") == "# m"


# -- 字符上限 ---------------------------------------------------------------


def test_write_rejects_over_char_limit(store: IdentityStore) -> None:
    with pytest.raises(IdentityStoreError) as exc:
        store.write_file("SOUL.md", "x" * (CHAR_LIMIT + 1))

    assert "1500" in str(exc.value)
    # 被拒的写入不能破坏原文件
    assert store.read_file("SOUL.md") == "# soul"


def test_write_accepts_exactly_char_limit(store: IdentityStore) -> None:
    boundary = "y" * CHAR_LIMIT
    store.write_file("SOUL.md", boundary)
    assert store.read_file("SOUL.md") == boundary


def test_write_rejects_non_string_content(store: IdentityStore) -> None:
    with pytest.raises(IdentityStoreError):
        store.write_file("SOUL.md", b"# bytes")  # type: ignore[arg-type]


# -- YAML 校验 --------------------------------------------------------------


def test_markdown_is_not_yaml_validated(store: IdentityStore) -> None:
    """POLICIES.yaml 已移出白名单，无任何 .yaml 校验；Markdown 里写冒号不该被拦。"""
    store.write_file("SOUL.md", "title: not yaml at all")
    assert store.read_file("SOUL.md") == "title: not yaml at all"


# -- 读写往返 ---------------------------------------------------------------


def test_read_write_roundtrip(store: IdentityStore) -> None:
    store.write_file("SOUL.md", "# updated")
    assert store.read_file("SOUL.md") == "# updated"


def test_write_creates_missing_parent_dir(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path)
    store.write_file("prompts/policies.md", "# p")

    assert (
        tmp_path / IDENTITY_DIR_NAME / "prompts" / "policies.md"
    ).read_text(encoding="utf-8") == "# p"


def test_read_missing_file_is_404(store: IdentityStore) -> None:
    with pytest.raises(IdentityStoreError) as exc:
        store.read_file("USER.md")
    assert exc.value.status == 404


# -- workspace 根回退（未迁移老工作区） --------------------------------------


def test_read_falls_back_to_workspace_root(tmp_path: Path) -> None:
    """identity/ 还没有、workspace 根有——读到根那份（与 context.py 注入一致）。"""
    (tmp_path / "SOUL.md").write_text("# legacy root soul", encoding="utf-8")
    store = IdentityStore(tmp_path)

    assert store.read_file("SOUL.md") == "# legacy root soul"


def test_identity_dir_wins_over_workspace_root(tmp_path: Path) -> None:
    """两边都有时 identity/ 优先——与 context.py 的存在性判断同序。"""
    (tmp_path / "SOUL.md").write_text("# root", encoding="utf-8")
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    identity_dir.mkdir()
    (identity_dir / "SOUL.md").write_text("# identity", encoding="utf-8")
    store = IdentityStore(tmp_path)

    assert store.read_file("SOUL.md") == "# identity"


def test_write_falls_back_to_existing_workspace_root(tmp_path: Path) -> None:
    """根上有老文件时写回根，避免 identity/ 新建一份与注入侧分裂。"""
    (tmp_path / "SOUL.md").write_text("# root", encoding="utf-8")
    store = IdentityStore(tmp_path)
    store.write_file("SOUL.md", "# edited")

    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "# edited"
    assert not (tmp_path / IDENTITY_DIR_NAME / "SOUL.md").exists()


def test_write_creates_identity_dir_when_no_legacy_root(tmp_path: Path) -> None:
    """根上没有老文件时仍落 identity/——新工作区直接走新位置。"""
    store = IdentityStore(tmp_path)
    store.write_file("SOUL.md", "# fresh")

    assert (tmp_path / IDENTITY_DIR_NAME / "SOUL.md").read_text(encoding="utf-8") == "# fresh"


def test_list_files_marks_root_fallback_exists(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text("# root", encoding="utf-8")
    store = IdentityStore(tmp_path)

    by_name = {f["name"]: f for f in store.list_files()}
    assert by_name["SOUL.md"]["exists"] is True


def test_list_files_marks_memory_md_derived_exists(tmp_path: Path) -> None:
    """MEMORY.md 真身在 memory/，exists 不能只查 identity/。"""
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "MEMORY.md").write_text("# derived", encoding="utf-8")
    store = IdentityStore(tmp_path)

    by_name = {f["name"]: f for f in store.list_files()}
    assert by_name["MEMORY.md"]["exists"] is True


def test_read_memory_md_from_derived_location(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "MEMORY.md").write_text("# derived", encoding="utf-8")
    store = IdentityStore(tmp_path)

    assert store.read_file("MEMORY.md") == "# derived"


def test_read_memory_md_prefers_derived_over_identity_leftover(tmp_path: Path) -> None:
    """memory/ 与 identity/ 都有残留时读 memory/——与 lifecycle 写落点一致。"""
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "MEMORY.md").write_text("# derived", encoding="utf-8")
    identity_dir = tmp_path / IDENTITY_DIR_NAME
    identity_dir.mkdir()
    (identity_dir / "MEMORY.md").write_text("# stale leftover", encoding="utf-8")
    store = IdentityStore(tmp_path)

    assert store.read_file("MEMORY.md") == "# derived"


def test_read_rejects_non_utf8_file(store: IdentityStore) -> None:
    (store.identity_dir / "USER.md").write_bytes(b"\xff\xfe\x00binary")
    with pytest.raises(IdentityStoreError):
        store.read_file("USER.md")


def test_error_is_value_error() -> None:
    assert issubclass(IdentityStoreError, ValueError)

    default = IdentityStoreError("boom")
    assert default.message == "boom"
    assert str(default) == "boom"
    assert default.status == 400
    assert IdentityStoreError("nope", status=403).status == 403


# -- list_files -------------------------------------------------------------


def test_list_files_groups_core_and_personas(store: IdentityStore) -> None:
    files = store.list_files()

    assert [f["name"] for f in files if f["group"] == "core"] == [
        "SOUL.md",
        "AGENT.md",
        "USER.md",
        "MEMORY.md",
        "prompts/policies.md",
    ]
    assert [f["name"] for f in files if f["group"] == "personas"] == ["default.md"]


def test_list_files_marks_exists(store: IdentityStore) -> None:
    by_name = {f["name"]: f for f in store.list_files()}

    assert by_name["SOUL.md"]["exists"] is True
    assert by_name["prompts/policies.md"]["exists"] is True
    assert by_name["default.md"]["exists"] is True
    assert by_name["AGENT.md"]["exists"] is False


def test_list_files_marks_restricted_and_badges(store: IdentityStore) -> None:
    by_name = {f["name"]: f for f in store.list_files()}

    assert by_name["AGENT.md"]["restricted"] is True
    assert by_name["SOUL.md"]["restricted"] is False
    assert "badge" not in by_name["SOUL.md"]
    assert "badge" not in by_name["AGENT.md"]
    assert by_name["MEMORY.md"]["badge"] == {
        "tone": "amber",
        "labelKey": "settings.identity.badgeAutoRegen",
    }


def test_list_files_exposes_logical_path_for_personas(store: IdentityStore) -> None:
    by_name = {f["name"]: f for f in store.list_files()}
    assert by_name["default.md"]["logicalPath"] == "personas/default.md"


def test_list_files_exposes_char_limit(store: IdentityStore) -> None:
    assert all(f["charLimit"] == CHAR_LIMIT for f in store.list_files())


def test_policies_yaml_is_not_editable(store: IdentityStore) -> None:
    """POLICIES.yaml 移出可编辑白名单：读写均 403，且不在清单里。"""
    with pytest.raises(IdentityStoreError) as read_exc:
        store.read_file("POLICIES.yaml")
    assert read_exc.value.status == 403

    with pytest.raises(IdentityStoreError) as write_exc:
        store.write_file("POLICIES.yaml", "tool_policies:\n  shell: allow\n")
    assert write_exc.value.status == 403

    assert "POLICIES.yaml" not in {f["name"] for f in store.list_files()}


def test_list_files_without_personas_dir(tmp_path: Path) -> None:
    """「还没建 personas」是合法状态，不该报错也不该臆造条目。"""
    files = IdentityStore(tmp_path).list_files()

    assert {f["group"] for f in files} == {"core"}
    assert all(f["exists"] is False for f in files)
