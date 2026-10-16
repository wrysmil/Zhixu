"""WU-07 / plan Task 7：写作适配层读回**完整**产物，通用下游摘要语义不变（回归）。

两件事：

- ``TeamRepository.get_task_full_result`` 让写作适配按 ``taskId`` 取回成员完整产出；
- ``TeamScheduler`` 的**下游准备阶段**默认仍然只拼 ``result.summary`` 并按
  ``UPSTREAM_CONTEXT_BUDGET=8000`` 截断；只有写作批次显式注入 ``upstream_expander``
  时才展开全文。通用聊天（主编回合、普通重试）因此一字不变。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nanobot.teams.database import TeamDatabase
from nanobot.teams.models import (
    AgentProfileSnapshot,
    MAX_TASK_INSTRUCTION_CHARS,
    TeamBinding,
    TeamDefinition,
    TeamLimits,
    TeamMember,
    TeamRevisionMember,
    TeamRevisionSnapshot,
    TeamTaskInput,
)
from nanobot.teams.repository import TeamRepository
from nanobot.teams.scheduler import (
    UPSTREAM_CONTEXT_BUDGET,
    SnapshotMemberCatalog,
    TeamScheduler,
)
from nanobot.webui.session_identity import WEBUI_SESSION_STORAGE_PREFIX

_NOW = "2026-10-17T00:00:00Z"
_LONG_DRAFT = "长" * 20_000


def _profile(agent_id: str, *, skills: list[str]) -> AgentProfileSnapshot:
    return AgentProfileSnapshot.from_dict(
        {
            "id": agent_id,
            "updatedAt": _NOW,
            "prompt": "",
            "name": agent_id,
            "modelId": None,
            "tools": {"mode": "include", "entries": ["read_file"]},
            "skills": {"mode": "include", "entries": skills},
            "subAgents": {"mode": "all", "entries": []},
        }
    )


@dataclass
class _FakeExecutor:
    """把成员执行换成本地字符串：不做 LLM，只记录拿到的指令。"""

    content: str = _LONG_DRAFT
    instructions: list[str] = field(default_factory=list)

    async def execute_member(self, request: Any, *, events: Any = None) -> Any:
        self.instructions.append(request.instruction)
        return SimpleNamespace(
            outcome="succeeded",
            final_content=self.content,
            stop_reason="completed",
            usage=None,
            error_code=None,
        )


@dataclass
class _Bed:
    repository: TeamRepository
    scheduler: TeamScheduler
    executor: _FakeExecutor
    binding: TeamBinding
    definition: TeamDefinition

    async def run_two_stage(self, *, expander: Any = None, budget: int | None = None) -> list[str]:
        handle = await self.scheduler.start_run(
            binding=self.binding,
            chat_id="chat-1",
            session_key=f"{WEBUI_SESSION_STORAGE_PREFIX}chat-1",
            turn_id="turn-1",
            internal_turn_id="turn-1",
            snapshot=self.repository.run_binding_snapshot(self.binding),
            upstream_expander=expander,
            upstream_budget=budget,
        )
        await handle.dispatch(
            batch_id="batch-1",
            tasks=[
                TeamTaskInput(
                    key="draft",
                    member_id="writing-author",
                    title="初稿",
                    instruction="写初稿",
                    acceptance="写完",
                ),
                TeamTaskInput(
                    key="review",
                    member_id="writing-reviewer",
                    title="审校",
                    instruction="审这一稿",
                    acceptance="给意见",
                    depends_on=["draft"],
                ),
            ],
        )
        await handle.finalize(delivered=True)
        return list(self.executor.instructions)


def _bed(tmp_path: Path) -> _Bed:
    db = TeamDatabase(tmp_path / "team-ws")
    db.init_schema()
    repository = TeamRepository(db)
    definition = TeamDefinition(
        id="writing-team",
        revision=1,
        name="创作团队",
        description="",
        instructions="",
        leader_agent_id="writing-editor",
        members=[
            TeamMember(member_id="writing-author", agent_id="writing-author", role="作者"),
            TeamMember(member_id="writing-reviewer", agent_id="writing-reviewer", role="审校"),
        ],
        limits=TeamLimits(),
        archived=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    snapshot = TeamRevisionSnapshot(
        definition=definition,
        leader=_profile("writing-editor", skills=[]),
        members=[
            TeamRevisionMember(
                member=definition.members[0], profile=_profile("writing-author", skills=[])
            ),
            TeamRevisionMember(
                member=definition.members[1], profile=_profile("writing-reviewer", skills=[])
            ),
        ],
    )
    repository.save_team(
        definition=definition,
        snapshot=snapshot,
        expected_revision=None,
        request_id="seed",
    )
    executor = _FakeExecutor()
    scheduler = TeamScheduler(
        repository=repository,
        executor=executor,
        members=SnapshotMemberCatalog(known_tools=["read_file"], known_skills=[]),
        runtime=SimpleNamespace(),
        workspace=tmp_path,
    )
    binding = TeamBinding(
        team_id=definition.id, team_revision=1, team_name=definition.name
    )
    return _Bed(
        repository=repository,
        scheduler=scheduler,
        executor=executor,
        binding=binding,
        definition=definition,
    )


async def test_generic_downstream_keeps_the_8000_char_summary(tmp_path: Path) -> None:
    bed = _bed(tmp_path)

    instructions = await bed.run_two_stage()

    downstream = instructions[1]
    assert "上游成员已完成的结果" in downstream
    assert "上游结果过长，已按预算截断" in downstream
    assert _LONG_DRAFT not in downstream
    assert len(downstream) < UPSTREAM_CONTEXT_BUDGET + 1_000


async def test_writing_downstream_receives_the_full_draft(tmp_path: Path) -> None:
    bed = _bed(tmp_path)

    async def _expand(task_id: str) -> str | None:
        return await asyncio.to_thread(bed.repository.get_task_full_result, task_id)

    instructions = await bed.run_two_stage(expander=_expand, budget=24_000)

    downstream = instructions[1]
    assert _LONG_DRAFT in downstream
    assert "已按预算截断" not in downstream
    assert len(downstream) < MAX_TASK_INSTRUCTION_CHARS


async def test_writing_expander_falls_back_to_summary(tmp_path: Path) -> None:
    bed = _bed(tmp_path)
    bed.executor.content = "短初稿" * 10

    async def _expand(task_id: str) -> str | None:
        full = await asyncio.to_thread(bed.repository.get_task_full_result, task_id)
        if full is not None:
            return full
        snapshot = await asyncio.to_thread(bed.repository.get_task, task_id)
        assert snapshot.result is not None
        return snapshot.result.summary

    instructions = await bed.run_two_stage(expander=_expand, budget=24_000)

    assert "短初稿" * 10 in instructions[1]
