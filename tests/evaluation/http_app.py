"""HTTP 层测试共用装配：应用工厂、口令、默认 profile 与登录辅助。

conftest 的约定是「后续 WU 在自己的文件里定义局部 fixture，不并行改 conftest」，
所以共用的是**装配函数**而不是 fixture 对象——fixture 一旦跨模块导入，参数名就会和
导入名撞 ruff F811，测试代码反而要去压制告警。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient

from nanobot.evaluation.auth import AuthConfig
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import SchedulerConfig
from nanobot.evaluation.schemas import RunCreate
from nanobot.evaluation.server import API_PREFIX, create_app
from nanobot.evaluation.services import EvaluationService, Profile

from .conftest import make_case_payload, make_run_payload, make_suite_payload

PASSPHRASE = "correct-horse-battery-staple"
AGENT_PROFILE_ID = "agent-1"
JUDGE_PROFILE_ID = "judge-1"


def default_profiles() -> tuple[Profile, ...]:
    return (
        Profile(id=AGENT_PROFILE_ID, label="受控执行器", provider="controlled",
                model="eval-model", kind="agent"),
        Profile(id=JUDGE_PROFILE_ID, label="受控评分器", provider="controlled",
                model="eval-model", kind="judge"),
    )


def auth_config() -> AuthConfig:
    return AuthConfig(
        passphrase=PASSPHRASE,
        allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
        allowed_origins=frozenset({"http://127.0.0.1:5173"}),
    )


def build_app(
    repository: EvaluationRepository,
    data_dir: Path,
    *,
    judge_runner: Any = None,
    profiles: tuple[Profile, ...] | None = None,
) -> web.Application:
    """起真应用（含调度器生命周期），端口由 TestServer 随机分配。

    ``judge_runner`` 是服务层留给测试的评分注入缝：离线用例不必为每条 gold 起一个
    评分子进程。默认 None 时走的仍是生产的子进程实现。
    """
    active_profiles = profiles or default_profiles()
    service = EvaluationService(
        repository=repository,
        data_dir=data_dir,
        profiles=active_profiles,
        judge_runner=judge_runner,
    )
    return create_app(
        repository=repository,
        service=service,
        auth_config=auth_config(),
        scheduler_config=SchedulerConfig(
            data_dir=data_dir, concurrency=1, poll_interval_s=0.05
        ),
        profiles=active_profiles,
    )


async def login(client: TestClient) -> str:
    response = await client.post(
        f"{API_PREFIX}/auth/login", json={"passphrase": PASSPHRASE}
    )
    assert response.status == 200
    return str((await response.json())["csrf_token"])


def csrf_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def run_request(repository: EvaluationRepository) -> RunCreate:
    """造一个契约合法的入队请求（用例与用例集都真落库）。"""
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    return RunCreate.model_validate(
        make_run_payload(
            [case["id"]], suite_id=suite["id"], agent_profile_id=AGENT_PROFILE_ID
        )
    )
