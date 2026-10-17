"""内置创作团队的权威定义（团队管理中的真实种子数据）。

与旧协作批次的固定配方解耦：这里不写死「研究 → 提纲 → 初稿 → 审校」的阶段流程，
只描述团队身份、成员与边界。每次运行派不派活、派给谁，由主编根据作者这一次的请求
决定（契约 §2）。``TeamService.ensure_builtin_team`` 负责幂等播种与内容迁移，用户
同名团队不会被覆盖。
"""

from __future__ import annotations

from nanobot.teams.models import TeamDefinition, TeamLimits
from nanobot.writing.builtin import WRITING_TEAM_LOGICAL_ID
from nanobot.writing.models import utc_now_iso

# 模板内容版本：参与播种 request_id。修改下面任何可写字段时必须同时改这里，
# 否则同一 request_id 携带不同载荷会被仓储的幂等守卫拒绝。
WRITING_TEAM_TEMPLATE_VERSION = "2026-10-17-1"

EDITOR_AGENT_ID = "writing-editor"
MEMBER_AGENT_IDS: tuple[str, ...] = (
    "writing-researcher",
    "writing-architect",
    "writing-author",
    "writing-reviewer",
)

_MEMBER_ROLES = {
    "writing-researcher": "研究员：按请求检索、核对资料并记录疑点。",
    "writing-architect": "结构编辑：调整主张、论据与提纲结构。",
    "writing-author": "写手：按作者确认的范围起草、续写与局部改写。",
    "writing-reviewer": "审校：独立检查事实、结构与可读性并给出修改建议。",
}


def writing_team_template() -> TeamDefinition:
    """返回内置团队模板；``id`` 是逻辑 ID，物理 ID 由 allocator 决定。"""
    moment = utc_now_iso()
    return TeamDefinition.from_dict(
        {
            "id": WRITING_TEAM_LOGICAL_ID,
            "revision": 1,
            "name": "创作团队",
            "description": "主编按作者每一次的请求自主决定是否派工、派给谁的创作团队。",
            "instructions": (
                "你是创作团队的主编。先理解作者这次要什么：回答问题、查资料、梳理结构、"
                "改写或续写正文，都由你决定要不要派成员、派给谁，不要求固定流程。"
                "作者点名成员时优先交给该成员；作者没有要求修改正文时，只讨论与回答，"
                "不落库。任何写入都必须使用创作会话提供的 writing_* 工具，"
                "并在答复中说明改了什么。"
            ),
            "leaderAgentId": EDITOR_AGENT_ID,
            "members": [
                {
                    "memberId": agent_id,
                    "agentId": agent_id,
                    "role": _MEMBER_ROLES[agent_id],
                }
                for agent_id in MEMBER_AGENT_IDS
            ],
            "limits": TeamLimits().to_dict(),
            "archived": False,
            "createdAt": moment,
            "updatedAt": moment,
        }
    )


def writing_team_seed_request_id() -> str:
    return f"writing-team-seed:{WRITING_TEAM_TEMPLATE_VERSION}"
