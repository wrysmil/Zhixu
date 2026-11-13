"""执行轨迹观察域。

- ``models``：事件、记录与正文引用的稳定值对象
- ``policy``：脱敏、截断与预算
- ``store``：单 writer SQLite 事实与查询投影
- ``recorder`` / ``capture``：运行时采集门面（fail-open）
- ``service``：按 workspace 管理存储与只读查询
"""

from nanobot.trajectory.service import (
    TrajectoryService,
    TrajectorySettings,
    configure_trajectory,
    trajectory_service,
)

__all__ = [
    "TrajectoryService",
    "TrajectorySettings",
    "configure_trajectory",
    "trajectory_service",
]
