"""评估「团队目标」runner 的接线契约。

曾经的缺陷：runner 只建运行记录，从不跑 Leader 回合，团队运行永远停在
``running``，评估侧等到 120s 超时后以 ``SCHEDULER_STAGE_FAILED`` 收场。
这些测试锁住两件事：真的发起了一次带团队绑定的回合；非终态不会被当成完成。
"""

from __future__ import annotations

from types import SimpleNamespace

from nanobot.webui.gateway_services import build_team_eval_runner


def _team_service(status: str = "completed") -> SimpleNamespace:
    class _Repository:
        def get_team(self, team_id: str) -> SimpleNamespace:
            return SimpleNamespace(revision=3, name="创作团队")

        def get_run(self, run_id: str) -> SimpleNamespace:
            return SimpleNamespace(status=status)

    return SimpleNamespace(repository=_Repository())


class _Scheduler:
    def __init__(self) -> None:
        self.started: dict[str, str] = {}

    async def start_run(self, **kwargs: str) -> SimpleNamespace:
        self.started = dict(kwargs)
        return SimpleNamespace(run_id="run-1")


class _Host:
    def __init__(self, *, content: str = "秋日落叶，一句话。") -> None:
        self.content = content
        self.calls: list[dict] = []

    async def process_direct(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(content=self.content)


class _Sessions:
    def get_or_create(self, session_key: str) -> SimpleNamespace:
        return SimpleNamespace(
            messages=[
                {"role": "user", "content": "写一句"},
                {"role": "assistant", "content": "秋日落叶，一句话。"},
            ]
        )


async def test_team_eval_runner_runs_a_leader_turn_with_team_binding() -> None:
    scheduler, host = _Scheduler(), _Host()
    runner = build_team_eval_runner(
        team_service=_team_service(),
        scheduler=scheduler,
        session_manager=_Sessions(),
        host=host,
    )
    outcome = await runner({"team_id": "creative", "prompt": "写一句", "timeout_s": 30})

    assert outcome["status"] == "succeeded"
    assert outcome["final_answer"] == "秋日落叶，一句话。"
    assert len(host.calls) == 1
    call = host.calls[0]
    assert call["prompt"] == "写一句"
    assert call["session_key"].startswith("eval:")
    assert call["extra_metadata"]["team_binding"] == {
        "teamId": "creative",
        "teamRevision": 3,
        "teamName": "创作团队",
    }
    # 回合必须复用 start_run 的可见 turn_id，团队钩子才能 open_run 命中同一条运行。
    assert call["extra_metadata"]["webui_turn_id"] == scheduler.started["turn_id"]


async def test_team_eval_runner_without_host_reports_unavailable() -> None:
    runner = build_team_eval_runner(
        team_service=_team_service(), scheduler=_Scheduler(), session_manager=_Sessions(),
    )
    outcome = await runner({"team_id": "creative", "prompt": "写一句", "timeout_s": 30})
    assert outcome["status"] == "failed"
    assert outcome["error"] == "TEAM_HOST_UNAVAILABLE"


async def test_team_eval_runner_reports_non_completed_status_verbatim() -> None:
    runner = build_team_eval_runner(
        team_service=_team_service(status="partial"),
        scheduler=_Scheduler(),
        session_manager=_Sessions(),
        host=_Host(),
    )
    outcome = await runner({"team_id": "creative", "prompt": "写一句", "timeout_s": 30})
    assert outcome["status"] == "partial"
    assert outcome["error"] == "partial"
