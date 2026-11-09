"""WebUI 知识库依赖容器。

装配口径（知识中心 spec §13.1）：

- **懒**：import 本模块不读配置、不建库、不起 worker；真正的
  ``KnowledgeService`` 只在首个请求访问 ``.service`` 时按 ``Config.knowledge``
  解析（与 ``nanobot.knowledge.service.get_knowledge_service`` 的进程级单例同源）。
- **测试缝**：构造参数 ``service`` 直接注入替身，``service_factory`` 可换装配函数；
  两者都不触发磁盘访问。
- **作用域**：``workspace_id`` 是**已认证 WebUI 上下文**的工作区，读过滤与写目标
  都以它为唯一真相源；payload 里的 ``workspaceId`` 只能在此集合内选择。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

DEFAULT_WORKSPACE_ID = "default"


def normalize_workspace_id(value: Any) -> str:
    """统一 workspace id 口径：去空白，空值回落 ``default``（与记忆域一致）。"""
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return DEFAULT_WORKSPACE_ID


def _load_knowledge_config() -> Any:
    """进程级配置里的 ``knowledge`` 段；只在确实需要时才加载。"""
    from nanobot.config.loader import load_config

    return load_config().knowledge


def _default_service_factory(knowledge: Any) -> Any:
    """默认装配：复用知识服务的进程级单例（按解析后的 DB 路径缓存）。"""
    from nanobot.knowledge.service import get_knowledge_service

    return get_knowledge_service(knowledge)


class KnowledgeServices:
    """WebUI 知识域的服务容器（可注入替身）。"""

    def __init__(
        self,
        *,
        service: Any = None,
        config: Any = None,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        service_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._service = service
        self._config = config
        self._workspace_id = normalize_workspace_id(workspace_id)
        self._service_factory: Callable[[Any], Any] = (
            service_factory if service_factory is not None else _default_service_factory
        )

    @property
    def workspace_id(self) -> str:
        """已认证 WebUI 上下文的工作区 id。"""
        return self._workspace_id

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        """可见工作区集合（当前 WebUI 会话只绑定一个工作区）。"""
        return (self._workspace_id,)

    def is_visible(self, workspace_id: Any) -> bool:
        return normalize_workspace_id(workspace_id) == self._workspace_id

    @property
    def config(self) -> Any:
        """``Config.knowledge``；未注入配置时才加载进程级配置。"""
        if self._config is None:
            self._config = _load_knowledge_config()
        return self._config

    @property
    def enabled(self) -> bool:
        """``knowledge.enabled``；注入了替身服务且无配置时视为启用（测试缝）。"""
        if self._config is None:
            return True if self._service is not None else bool(
                getattr(self.config, "enabled", True)
            )
        return bool(getattr(self._config, "enabled", True))

    @property
    def service(self) -> Any:
        """知识服务实例；首次访问时才按配置装配（懒，无 import 期副作用）。"""
        if self._service is None:
            self._service = self._service_factory(self.config)
        return self._service

    def upload_limit_bytes(self, default: int) -> int:
        """WebUI 单文件上限：``min(default, knowledge.chunking.max_file_mb)``。

        WebUI 默认 6 MB（spec §13.1）；配置更小时以配置为准。未注入配置且已有
        替身服务时不加载真实配置，直接用默认值。
        """
        config = self._config
        if config is None:
            if self._service is not None:
                return default
            config = self.config
        chunking = getattr(config, "chunking", None)
        try:
            max_mb = int(getattr(chunking, "max_file_mb", 0))
        except (TypeError, ValueError):
            return default
        if max_mb <= 0:
            return default
        return min(default, max_mb * 1024 * 1024)
