"""Pure payload conversion + action functions for the WebUI identity domain.

Owns the JSON wire format for identity file listing/reading/writing and the
transport-neutral entry points consumed by ``identity_routes.py``. Mirrors
``memory_api.py``: it does NOT depend on HTTP/WebSocket machinery, so the
router (``settings_routes.py``, WU-04) can wire it to either transport.

The only documented cross-domain write is ``MEMORY.md``: it is a derived
artifact owned by ``MemoryLifecycle`` (Dream), so the manual-edit path must
go through the lifecycle's backup semantics instead of ``IdentityStore``.
That split lives here — ``IdentityStore.write_file`` refuses MEMORY.md on
purpose, so a caller that forgets the branch gets a loud 500 rather than a
silent unbacked write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.identity.bootstrap import PERSONA_PRESET_STEMS, load_identity_template
from nanobot.identity.catalog import (
    CHAR_LIMIT,
    LIFECYCLE_OWNED_FILES,
    PERSONA_STATE_FILE,
    PERSONAS_SUBDIR,
    is_safe_persona_stem,
    resolve_identity_dir,
)
from nanobot.identity.compiler import COMPILE_TARGETS, compile_identity
from nanobot.identity.store import IdentityStore, IdentityStoreError
from nanobot.webui.settings_contracts import WebUISettingsError


def _estimate_tokens(text: str) -> int:
    """粗估 token 数：CJK 按 1.5 字/token，其余按 4 字符/token。

    纯展示用途，不参与任何截断决策——身份段实测占比不到上下文的 0.04%，
    自动截断只会在制造「用户规则被静默砍掉」的风险。
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return round(cjk / 1.5 + (len(text) - cjk) / 4)


def _active_persona_path(workspace: Path) -> Path:
    return resolve_identity_dir(workspace) / PERSONA_STATE_FILE


def _persona_stems(workspace: Path) -> list[str]:
    personas_dir = resolve_identity_dir(workspace) / PERSONAS_SUBDIR
    if not personas_dir.is_dir():
        return []
    return sorted(p.stem for p in personas_dir.glob("*.md"))


def identity_get_active_persona(workspace: Path) -> dict[str, Any]:
    """返回当前激活的 persona stem 与可选列表。

    激活态落在 ``identity/active_persona`` 而非 config：ContextBuilder 不持有
    config 对象，走 workspace 文件与既有的 policies 读取路径同构，无需新增
    参数穿透。指向已删除 persona 的陈旧激活态按未激活处理。
    """
    options = _persona_stems(workspace)
    try:
        stem = _active_persona_path(workspace).read_text(encoding="utf-8").strip()
    except OSError:
        stem = ""
    if stem not in options:
        stem = ""
    return {"active": stem, "options": options}


def identity_set_active_persona(workspace: Path, stem: str) -> dict[str, Any]:
    """写入激活态。空串表示关闭。状态文件是用户可写的，落盘前必须校验。"""
    stem = (stem or "").strip()
    if stem and not is_safe_persona_stem(stem):
        raise WebUISettingsError("persona 名称非法", status=400)
    if stem and stem not in _persona_stems(workspace):
        raise WebUISettingsError(f"persona 不存在：{stem}", status=404)
    path = _active_persona_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stem, encoding="utf-8")
    return {"active": stem}


def _file_tokens(store: IdentityStore, name: str, exists: bool) -> int:
    """Best-effort token count for one listed identity file.

    A missing file is priced against its bundled factory template so the editor
    can show the cost of a persona the user has not customized yet. Any read
    failure degrades to 0 rather than failing the listing — an estimate must
    never make the manifest unusable. ``UnicodeDecodeError`` is a ``ValueError``,
    not an ``OSError``, so a persona saved in a non-UTF-8 encoding would
    otherwise 500 the whole manifest; name it explicitly.
    """
    try:
        if exists:
            text = store.resolve_path(name).read_text(encoding="utf-8")
        else:
            text = load_identity_template(name) or ""
    except (OSError, UnicodeDecodeError, IdentityStoreError):
        return 0
    return _estimate_tokens(text)


def identity_list_files(workspace: Path) -> dict[str, Any]:
    """Return the identity file manifest plus the shared char limit.

    ``charLimit`` is read from ``CHAR_LIMIT`` rather than restated here so the
    frontend's identity editor and MEMORY.md truncation can never drift apart.
    Each entry also carries a ``tokens`` estimate — display only, see
    :func:`_estimate_tokens`.
    """
    store = IdentityStore(workspace)
    try:
        files = store.list_files()
    except IdentityStoreError as exc:
        raise WebUISettingsError(exc.message, status=exc.status) from exc
    for item in files:
        item["tokens"] = _file_tokens(store, item["name"], bool(item.get("exists")))
    from nanobot.identity.compiler import compiled_info

    return {
        "files": files,
        "charLimit": CHAR_LIMIT,
        # P3：身份编辑器显示「上次编译时间」与「有未编译改动」。
        "compile": compiled_info(workspace),
    }


def identity_read_file(workspace: Path, name: str) -> dict[str, Any]:
    """Read one whitelisted identity file.

    Unknown/escaping names stay 4xx. A *missing* file (store 404) no longer
    surfaces as an error: the bundled factory template is returned with
    ``exists=false`` / ``fromTemplate=true`` so the editor can prefill instead
    of showing a red banner. No bundled template → empty content, both flags
    false (create-on-save placeholder path).
    """
    try:
        content = IdentityStore(workspace).read_file(name)
    except IdentityStoreError as exc:
        if exc.status != 404:
            raise WebUISettingsError(exc.message, status=exc.status) from exc
        template = load_identity_template(name)
        if template is None:
            return {"name": name, "content": "", "exists": False, "fromTemplate": False}
        return {"name": name, "content": template, "exists": False, "fromTemplate": True}
    return {"name": name, "content": content, "exists": True, "fromTemplate": False}


def identity_list_presets() -> dict[str, Any]:
    """Factory persona presets for the SOUL editor dropdown.

    Labels/descriptions are i18n keys — the backend only ships content and
    stable ids so translations stay on the frontend.
    """
    from nanobot.utils.helpers import load_bundled_template  # local: avoid import cycle

    presets: list[dict[str, Any]] = []
    for stem in PERSONA_PRESET_STEMS:
        content = load_bundled_template(f"personas/{stem}.md") or ""
        presets.append(
            {
                "name": stem,
                "labelKey": f"settings.identity.preset.{stem}.label",
                "descriptionKey": f"settings.identity.preset.{stem}.description",
                "content": content,
            }
        )
    return {"presets": presets}


def identity_write_file(
    workspace: Path,
    name: str,
    content: str,
    *,
    lifecycle: Any = None,
) -> dict[str, Any]:
    """Write one identity file, routing derived artifacts to the memory lifecycle.

    ``MEMORY.md`` / ``USER.md`` are derived from SQLite: they are written through
    ``MemoryLifecycle``'s backup semantics (and then re-exported), so the WebUI
    cannot create a second, unbacked writer for the same file. Everything else
    goes through ``IdentityStore``.
    """
    if name in LIFECYCLE_OWNED_FILES:
        if lifecycle is None:
            raise WebUISettingsError(f"{name} 写入需要 MemoryLifecycle", status=500)
        writer = (
            lifecycle.write_memory_md
            if name == "MEMORY.md"
            else lifecycle.write_user_md
        )
        try:
            writer(content)
        except ValueError as exc:
            # Length / type validation from the lifecycle surfaces as a 4xx.
            raise WebUISettingsError(str(exc), status=400) from exc
        except OSError as exc:
            logger.exception("Failed to write %s via MemoryLifecycle", name)
            raise WebUISettingsError(f"{name} 写入失败", status=500) from exc
        return {"name": name, "saved": True, "autoRegenerated": True}

    try:
        IdentityStore(workspace).write_file(name, content)
    except IdentityStoreError as exc:
        raise WebUISettingsError(exc.message, status=exc.status) from exc

    # 保存即编译：写的是编译源文件时刷新 runtime 产物。编译是纯本地毫秒级
    # 操作；失败不得让「保存」判失败——写盘已成功，产物下次启动补齐即可。
    if name in {target.source for target in COMPILE_TARGETS}:
        try:
            compile_identity(workspace)
        except Exception:
            logger.warning("identity: 保存后自动编译失败 {}，下次启动补齐", name)

    return {"name": name, "saved": True}


def identity_reload(*, refresh_memory_md: Any = None) -> dict[str, Any]:
    """Re-read identity files from disk for the injection path.

    Injection reads the files on every turn, so there is nothing to cache —
    the only side effect worth triggering is a re-derivation of the two derived
    artifacts (MEMORY.md + USER.md). When the gateway has not injected the
    service (e.g. standalone HTTP handler), report ``skipped`` instead of
    failing; a reload is never worth a 500.
    """
    if refresh_memory_md is None:
        return {"status": "skipped", "reason": "no_services"}
    try:
        return dict(refresh_memory_md())
    except Exception:
        logger.exception("identity_reload: derived memory refresh failed")
        return {"status": "error"}
