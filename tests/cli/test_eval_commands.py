"""T13 独立评估入口：无交互回归与有意义的退出码。

退出码是 CI 的抓手：

- 0 = 全部合格；
- 1 = 存在已知不合格；
- 2 = 未完成 / 待复核 / 配置问题（混合时 2 优先）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.schemas import CaseWrite

from tests.evaluation.conftest import make_case_payload, make_suite_payload

runner = CliRunner()


def _repository(tmp_path: Path) -> EvaluationRepository:
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    database.init_schema()
    return EvaluationRepository(database)


def _case(repository: EvaluationRepository, *, answer: str = "alpha") -> tuple[str, str]:
    suite = repository.create_suite(make_suite_payload())
    payload = make_case_payload(
        suite_id=suite["id"],
        script=[{"text": answer}],
        artifact_paths=[],
    )
    case = repository.save_case(payload)
    return suite["id"], case["id"]


def test_missing_suite_exits_with_review_required(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--suite-id",
            "00000000-0000-4000-8000-0000000000ff",
            "--profile",
            "controlled-agent",
            "--data-dir",
            str(tmp_path / "data"),
        ],
    )
    assert result.exit_code == 2


def test_controlled_run_reports_qualified(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    repository = _repository(data_dir)
    suite_id, _case_id = _case(repository)
    output = tmp_path / "out" / "run.json"

    result = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--suite-id",
            suite_id,
            "--profile",
            "controlled-agent",
            "--data-dir",
            str(data_dir),
            "--output",
            str(output),
            "--timeout",
            "120",
        ],
    )
    assert result.exit_code == 0, result.output
    assert output.is_file()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["attempts"]


def test_disqualified_run_exits_with_one(tmp_path: Path) -> None:
    """脚本回放的内容不满足断言时退出码是 1（已知不合格），不是 2。"""
    data_dir = tmp_path / "data"
    repository = _repository(data_dir)
    suite_id, _case_id = _case(repository, answer="完全不含关键词")

    result = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--suite-id",
            suite_id,
            "--profile",
            "controlled-agent",
            "--data-dir",
            str(data_dir),
            "--timeout",
            "120",
        ],
    )
    assert result.exit_code == 1, result.output


def test_export_contains_no_credentials(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    repository = _repository(data_dir)
    suite_id, _case_id = _case(repository)
    output = tmp_path / "run.json"

    result = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--suite-id",
            suite_id,
            "--profile",
            "controlled-agent",
            "--data-dir",
            str(data_dir),
            "--output",
            str(output),
            "--timeout",
            "120",
        ],
    )
    assert result.exit_code == 0, result.output
    body = output.read_text(encoding="utf-8").lower()
    # 导出里可以出现 token 用量字段，但不能出现任何凭据。
    for secret_marker in ("api_key", "apikey", "secret", "password", "sk-"):
        assert secret_marker not in body
