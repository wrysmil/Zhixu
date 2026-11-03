"""身份文件读写。

所有路径都必须落在 ``identity/`` 目录内：先查白名单，再把候选路径 ``resolve()``
后做前缀比对。这两步是这里唯一的安全边界，任何越界都在
:meth:`IdentityStore._resolve` 处被拦下。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.identity.bootstrap import LEGACY_ROOT_FILES
from nanobot.identity.catalog import (
    CHAR_LIMIT,
    CORE_FILES,
    LIFECYCLE_OWNED_FILES,
    MEMORY_DIR_DERIVED_FILES,
    WORKSPACE_ROOT_FILES,
    IdentityFileSpec,
    build_persona_specs,
    resolve_identity_dir,
)


class IdentityStoreError(ValueError):
    """身份文件操作失败。

    ``message`` 直接面向调用方（会被翻译成 4xx/5xx 响应），``status`` 是建议的
    HTTP 状态码。
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class IdentityStore:
    """workspace 级身份文件读写。

    实例不缓存文件内容：每次调用都重新读盘，保证「编辑后立刻对注入侧可见」
    （与 openakita 注入侧读磁盘文件的语义一致）。
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.identity_dir = resolve_identity_dir(self.workspace)

    # -- 白名单 ---------------------------------------------------------------

    def _specs(self) -> list[IdentityFileSpec]:
        """核心文件 + 当前磁盘上真实存在的 personas。

        persona 列表**每次调用都重新扫描磁盘**，这是有意的：调用方（API 层）
        每次请求新建一个 store，「刚放进 personas/ 的文件立刻出现在清单里」
        比省这一次 iterdir 更重要。不要改成 __init__ 里固化。
        """
        return [*CORE_FILES, *build_persona_specs(self.workspace)]

    def _spec_for(self, name: str) -> IdentityFileSpec | None:
        return next((spec for spec in self._specs() if spec.name == name), None)

    def _resolve(self, name: str) -> Path:
        """把逻辑名解析为绝对路径，并强制其位于 ``identity/`` 内。

        唯一的安全边界，两道闸：

        1. **白名单**——``name`` 必须精确命中 catalog 里的逻辑名。绝对路径、
           ``..`` 片段、``AGENTS.md`` 之类不在表内的名字都在这里被拒。
        2. **前缀比对**——``resolve()`` 会展开符号链接，展开后的候选路径必须
           仍然（严格地）位于 ``identity/`` 之内，否则视为越界。
        """
        spec = self._spec_for(name)
        if spec is None:
            raise IdentityStoreError(f"不是可编辑的身份文件：{name}", status=403)

        if spec.name in WORKSPACE_ROOT_FILES:
            root = self.workspace.resolve()
            candidate = (self.workspace / spec.path).resolve()
            if candidate == root or not candidate.is_relative_to(root):
                raise IdentityStoreError("路径越界：工作区文件必须位于工作区根内", status=400)
            return candidate

        root = self.identity_dir.resolve()
        candidate = (self.identity_dir / spec.path).resolve()
        if candidate == root or not candidate.is_relative_to(root):
            raise IdentityStoreError("路径越界：身份文件必须位于 identity/ 目录内", status=400)
        return candidate

    def _target(self, name: str) -> Path:
        """经 :meth:`_resolve` 完成安全校验后的实际读写落点。

        两处回退与各自的真相源对齐（写路径不受影响：``MEMORY.md`` /
        ``USER.md`` 在 :meth:`write_file` 已被拦去 lifecycle）：

        - ``MEMORY.md`` 真身在 ``memory/``（MemoryLifecycle 派生产物）；
        - ``USER.md`` 是同一套派生机制的产物，但真身在 ``identity/``——注入侧
          ``ContextBuilder`` 优先读那里，落点必须一致；
        - ``SOUL.md`` 老位置在 workspace 根，与 ``MemoryStore.soul_file`` 一致。

        其余文件（含越界候选、逃逸 symlink）仍由 ``_resolve`` 拦在 ``identity/`` 内。
        """
        candidate = self._resolve(name)
        spec = self._spec_for(name)
        if spec is not None and spec.name in MEMORY_DIR_DERIVED_FILES:
            # 派生产物优先读 memory/——写路径（lifecycle）永远落那里，
            # 若先命中 identity/ 残留文件会读到与写不一致的旧内容。
            derived = self.workspace / "memory" / spec.path
            if derived.is_file():
                return derived
            if candidate.is_file():
                return candidate
        if candidate.is_file():
            return candidate
        if spec is None:
            return candidate
        if spec.path == spec.name and spec.name in LEGACY_ROOT_FILES:
            legacy = self.workspace / spec.name
            if legacy.is_file():
                return legacy
        return candidate

    # -- 读 -------------------------------------------------------------------

    def resolve_path(self, name: str) -> Path:
        """经安全校验后的实际落点（可能尚不存在）。

        公开只读入口：调用方需要按「文件真实位置」而非逻辑名来判断磁盘状态时
        用它——例如编译器算源文件 mtime 决定产物是否过期，或任何想比对落点的
        诊断代码。写入仍必须走 :meth:`write_file`。
        """
        return self._target(name)

    def list_files(self) -> list[dict[str, Any]]:
        """返回前端文件清单（含 exists / restricted / badge / charLimit）。"""
        items: list[dict[str, Any]] = []
        for spec in self._specs():
            item: dict[str, Any] = {
                "name": spec.name,
                "group": spec.group,
                "exists": self._exists(spec),
                "restricted": spec.restricted,
                "charLimit": CHAR_LIMIT,
            }
            if spec.group == "personas":
                item["logicalPath"] = spec.path
            if spec.badge_tone and spec.badge_label_key:
                item["badge"] = {"tone": spec.badge_tone, "labelKey": spec.badge_label_key}
            items.append(item)
        return items

    def _exists(self, spec: IdentityFileSpec) -> bool:
        """文件是否真实存在——按各自的实际落点判断，而非一律查 ``identity/``。

        两处例外与读路径对齐，否则清单说 ``exists=true``、读却 404：

        - ``MEMORY.md`` 真身在 ``memory/``（MemoryLifecycle 派生产物，写走 api 分流）；
        - ``SOUL.md`` 老位置在 workspace 根（见 :meth:`_target`）。
        """
        if (self.identity_dir / spec.path).is_file():
            return True
        if spec.name in MEMORY_DIR_DERIVED_FILES:
            return (self.workspace / "memory" / spec.path).is_file()
        if spec.name in WORKSPACE_ROOT_FILES:
            return (self.workspace / spec.path).is_file()
        if spec.path == spec.name and spec.name in LEGACY_ROOT_FILES:
            return (self.workspace / spec.name).is_file()
        return False

    def read_file(self, name: str) -> str:
        path = self._target(name)
        if not path.is_file():
            raise IdentityStoreError(f"文件不存在：{name}", status=404)
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise IdentityStoreError(f"{name} 不是 UTF-8 文本文件", status=500) from exc

    # -- 写 -------------------------------------------------------------------

    def write_file(self, name: str, content: str) -> None:
        """校验后写入。

        ``MEMORY.md`` / ``USER.md`` 一律拒绝：它们是记忆库的派生产物，必须走
        ``MemoryLifecycle`` 的备份写入，由 API 层分流。在这里拦住，是为了保证
        「谁能写这两个文件」只有那一个入口——否则又会出两个整份覆盖的写手。
        """
        if name in LIFECYCLE_OWNED_FILES:
            raise IdentityStoreError(
                f"{name} 由记忆生命周期托管，必须经 MemoryLifecycle 的备份写入，"
                "不能通过 IdentityStore 直接落盘",
                status=500,
            )

        path = self._target(name)

        if not isinstance(content, str):
            raise IdentityStoreError("内容必须是字符串", status=400)

        if len(content) > CHAR_LIMIT:
            raise IdentityStoreError(
                f"内容超过 {CHAR_LIMIT} 字符上限（当前 {len(content)}）",
                status=400,
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
