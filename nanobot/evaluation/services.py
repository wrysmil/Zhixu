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
import hashlib
import json
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.config.paths import get_workspace_path
from nanobot.evaluation.assessment import assess
from nanobot.evaluation.comparisons import build_comparison
from nanobot.evaluation.coverage import (
    compute_coverage,
    evaluation_capability,
    summarize_coverage,
)
from nanobot.evaluation.evidence import (
    AttemptEvidence,
    EvidenceArtifact,
    build_attempt_evidence,
)
from nanobot.evaluation.graders.calibration import (
    CalibrationError,
    judge_fingerprint,
    lookup_calibration,
    rubric_payload,
    rubric_version,
    verdict_from_scores,
)
from nanobot.evaluation.graders.judge import JudgeResult
from nanobot.evaluation.graders.rules import evaluate_rules
from nanobot.evaluation.graders.verdict import derive_verdict, judge_state_of
from nanobot.evaluation.observability import (
    build_execution_page,
    build_tool_calls,
    build_tool_calls_page,
    build_usage_page,
)
from nanobot.evaluation.processes import WorkerProcess
from nanobot.evaluation.reports import (
    aggregate_run,
    export_run_csv,
    export_run_json,
    normalize_attempt_for_metrics,
    render_summary_html,
)
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.runtime_factory import attempt_paths
from nanobot.evaluation.scheduler import AttemptOutcome, CalibrationItemOutcome
from nanobot.evaluation.schemas import (
    AgentWorkerBoot,
    AttemptError,
    CaseWrite,
    ComparisonCreate,
    JudgeDimensionResult,
    JudgeWorkerBoot,
    Rubric,
    RunCreate,
    RunLimits,
    TargetRef,
)
from nanobot.evaluation.team_runtime import (
    TeamRunOutcome,
    TeamRunRequest,
    TeamRuntimeError,
    build_frozen_team,
)
from nanobot.evaluation.trace import TraceCollector, TraceError, read_trace
from nanobot.evaluation.worker import (
    ARTIFACT_INDEX_NAME,
    TraceRecorder,
    WorkerChannel,
    evidence_from_payload,
)

WORKER_MODULE = "nanobot.evaluation.worker"


def _null_sink(raw: bytes) -> None:
    """团队路径不需要 IPC 帧：Trace 直接落盘，帧只作空转出口。"""
    return None

#: 契约 §5：评分总预算（含格式重试），超时按 judge_error 记，不无限等。
JUDGE_BUDGET_S = 60

#: run 的终态：只有终态运行才能进入比较。
_TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "cancelled", "interrupted", "timed_out"}
)

#: 评分实现的可注入点：attempt 评分与校准项评分共用，生产默认是评分子进程。
JudgeRunner = Callable[..., Awaitable[JudgeResult]]

#: 团队执行注入点：gateway 把进程内团队执行栈接进来，评估服务不感知团队内部。
TeamRunner = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
#: 团队冻结快照来源：按 team_id 返回已冻结的团队定义/Leader/成员档案。
#: 缺省时直接读本机团队库；测试注入替身，避免碰日常团队库。
TeamSnapshotProvider = Callable[[str], Mapping[str, Any]]
#: 隔离团队运行时工厂：按 attempt 上下文产出运行时；生产由 gateway 注入。
TeamRuntimeFactory = Callable[..., Any]


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
        judge_runner: JudgeRunner | None = None,
        team_runner: TeamRunner | None = None,
        team_snapshot_provider: TeamSnapshotProvider | None = None,
        team_runtime_factory: TeamRuntimeFactory | None = None,
        workspace: Path | None = None,
    ) -> None:
        self._repository = repository
        self._data_dir = Path(data_dir)
        self._workspace = (
            Path(workspace) if workspace is not None else get_workspace_path()
        )
        self._profiles = {profile.id: profile for profile in profiles} or {
            profile.id: profile for profile in default_profiles()
        }
        # Judge 与 Agent 共用的进程预算；与调度器传入的是同一个对象。
        self._judge_slots = judge_slots or asyncio.Semaphore(3)
        self._judge_runner: JudgeRunner = judge_runner or self._run_judge
        self._team_runner = team_runner
        self._team_snapshot_provider = team_snapshot_provider
        self._team_runtime_factory = team_runtime_factory

    # ------------------------------------------------------------------ 配置

    @property
    def repository(self) -> EvaluationRepository:
        return self._repository

    @property
    def profiles(self) -> tuple[Profile, ...]:
        """已装配的执行/评分档案（不含凭据：bundle 只在服务内存里）。"""
        return tuple(self._profiles.values())

    @property
    def runtime_root(self) -> Path:
        return self._data_dir / "runs"

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def import_sample(self, sample_id: str) -> dict[str, Any]:
        """内置样本随包发布：JSON 只读，导入幂等由仓储的 sample_imports 表负责。

        带判据元数据的样本（reliability-v2）先过适配器：元数据进 tags 与描述，
        判据原样保留，gold 不入库（人工确认走校准通道）。
        """
        from nanobot.evaluation.sample_cases import (
            ADAPTED_SAMPLE_IDS,
            adapt_sample_payload,
        )

        path = self._sample_path(sample_id)
        if not path.is_file():
            raise ServiceError("SAMPLE_NOT_FOUND", sample_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if sample_id in ADAPTED_SAMPLE_IDS:
            try:
                payload = adapt_sample_payload(payload)
            except ValueError as exc:
                raise ServiceError("SAMPLE_INVALID", f"{sample_id}: {exc}") from exc
        return self._repository.import_sample(sample_id, payload)

    def seed_builtin_gold_samples(self) -> int:
        """首启把内置 gold 写成 ``proposed`` 建议标签；已有数据时绝不覆盖。

        ``seed_gold_samples`` 是 ``INSERT OR REPLACE``：重复执行会把手动确认过的
        ``confirmed`` 打回 ``proposed``，所以这里以「空表」为唯一执行条件，重启不重置标签。
        """
        if self._repository.list_gold_samples()["data"]:
            return 0
        payload = json.loads(self._sample_path("calibration-v1").read_text(encoding="utf-8"))
        samples = payload.get("samples")
        if payload.get("schema_version") != 1 or not isinstance(samples, list) or not samples:
            raise ServiceError("SAMPLE_INVALID", "calibration-v1")
        return self._repository.seed_gold_samples(
            [dict(item) for item in samples],
            gold_revision=int(payload.get("gold_revision", 1)),
        )

    @staticmethod
    def _sample_path(sample_id: str) -> Path:
        return Path(__file__).parent / "samples" / f"{sample_id}.json"

    def list_gold_samples(self) -> dict[str, Any]:
        return self._repository.list_gold_samples()

    def profile(self, profile_id: str) -> Profile:
        profile = self._profiles.get(profile_id)
        if profile is None:
            legacy_aliases = {
                "agent-1": "controlled-agent",
                "judge-1": "controlled-judge",
            }
            profile = self._profiles.get(legacy_aliases.get(profile_id, ""))
        if profile is None:
            raise ServiceError("PROFILE_NOT_FOUND", profile_id)
        return profile

    def _bundle_for(self, profile_id: str) -> dict[str, Any]:
        """凭据只进内存：快照里没有 bundle，执行时从启动时装入的 profile 重建。

        gateway 重启后 bundle 由启动时的 config 重建，rerun 依旧可用；profile 已不
        存在（如换了一套预设）时给空 config，让下游按「无凭据」显式失败而不是 KeyError。
        """
        profile = self._profiles.get(profile_id)
        bundle = dict(profile.bundle or {}) if profile is not None else {}
        bundle.setdefault("config", {})
        return bundle

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
        frozen_target: Mapping[str, Any] | None = None,
        frozen_cases: Sequence[Mapping[str, Any]] | None = None,
        effective_limits: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """冻结快照并幂等入队。入队即返回 202，真正的执行由调度器接手。

        effective_limits 是 run 级覆盖里显式设置的项目；为 None 时按请求里的
        limits 推断（未显式设置的项不覆盖用例限制）。

        ``frozen_target`` 供 rerun 透传源运行已冻结的目标载荷：重跑必须引用**原
        冻结快照**，不能按当前工作区重新冻结一遍。
        """
        agent = self.profile(request.agent_profile_id)
        judge = (
            self.profile(request.judge_profile_id) if request.judge_profile_id else None
        )
        if frozen_cases is None:
            request = self._normalize_model_mode(request)
        self._require_judge_when_needed(request, judge)
        target_payload = (
            dict(frozen_target) if frozen_target is not None
            else self._freeze_target(request.target)
        )
        snapshot_bundle = {
            "agent_profile": {
                "provider": agent.provider, "model": agent.model, "id": agent.id,
            },
            "judge_profile": None if judge is None else {
                "provider": judge.provider, "model": judge.model, "id": judge.id,
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
            target=target_payload,
            frozen_cases=frozen_cases,
            effective_limits=(
                dict(effective_limits)
                if effective_limits is not None
                else self._explicit_limits(request.limits)
            ),
        )

    @staticmethod
    def _explicit_limits(limits: RunLimits) -> dict[str, int]:
        """只取 run 里**显式设置**的限额项。

        未显式设置的项不参与收敛：RunLimits 的默认值 12/120 是占位而不是意图，
        拿它去收紧用例会把「用例允许 30 轮迭代」悄悄压回 12。
        """
        fields = limits.model_fields_set
        payload: dict[str, int] = {}
        if "max_iterations" in fields:
            payload["max_iterations"] = int(limits.max_iterations)
        if "timeout_s" in fields:
            payload["timeout_s"] = int(limits.timeout_s)
        return payload

    def _normalize_model_mode(self, request: RunCreate) -> RunCreate:
        """判定本次运行是真模型还是回放脚本（契约 §1）。

        三条硬规则：

        - 显式 real 遇到任何脚本 → 拒绝，不静默回放；
        - 显式 scripted 要求所有用例都有脚本；
        - live 工具模式带脚本一律拒绝——回放脚本在 live 工具下毫无意义，
          放行等于把"脚本结果"当成"真实工具结果"。

        未显式指定时：全部用例都有脚本才推导为 scripted，其余为 real。
        """
        cases = [self._repository.get_case(str(case_id)) for case_id in request.case_ids]
        scripted = [bool((case or {}).get("script")) for case in cases]
        live_with_script = any(
            has_script
            and str((case or {}).get("execution", {}).get("tool_mode")) == "live"
            for case, has_script in zip(cases, scripted, strict=True)
        )
        if live_with_script:
            raise ServiceError("MODEL_MODE_CONFLICT", "live tool mode cannot carry scripts")
        any_script = any(scripted)
        all_script = bool(scripted) and all(scripted)
        explicit = "model_mode" in request.model_fields_set
        mode = request.model_mode
        if not explicit:
            mode = "scripted" if all_script else "real"
        if mode == "real" and any_script:
            raise ServiceError("MODEL_MODE_CONFLICT", "real mode cannot replay scripts")
        if mode == "scripted" and not all_script:
            raise ServiceError("MODEL_MODE_CONFLICT", "scripted mode requires scripts on every case")
        return request.model_copy(update={"model_mode": mode})

    def _freeze_target(self, target: TargetRef | None) -> dict[str, Any] | None:
        """入队时冻结运行目标。

        team/dataset 只冻结引用；Agent/Skill 目标在入队时把档案与技能文件
        **内容**冻结进快照——之后源文件改动不影响本次运行。
        """
        if target is None:
            return None
        if target.kind == "agent":
            return self._freeze_agent_target(target.id)
        if target.kind == "skill":
            return self._freeze_skill_target(target.id)
        if target.kind == "team":
            return self._freeze_team_target(target.id)
        return {"kind": target.kind, "id": target.id}

    #: 目标冻结的文件总量上限：技能目录失控时快速失败，而不是拖垮快照与 boot。
    _TARGET_FILE_LIMIT = 512 * 1024

    def _freeze_agent_target(self, agent_id: str) -> dict[str, Any]:
        from nanobot.agents.store import AgentStore

        try:
            profile = AgentStore(self._workspace).get_profile(agent_id)
        except Exception as exc:  # noqa: BLE001 - 档案缺失/损坏统一按资源缺失报
            raise ServiceError("AGENT_NOT_FOUND", agent_id) from exc
        data = profile.to_dict()
        return {
            "kind": "agent",
            "id": agent_id,
            "agent": {
                "profile": data,
                "skills": self._freeze_skill_files(self._profile_skill_names(data)),
            },
        }

    def _freeze_team_target(self, team_id: str) -> dict[str, Any]:
        """冻结团队：revision + Leader + 成员档案/提示词/所需技能。

        评估运行不读共享团队库——入队时把团队定义与成员档案内容冻结进快照，
        之后源团队被改、被归档都不影响已入队的运行。库文件不存在时直接报
        TEAM_NOT_FOUND，不在用户工作区里凭空建库。
        """
        provider = self._team_snapshot_provider
        if provider is not None:
            try:
                payload = dict(provider(team_id))
            except ServiceError:
                raise
            except Exception as exc:  # noqa: BLE001 - 替身异常统一按资源缺失报
                raise ServiceError("TEAM_NOT_FOUND", team_id) from exc
        else:
            from nanobot.teams.database import TeamDatabase
            from nanobot.teams.repository import TeamRepository

            try:
                repository = TeamRepository(TeamDatabase(self._workspace))
                snapshot = repository.get_revision_snapshot(team_id)
            except Exception as exc:  # noqa: BLE001 - 团队缺失/未初始化统一按资源缺失报
                raise ServiceError("TEAM_NOT_FOUND", team_id) from exc
            payload = snapshot.to_dict()
        definition = payload.get("definition") or {}
        return {
            "kind": "team",
            "id": team_id,
            "team": {
                "revision": definition.get("revision"),
                "definition": definition,
                "leader": payload.get("leader"),
                "members": payload.get("members"),
            },
        }

    def _freeze_skill_target(self, skill_name: str) -> dict[str, Any]:
        return {
            "kind": "skill",
            "id": skill_name,
            "skill": {"name": skill_name, "files": self._freeze_skill_files([skill_name])},
        }

    def _profile_skill_names(self, profile: Mapping[str, Any]) -> list[str]:
        """档案的技能选择 ∩ 本机可用技能（语义与 agents.runtime 的白名单一致）。"""
        from nanobot.agent.skills import SkillsLoader
        from nanobot.agents.models import AgentSelection
        from nanobot.agents.runtime import resolve_selection

        selection = AgentSelection.from_dict(profile.get("skills"))
        available = [
            str(item["name"])
            for item in SkillsLoader(self._workspace).list_skills(filter_unavailable=False)
        ]
        return sorted(resolve_selection(selection, available))

    def _freeze_skill_files(self, names: Sequence[str]) -> dict[str, str]:
        """把技能目录的文本文件读成 {attempt workspace 相对路径: 内容}。

        技能目录必须与 SkillsLoader 的三级来源（workspace → 插件 → builtin）同源，
        否则选了内置技能的 Agent 档案在入队时就会假性 SKILL_NOT_FOUND。
        统一写成 ``skills/<name>/…``：workspace 技能与旧键一致，builtin/插件技能
        物化进 attempt workspace 后会被 worker 的 SkillsLoader 以同名优先加载，
        内容正是这份冻结快照。
        """
        from nanobot.agent.skills import SkillsLoader

        roots: dict[str, Path] = {}
        for item in SkillsLoader(self._workspace).list_skills(filter_unavailable=False):
            roots.setdefault(str(item["name"]), Path(str(item["path"])).parent)
        files: dict[str, str] = {}
        total = 0
        for name in names:
            base = roots.get(name)
            if base is None or not base.is_dir():
                raise ServiceError("SKILL_NOT_FOUND", name)
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                data = path.read_text(encoding="utf-8", errors="replace")
                total += len(data)
                if total > self._TARGET_FILE_LIMIT:
                    raise ServiceError("TARGET_TOO_LARGE", name)
                key = f"skills/{name}/{path.relative_to(base).as_posix()}"
                files[key] = data
        return files

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
        frozen_cases: list[dict[str, Any]] = []
        for attempt_id in attempt_ids:
            item = known.get(attempt_id)
            if item is None:
                raise ServiceError("ATTEMPT_NOT_RERUNNABLE", attempt_id)
            chosen.append(str(item["case_id"]))
            frozen_cases.append({
                "case_id": str(item["case_id"]),
                "case_revision": int(item["case_revision"]),
                "case_snapshot": dict(item["case_snapshot"]),
                "source_attempt_id": str(item["id"]),
            })
        snapshot = source["snapshot"]
        target_raw = snapshot.get("target")
        request = RunCreate(
            suite_id=source["suite_id"],
            case_ids=chosen,
            agent_profile_id=str(snapshot["agent_profile"]["id"]),
            judge_profile_id=(
                str(snapshot["judge_profile"]["id"])
                if snapshot.get("judge_profile") else None
            ),
            limits=RunLimits.model_validate(snapshot.get("limits") or {}),
            run_timeout_s=snapshot.get("run_timeout_s"),
            repetitions=1, model_mode=str(snapshot.get("model_mode") or "real"),
        )
        explicit = snapshot.get("explicit_limits")
        return self.create_run(
            request, idempotency_key=idempotency_key, scope=scope,
            parent_run_id=run_id,
            frozen_target=(
                dict(target_raw) if isinstance(target_raw, Mapping) else None
            ),
            frozen_cases=frozen_cases,
            effective_limits=(
                {str(k): int(v) for k, v in explicit.items()}
                if isinstance(explicit, Mapping)
                else {}
            ),
        )

    # -------------------------------------------------------------- 执行流水线

    async def execute(
        self, attempt: Mapping[str, Any], *, run: Mapping[str, Any]
    ) -> AttemptOutcome:
        """一次 attempt 的完整流水线；结论原样交给调度器提交。"""
        attempt_id = str(attempt["id"])
        case = CaseWrite.model_validate(attempt["case_snapshot"])
        snapshot = run.get("snapshot") or {}
        limits = _limits_of(snapshot)
        # 生效限额：冻结时已收敛过，这里再取一次更严值作为双保险，
        # 保证进程级超时、团队限额与 Worker 内部迭代上限用的是同一组数。
        max_iterations, timeout_s = _effective_attempt_limits(snapshot, case)
        limits = limits.model_copy(
            update={"max_iterations": max_iterations, "timeout_s": timeout_s}
        )
        target = snapshot.get("target")
        if isinstance(target, Mapping) and target.get("kind") == "team":
            return await self._execute_team(
                attempt_id=attempt_id, case=case, run=run, limits=limits,
            )
        paths = self._attempt_paths(attempt_id)
        paths.create()
        errors: list[AttemptError] = []
        boot = self._agent_boot(attempt_id, case, run, paths, limits=limits)
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

    async def _execute_team(
        self,
        *,
        attempt_id: str,
        case: CaseWrite,
        run: Mapping[str, Any],
        limits: RunLimits,
    ) -> AttemptOutcome:
        """团队目标：隔离运行时 + 真实 Trace，不再有强制完整旁路。

        与 Agent 路径的差异只在执行段：团队有自己的 Leader/成员栈，
        但 Trace、产物、评分与证据口径完全一致——
        缺 Trace 只能判待复核，不能靠开关把"没采到证据"说成"证据完整"。
        """
        started = time.monotonic()
        snapshot = run.get("snapshot") or {}
        target = snapshot.get("target") or {}
        errors: list[AttemptError] = []
        try:
            team = build_frozen_team(target if isinstance(target, Mapping) else None)
        except TeamRuntimeError as exc:
            return self._setup_failure([
                AttemptError(phase="setup", code=exc.code, message=str(exc)),
            ])
        factory = self._team_runtime_factory
        if factory is None:
            return self._setup_failure([
                AttemptError(
                    phase="setup", code="TEAM_RUNTIME_UNAVAILABLE",
                    message="未注入隔离团队运行时",
                ),
            ])

        paths = self._attempt_paths(attempt_id)
        paths.create()
        recorder = TraceRecorder(
            attempt_id=attempt_id,
            collector=TraceCollector(attempt_id=attempt_id, trace_dir=paths.trace_dir),
            channel=WorkerChannel(attempt_id=attempt_id, sink=_null_sink),
            started_at=started,
            trace_errors=errors,
        )
        execution_id = str(uuid.uuid4())
        recorder.execution_id = execution_id
        recorder.emit(
            "attempt.started",
            payload={
                "role": "team",
                "tool_mode": case.execution.tool_mode,
                "turns": len(case.turns),
                "team_id": team.team_id,
                "team_revision": team.revision,
            },
        )
        recorder.emit(
            "agent.started",
            target_kind="team",
            target_id=team.team_id,
            payload={
                "role": "team",
                "execution_id": execution_id,
                "capability": team.capability(),
                "turns": len(case.turns),
            },
        )
        request = TeamRunRequest(
            attempt_id=attempt_id,
            team=team,
            turns=tuple(turn.content for turn in case.turns),
            workspace=paths.workspace,
            timeout_s=limits.timeout_s,
            max_iterations=limits.max_iterations,
            allowed_tools=tuple(case.execution.allowed_tools),
        )
        try:
            runtime = factory(request=request, events=recorder)
            outcome = await runtime.execute()
        except Exception as exc:  # noqa: BLE001 - 运行时异常按执行失败收口
            errors.append(
                AttemptError(phase="execution", code="TEAM_RUNTIME_FAILED", message=str(exc))
            )
            outcome = TeamRunOutcome(status="failed", errors=[])
        status = str(outcome.status or "failed")
        stop_reason = outcome.stop_reason
        # 团队运行状态归一：partial 表示"跑完了但部分子任务失败"，
        # 执行阶段本身是终结的；非终态按执行失败收口，不能交给 finish_attempt。
        if status == "partial":
            status, stop_reason = "succeeded", "partial"
        elif status not in ("succeeded", "failed", "timed_out", "cancelled", "setup_error"):
            errors.append(
                AttemptError(
                    phase="execution", code="TEAM_RUN_STATUS_UNEXPECTED",
                    message=f"team run ended in non-terminal status {status!r}",
                )
            )
            status = "failed"
        if outcome.errors:
            for item in outcome.errors:
                errors.append(
                    AttemptError(
                        phase=str(item.get("phase") or "execution"),
                        code=str(item.get("code") or "TEAM_RUN_FAILED"),
                        message=str(item.get("message") or ""),
                    )
                )
        final_answer = outcome.final_answer
        recorder.emit(
            "agent.completed" if status == "succeeded" else "agent.failed",
            target_kind="team",
            target_id=team.team_id,
            payload={
                "execution_status": status,
                "stop_reason": stop_reason,
                "answer_chars": len(final_answer or ""),
                "answer_sha256": (
                    hashlib.sha256(final_answer.encode("utf-8")).hexdigest()
                    if final_answer
                    else None
                ),
            },
        )
        recorder.emit(
            "attempt.completed" if status == "succeeded" else "attempt.failed",
            payload={
                "execution_status": status,
                "stop_reason": stop_reason,
            },
        )
        errors.extend(recorder.trace_errors)
        trace_complete = True
        try:
            trace_complete = recorder.seal().trace_complete
        except TraceError as exc:
            trace_complete = False
            errors.append(AttemptError(phase="trace", code=exc.code, message=str(exc)))

        if status != "succeeded":
            return AttemptOutcome(
                execution_status=(
                    status
                    if status in ("failed", "timed_out", "cancelled", "setup_error")
                    else "failed"
                ),
                grading_status="not_applicable",
                verdict="failed" if status in ("failed", "timed_out") else "not_evaluated",
                result=_result_payload(
                    execution_status=status,
                    errors=errors,
                    worker_result={
                        "final_answer": final_answer,
                        "stop_reason": stop_reason,
                        "iterations": 0,
                        "trace_complete": trace_complete,
                    },
                    timing={"agent_ms": int((time.monotonic() - started) * 1000)},
                ),
            )

        self._repository.mark_attempt_execution_done(
            attempt_id=attempt_id, execution_status="succeeded"
        )
        self._repository.mark_attempt_grading_running(attempt_id)
        timing = {"agent_ms": int((time.monotonic() - started) * 1000)}

        class _TeamOwner:
            """_grade 只用 owner.timing()；团队路径没有 WorkerProcess 可言。"""

            def timing(self) -> dict[str, Any]:
                return dict(timing)

        return await self._grade(
            attempt_id=attempt_id, case=case, run=run, paths=paths,
            errors=errors, owner=_TeamOwner(),
            result={
                "final_answer": final_answer,
                "stop_reason": stop_reason,
                "iterations": 0,
                "trace_complete": trace_complete,
            },
        )

    def _agent_boot(
        self,
        attempt_id: str,
        case: CaseWrite,
        run: Mapping[str, Any],
        paths: Any,
        *,
        limits: RunLimits | None = None,
    ) -> AgentWorkerBoot:
        snapshot = run.get("snapshot") or {}
        profile = dict(snapshot.get("agent_profile") or {})
        # 凭据不再落入快照（S7）：执行时从启动时装入的 profile 重建。
        bundle = self._bundle_for(str(profile.get("id") or ""))
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
            target=(run.get("snapshot") or {}).get("target"),
            limits=limits,
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
        # Trace 完整只来自密封结果：没有旁路开关能把"没采到证据"说成完整。
        trace_complete = trace.trace_complete
        evidence = build_attempt_evidence(
            attempt_id=attempt_id,
            case_snapshot=case.model_dump(mode="json"),
            workspace=paths.workspace,
            trace_events=trace.events,
            artifacts=self._artifacts(attempt_id, paths),
            final_answer=_text(result, "final_answer"),
            stop_reason=_text(result, "stop_reason"),
            iterations=_iterations_of(result),
            trace_complete=trace_complete,
            execution_status="succeeded",
        )
        rules = evaluate_rules(case, evidence)
        required = [item.passed for item in rules if item.required]
        artifacts = self._artifacts(attempt_id, paths)
        coverage = compute_coverage(
            trace_events=trace.events,
            trace_sealed=trace.sealed,
            trace_complete=trace_complete,
            usage=result.get("usage") if isinstance(result, Mapping) else None,
            declared_artifacts=list(case.artifact_paths),
            registered_artifacts=[
                {"relative_path": item.relative_path} for item in artifacts
            ],
            required_evidence=(
                list(case.evaluation.required_evidence) if case.evaluation else []
            ),
            final_answer=_text(result, "final_answer"),
        )

        rubric = case.rubric
        judge_result: JudgeResult | None = None
        calibration: Mapping[str, Any] | None = None
        if rubric is not None:
            judge_result, calibration = await self.judge(
                attempt_id=attempt_id, rubric=rubric, evidence=evidence, run=run,
            )

        state = judge_state_of(rubric, judge_result, calibration)
        rule_results = [
            {
                "id": item.id,
                "passed": item.passed,
                "required": item.required,
                "message": item.message,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in rules
        ]
        # v2 用例（带 evaluation 判据）走五维度合格策略；
        # 老用例保持既有 verdict 口径，不冒充新评分。
        assessment = None
        if case.evaluation is not None:
            assessment = assess(
                evaluation=case.evaluation.model_dump(mode="json"),
                assertions=list(case.assertions),
                rule_results=rule_results,
                coverage=coverage,
                judge_state=state,
                execution_status="succeeded",
            )
            verdict = assessment.verdict
        else:
            verdict = derive_verdict("succeeded", required, state, trace_complete)
        payload = _result_payload(
            execution_status="succeeded",
            errors=errors,
            worker_result=result,
            timing=owner.timing(),
            assertions=rule_results,
            judge=judge_result.model_dump(mode="json") if judge_result else None,
        )
        payload["coverage"] = coverage
        if assessment is not None:
            payload["evaluation"] = assessment.to_dict()
        grading_status = "judge_error" if state == "error" and rubric is not None \
            and rubric.required else "completed"
        return AttemptOutcome(
            execution_status="succeeded",
            grading_status=grading_status,
            verdict=verdict,
            result=payload,
        )

    def _artifacts(self, attempt_id: str, paths: Any) -> tuple[EvidenceArtifact, ...]:
        """登记并索引**已声明**产物：唯一来源是 Worker 密封的产物索引。

        不能扫目录找文件：``artifact_dir`` 里只有索引本身，而扫 workspace 全量会把
        未声明的中间文件也当成产物证据。索引条目先过 ``resolve_inside`` 再做工作区
        包含判定——索引文件本身被篡改时也不会登记到工作区之外的文件。
        """
        index_path = Path(paths.artifact_dir) / ARTIFACT_INDEX_NAME
        if not index_path.is_file():
            return ()
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        entries = index.get("artifacts") if isinstance(index, Mapping) else None
        if not isinstance(entries, list):
            return ()
        workspace = Path(paths.workspace)
        found: list[EvidenceArtifact] = []
        for entry in entries:
            if not isinstance(entry, Mapping) or entry.get("kind") != "workspace":
                continue
            raw = entry.get("relative_path")
            if not isinstance(raw, str) or not raw:
                continue
            try:
                target = _resolve(Path(paths.root), raw)
            except ValueError:
                continue
            if not _inside(workspace, target) or not target.is_file():
                continue
            relative = target.relative_to(workspace.resolve()).as_posix()
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
    ) -> tuple[JudgeResult, Mapping[str, Any] | None]:
        """按需评分。Judge 阶段**重新申请**进程槽位，不嵌套持有 Agent 的槽位。"""
        snapshot = run.get("snapshot") or {}
        judge_ref = dict(snapshot.get("judge_profile") or {})
        judge_id = str(judge_ref.get("id") or "")
        # 快照里没有凭据（S7）：评分子进程的 bundle 同样在执行时从内存 profile 重建。
        profile = {
            "provider": judge_ref.get("provider"), "model": judge_ref.get("model"),
            "id": judge_id, "bundle": self._bundle_for(judge_id),
        }
        fingerprint = _fingerprint(profile, rubric)
        calibration = self._find_calibration(fingerprint, rubric)
        # 评分子进程必须有自己的 runtime/trace 目录：共用 agent 的 trace 目录会让
        # 评分事件追加到已密封的段上，既污染被测证据，又让 judge 自己 seal 失败。
        paths = self._judge_paths(attempt_id)
        paths.create()
        raw = await self._judge_runner(
            attempt_id=attempt_id, rubric=rubric, evidence=evidence,
            profile=profile, paths=paths,
        )
        return (
            JudgeResult(
                **raw.model_dump(exclude={"calibrated"}),
                # 未校准就是未校准：不在这里替用户把分数变成可信。
                calibrated=bool(calibration and calibration.get("passed")),
            ),
            calibration,
        )

    async def _run_judge(
        self,
        *,
        attempt_id: str,
        rubric: Rubric,
        evidence: AttemptEvidence,
        profile: Mapping[str, Any],
        paths: Any,
    ) -> JudgeResult:
        """默认评分实现：拉起评分子进程并回解析结果。

        Judge 实现在 :attr:`judge_runner` 处注入——attempt 评分与校准项评分共用这一处，
        离线测试可换成受控回放而不必为每个用例付出一次子进程。
        """
        boot = JudgeWorkerBoot(
            type="boot",
            protocol_version=1,
            attempt_id=attempt_id,
            runtime_root=str(paths.runtime_root),
            workspace=str(paths.workspace),
            # 注入点拿到的 profile 已带重建后的 bundle（快照不再存凭据，S7）；
            # build_attempt_config 要求 config 一定是对象：空预设也要给 {}。
            profile_bundle=_boot_bundle(profile),
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
                )
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
            )
        return JudgeResult(
            status="completed",
            model=str(payload.get("model") or profile.get("model", "")),
            version=str(payload.get("version") or "judge-v1"),
            dimensions=dimensions,
            calibrated=False,
        )

    def _find_calibration(
        self, fingerprint: str, rubric: Rubric
    ) -> Mapping[str, Any] | None:
        with contextlib.suppress(Exception):
            # gold 版本必须带上：旧版本的校准结论在新标签下不成立。
            return lookup_calibration(
                self._repository,
                judge_fingerprint=fingerprint,
                rubric_version=rubric_version(rubric),
                gold_revision=self._repository.gold_revision(),
            )
        return None

    # ------------------------------------------------------------------ 校准

    def _gold_rows(self, sample_ids: Sequence[str]) -> list[dict[str, Any]]:
        rows = {
            str(row["id"]): dict(row)
            for row in self._repository.list_gold_samples()["data"]
        }
        missing = [sample_id for sample_id in sample_ids if sample_id not in rows]
        if missing:
            raise CalibrationError(f"GOLD_SAMPLE_NOT_FOUND: {missing}")
        return [rows[sample_id] for sample_id in sample_ids]

    @staticmethod
    def _job_rubric(rows: Sequence[Mapping[str, Any]]) -> Rubric:
        """作业内所有 gold 必须共用同一份量表。

        指纹与一致率都是「按量表」算的：混用量表等于把不同标尺的成绩平均成一个数，
        得到的 agreement 没有意义。
        """
        bodies = {
            json.dumps(rubric_payload(row.get("rubric") or {}), sort_keys=True, ensure_ascii=False)
            for row in rows
        }
        if len(bodies) > 1:
            raise CalibrationError("GOLD_RUBRIC_MISMATCH: 校准样本的量表不一致")
        if not rows:
            raise CalibrationError("GOLD_SAMPLES_EMPTY: 校准作业没有任何样本")
        return Rubric.model_validate(rows[0].get("rubric") or {})

    def _judge_profile(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        profile = self.profile(str(snapshot.get("judge_profile_id") or ""))
        return {
            "provider": profile.provider,
            "model": profile.model,
            "bundle": dict(profile.bundle or {}),
        }

    def _calibration_paths(self, item_id: str) -> Any:
        base = self.runtime_root / "calibrations" / item_id
        return self._layout(item_id, base), self._layout(item_id, base.with_name(f"{item_id}-judge"))

    async def calibrate(self, claimed: Mapping[str, Any]) -> CalibrationItemOutcome:
        """执行**一个**已领取的校准项；调度器是唯一提交者。

        证据只认 gold 行里冻结的那份 ``evidence``：不重跑 attempt，也不接受调用方递进来的
        证据对象——否则一致率就变成自己给自己打分。标签与量表版本的前置校验在评分之前，
        失败按项记 failed，绝不写成「跑完了但一致率偏低」。
        """
        snapshot = dict(claimed.get("snapshot") or {})
        sample_ids = [str(item) for item in snapshot.get("sample_ids") or ()]
        item_id = str(claimed["id"])
        position = int(claimed["position"])
        if not 1 <= position <= len(sample_ids):
            raise CalibrationError(f"CALIBRATION_ITEM_OUT_OF_RANGE: position={position}")
        frozen = int(snapshot.get("gold_revision", -1))
        if frozen != self._repository.gold_revision():
            raise CalibrationError(
                f"GOLD_REVISION_STALE: 作业冻结 {frozen}，当前 {self._repository.gold_revision()}"
            )
        rows = self._gold_rows(sample_ids)
        row = rows[position - 1]
        unconfirmed = sorted(
            str(item["id"]) for item in rows if item.get("label_status") != "confirmed"
        )
        if unconfirmed:
            raise CalibrationError(
                f"GOLD_NOT_CONFIRMED: {len(unconfirmed)} 条标签仍是建议状态，需用户确认后才能校准"
            )
        rubric = self._job_rubric(rows)
        paths, judge_paths = self._calibration_paths(item_id)
        paths.create()
        judge_paths.create()
        evidence = evidence_from_payload(row.get("evidence") or {}, workspace=paths.workspace)
        graded = await self._judge_runner(
            attempt_id=item_id,
            rubric=rubric,
            evidence=evidence,
            profile=self._judge_profile(snapshot),
            paths=judge_paths,
        )
        actual = verdict_from_scores(rubric, graded.dimensions)
        matched = graded.status == "completed" and actual == row.get("expected_verdict")
        return CalibrationItemOutcome(
            status="completed" if graded.status == "completed" else "failed",
            result={
                "id": str(row["id"]),
                "expected_verdict": row.get("expected_verdict"),
                "actual_verdict": actual,
                "judge_status": graded.status,
                "matched": bool(matched),
                "error": graded.error,
            },
        )

    def settle_calibration(self, job_id: str) -> None:
        """把作业结论并进 ``calibration_results``（门禁查的表），并补指纹与错误。

        只有作业真的 ``completed`` 才写结论：取消、中断、任一项失败都保持 ``passed=null``，
        宁可「未校准」也不要一个假的一致率。幂等：指纹已写入就直接返回。
        """
        job = self._repository.get_calibration(job_id)
        snapshot = dict(job.get("snapshot") or {})
        result = dict(job.get("result") or {})
        errors = [
            str(item["result"]["error"])
            for item in self._repository.list_calibration_items(job_id)
            if item["status"] != "completed" and item["result"].get("error")
        ]
        if job["status"] != "completed":
            if errors:
                self._repository.save_calibration_job_result(
                    job_id, {"error": "; ".join(errors)}
                )
            return
        if result.get("judge_fingerprint"):
            return
        try:
            rows = self._gold_rows(
                [str(item) for item in snapshot.get("sample_ids") or ()]
            )
            rubric = self._job_rubric(rows)
            fingerprint = judge_fingerprint(profile=self._judge_profile(snapshot), rubric=rubric)
        except (CalibrationError, ServiceError) as exc:
            # 作业已完成但结论算不出来：留错误、不写门禁行，保持未校准。
            self._repository.save_calibration_job_result(job_id, {"error": str(exc)})
            return
        recorded = self._repository.record_calibration_result(
            judge_fingerprint=fingerprint,
            rubric_version=rubric_version(rubric),
            gold_revision=int(snapshot.get("gold_revision", 0)),
            total=int(result.get("total", 0)),
            matched=int(result.get("matched", 0)),
        )
        self._repository.save_calibration_job_result(
            job_id,
            {
                "judge_fingerprint": recorded["judge_fingerprint"],
                "rubric_version": recorded["rubric_version"],
                "gold_revision": recorded["gold_revision"],
                "total": recorded["total"],
                "matched": recorded["matched"],
                "agreement": recorded["agreement"],
                "passed": recorded["passed"],
                "error": "; ".join(errors) or None,
            },
        )

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
            "terminal_count": max(
                0,
                int(detail["planned_count"])
                - self._repository.run_counts(run_id).get("pending", 0),
            ),
        })

    def run_read(self, run_id: str) -> dict[str, Any]:
        detail = self._repository.get_run(run_id)
        summary = self.run_summary(run_id)
        attempts = self._repository.list_run_attempts(run_id, include_snapshot=True)
        metrics = aggregate_run(
            [normalize_attempt_for_metrics(item) for item in attempts],
            summary["planned_count"],
        )
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
                "manifest": dict(snapshot.get("manifest") or {}),
                "repetitions": int(snapshot.get("repetitions") or 1),
                "model_mode": str(snapshot.get("model_mode") or "real"),
                "tool_modes": list(snapshot.get("tool_modes") or []),
            },
            "evaluation_capability": evaluation_capability(
                manifest=snapshot.get("manifest"),
                cases_have_criteria=any(
                    bool((item.get("case_snapshot") or {}).get("evaluation"))
                    for item in detail["attempts"]
                ),
            ),
            "coverage": summarize_coverage([
                dict((item.get("result") or {}).get("coverage") or {})
                for item in detail["attempts"]
            ]),
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
            "trial_index": int(row.get("trial_index") or 0),
            "source_attempt_id": row.get("source_attempt_id"),
            "evidence_refs": list(result.get("evidence_refs") or []),
            "coverage": result.get("coverage"),
        }

    # ------------------------------------------------------------------ 比较

    def create_comparison(
        self,
        request: ComparisonCreate,
        *,
        idempotency_key: str,
        scope: str,
    ) -> dict[str, Any]:
        """严格比较：先校验可比性，再逐 case 配对。

        不可比时**不生成比较记录**：把不兼容的两次运行存成"比较"，
        会让读者以为已经比过了。
        """
        baseline = self._repository.get_run(str(request.baseline_run_id))
        candidate = self._repository.get_run(str(request.candidate_run_id))
        for label, run in (("baseline", baseline), ("candidate", candidate)):
            if str(run.get("status")) not in _TERMINAL_RUN_STATUSES:
                raise ServiceError(
                    "COMPARISON_RUN_NOT_TERMINAL",
                    f"{label} run is not terminal",
                )
        result = build_comparison(
            comparison_id="",
            baseline_run=baseline,
            candidate_run=candidate,
            baseline_attempts=self._repository.list_run_attempts(
                str(request.baseline_run_id)
            ),
            candidate_attempts=self._repository.list_run_attempts(
                str(request.candidate_run_id)
            ),
            mode=request.mode,
        )
        if result.compatibility != "strict":
            code = (
                "COMPARISON_METADATA_MISSING"
                if any("METADATA_MISSING" in item for item in result.reasons)
                else "COMPARISON_INCOMPATIBLE"
            )
            raise ServiceError(code, ";".join(result.reasons))
        stored = self._repository.create_comparison(
            scope=scope,
            idempotency_key=idempotency_key,
            baseline_run_id=str(request.baseline_run_id),
            candidate_run_id=str(request.candidate_run_id),
            mode=request.mode,
            compatibility=result.compatibility,
            result=result.to_dict(),
        )
        return dict(stored.get("result") or {})

    def comparison_read(self, comparison_id: str) -> dict[str, Any]:
        stored = self._repository.get_comparison(comparison_id)
        return dict(stored.get("result") or {})

    def comparison_report(self, comparison_id: str) -> str:
        """自包含 HTML 报告：与 API 读模型同源，不另算一套数。"""
        stored = self._repository.get_comparison(comparison_id)
        result = dict(stored.get("result") or {})
        rows = [
            [
                str(pair.get("case_id") or "")[:8],
                str(pair.get("status") or ""),
                str((pair.get("baseline") or {}).get("success_rate")),
                str((pair.get("candidate") or {}).get("success_rate")),
                str(pair.get("delta_success_rate")),
            ]
            for pair in (result.get("pairs") or [])
        ]
        metrics = result.get("metrics") or {}
        return render_summary_html(
            title="评估配对比较",
            generated_at=datetime.now(timezone.utc).isoformat(),
            sections=[
                {
                    "heading": "比较范围",
                    "headers": ["项", "值"],
                    "rows": [
                        ["baseline", str(result.get("baseline_run_id") or "")],
                        ["candidate", str(result.get("candidate_run_id") or "")],
                        ["可比性", str(result.get("compatibility") or "")],
                        ["配对 case", str(metrics.get("compared_cases"))],
                        ["改进 / 退步 / 不变", (
                            f"{metrics.get('improved_cases')} / "
                            f"{metrics.get('regressed_cases')} / "
                            f"{metrics.get('unchanged_cases')}"
                        )],
                        ["成功率差", str(metrics.get("task_success_rate_delta"))],
                    ],
                },
                {
                    "heading": "逐 case",
                    "headers": ["case", "状态", "baseline", "candidate", "差值"],
                    "rows": rows or [["—", "—", "—", "—", "—"]],
                },
            ],
        )

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

    # ---------------------------------------------------------------- 观测读模型

    def _trace_events(self, attempt_id: str) -> tuple[list[Any], bool]:
        """读取 attempt 的全部 Trace 事件；目录缺失或损坏时返回空表而不是抛错。"""
        paths = self._paths_for(attempt_id)
        trace = read_trace(paths.trace_dir) if paths is not None else None
        if trace is None:
            return [], False
        return sorted(trace.events, key=lambda event: event.seq), bool(trace.sealed)

    def execution_page(
        self, attempt_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> dict[str, Any]:
        """执行图分页；旧 Trace 缺身份时降级 coverage，不虚构父子关系。"""
        events, sealed = self._trace_events(attempt_id)
        return build_execution_page(
            events,
            after_seq=after_seq,
            limit=limit,
            sealed=sealed,
            attempt_id=attempt_id,
        )

    def tool_calls_page(
        self,
        attempt_id: str,
        *,
        execution_id: str | None = None,
        phase_id: str | None = None,
        name: str | None = None,
        status: str | None = None,
        q: str | None = None,
        after_key: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """工具调用分页；``total_count`` 是过滤后的全量条数，与当前页长度无关。"""
        events, _sealed = self._trace_events(attempt_id)
        return build_tool_calls_page(
            events,
            attempt_id=attempt_id,
            execution_id=execution_id,
            phase_id=phase_id,
            name=name,
            status=status,
            q=q,
            after_key=after_key,
            limit=limit,
        )

    def _run_attempt_events(self, run_id: str) -> list[tuple[str, list[Any]]]:
        attempts = self._repository.list_run_attempts(run_id)
        out: list[tuple[str, list[Any]]] = []
        for row in attempts:
            attempt_id = str(row["id"])
            events, _sealed = self._trace_events(attempt_id)
            out.append((attempt_id, events))
        return out

    def run_usage_page(
        self,
        run_id: str,
        *,
        group_by: str = "role",
        after_key: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """整轮 Token 主账本；``totals`` 覆盖全轮而不是当前页求和。"""
        self._repository.get_run(run_id)
        return build_usage_page(
            self._run_attempt_events(run_id),
            run_id=run_id,
            group_by=group_by,  # type: ignore[arg-type]
            after_key=after_key,
            limit=limit,
        )

    def run_report_snapshot(self, run_id: str) -> dict[str, Any]:
        """组装离线 HTML 报告用的快照；页面与报告消费同一份派生数据。"""
        detail = self._repository.get_run(run_id)
        attempts_raw = self._repository.list_run_attempts(run_id, include_snapshot=True)
        summary = self.run_summary(run_id)
        snapshot = detail.get("snapshot") or {}
        profile = snapshot.get("agent_profile") or {}
        attempts: list[dict[str, Any]] = []
        execution: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        for row in attempts_raw:
            attempt_id = str(row["id"])
            normalized = normalize_attempt_for_metrics(row)
            attempts.append(
                {
                    "id": attempt_id,
                    "position": normalized.get("position"),
                    "case_id": normalized.get("case_id"),
                    "case_revision": normalized.get("case_revision"),
                    "case_name": normalized.get("case_name"),
                    "execution_status": normalized.get("execution_status"),
                    "grading_status": normalized.get("grading_status"),
                    "verdict": normalized.get("verdict"),
                    "errors": normalized.get("errors") or [],
                    "judge_summary": _judge_summary(normalized.get("judge")),
                    "usage": normalized.get("usage"),
                    "timing": normalized.get("timing") or {},
                    "trace_complete": bool(normalized.get("trace_complete")),
                }
            )
            events, sealed = self._trace_events(attempt_id)
            page = build_execution_page(
                events, after_seq=0, limit=1000, sealed=sealed, attempt_id=attempt_id
            )
            execution.append(
                {
                    "attempt_id": attempt_id,
                    "nodes": page["data"]["nodes"],
                    "edges": page["data"]["edges"],
                    "coverage": page["coverage"],
                }
            )
            tool_calls.extend(build_tool_calls(events, attempt_id=attempt_id))
        # 报告的 Token 表要能看出「哪个智能体花了多少」：按 agent 归组，
        # Judge 单独成组，避免与执行智能体合并成一行。
        usage = build_usage_page(
            self._run_attempt_events(run_id),
            run_id=run_id,
            group_by="agent",
            limit=200,
        )
        return {
            "run": {
                "id": str(detail["id"]),
                "suite_id": str(detail.get("suite_id") or ""),
                "suite_name": self._repository.get_suite_name(str(detail.get("suite_id") or "")),
                "status": str(detail.get("status") or ""),
                "model": str(profile.get("model") or ""),
                "mode": str(snapshot.get("tool_mode") or ""),
                "created_at": detail.get("created_at"),
                "started_at": detail.get("started_at"),
                "finished_at": detail.get("finished_at"),
                "planned_count": summary.get("planned_count"),
                "terminal_count": summary.get("terminal_count"),
            },
            "attempts": attempts,
            "execution": execution,
            "tool_calls": tool_calls,
            "usage": {"totals": usage["totals"], "rows": usage["data"]},
            "options": {
                "max_tool_rows_per_table": 200,
                "max_total_tool_rows": 10000,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    def _paths_for(self, attempt_id: str) -> Any | None:
        try:
            return self._attempt_paths(attempt_id)
        except ValueError:
            return None

    def _attempt_paths(self, attempt_id: str) -> Any:
        """每个 attempt 独占 ``runtime/``、``workspace/``、``trace/``、``artifacts/``。

        共享 trace/artifact 目录会让并发 attempt 的密封索引与日志段互相覆盖：
        证据串台之后判分结论就不可信了，所以布局必须是 attempt 私有。
        """
        base = self.runtime_root / attempt_id
        return self._layout(attempt_id, base)

    def _judge_paths(self, attempt_id: str) -> Any:
        """评分阶段同样独占一套目录；attempt_id 仍是被测 attempt 的 id。"""
        return self._layout(attempt_id, self.runtime_root / f"{attempt_id}-judge")

    def _layout(self, attempt_id: str, base: Path) -> Any:
        return attempt_paths(
            attempt_id=attempt_id,
            runtime_root=str(base / "runtime"),
            workspace=str(base / "workspace"),
        )

    def overview(self) -> dict[str, Any]:
        recent = self._repository.list_runs(limit=10)
        counts: dict[str, int] = {}
        for row in self._repository.list_runs(limit=1000):
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        latest = next((row for row in recent if row["status"] != "completed"), None) \
            or (recent[0] if recent else None)
        metrics = None
        if latest is not None:
            attempts = self._repository.list_run_attempts(
                str(latest["id"]), include_snapshot=True
            )
            metrics = aggregate_run(
                [normalize_attempt_for_metrics(item) for item in attempts],
                int(latest["planned_count"]),
            )
        return {
            "recent_runs": [_summary(row) for row in recent],
            "metrics": metrics,
            "metrics_run_id": str(latest["id"]) if latest is not None else None,
            "metrics_scope": "single_run" if latest is not None else None,
            "status_counts": counts,
        }

    def summary_report(self, suite_id: str) -> str:
        """数据集维度的 HTML 汇总报告：按 suite 聚合全部运行与用例最新结论。"""
        from nanobot.evaluation.reports import render_summary_html

        total_run_count = self._repository.count_runs(suite_id=suite_id)
        runs = self._repository.list_runs(suite_id=suite_id, limit=100)
        run_rows: list[list[str]] = []
        case_rows: dict[str, list[str]] = {}
        for row in runs:
            run_id = str(row["id"])
            attempts = self._repository.list_run_attempts(run_id)
            counts: dict[str, int] = {"passed": 0, "failed": 0, "review_required": 0}
            for attempt in attempts:
                verdict = str(attempt.get("verdict") or "not_evaluated")
                if verdict in counts:
                    counts[verdict] += 1
                case_id = str(attempt.get("case_id") or "")
                name = str(attempt.get("case_name") or case_id or "?")
                revision = str(attempt.get("case_revision") or "-")
                entry = case_rows.setdefault(
                    case_id, [name, case_id[:8], revision, "-", "-"]
                )
                # list_runs 已按 created_at DESC 排序：第一次遇到的 verdict 就是最新结论。
                # 历史运行（哪怕失败过）不得覆盖它，否则「修复后重跑通过」永远显示 failed。
                if entry[3] == "-":
                    entry[3], entry[4] = verdict, run_id[:8]
            run_rows.append([
                run_id[:8], str(row.get("status") or ""), str(len(attempts)),
                str(counts["passed"]), str(counts["failed"]),
                str(counts["review_required"]),
            ])
        return render_summary_html(
            title=f"评估汇总 · 数据集 {self._repository.get_suite_name(suite_id)}",
            generated_at=datetime.now(timezone.utc).isoformat(),
            sections=[
                {
                    "heading": "报告范围",
                    "headers": ["范围", "已选择运行", "总运行", "是否截断"],
                    "rows": [[
                        "数据集历史（最近运行）",
                        str(len(runs)),
                        str(total_run_count),
                        "是" if total_run_count > len(runs) else "否",
                    ]],
                },
                {
                    "heading": "运行",
                    "headers": ["运行", "状态", "用例数", "通过", "失败", "待复核"],
                    "rows": run_rows,
                },
                {
                    "heading": "用例最新结论",
                    "headers": ["用例", "用例 ID", "版本", "最新结论", "最近运行"],
                    "rows": sorted(case_rows.values(), key=lambda item: (item[0], item[1])),
                },
            ],
        )

    def export_run(self, run_id: str, *, fmt: str) -> tuple[str, str, str]:
        # 先确认运行存在：导出不能给不存在的运行编一个空文件。
        detail = self._repository.get_run(run_id)
        attempts = self._repository.list_run_attempts(run_id, include_snapshot=True)
        summary = self.run_summary(run_id)
        metrics = aggregate_run(
            [normalize_attempt_for_metrics(item) for item in attempts],
            int(summary["planned_count"]),
        )
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


def _effective_attempt_limits(
    snapshot: Any, case: CaseWrite
) -> tuple[int, int]:
    """返回这次 attempt 真正生效的 (max_iterations, timeout_s)。

    新快照在冻结时已经把 run 级覆盖按更严值收敛进用例快照，这里再取一次更严值
    只是双保险；旧快照没有收敛记录，那时 run 级限额是唯一覆盖来源，按它收紧。
    """
    max_iterations = int(case.execution.max_iterations)
    timeout_s = int(case.execution.timeout_s)
    explicit = snapshot.get("explicit_limits") if isinstance(snapshot, Mapping) else None
    if isinstance(explicit, Mapping):
        override_iterations = explicit.get("max_iterations")
        override_timeout = explicit.get("timeout_s")
        if isinstance(override_iterations, int):
            max_iterations = min(max_iterations, override_iterations)
        if isinstance(override_timeout, int):
            timeout_s = min(timeout_s, override_timeout)
        return max_iterations, timeout_s
    limits = _limits_of(snapshot)
    return min(max_iterations, limits.max_iterations), min(timeout_s, limits.timeout_s)


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
        for key in ("final_answer", "stop_reason", "iterations", "evidence_refs"):
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


def _judge_summary(judge: Any) -> dict[str, Any] | None:
    """判分结果只留口径需要的字段：结论、评分与理由摘要。

    原始 judge payload 可能包含维度明细与长文本，报告里只需要能复核的结论。
    """
    if not isinstance(judge, Mapping):
        return None
    summary: dict[str, Any] = {}
    for key in ("verdict", "passed", "score", "reason", "reasoning", "agreement"):
        if key in judge:
            value = judge[key]
            if isinstance(value, str) and len(value) > 240:
                value = value[:239] + "…"
            summary[key] = value
    return summary or None


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


def _iterations_of(result: Mapping[str, Any] | None) -> int:
    """iterations 在 worker 结果帧的 ``usage`` 下，不在顶层；读错位置会让 iteration_limit 恒过。"""
    if result is None:
        return 0
    usage = result.get("usage")
    value = usage.get("iterations") if isinstance(usage, Mapping) else None
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
        # 证据属于被测 attempt：Judge 引用校验必须用它自己的工作区，
        # 评分 scratch 目录只负责进程隔离，不承载证据。
        "workspace": str(evidence.workspace),
    }


def _boot_bundle(profile: Mapping[str, Any]) -> dict[str, Any]:
    """把注入点给的 profile 载荷收敛成 boot 可用的 bundle（config 缺省补空对象）。"""
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
