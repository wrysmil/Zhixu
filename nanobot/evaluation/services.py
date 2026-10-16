"""T09 服务层：执行流水线、判分编排与读出装配。

调度器只管「谁来跑、什么时候停」，本模块管「跑出什么结论」：

    执行 Agent Worker → 规则判分 → （按需）Judge 评分 → 合并 verdict → 提交

凭据边界在 :func:`_agent_boot` 一处收口：``profile_bundle`` 只进内存里的 boot 对象，
不写数据库、不进命令行。读出装配（RunRead/AttemptRead/Metrics）也集中在这里，
路由层不再自己拼字段。

进程预算的约定：Agent 阶段由调度器持槽，Judge 阶段通过 :meth:`EvaluationService.judge`
**重新申请同一个信号量**，绝不嵌套持有。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.evaluation.evidence import (
    AttemptEvidence,
    EvidenceArtifact,
    build_attempt_evidence,
)
from nanobot.evaluation.graders.calibration import (
    lookup_calibration,
    run_calibration,
)
from nanobot.evaluation.graders.judge import JudgeResult
from nanobot.evaluation.graders.rules import evaluate_rules
from nanobot.evaluation.graders.verdict import derive_verdict, judge_state_of
from nanobot.evaluation.processes import WorkerProcess
from nanobot.evaluation.reports import aggregate_run, export_run_csv, export_run_json
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.runtime_factory import attempt_paths
from nanobot.evaluation.scheduler import AttemptOutcome
from nanobot.evaluation.schemas import (
    AgentWorkerBoot,
    AttemptError,
    CaseWrite,
    JudgeDimensionResult,
    JudgeWorkerBoot,
    Rubric,
    RunCreate,
    RunLimits,
)
from nanobot.evaluation.trace import read_trace

WORKER_MODULE = "nanobot.evaluation.worker"

#: 契约 §5：评分总预算（含格式重试），超时按 judge_error 记，不无限等。
JUDGE_BUDGET_S = 60


class ServiceError(RuntimeError):
    """服务层错误，带错误码；路由层直接映射状态码。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Profile:
    """可选模型预设。**只含公开信息**：密钥由服务端配置持有，绝不进入 DTO。"""

    id: str
    label: str
    provider: str
    model: str
    kind: str = "both"
    bundle: Mapping[str, Any] | None = None

    def to_read(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "kind": self.kind,
        }


def default_profiles() -> tuple[Profile, ...]:
    """离线可用的受控预设；真实 provider 由服务端配置注入。"""
    return (
        Profile(id="controlled-agent", label="受控执行器", provider="controlled",
                model="eval-model", kind="agent"),
        Profile(id="controlled-judge", label="受控评分器", provider="controlled",
                model="eval-model", kind="judge"),
    )


class EvaluationService:
    """执行流水线 + 读出装配 + 校准编排。"""

    def __init__(
        self,
        *,
        repository: EvaluationRepository,
        data_dir: Path,
        profiles: Sequence[Profile] = (),
        judge_slots: asyncio.Semaphore | None = None,
    ) -> None:
        self._repository = repository
        self._data_dir = Path(data_dir)
        self._profiles = {profile.id: profile for profile in profiles} or {
            profile.id: profile for profile in default_profiles()
        }
        # Judge 与 Agent 共用的进程预算；与调度器传入的是同一个对象。
        self._judge_slots = judge_slots or asyncio.Semaphore(3)

    # ------------------------------------------------------------------ 配置

    @property
    def repository(self) -> EvaluationRepository:
        return self._repository

    @property
    def runtime_root(self) -> Path:
        return self._data_dir / "runs"

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def list_gold_samples(self) -> dict[str, Any]:
        return self._repository.list_gold_samples()

    def profile(self, profile_id: str) -> Profile:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ServiceError("PROFILE_NOT_FOUND", profile_id)
        return profile

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.to_read() for profile in self._profiles.values()]

    # -------------------------------------------------------------- 运行入队

    def create_run(
        self,
        request: RunCreate,
        *,
        idempotency_key: str,
        scope: str,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        """冻结快照并幂等入队。入队即返回 202，真正的执行由调度器接手。"""
        agent = self.profile(request.agent_profile_id)
        judge = (
            self.profile(request.judge_profile_id) if request.judge_profile_id else None
        )
        self._require_judge_when_needed(request, judge)
        snapshot_bundle = {
            "agent_profile": {
                "provider": agent.provider, "model": agent.model,
                "id": agent.id, "bundle": dict(agent.bundle or {}),
            },
            "judge_profile": None if judge is None else {
                "provider": judge.provider, "model": judge.model,
                "id": judge.id, "bundle": dict(judge.bundle or {}),
            },
        }
        return self._repository.create_run(
            scope=scope,
            idempotency_key=idempotency_key,
            request=request,
            agent=snapshot_bundle["agent_profile"],
            judge=snapshot_bundle["judge_profile"],
            rules_version="rules-v1",
            grader_version="judge-v1",
            parent_run_id=parent_run_id,
        )

    def _require_judge_when_needed(
        self, request: RunCreate, judge: Profile | None
    ) -> None:
        """契约 §4：所选用例里只要有一个 rubric.required=true，就必须给评分器。

        缺了它，开放式任务会被算成「无 rubric」而白拿通过——这是必须在入队时
        拦住的错误，跑完再补救等于白烧一轮 token。
        """
        if judge is not None:
            return
        for case_id in request.case_ids:
            case = self._repository.get_case(str(case_id))
            rubric = (case or {}).get("rubric")
            if isinstance(rubric, Mapping) and rubric.get("required"):
                raise ServiceError("JUDGE_PROFILE_REQUIRED", str(case_id))

    def rerun(
        self, run_id: str, attempt_ids: Sequence[str], *, idempotency_key: str,
        scope: str,
    ) -> dict[str, Any]:
        """重跑只能选同运行的终态尝试，输入引用原冻结快照。"""
        source = self._repository.get_run(run_id)
        known = {
            item["id"]: item for item in source["attempts"]
            if item["execution_status"] not in ("queued", "running")
        }
        chosen: list[str] = []
        for attempt_id in attempt_ids:
            item = known.get(attempt_id)
            if item is None:
                raise ServiceError("ATTEMPT_NOT_RERUNNABLE", attempt_id)
            chosen.append(str(item["case_id"]))
        request = RunCreate(
            suite_id=source["suite_id"],
            case_ids=chosen,
            agent_profile_id=str(source["snapshot"]["agent_profile"]["id"]),
            judge_profile_id=(
                str(source["snapshot"]["judge_profile"]["id"])
                if source["snapshot"].get("judge_profile") else None
            ),
            limits=RunLimits.model_validate(source["snapshot"].get("limits") or {}),
        )
        return self.create_run(
            request, idempotency_key=idempotency_key, scope=scope,
            parent_run_id=run_id,
        )

    # -------------------------------------------------------------- 执行流水线

    async def execute(
        self, attempt: Mapping[str, Any], *, run: Mapping[str, Any]
    ) -> AttemptOutcome:
        """一次 attempt 的完整流水线；结论原样交给调度器提交。"""
        attempt_id = str(attempt["id"])
        case = CaseWrite.model_validate(attempt["case_snapshot"])
        limits = _limits_of(run.get("snapshot"))
        paths = attempt_paths(
            attempt_id=attempt_id,
            runtime_root=str(self.runtime_root),
            workspace=str(self.runtime_root / attempt_id / "workspace"),
        )
        paths.create()
        errors: list[AttemptError] = []
        boot = self._agent_boot(attempt_id, case, run, paths)
        owner = WorkerProcess(
            attempt_id=attempt_id,
            command=[sys.executable, "-m", WORKER_MODULE, attempt_id],
            log_path=paths.log_path,
        )
        try:
            await owner.start(boot.model_dump(mode="json"))
            self._record_process(attempt_id, owner)
            await owner.run_with_timeout(timeout_s=limits.timeout_s)
        except asyncio.CancelledError:
            await owner.cancel()
            raise
        except Exception as exc:
            errors.append(
                AttemptError(phase="setup", code="WORKER_SETUP_FAILED", message=str(exc))
            )
            await owner.cancel()
            return self._setup_failure(errors)
        finally:
            with contextlib.suppress(Exception):
                await owner.cancel()

        result = owner.ipc.result
        execution_status = _execution_status(owner, result)
        errors.extend(_errors_of(result))
        if execution_status != "succeeded":
            return AttemptOutcome(
                execution_status=execution_status,
                grading_status="not_applicable",
                verdict="failed" if execution_status in ("failed", "timed_out")
                else "not_evaluated",
                result=_result_payload(
                    execution_status=execution_status,
                    errors=errors,
                    worker_result=result,
                    timing=owner.timing(),
                ),
            )

        # 执行阶段结束、评分还没写库：让进度和重启恢复都能区分这两种等待。
        self._repository.mark_attempt_execution_done(
            attempt_id, execution_status=execution_status
        )
        self._repository.mark_attempt_grading_running(attempt_id)
        return await self._grade(
            attempt_id=attempt_id, case=case, run=run, paths=paths,
            errors=errors, owner=owner, result=result,
        )

    def _agent_boot(
        self,
        attempt_id: str,
        case: CaseWrite,
        run: Mapping[str, Any],
        paths: Any,
    ) -> AgentWorkerBoot:
        snapshot = run.get("snapshot") or {}
        profile = dict(snapshot.get("agent_profile") or {})
        bundle = dict(profile.get("bundle") or {})
        # build_attempt_config 要求 config 一定是对象：空预设也要给 {}，
        # 否则每个不带凭据的预设都会变成 setup_error。
        bundle.setdefault("config", {})
        return AgentWorkerBoot(
            type="boot",
            protocol_version=1,
            attempt_id=attempt_id,
            runtime_root=str(paths.runtime_root),
            workspace=str(paths.workspace),
            # 凭据只在内存：这一坨不进数据库、不进命令行、不进 Trace。
            profile_bundle=bundle,
            role="agent",
            case_snapshot=case,
        )

    def _record_process(self, attempt_id: str, owner: WorkerProcess) -> None:
        identity = owner.launch_identity or {}
        pid, nonce = identity.get("pid"), identity.get("nonce")
        if isinstance(pid, int) and isinstance(nonce, str) and nonce:
            with contextlib.suppress(Exception):
                self._repository.set_attempt_process(
                    attempt_id, pid=pid, nonce=nonce
                )

    async def _grade(
        self,
        *,
        attempt_id: str,
        case: CaseWrite,
        run: Mapping[str, Any],
        paths: Any,
        errors: list[AttemptError],
        owner: WorkerProcess,
        result: Mapping[str, Any] | None,
    ) -> AttemptOutcome:
        trace = read_trace(paths.trace_dir)
        evidence = build_attempt_evidence(
            attempt_id=attempt_id,
            case_snapshot=case.model_dump(mode="json"),
            workspace=paths.workspace,
            trace_events=trace.events,
            artifacts=self._artifacts(attempt_id, paths),
            final_answer=_text(result, "final_answer"),
            stop_reason=_text(result, "stop_reason"),
            iterations=int(_number(result, "iterations")),
            trace_complete=trace.trace_complete,
            execution_status="succeeded",
        )
        rules = evaluate_rules(case, evidence)
        required = [item.passed for item in rules if item.required]

        rubric = case.rubric
        judge_result: JudgeResult | None = None
        calibration: Mapping[str, Any] | None = None
        if rubric is not None:
            judge_result, calibration = await self.judge(
                attempt_id=attempt_id, rubric=rubric, evidence=evidence,
                run=run, paths=paths,
            )

        state = judge_state_of(rubric, judge_result, calibration)
        verdict = derive_verdict("succeeded", required, state, trace.trace_complete)
        payload = _result_payload(
            execution_status="succeeded",
            errors=errors,
            worker_result=result,
            timing=owner.timing(),
            assertions=[
                {
                    "id": item.id, "passed": item.passed, "required": item.required,
                    "message": item.message,
                    "evidence_refs": list(item.evidence_refs),
                }
                for item in rules
            ],
            judge=judge_result.model_dump(mode="json") if judge_result else None,
        )
        grading_status = "judge_error" if state == "error" and rubric is not None \
            and rubric.required else "completed"
        return AttemptOutcome(
            execution_status="succeeded",
            grading_status=grading_status,
            verdict=verdict,
            result=payload,
        )

    def _artifacts(self, attempt_id: str, paths: Any) -> tuple[EvidenceArtifact, ...]:
        """登记并索引产物；越界路径在这里被 resolve_inside 挡住。"""
        found: list[EvidenceArtifact] = []
        root = Path(paths.artifact_dir)
        if not root.is_dir():
            return ()
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(Path(paths.workspace)).as_posix() \
                if _inside(Path(paths.workspace), path) else path.name
            try:
                target = _resolve(Path(paths.workspace), relative)
            except ValueError:
                continue
            if not target.is_file():
                continue
            digest, size = _digest(target)
            registered = self._repository.append_artifact(
                attempt_id=attempt_id,
                relative_path=relative,
                media_type=_media_type(target),
                size_bytes=size,
                sha256=digest,
            )
            found.append(
                EvidenceArtifact(
                    relative_path=relative,
                    media_type=registered["media_type"],
                    size_bytes=size,
                    sha256=digest,
                    artifact_id=registered["id"],
                )
            )
        return tuple(found)

    # ------------------------------------------------------------------ 评分

    async def judge(
        self,
        *,
        attempt_id: str,
        rubric: Rubric,
        evidence: AttemptEvidence,
        run: Mapping[str, Any],
        paths: Any,
    ) -> tuple[JudgeResult, Mapping[str, Any] | None]:
        """按需评分。Judge 阶段**重新申请**进程槽位，不嵌套持有 Agent 的槽位。"""
        snapshot = run.get("snapshot") or {}
        profile = dict(snapshot.get("judge_profile") or {})
        fingerprint = _fingerprint(profile, rubric)
        calibration = self._find_calibration(fingerprint, rubric)
        boot = JudgeWorkerBoot(
            type="boot",
            protocol_version=1,
            attempt_id=attempt_id,
            runtime_root=str(paths.runtime_root),
            workspace=str(paths.workspace),
            profile_bundle=_judge_bundle(profile),
            role="judge",
            rubric=rubric,
            evidence=_evidence_payload(evidence),
        )
        async with self._judge_slots:
            owner = WorkerProcess(
                attempt_id=attempt_id,
                command=[sys.executable, "-m", WORKER_MODULE, attempt_id],
                log_path=paths.log_path,
            )
            try:
                await owner.start(boot.model_dump(mode="json"))
                async with asyncio.timeout(JUDGE_BUDGET_S):
                    await owner.run_with_timeout(timeout_s=JUDGE_BUDGET_S)
            except asyncio.CancelledError:
                await owner.cancel()
                raise
            except Exception:
                await owner.cancel()
                return JudgeResult(
                    status="failed", model=str(profile.get("model", "")),
                    version="judge-v1", calibrated=False,
                    error="JUDGE_WORKER_FAILED",
                ), calibration
            finally:
                with contextlib.suppress(Exception):
                    await owner.cancel()

        payload = owner.ipc.result
        dimensions = _dimensions_of(payload)
        error = _text(payload, "error") if dimensions is None else None
        if dimensions is None:
            return JudgeResult(
                status="failed", model=str(profile.get("model", "")),
                version="judge-v1", calibrated=False,
                error=error or "JUDGE_RESULT_MISSING",
            ), calibration
        return JudgeResult(
            status="completed",
            model=str(payload.get("model") or profile.get("model", "")),
            version=str(payload.get("version") or "judge-v1"),
            dimensions=dimensions,
            # 未校准就是未校准：不在这里替用户把分数变成可信。
            calibrated=bool(calibration and calibration.get("passed")),
        ), calibration

    def _find_calibration(
        self, fingerprint: str, rubric: Rubric
    ) -> Mapping[str, Any] | None:
        from nanobot.evaluation.graders.calibration import rubric_version

        with contextlib.suppress(Exception):
            return lookup_calibration(
                self._repository,
                judge_fingerprint=fingerprint,
                rubric_version=rubric_version(rubric),
            )
        return None

    # ------------------------------------------------------------------ 校准

    async def run_calibration_job(
        self, job_id: str, *, judge: Any, samples: Sequence[Mapping[str, Any]] = ()
    ) -> dict[str, Any]:
        """校准作业与普通运行共享并发与取消：逐项领取、逐项提交。"""
        snapshot = self._repository.get_calibration(job_id)["snapshot"]
        rubric = Rubric.model_validate(snapshot["rubric"])
        result = await run_calibration(
            self._repository,
            profile=snapshot.get("profile") or {},
            rubric=rubric,
            judge=judge,
            evidence_for=lambda row: None,
            samples=list(samples),
            gold_revision=snapshot.get("gold_revision"),
        )
        self._repository.record_calibration_result(
            judge_fingerprint=result["judge_fingerprint"],
            rubric_version=result["rubric_version"],
            gold_revision=result["gold_revision"],
            total=result["total"],
            matched=result["matched"],
        )
        return result

    # ------------------------------------------------------------------ 读出

    def run_summary(self, run_id: str) -> dict[str, Any]:
        for row in self._repository.list_runs():
            if row["id"] == run_id:
                return _summary(row)
        detail = self._repository.get_run(run_id)
        return _summary({
            "id": detail["id"], "suite_id": detail["suite_id"],
            "suite_name": self._repository.get_suite_name(detail["suite_id"]),
            "status": detail["status"], "created_at": detail["created_at"],
            "started_at": detail["started_at"], "finished_at": detail["finished_at"],
            "parent_run_id": detail["parent_run_id"],
            "planned_count": detail["planned_count"],
            "terminal_count": self._repository.run_counts(run_id).get("pending", 0),
        })

    def run_read(self, run_id: str) -> dict[str, Any]:
        detail = self._repository.get_run(run_id)
        summary = self.run_summary(run_id)
        attempts = self._repository.list_run_attempts(run_id)
        metrics = aggregate_run(attempts, summary["planned_count"])
        snapshot = detail.get("snapshot") or {}
        limits = snapshot.get("limits") or {}
        return {
            **summary,
            "snapshot": {
                "suite_id": detail["suite_id"],
                "suite_revision": 1,
                "case_revisions": {
                    str(item["id"]): item["case_revision"]
                    for item in detail["attempts"]
                },
                "agent_profile_id": str(
                    (snapshot.get("agent_profile") or {}).get("id", "")
                ),
                "judge_profile_id": (
                    str((snapshot.get("judge_profile") or {}).get("id", ""))
                    if snapshot.get("judge_profile") else None
                ),
                "provider": str(
                    (snapshot.get("agent_profile") or {}).get("provider", "")
                ),
                "model": str((snapshot.get("agent_profile") or {}).get("model", "")),
                "generation": (snapshot.get("agent_profile") or {}).get("generation")
                or {},
                "limits": limits,
                "rules_version": str(snapshot.get("rules_version", "")),
                "grader_version": str(snapshot.get("grader_version", "")),
                "tool_mode": str(snapshot.get("tool_mode", "")),
            },
            "counts": self._repository.run_counts(run_id),
            "metrics": metrics,
            "attempts_url": f"/api/eval/v1/runs/{run_id}/attempts",
        }

    def attempt_read(self, attempt_id: str) -> dict[str, Any]:
        row = self._repository.get_attempt(attempt_id)
        result = row.get("result") or {}
        snapshot = row.get("case_snapshot") or {}
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "position": row["position"],
            "case_id": row["case_id"],
            "case_revision": row["case_revision"],
            "case_name": str(snapshot.get("name") or ""),
            "execution_status": row["execution_status"],
            "grading_status": row["grading_status"],
            "verdict": row["verdict"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "final_answer": result.get("final_answer"),
            "stop_reason": result.get("stop_reason"),
            "errors": result.get("errors") or [],
            "assertions": result.get("assertions") or [],
            "judge": result.get("judge"),
            "usage": _usage_pair(result.get("usage")),
            "timing": result.get("timing") or {},
            "trace_complete": bool(result.get("trace_complete", False)),
            "artifacts": [
                {
                    "id": item["id"], "name": item["relative_path"],
                    "media_type": item["media_type"],
                    "size_bytes": item["size_bytes"], "sha256": item["sha256"],
                }
                for item in self._repository.list_artifacts_for_attempt(attempt_id)
            ],
            "reviews": self._repository.list_reviews(attempt_id),
        }

    def trace_page(
        self, attempt_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> dict[str, Any]:
        """``after_seq`` 严格递增；未密封时 sealed=false，前端不得当最终 Trace。"""
        paths = self._paths_for(attempt_id)
        trace = read_trace(paths.trace_dir) if paths is not None else None
        events = sorted(trace.events, key=lambda event: event.seq) if trace else []
        page = [
            event.model_dump(mode="json")
            for event in events
            if event.seq > after_seq
        ][:limit]
        last = page[-1]["seq"] if page else after_seq
        return {
            "data": page,
            "next_after_seq": last,
            "has_more": bool(trace) and any(
                event.seq > last for event in events
            ),
            "sealed": bool(trace.sealed) if trace else False,
        }

    def _paths_for(self, attempt_id: str) -> Any | None:
        try:
            return attempt_paths(
                attempt_id=attempt_id,
                runtime_root=str(self.runtime_root),
                workspace=str(self.runtime_root / attempt_id / "workspace"),
            )
        except ValueError:
            return None

    def overview(self) -> dict[str, Any]:
        recent = self._repository.list_runs(limit=10)
        counts: dict[str, int] = {}
        for row in self._repository.list_runs(limit=1000):
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        latest = next((row for row in recent if row["status"] != "completed"), None) \
            or (recent[0] if recent else None)
        metrics = None
        if latest is not None:
            metrics = aggregate_run(
                self._repository.list_run_attempts(str(latest["id"])),
                int(latest["planned_count"]),
            )
        return {
            "recent_runs": [_summary(row) for row in recent],
            "metrics": metrics,
            "status_counts": counts,
        }

    def export_run(self, run_id: str, *, fmt: str) -> tuple[bytes, str, str]:
        # 先确认运行存在：导出不能给不存在的运行编一个空文件。
        detail = self._repository.get_run(run_id)
        attempts = self._repository.list_run_attempts(run_id)
        summary = self.run_summary(run_id)
        metrics = aggregate_run(attempts, int(summary["planned_count"]))
        if fmt == "csv":
            body = export_run_csv(detail, metrics, attempts)
            return body, "text/csv; charset=utf-8", f"{run_id}.csv"
        body = export_run_json(detail, metrics, attempts)
        return body, "application/json; charset=utf-8", f"{run_id}.json"

    # -------------------------------------------------------------- 辅助函数

    def _setup_failure(self, errors: list[AttemptError]) -> AttemptOutcome:
        return AttemptOutcome(
            execution_status="setup_error",
            grading_status="not_applicable",
            verdict="not_evaluated",
            result=_result_payload(
                execution_status="setup_error", errors=errors, worker_result=None,
                timing={},
            ),
        )


def _summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "suite_id": row["suite_id"],
        "suite_name": row["suite_name"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "parent_run_id": row.get("parent_run_id"),
        "planned_count": row["planned_count"],
        "terminal_count": row["terminal_count"],
    }


def _limits_of(snapshot: Any) -> RunLimits:
    limits = snapshot.get("limits") if isinstance(snapshot, Mapping) else None
    try:
        return RunLimits.model_validate(limits or {})
    except Exception:
        return RunLimits()


def _execution_status(
    owner: WorkerProcess, result: Mapping[str, Any] | None
) -> str:
    if result is not None:
        status = str(result.get("execution_status") or "failed")
        return status
    code = owner.exit_code
    if code == 0:
        # 退出码 0 但没有 result 帧：证据不足，不能当成成功。
        return "failed"
    return "failed"


def _errors_of(result: Mapping[str, Any] | None) -> list[AttemptError]:
    if result is None:
        return []
    out: list[AttemptError] = []
    for item in result.get("errors") or []:
        if isinstance(item, Mapping):
            out.append(
                AttemptError(
                    phase=str(item.get("phase", "execution")),
                    code=str(item.get("code", "WORKER_ERROR")),
                    message=str(item.get("message", "")),
                )
            )
    return out


def _result_payload(
    *,
    execution_status: str,
    errors: Sequence[AttemptError],
    worker_result: Mapping[str, Any] | None,
    timing: Mapping[str, Any],
    assertions: list[dict[str, Any]] | None = None,
    judge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "errors": [error.model_dump(mode="json") for error in errors],
        "timing": dict(timing),
        "trace_complete": bool((worker_result or {}).get("trace_complete", False)),
    }
    if worker_result is not None:
        for key in ("final_answer", "stop_reason", "iterations"):
            if key in worker_result:
                payload[key] = worker_result[key]
        if "usage" in worker_result:
            payload["usage"] = worker_result["usage"]
    if assertions is not None:
        payload["assertions"] = assertions
    if judge is not None:
        payload["judge"] = judge
    payload.setdefault("usage", _empty_usage())
    return payload


def _usage_pair(reported: Any) -> dict[str, Any]:
    """worker 上报的是**单路**聚合；AttemptRead 要求 agent/judge 两路分开。

    合并成一个扁平的 usage 会让前端分不清 token 花在执行还是评分上。
    """
    empty = {
        "input_tokens": None, "output_tokens": None, "total_tokens": None,
        "source": "unavailable", "coverage": "unavailable",
        "physical_requests": 0, "iterations": 0, "missing_usage_calls": 0,
    }
    if not isinstance(reported, Mapping):
        return {"agent": dict(empty), "judge": dict(empty)}
    if isinstance(reported.get("agent"), Mapping):
        return {
            "agent": {**empty, **dict(reported["agent"])},
            "judge": {**empty, **dict(reported["judge"])},
        }
    return {"agent": {**empty, **dict(reported)}, "judge": dict(empty)}


def _empty_usage() -> dict[str, Any]:
    empty = {
        "input_tokens": None, "output_tokens": None, "total_tokens": None,
        "source": "unavailable", "coverage": "unavailable",
        "physical_requests": 0, "iterations": 0, "missing_usage_calls": 0,
    }
    return {"agent": dict(empty), "judge": dict(empty)}


def _text(result: Mapping[str, Any] | None, key: str) -> str | None:
    if result is None:
        return None
    value = result.get(key)
    return None if value is None else str(value)


def _number(result: Mapping[str, Any] | None, key: str) -> int:
    if result is None:
        return 0
    value = result.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _dimensions_of(payload: Mapping[str, Any] | None) -> list[JudgeDimensionResult] | None:
    """Worker 上报的评分维度；格式不合法一律当失败，不猜。"""
    if not payload:
        return None
    raw = payload.get("judge") or payload.get("dimensions")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[JudgeDimensionResult] = []
    for item in raw:
        if not isinstance(item, Mapping):
            return None
        try:
            out.append(
                JudgeDimensionResult.model_validate(
                    {
                        "id": item["id"], "score": item["score"],
                        "reason": str(item.get("reason", "")),
                        "evidence_refs": [str(x) for x in item.get("evidence_refs") or []],
                    }
                )
            )
        except (KeyError, TypeError, ValueError):
            return None
    return out


def _evidence_payload(evidence: AttemptEvidence) -> dict[str, Any]:
    return {
        "attempt_id": evidence.attempt_id,
        "case_snapshot": dict(evidence.case_snapshot),
        "final_answer": evidence.final_answer,
        "stop_reason": evidence.stop_reason,
        "iterations": evidence.iterations,
        "trace_events": [event.model_dump(mode="json") for event in evidence.trace_events],
        "tool_calls": [
            {
                "name": call.name, "arguments": dict(call.arguments),
                "result": call.result, "is_error": call.is_error,
                "seq": call.seq, "turn": call.turn, "iteration": call.iteration,
            }
            for call in evidence.tool_calls
        ],
        "artifacts": [
            {
                "relative_path": item.relative_path, "media_type": item.media_type,
                "size_bytes": item.size_bytes, "sha256": item.sha256,
                "artifact_id": item.artifact_id,
            }
            for item in evidence.artifacts
        ],
        "trace_complete": evidence.trace_complete,
        "execution_status": evidence.execution_status,
    }


def _judge_bundle(profile: Mapping[str, Any]) -> dict[str, Any]:
    bundle = dict(profile.get("bundle") or {})
    bundle.setdefault("config", {})
    return bundle


def _fingerprint(profile: Mapping[str, Any], rubric: Rubric) -> str:
    from nanobot.evaluation.graders.calibration import judge_fingerprint

    with contextlib.suppress(Exception):
        return judge_fingerprint(profile=profile, rubric=rubric)
    return "unknown"


def _digest(path: Path) -> tuple[str, int]:
    import hashlib

    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _media_type(path: Path) -> str:
    return {
        ".txt": "text/plain; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
    }.get(path.suffix.lower(), "application/octet-stream")


def _resolve(root: Path, relative: str) -> Path:
    from nanobot.evaluation.validation import resolve_inside

    return resolve_inside(root, relative)


def _inside(root: Path, path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False
