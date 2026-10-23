"""Agent 档案的持久化存储。

布局照 openakita 的 ``ProfileStore``：``{workspace}/agents/profiles/{id}.json``
+ ``{workspace}/agents/categories.json``。base_dir 落在 workspace 之下与
``identity/`` 并列，理由见契约 §5。

**读路径不落盘。** 出厂预置只补进内存缓存，文件要到 ``save_profile`` /
``reset_profile`` / ``set_visibility`` 才真正产生——刷新一次列表不该在
用户磁盘上创建目录。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agents.catalog import (
    AGENTS_DIR_NAME,
    CATEGORIES_FILE,
    FACTORY_AGENT_CATEGORIES,
    FACTORY_PROFILES,
    PROFILES_SUBDIR,
    factory_for,
    is_customized,
    is_valid_agent_id,
)
from nanobot.agents.models import AgentCategory, AgentProfile


class AgentStoreError(ValueError):
    """可安全回传给 WebUI 的存储层错误。

    ``code`` 只在需要业务码的拒绝路径上给（如 ``BUILTIN_RESOURCE_PROTECTED``）；
    其余错误保持 ``None``，传输层照旧只看 ``message`` / ``status``。
    """

    def __init__(self, message: str, *, status: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# 归属模块与业务码取自 ``nanobot.writing.builtin``（唯一真相源），此处只做转发。
_ASSET_KIND_AGENT = "agent"
_PROTECTED_TEMPLATE = "builtin resource is protected: {kind}/{logical_id}"

# 说明：本模块**不得**在导入期 import ``nanobot.writing.builtin``。那条链（
# ``nanobot.writing.database`` → ``nanobot.config.paths``）会让 ``nanobot.agent`` 在
# 后续 ``mock.patch("nanobot.agent.tools.mcp...")`` 这类按名解析里不可达——既有测试
# 因此会失败。内置判定一律走下面这些懒引入 helper。


def protected_write_message(kind: str, logical_id: str) -> str:
    """契约 §4 的统一受保护 message。四个域共用，避免文案各自漂移。"""
    return _PROTECTED_TEMPLATE.format(kind=kind, logical_id=logical_id)


def _builtin_asset_registry() -> type[Any]:
    """懒引入内置清单：导入期不拉进 ``nanobot.writing``，理由见上面的说明。"""
    from nanobot.writing.builtin import BuiltinAssetRegistry

    return BuiltinAssetRegistry


def protected_write_code() -> str:
    """统一业务码（契约 §4）。"""
    from nanobot.writing.builtin import BUILTIN_RESOURCE_PROTECTED

    return BUILTIN_RESOURCE_PROTECTED


def protected_write_error(kind: str, logical_id: str) -> AgentStoreError:
    """统一构造「内置资产受保护」存储层错误（409 / ``BUILTIN_RESOURCE_PROTECTED``）。"""
    return AgentStoreError(
        protected_write_message(kind, logical_id),
        status=409,
        code=protected_write_code(),
    )


def coded_error_message(code: str | None, message: str) -> str:
    """把业务码透出成错误体（口径 = ``teams_routes.translate``：``<CODE> <原文>``）。

    agent-team 契约 §3 冻结了「失败码放固定前缀」：全站错误信封都是 ``{error: string}``，
    因此码只能进字符串。四个域（teams / agents / skills / mcp）共用这一个 helper，
    避免某一域漏码或拼两遍；``code`` 为空（普通错误）时原文一字不动。
    """
    if not code or message.startswith(code):
        return message
    return f"{code} {message}"


def _builtin_agent_asset(agent_id: str):
    """该 id 对应的内置清单条目；不是创作空间内置 Agent 则为 ``None``。"""
    return _builtin_asset_registry().get_asset(_ASSET_KIND_AGENT, agent_id)


def is_protected_builtin_agent(agent_id: str) -> bool:
    """受保护的创作空间内置 Agent（数据源只有 ``WRITING_BUILTIN_ASSETS``）。"""
    asset = _builtin_agent_asset(agent_id)
    return bool(asset is not None and asset.protected)


def agent_builtin_projection(agent_id: str) -> dict[str, object]:
    """管理投影的创作内置字段；非内置 Agent 返回空 dict（不得给用户资源加字段）。"""
    asset = _builtin_agent_asset(agent_id)
    if asset is None or not asset.protected:
        return {}
    from nanobot.writing.builtin import (
        ORIGIN_BUILTIN,
        OWNER_MODULE,
        WRITING_BUILTIN_VERSION,
    )

    return {
        "ownerModule": OWNER_MODULE,
        "origin": ORIGIN_BUILTIN,
        "builtinVersion": WRITING_BUILTIN_VERSION,
        "deletable": False,
        "protectionReason": protected_write_message(_ASSET_KIND_AGENT, agent_id),
    }


def _atomic_write(path: Path, content: str) -> None:
    """同 nanobot/identity/compiler.py:224 的写法：先写临时文件再 os.replace。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sort_key(profile: AgentProfile) -> tuple[bool, str, str, str]:
    """系统预设先于自定义，同组内按分类、名称、id 稳定排序。"""
    return (profile.type != "system", profile.category_id or "", profile.name, profile.id)


class AgentStore:
    def __init__(self, workspace: Path) -> None:
        self._root = Path(workspace) / AGENTS_DIR_NAME
        self._profiles_dir = self._root / PROFILES_SUBDIR
        self._categories_file = self._root / CATEGORIES_FILE
        self._lock = threading.RLock()
        self._cache: dict[str, AgentProfile] | None = None
        self._categories: list[AgentCategory] | None = None

    # -- 路径 -----------------------------------------------------------------

    def _path_for(self, agent_id: str) -> Path:
        """档案 id → 绝对路径，两道闸：白名单正则 + 前缀比对。

        与 ``nanobot/identity/store.py:62`` 的 ``_resolve`` 同构。白名单挡
        绝对路径与 ``..``，``is_relative_to`` 挡符号链接逃逸。
        """
        if not is_valid_agent_id(agent_id):
            raise AgentStoreError(f"非法 Agent id：{agent_id!r}", status=400)
        root = self._profiles_dir.resolve()
        candidate = (self._profiles_dir / f"{agent_id}.json").resolve()
        if candidate == root or not candidate.is_relative_to(root):
            raise AgentStoreError("路径越界：档案必须位于 agents/profiles/ 目录内", status=403)
        return candidate

    # -- 加载 -----------------------------------------------------------------

    def _load_all(self) -> dict[str, AgentProfile]:
        if self._cache is not None:
            return self._cache
        loaded: dict[str, AgentProfile] = {}
        if self._profiles_dir.is_dir():
            for path in sorted(self._profiles_dir.glob("*.json")):
                try:
                    profile = AgentProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError) as exc:
                    logger.warning("跳过无法解析的 Agent 档案 {}：{}", path.name, exc)
                    continue
                if not is_valid_agent_id(profile.id):
                    logger.warning("跳过 id 非法的 Agent 档案 {}：{}", path.name, profile.id)
                    continue
                loaded[profile.id] = profile
        # 出厂预置只补内存：磁盘上已有的档案一律不覆盖。
        for factory in FACTORY_PROFILES:
            if factory.id not in loaded:
                loaded[factory.id] = factory_for(factory.id) or factory
        self._cache = loaded
        return loaded

    def _load_categories(self) -> list[AgentCategory]:
        if self._categories is not None:
            return self._categories
        parsed: list[AgentCategory] = []
        if self._categories_file.is_file():
            try:
                raw = json.loads(self._categories_file.read_text(encoding="utf-8"))
                parsed = [AgentCategory.from_dict(x) for x in raw if isinstance(x, dict)]
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("分类文件无法解析，回落到出厂分类：{}", exc)
                parsed = []
        self._categories = parsed or list(FACTORY_AGENT_CATEGORIES)
        return self._categories

    def _persist(self, profile: AgentProfile) -> None:
        path = self._path_for(profile.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
        self._load_all()[profile.id] = profile

    # -- 读 -------------------------------------------------------------------

    def list_profiles(self, *, include_hidden: bool = True) -> list[AgentProfile]:
        """列出档案。

        ``include_hidden=False`` 排除 ``hidden=True`` 的档案。给「对 LLM 可见」
        的消费方用——用户把档案隐藏起来，意图是不想让它出现在被枚举的清单里，
        而 spawn 的工具描述正是这样一个清单。
        """
        with self._lock:
            profiles = list(self._load_all().values())
        if not include_hidden:
            profiles = [p for p in profiles if not p.hidden]
        profiles.sort(key=_sort_key)
        return profiles

    def get_profile(self, agent_id: str) -> AgentProfile:
        with self._lock:
            # 读路径也要过白名单：id 来自 URL query / mutation payload，
            # 非法 id 是 400 而不是「查无此人」的 404。
            if not is_valid_agent_id(agent_id):
                raise AgentStoreError(f"非法 Agent id：{agent_id!r}", status=400)
            profile = self._load_all().get(agent_id)
        if profile is None:
            raise AgentStoreError(f"Agent 不存在：{agent_id}", status=404)
        return profile

    def list_categories(self) -> list[AgentCategory]:
        with self._lock:
            return list(self._load_categories())

    # -- 写 -------------------------------------------------------------------

    def save_profile(self, profile: AgentProfile) -> AgentProfile:
        """校验并落盘。``type`` / ``customized`` / ``updatedAt`` 一律由服务端决定。

        创作空间内置档案不得被改写：身份由 ``nanobot.writing.builtin`` 给出，档案内容
        由 catalog 出厂预设给出。仅改 ``hidden``（视图偏好）不算改写，仍然放行——
        「隐藏内置档案」是既有承诺，本层不撤销它。
        """
        with self._lock:
            self._path_for(profile.id)
            profile.validate()
            factory = factory_for(profile.id)
            if factory is not None and is_protected_builtin_agent(profile.id):
                if is_customized(profile, factory):
                    raise protected_write_error(_ASSET_KIND_AGENT, profile.id)
            stored = replace(
                profile,
                type="system" if factory is not None else "custom",
                customized=is_customized(profile, factory) if factory is not None else False,
                updated_at=_utc_now(),
            )
            self._persist(stored)
            return stored

    def delete_profile(self, agent_id: str) -> None:
        with self._lock:
            profile = self.get_profile(agent_id)
            if is_protected_builtin_agent(agent_id):
                # 创作空间内置档案的归属事实优先于通用 system 文案（契约 §4）。
                raise protected_write_error(_ASSET_KIND_AGENT, agent_id)
            if profile.type == "system":
                # 拒绝而不是删：出厂自愈会在下一次加载时把它补回来，
                # 用户会看到「删了又出现」。见契约 §9.5。
                raise AgentStoreError(
                    f"系统预设「{agent_id}」不可删除，可重置或隐藏", status=409
                )
            path = self._path_for(agent_id)
            path.unlink(missing_ok=True)
            self._load_all().pop(agent_id, None)

    def reset_profile(self, agent_id: str) -> AgentProfile:
        with self._lock:
            current = self.get_profile(agent_id)
            factory = factory_for(agent_id)
            if factory is None or current.type != "system":
                raise AgentStoreError("只有系统预设可以重置", status=409)
            if is_protected_builtin_agent(agent_id):
                # 重置会把档案恢复成出厂值——那正是「内置身份的内容由 catalog 决定」，
                # 但用户对内置档案的显式覆盖必须可见地失败，而不是被静默抹平。
                raise protected_write_error(_ASSET_KIND_AGENT, agent_id)
            # hidden 是视图偏好而非内容定制，重置不该把它一并抹掉。
            fresh = replace(factory, hidden=current.hidden, updated_at=_utc_now())
            self._persist(fresh)
            return fresh

    def set_visibility(self, agent_id: str, hidden: bool) -> AgentProfile:
        with self._lock:
            current = self.get_profile(agent_id)
            updated = replace(current, hidden=hidden, updated_at=_utc_now())
            self._persist(updated)
            return updated

    def save_categories(self, categories: list[AgentCategory]) -> list[AgentCategory]:
        """一期不接线（前端分类写死在前端常量里），保留供二期使用。"""
        with self._lock:
            self._categories = list(categories)
            self._root.mkdir(parents=True, exist_ok=True)
            _atomic_write(
                self._categories_file,
                json.dumps([c.to_dict() for c in categories], ensure_ascii=False, indent=2),
            )
            return list(self._categories)
