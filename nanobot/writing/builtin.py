"""创作域内置资产清单（契约 §7）与 `(workspace, kind, logical_id, version) → physical_id` 幂等 preparation。

唯一真相源：L4 契约 §2（`writing_builtin_assets` DDL）、§4（`BUILTIN_RESOURCE_PROTECTED`）、
§7（清单与统一投影字段）。本模块只做三件事：**列出**内置资产、**登记**逻辑身份到物理 ID 的
映射、**判定**受保护写。它不读写 Agent / Skill / Team / MCP 存储——那是 allocator 的职责。

allocator 协议（``prepare`` 的 ``allocator`` 参数）
--------------------------------------------------

``allocator(candidate: str) -> str``：以 ``candidate`` 为首选在目标存储中创建内置记录，
返回该内置记录实际使用的物理 ID；**绝不允许**修改、重命名或删除既有用户对象。
``candidate`` 已被用户对象占用时返回空串表示「这个名字不归内置资产」，此时不创建任何记录。

本模块据此避让：空串 → 按 ``<logical_id>-builtin``、``<logical_id>-builtin-2`` … 追加后缀重试，
直到 allocator 给出非空物理 ID。用户对象始终原样保留（spec §「产品逻辑身份与存储记录 ID 分开」）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import utc_now_iso

WRITING_BUILTIN_VERSION = "v1"
WRITING_TEAM_LOGICAL_ID = "writing-team"
OWNER_MODULE = "writing"
ORIGIN_BUILTIN = "builtin"
BUILTIN_RESOURCE_PROTECTED = "BUILTIN_RESOURCE_PROTECTED"

# 后缀重试上限：正常路径最多两次（首选 + `-builtin`），到上限说明 allocator 违约。
_MAX_ALLOCATION_ATTEMPTS = 8


@dataclass(frozen=True)
class BuiltinAsset:
    """一个内置资产的产品逻辑身份；物理 ID 由 :meth:`BuiltinAssetRegistry.prepare` 决定。"""

    kind: str
    logical_id: str
    display_name: str
    protected: bool = True


WRITING_BUILTIN_ASSETS: tuple[BuiltinAsset, ...] = (
    BuiltinAsset("team", WRITING_TEAM_LOGICAL_ID, "创作团队"),
    BuiltinAsset("agent", "writing-editor", "创作团队主编"),
    BuiltinAsset("agent", "writing-researcher", "资料研究员"),
    BuiltinAsset("agent", "writing-architect", "结构编辑"),
    BuiltinAsset("agent", "writing-author", "写手"),
    BuiltinAsset("agent", "writing-reviewer", "审校"),
    BuiltinAsset("skill", "article-brief", "选题简报"),
    BuiltinAsset("skill", "article-research", "资料研究"),
    BuiltinAsset("skill", "article-outline", "结构提纲"),
    BuiltinAsset("skill", "article-draft", "初稿撰写"),
    BuiltinAsset("skill", "article-rewrite", "片段改写"),
    BuiltinAsset("skill", "article-expand", "内容扩写"),
    BuiltinAsset("skill", "article-review", "审校复查"),
)

_ASSETS_BY_KEY: dict[tuple[str, str], BuiltinAsset] = {
    (asset.kind, asset.logical_id): asset for asset in WRITING_BUILTIN_ASSETS
}


class BuiltinResourceProtected(PermissionError):  # noqa: N818  # 名称即业务码语义，与异常后缀无关
    """受保护的内置资产被写入/删除/覆盖：业务码 ``BUILTIN_RESOURCE_PROTECTED``，HTTP 409。"""

    code = BUILTIN_RESOURCE_PROTECTED
    status = 409

    def __init__(self, kind: str, logical_id: str) -> None:
        self.asset_kind = kind
        self.logical_id = logical_id
        self.message = f"builtin resource is protected: {kind}/{logical_id}"
        super().__init__(self.message)


class BuiltinAssetRegistry:
    """一个工作区一个实例；仅持久化逻辑身份到物理 ID 的映射。"""

    def __init__(self, db: WritingDatabase) -> None:
        self._db = db
        self._workspace_key = db.workspace_key

    # ---- 清单 --------------------------------------------------------------

    @staticmethod
    def get_asset(kind: str, logical_id: str) -> BuiltinAsset | None:
        """查内置清单；非内置（用户资源或未知 kind）返回 ``None``，不抛错。"""
        return _ASSETS_BY_KEY.get((kind, logical_id))

    def is_protected(self, kind: str, logical_id: str) -> bool:
        asset = self.get_asset(kind, logical_id)
        return bool(asset is not None and asset.protected)

    def ensure_writable(self, kind: str, logical_id: str) -> None:
        """受保护内置资产抛 :class:`BuiltinResourceProtected`；用户资源静默放行。"""
        if self.is_protected(kind, logical_id):
            raise BuiltinResourceProtected(kind, logical_id)

    # ---- 幂等 preparation ---------------------------------------------------

    def physical_id(self, kind: str, logical_id: str) -> str | None:
        """当前版本已登记的物理 ID；尚未准备则为 ``None``。"""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT physical_id FROM writing_builtin_assets"
                " WHERE workspace_key = ? AND asset_kind = ? AND logical_id = ?"
                " AND builtin_version = ?",
                (self._workspace_key, kind, logical_id, WRITING_BUILTIN_VERSION),
            ).fetchone()
        return None if row is None else str(row["physical_id"])

    def prepare(self, kind: str, logical_id: str, *, allocator: Callable[[str], str]) -> str:
        """返回内置资产在本工作区的物理 ID；重复调用不再触发 allocator。"""
        asset = self.get_asset(kind, logical_id)
        if asset is None:
            raise ValueError(f"unknown builtin asset: {kind}/{logical_id}")
        existing = self.physical_id(kind, logical_id)
        if existing is not None:
            return existing
        physical_id = self._allocate(kind, logical_id, allocator)
        with self._db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO writing_builtin_assets"
                " (workspace_key, asset_kind, logical_id, builtin_version, physical_id,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._workspace_key, kind, logical_id, WRITING_BUILTIN_VERSION,
                    physical_id, utc_now_iso(),
                ),
            )
            row = conn.execute(
                "SELECT physical_id FROM writing_builtin_assets"
                " WHERE workspace_key = ? AND asset_kind = ? AND logical_id = ?"
                " AND builtin_version = ?",
                (self._workspace_key, kind, logical_id, WRITING_BUILTIN_VERSION),
            ).fetchone()
        return str(row["physical_id"])

    def _allocate(
        self, kind: str, logical_id: str, allocator: Callable[[str], str]
    ) -> str:
        """allocator 给出空串即候选被用户对象占用：追加合法后缀重试。"""
        for attempt in range(_MAX_ALLOCATION_ATTEMPTS):
            candidate = logical_id if attempt == 0 else (
                f"{logical_id}-builtin" if attempt == 1 else f"{logical_id}-builtin-{attempt}"
            )
            assigned = allocator(candidate)
            if assigned:
                return str(assigned)
        raise ValueError(
            f"builtin physical id allocation failed for {kind}/{logical_id}:"
            " allocator kept rejecting every candidate"
        )

    # ---- 投影 --------------------------------------------------------------

    def project(self, kind: str, logical_id: str) -> dict[str, Any]:
        """管理投影统一字段（契约 §7）：四类资源同键同语义，供管理页合并进既有投影。

        ``physicalId`` 在尚未 :meth:`prepare` 时为 ``None``（只读查询可返回「尚未使用」）；
        调用方不得据此认定资产不存在——产品逻辑身份永远是 ``logicalId``。
        """
        asset = self.get_asset(kind, logical_id)
        if asset is None:
            raise ValueError(f"unknown builtin asset: {kind}/{logical_id}")
        physical = self.physical_id(kind, logical_id)
        # 物理 ID 与逻辑 ID 不同 ⇒ 逻辑 ID 被用户对象占着：用户对象未被改动，
        # 投影同时给出内置来源（physicalId/origin）与这处冲突事实。
        return {
            "assetKind": asset.kind,
            "logicalId": asset.logical_id,
            "displayName": asset.display_name,
            "physicalId": physical,
            "ownerModule": OWNER_MODULE,
            "origin": ORIGIN_BUILTIN,
            "builtinVersion": WRITING_BUILTIN_VERSION,
            "protected": asset.protected,
            "deletable": not asset.protected,
            "protectionReason": f"builtin resource is protected: {kind}/{logical_id}",
            "userAssetConflict": bool(physical is not None and physical != logical_id),
        }
