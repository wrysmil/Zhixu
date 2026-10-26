"""T14 验收：用受控回放把 36 条分层样本真正跑一遍判分链路。

这个脚本验证的是**框架与判据的可执行性**，不是真实模型能力：

- 正确回放：按用例自己的断言推导工具调用与产物，再把参考输出喂给受控 provider，
  检查判分链路能给出通过；
- 错误回放：喂一段不含关键信息的回答，检查判分链路能抓住错误；
- 团队类用例需要真实委派栈，当前没有生产接线，预期只会判失败或未评估——
  这条本身就是「缺证据不通过」的证据。

用法（在仓库根）：
    .venv/Scripts/python.exe .ai-runtime-artifacts/verifications/2026-10-26-run-reliability-samples.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.sample_cases import adapt_case
from nanobot.evaluation.scheduler import Scheduler, SchedulerConfig
from nanobot.evaluation.schemas import CaseWrite
from nanobot.evaluation.services import EvaluationService, Profile

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "nanobot" / "evaluation" / "samples" / "reliability-v2.json"
OUT = REPO / ".ai-runtime-artifacts" / "verifications" / "2026-10-26-reliability-sample-run.json"

#: 只靠最终回答就能判定的断言：这些用例可以做「正确回放 / 错误回放」双跑。
TEXT_ONLY_KINDS = {
    "answer_contains",
    "answer_not_contains",
    "answer_exact",
    "answer_length",
    "answer_json_block",
    "answer_json",
}


def load_cases() -> list[dict[str, Any]]:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return list(payload.get("cases") or [])


def _json_payload(assertion: Any) -> str:
    """按 file_json 断言的 checks 造一份满足要求的 JSON 文本。"""
    payload: dict[str, Any] = {}
    for check in getattr(assertion, "checks", []) or []:
        pointer = str(getattr(check, "pointer", "") or "")
        if not pointer.startswith("/") or "/" in pointer[1:]:
            continue
        key = pointer[1:]
        payload[key] = getattr(check, "equals", None)
    if not payload:
        return "{}"
    return json.dumps(payload, ensure_ascii=False)


def _tool_steps(case: CaseWrite) -> list[dict[str, Any]]:
    """按断言推导需要发生的工具调用：读文件、写产物。"""
    steps: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for assertion in case.assertions:
        kind = str(assertion.kind)
        if kind == "tool_called":
            name = str(getattr(assertion, "name", "") or "")
            args = dict(getattr(assertion, "arguments_subset", {}) or {})
            key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
            if not name or key in seen:
                continue
            seen.add(key)
            steps.append({"text": "", "tool_calls": [{"name": name, "arguments": args}]})
        elif kind in ("file_exists", "file_json", "file_contains"):
            path = str(getattr(assertion, "path", "") or "")
            if not path:
                continue
            key = ("write_file", path)
            if key in seen:
                continue
            seen.add(key)
            if kind == "file_json":
                content = _json_payload(assertion)
            else:
                content = str(getattr(assertion, "text", "") or "受控回放产物")
            steps.append({
                "text": "",
                "tool_calls": [
                    {"name": "write_file", "arguments": {"path": path, "content": content}}
                ],
            })
    return steps


def scripts_for(raw: dict[str, Any], case: CaseWrite) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """构造 (正确回放脚本, 错误回放脚本)。"""
    expected = raw.get("expected") or {}
    reference = str(expected.get("reference_answer") or "").strip()
    good = _tool_steps(case)
    good.append({"text": reference or "已完成。", "tool_calls": []})
    wrong = [{"text": "抱歉，我无法完成这个任务，也没有可交付的结果。", "tool_calls": []}]
    return good, wrong


def is_text_only(case: CaseWrite) -> bool:
    kinds = {str(item.kind) for item in case.assertions}
    if not kinds:
        return False
    if not kinds <= TEXT_ONLY_KINDS:
        return False
    return not case.artifact_paths


async def drive(
    repository: EvaluationRepository,
    service: EvaluationService,
    run_id: str,
) -> dict[str, Any]:
    scheduler = Scheduler(
        repository=repository,
        pipeline=service,
        config=SchedulerConfig(data_dir=service.data_dir, concurrency=1, poll_interval_s=0.02),
    )
    async with scheduler:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 120
        while loop.time() < deadline:
            status = repository.get_run(run_id)["status"]
            if status in ("completed", "cancelled", "interrupted"):
                return repository.get_run(run_id)
            await asyncio.sleep(0.05)
    raise AssertionError("run did not settle")


def _database(path: Path) -> Any:
    from nanobot.evaluation.database import EvaluationDatabase

    database = EvaluationDatabase(path)
    database.init_schema()
    return database


def _run_request(case_ids: list[str], suite_id: str) -> Any:
    from nanobot.evaluation.schemas import RunCreate

    return RunCreate.model_validate(
        {
            "suite_id": suite_id,
            "case_ids": case_ids,
            "agent_profile_id": "agent-1",
            "judge_profile_id": None,
            "limits": {"concurrency": 1, "max_iterations": 6, "timeout_s": 60},
            "model_mode": "scripted",
        }
    )


async def main() -> int:
    raw_cases = load_cases()
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="eval-t14-") as tmp:
        base = Path(tmp)
        repository = EvaluationRepository(_database(base / "evaluation.db"))
        service = EvaluationService(
            repository=repository,
            data_dir=base / "service",
            profiles=(
                Profile(
                    id="agent-1",
                    label="受控执行器",
                    provider="controlled",
                    model="eval-model",
                    kind="agent",
                    bundle={"config": {"providers": {"controlled": {"apiKey": "sk-local"}}}},
                ),
            ),
        )
        for raw in raw_cases:
            case = adapt_case(raw)
            case_id = str(raw.get("case_id"))
            suite = repository.create_suite(
                {
                    "name": f"reliability {case_id}",
                    "description": "T14 受控回放验收",
                    "tags": ["t14"],
                }
            )
            entry: dict[str, Any] = {
                "case_id": case_id,
                "target": raw.get("target"),
                "text_only": is_text_only(case),
            }
            good_steps, wrong_steps = scripts_for(raw, case)
            for label, steps in (("correct", good_steps), ("wrong", wrong_steps)):
                payload = case.model_dump(mode="json")
                payload["script"] = steps
                payload["suite_id"] = suite["id"]
                saved = repository.save_case(payload)
                run = service.create_run(
                    _run_request([saved["id"]], suite["id"]),
                    idempotency_key=f"t14-{case_id}-{label}-{uuid.uuid4()}",
                    scope="local-user",
                )
                detail = await drive(repository, service, run["id"])
                attempts = detail.get("attempts") or []
                attempt = attempts[0] if attempts else {}
                entry[label] = {
                    "verdict": attempt.get("verdict"),
                    "execution_status": attempt.get("execution_status"),
                    "grading_status": attempt.get("grading_status"),
                    "coverage": attempt.get("coverage"),
                }
            results.append(entry)
            print(
                f"{case_id} target={entry['target']} text_only={entry['text_only']} "
                f"correct={entry['correct']['verdict']} wrong={entry['wrong']['verdict']}",
                flush=True,
            )
    summary = {
        "total": len(results),
        "text_only": sum(1 for item in results if item["text_only"]),
        "correct_passed": sum(
            1 for item in results
            if item["text_only"] and item["correct"]["verdict"] == "passed"
        ),
        "wrong_not_passed": sum(
            1 for item in results
            if item["text_only"] and item["wrong"]["verdict"] != "passed"
        ),
        "results": results,
    }
    OUT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
