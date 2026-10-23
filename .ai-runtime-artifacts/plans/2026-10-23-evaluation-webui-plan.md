---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
dispatch: n/a（首批 GROUP-1 → GROUP-2 串行，Leader 直做；需要并行时补同 stem dispatch）
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-10-15-agent-evaluation-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-15-contract-agent-evaluation.md
  - .ai-runtime-artifacts/plans/2026-10-15-agent-evaluation-plan.md
created_at: 2026-10-23
status: draft
approved: false
---

# 评估模块 WebUI 与目标体系实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在桌面端 Settings 新增「评估」页，支持四类测试目标（Agent 档案 / 团队 / Skill / Excel 数据集）、真实模型、总运行时限（可不限时）、数据集 Excel 导入导出与 HTML 汇总报告。

**Architecture:** 评估服务继续作为独立 aiohttp 应用，但由 gateway 进程内嵌启动（127.0.0.1:8790，随 gateway 启停），口令复用 WebUI bootstrap secret，CORS 精确白名单。评估服务新增 `target` 快照维度与 `run_timeout_s`；Agent/Skill 目标在 worker 子进程内通过「冻结档案/技能文件 + `extra_metadata` 注入 agent_id」落地；团队目标由 gateway 注入 `team_runner` 回调在进程内执行；真实模型 profile 由 gateway 从 `model_presets` + `providers` 构建，凭据只进内存 boot（S7 修复）。前端复用现有 Settings 体系（contracts + sidebar + SettingsPage switch），数据面走 `evaluation-api.ts`。

**Tech Stack:** Python 3.11+ / aiohttp / pydantic / openpyxl（已在依赖中）；React 18 + TypeScript + Vite + Vitest；i18next（10 locale 必须同形）。

---

## 0. 基线与现状（开工前必读）

- 工作树：`D:/studyspace/project/.harness-worktrees/Zhixu/wt-agent-evaluation`，分支 `harness/agent-evaluation-calibration`。
- 已合并 `feature/desktop-app`（merge `627bc9f`），基线：`pytest tests/evaluation -q` → **702 passed / 5 failed**（5 条全部是 `test_calibration_jobs.py` 的 WIP，与本计划无关，**不要去修**）。
- 本计划开始前工作树已有未提交改动（不要回滚，作为 WU-09 的一部分收口）：
  - `nanobot/evaluation/server.py`：新增 `_cors_middleware` + `create_app` 中间件接入 + `create_eval_app(allowed_origins=...)`。
  - `nanobot/cli/gateway_runtime.py`：`_start_embedded_evaluation` + run 生命周期接线（WU-09 收口）。
  - `tests/evaluation/test_runtime_factory.py`：Windows 锁文件读取 lenient 修复。
- 既有失败清单（不要当成自己的回归）：`test_calibration_jobs.py` 5 条；WebUI `i18n.test.tsx` 的 `rosterCount` 缺键等见主 checkout 既有清单。
- 命令前缀：后端 `.venv/Scripts/python.exe -m pytest`、`.venv/Scripts/python.exe -m ruff check`；前端 `cd webui && npx vitest run <file>`、`npx tsc -p tsconfig.build.json --noEmit`。本机无 bun，一律用 npm/npx。

## 文件结构总览

```
nanobot/evaluation/
  profiles.py            # 新：gateway config → 评估 Profile（含凭据 bundle）
  datasets.py            # 新：Excel 模板/解析/导出
  schemas.py             # 改：TargetRef、RunCreate.target/run_timeout_s、RunStatus.timed_out、
                         #     LIVE_ONLY_TOOLS 扩容、_BootBase.target
  services.py            # 改：S7 凭据不落库、_bundle_for、目标冻结、team_runner、总时限
  scheduler.py           # 改：run deadline watchdog、cancel_run(reason)
  repository.py          # 改：run 状态允许 timed_out
  worker.py              # 改：bot.run(metadata=...) 注入 agent_id
  runtime_factory.py     # 改：materialize target 文件、live exec 开关
  server.py              # 改：datasets/reports 路由（CORS 已就绪）
  reports.py             # 改：render_summary_html
nanobot/cli/gateway_runtime.py   # 改：profiles 注入（WU-09 收口）
nanobot/agent/loop.py            # 改：process_direct(extra_metadata=...)
nanobot/nanobot.py               # 改：run(metadata=...) 透传
nanobot/webui/gateway_services.py# 改：build_team_eval_runner 注入
webui/src/lib/evaluation-api.ts  # 新
webui/src/components/settings/evaluation/*.tsx  # 新（4 个文件）
webui/src/components/settings/{contracts,SettingsSidebar,SettingsPage}.tsx  # 改
webui/src/i18n/locales/*/common.json  # 改（10 个）
webui/src/tests/evaluation-*.test.ts(x)  # 新
tests/evaluation/test_profiles.py test_datasets.py test_targets.py test_timeout.py  # 新
```

---

# GROUP-1 后端

## WU-01 S7 修复：profile 凭据不落库，boot 时从内存 profile 重建

**背景：** `create_run` 现在把 `Profile.bundle` 整块写进 `runs.snapshot_json`（services.py:201-208）。接真实模型前必须改为：快照只存 `{id, provider, model}`；`_agent_boot` / `_judge_bundle` 在执行时从 `self._profiles[profile_id].bundle` 重建。gateway 重启后 bundle 由启动时的 config 重建，rerun 仍可用。

**Files:**
- Modify: `nanobot/evaluation/services.py`（create_run ≈187-214、execute 内 bundle 取值 ≈340、`_judge_bundle` ≈1102）
- Test: `tests/evaluation/test_services.py`（追加）

- [ ] **Step 1: 写失败测试**（追加到 `tests/evaluation/test_services.py`）

```python
def _secret_profile() -> "Profile":
    from nanobot.evaluation.services import Profile
    return Profile(
        id="model:test", label="test", provider="openai", model="gpt-x", kind="both",
        bundle={"config": {"providers": {"openai": {"api_key": "sk-secret"}}}},
    )


def test_create_run_snapshot_strips_profile_bundle(repository, tmp_path) -> None:
    from nanobot.evaluation.schemas import RunCreate
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_case_payload, make_run_payload, make_suite_payload

    service = EvaluationService(repository=repository, data_dir=tmp_path, profiles=(_secret_profile(),))
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate(make_run_payload([case["id"]], suite_id=suite["id"], agent_profile_id="model:test")),
        idempotency_key="s7-1", scope="t",
    )
    snapshot = repository.get_run(run["id"])["snapshot"]
    assert "bundle" not in snapshot["agent_profile"]
    assert snapshot["agent_profile"]["id"] == "model:test"


def test_agent_boot_rebuilds_bundle_from_profile(repository, tmp_path) -> None:
    from nanobot.evaluation.schemas import CaseWrite, RunCreate
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_case_payload, make_run_payload, make_suite_payload

    service = EvaluationService(repository=repository, data_dir=tmp_path, profiles=(_secret_profile(),))
    suite = repository.create_suite(make_suite_payload())
    case = CaseWrite.model_validate(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate(make_run_payload([case.model_dump()["id"]] if False else [repository.save_case(make_case_payload(suite_id=suite["id"]))["id"]], suite_id=suite["id"], agent_profile_id="model:test")),
        idempotency_key="s7-2", scope="t",
    )
    paths = service._attempt_paths("00000000-0000-4000-8000-000000000001")
    boot = service._agent_boot("00000000-0000-4000-8000-000000000001", case, repository.get_run(run["id"]), paths)
    assert boot.profile_bundle["config"]["providers"]["openai"]["api_key"] == "sk-secret"
```

- [ ] **Step 2: 跑测试确认失败** — `.venv/Scripts/python.exe -m pytest tests/evaluation/test_services.py -k s7 -q`，预期第一条 FAIL（snapshot 里仍有 bundle）。
- [ ] **Step 3: 实现**。`create_run` 中：

```python
        snapshot_bundle = {
            "agent_profile": {"provider": agent.provider, "model": agent.model, "id": agent.id},
            "judge_profile": None if judge is None else {
                "provider": judge.provider, "model": judge.model, "id": judge.id,
            },
        }
```

新增方法（放在 `profile()` 附近）：

```python
    def _bundle_for(self, profile_id: str) -> dict[str, Any]:
        """凭据只进内存：快照里没有 bundle，执行时从启动时装入的 profile 重建。"""
        profile = self._profiles.get(profile_id)
        bundle = dict(profile.bundle) if profile is not None else {}
        bundle.setdefault("config", {})
        return bundle
```

`execute()` 内（≈340）把 `bundle = dict(profile.get("bundle") or {})` 改为
`bundle = self._bundle_for(str((run.get("snapshot", {}).get("agent_profile") or {}).get("id") or ""))`；
`_judge_bundle(profile)`（模块级函数）删除，judge boot 处改用
`self._bundle_for(str((run.get("snapshot", {}).get("judge_profile") or {}).get("id") or ""))`（`_grade` 需要把 run 传进来，已有参数）。

- [ ] **Step 4: 全量回归** — `.venv/Scripts/python.exe -m pytest tests/evaluation -q`，预期 702+2 passed / 5 failed（校准 WIP 不变）。
- [ ] **Step 5: Commit** — `git add nanobot/evaluation/services.py tests/evaluation/test_services.py && git commit -m "fix(evaluation): profile 凭据不再落入运行快照，boot 时从内存重建（S7）"`

## WU-02 真实模型 profiles：gateway config → 评估 Profile

**Files:**
- Create: `nanobot/evaluation/profiles.py`
- Modify: `nanobot/cli/gateway_runtime.py`（`_start_embedded_evaluation`）
- Test: `tests/evaluation/test_profiles.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/evaluation/test_profiles.py
from nanobot.config.schema import Config, ModelPresetConfig, ProviderConfig
from nanobot.evaluation.profiles import build_profiles_from_config


def test_profiles_from_config_maps_presets_with_credential_bundle() -> None:
    config = Config()
    config.providers.openai.api_key = "sk-live"
    config.model_presets["fast"] = ModelPresetConfig(model="gpt-4o-mini", provider="openai")
    profiles = build_profiles_from_config(config)
    by_id = {p.id: p for p in profiles}
    assert by_id["model:fast"].model == "gpt-4o-mini"
    bundle = by_id["model:fast"].bundle
    assert bundle["config"]["providers"]["openai"]["api_key"] == "sk-live"
    assert bundle["config"]["agents"]["defaults"]["model"] == "fast"
    assert bundle["config"]["modelPresets"]["fast"]["model"] == "gpt-4o-mini"


def test_profiles_never_contains_secret_outside_bundle() -> None:
    config = Config()
    config.providers.openai.api_key = "sk-live"
    config.model_presets["fast"] = ModelPresetConfig(model="gpt-4o-mini", provider="openai")
    for profile in build_profiles_from_config(config):
        assert "api_key" not in str(profile.to_read())
```

- [ ] **Step 2: 跑失败** — `.venv/Scripts/python.exe -m pytest tests/evaluation/test_profiles.py -q`（ModuleNotFoundError）。
- [ ] **Step 3: 实现** `nanobot/evaluation/profiles.py`：

```python
"""gateway 配置 → 评估可选模型 Profile。

bundle 里带整份 providers/presets：worker 的 Config() 默认值没有用户凭据，
合并这份 config 后 make_provider 的解析路径与 gateway 完全一致（含 auto）。
凭据只存在于内存中的 profile.bundle 与 boot 管道，不落库、不进快照（WU-01）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.evaluation.services import Profile

if TYPE_CHECKING:
    from nanobot.config.schema import Config


def build_profiles_from_config(config: Config) -> tuple[Profile, ...]:
    from nanobot.agent.model_presets import configured_model_presets

    presets = configured_model_presets(config)
    if not presets:
        return ()
    providers_payload: dict[str, Any] = config.providers.model_dump(mode="json", by_alias=True)
    profiles: list[Profile] = []
    for name, preset in presets.items():
        bundle_config = {
            "agents": {"defaults": {"model": name}},
            "modelPresets": {name: preset.model_dump(mode="json", by_alias=True)},
            "providers": providers_payload,
        }
        profiles.append(
            Profile(
                id=f"model:{name}", label=name,
                provider=preset.provider, model=preset.model, kind="both",
                bundle={"config": bundle_config},
            )
        )
    return tuple(profiles)
```

- [ ] **Step 4: gateway 注入** — `_start_embedded_evaluation` 内 `create_eval_app(...)` 增加 `profiles=(**needs import,)`：

```python
    from nanobot.evaluation.profiles import build_profiles_from_config
    from nanobot.evaluation.services import default_profiles

    app = create_eval_app(
        data_dir=get_data_dir() / "evaluation",
        passphrase=secret,
        allowed_origins=origins,
        profiles=(*build_profiles_from_config(config), *default_profiles()),
    )
```

- [ ] **Step 5: 回归 + lint** — `pytest tests/evaluation/test_profiles.py tests/evaluation/test_services.py -q` 全绿；`ruff check nanobot/evaluation/profiles.py`。
- [ ] **Step 6: Commit** — `feat(evaluation): 从 gateway 配置构建真实模型评估 profile`

## WU-03 目标快照与总运行时限

**Files:**
- Modify: `nanobot/evaluation/schemas.py`（RunCreate/RunStatus）、`services.py`（快照冻结）、`scheduler.py`（deadline watchdog + cancel reason）、`repository.py`（timed_out）
- Test: `tests/evaluation/test_timeout.py`（新建）、`tests/evaluation/test_targets.py`（新建，WU-04 起共用）

- [ ] **Step 1: schemas 失败测试**（追加到 `tests/evaluation/test_targets.py` 新文件）

```python
import pytest
from nanobot.evaluation.schemas import RunCreate
from pydantic import ValidationError


def test_run_create_accepts_target_and_timeout() -> None:
    body = RunCreate.model_validate({
        "suite_id": "00000000-0000-4000-8000-000000000001",
        "case_ids": ["00000000-0000-4000-8000-000000000002"],
        "agent_profile_id": "model:fast",
        "target": {"kind": "agent", "id": "writer"},
        "run_timeout_s": 600,
    })
    assert body.target is not None and body.target.kind == "agent"
    assert body.run_timeout_s == 600


def test_run_create_rejects_unknown_target_kind() -> None:
    with pytest.raises(ValidationError):
        RunCreate.model_validate({
            "suite_id": "00000000-0000-4000-8000-000000000001",
            "case_ids": ["00000000-0000-4000-8000-000000000002"],
            "agent_profile_id": "model:fast",
            "target": {"kind": "keyboard", "id": "x"},
        })
```

- [ ] **Step 2: 实现 schemas**。`RunStatus` Literal 增加 `"timed_out"`；`RunCreate` 增加：

```python
class TargetRef(JsonModel):
    kind: Literal["agent", "team", "skill", "dataset"]
    id: Annotated[StrictStr, Field(min_length=1, max_length=120)]


# RunCreate 内新增：
    target: TargetRef | None = None
    #: None = 不限时；到点由调度器批量停止（WU-03）。
    run_timeout_s: Annotated[int | None, Field(strict=True, ge=5, le=86400)] = None
```

- [ ] **Step 3: 快照冻结**（services.create_run）：

```python
        "target": None if request.target is None else request.target.model_dump(mode="json"),
        "run_timeout_s": request.run_timeout_s,
```

- [ ] **Step 4: 调度器 deadline**。`scheduler.py`：

```python
    def _run_deadline(self, run: Mapping[str, Any]) -> float | None:
        snapshot = run.get("snapshot") or {}
        budget = snapshot.get("run_timeout_s")
        if not budget:
            return None
        started = run.get("started_at")
        # repository 返回 ISO 字符串；用 attempt 已耗时间近似：以领取时刻为起点。
        return None  # 占位见下方实现说明
```

实现说明（写码时按此落地）：在 `_run_attempt` 外层的 run 级派发处（`_dispatch_once` 领取 run 后）记录 `self._deadlines[run_id] = loop.time() + run_timeout_s`，并 `asyncio.create_task(self._enforce_deadline(run_id))`：

```python
    async def _enforce_deadline(self, run_id: str) -> None:
        deadline = self._deadlines.get(run_id)
        if deadline is None:
            return
        delay = deadline - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)
        if self._deadlines.get(run_id) != deadline:
            return
        await self.cancel_run(run_id, reason="timeout")
```

`cancel_run(run_id, *, reason: str = "user")`：把 reason 记入 `self._cancel_reasons[run_id]`；attempt 收口处（原 `cancelled` 提交点）若 `self._cancel_reasons.get(run_id) == "timeout"` 则提交 `"timed_out"`，run 终态写 `"timed_out"`（repository.settle 处按 reason 分支）。run 收口与 task 清理处 `self._deadlines.pop(run_id, None)`、`self._cancel_reasons.pop(run_id, None)`。

- [ ] **Step 5: 失败→通过测试**（追加到 `test_timeout.py`）

```python
async def test_deadline_marks_inflight_attempt_timed_out(repository, tmp_path) -> None:
    import asyncio
    from nanobot.evaluation.repository import EvaluationRepository
    from nanobot.evaluation.scheduler import Scheduler, SchedulerConfig
    from nanobot.evaluation.schemas import RunCreate
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_case_payload, make_run_payload, make_suite_payload

    async def blocked_judge(*args, **kwargs):
        await asyncio.Event().wait()  # 永不完成

    service = EvaluationService(repository=repository, data_dir=tmp_path, judge_runner=blocked_judge)
    scheduler = Scheduler(repository=repository, pipeline=service,
                          config=SchedulerConfig(data_dir=tmp_path, concurrency=1, poll_interval_s=0.05))
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"], rubric={
        "required": True, "dimensions": [{"id": "d1", "name": "n", "description": "d",
                                          "min_score": 1, "max_score": 5, "anchors": {}}]}))
    run = service.create_run(RunCreate.model_validate({
        **make_run_payload([case["id"]], suite_id=suite["id"], agent_profile_id="controlled-agent"),
        "run_timeout_s": 5,
    }), idempotency_key="t1", scope="t")
    async with scheduler:
        for _ in range(200):
            detail = repository.get_run(run["id"])
            if detail["status"] == "timed_out":
                break
            await asyncio.sleep(0.1)
    assert repository.get_run(run["id"])["status"] == "timed_out"
```

（rubric 字段以 `schemas.Rubric` 实际定义为准；测试若因 rubric 形状报 VALIDATION_ERROR，先读 `nanobot/evaluation/schemas.py` 的 Rubric/ScoreDimension 再修测试数据，不改产品代码。）

- [ ] **Step 6: 回归** — `pytest tests/evaluation/test_timeout.py tests/evaluation/test_scheduler.py -q` 全绿；全量 `pytest tests/evaluation -q` 无新增失败。
- [ ] **Step 7: Commit** — `feat(evaluation): 运行目标快照与总时限（超时批量停止，timed_out 收口）`

## WU-04 Agent 目标：冻结档案 + worker 注入 agent_id + live 工具

**Files:**
- Modify: `nanobot/evaluation/schemas.py`（`_BootBase.target`、`LIVE_ONLY_TOOLS` 扩容）、`services.py`（目标冻结）、`runtime_factory.py`（materialize + live exec）、`worker.py`（metadata 注入）、`nanobot/agent/loop.py:3062`（`extra_metadata` 参数）、`nanobot/nanobot.py:144`（`run(metadata=...)`）
- Test: `tests/evaluation/test_targets.py`

- [ ] **Step 1: 核心失败测试**

```python
def test_process_direct_forwards_extra_metadata() -> None:
    # loop 的 metadata 注入是 agent 目标的枢钮：不加参数，worker 无法指名档案。
    import inspect
    from nanobot.agent.loop import AgentLoop
    assert "extra_metadata" in inspect.signature(AgentLoop.process_direct).parameters


def test_live_mode_allows_sandbox_tools() -> None:
    from nanobot.evaluation.schemas import ExecutionConfig
    body = ExecutionConfig.model_validate({
        "tool_mode": "live",
        "allowed_tools": ["read_file", "write_file", "edit_file", "list_dir",
                          "apply_patch", "grep", "find_files", "exec", "web_fetch"],
    })
    assert body.tool_mode == "live"


def test_boot_carries_target(repository, tmp_path) -> None:
    from nanobot.evaluation.schemas import AgentWorkerBoot, CaseWrite
    from tests.evaluation.conftest import make_case_payload
    boot = AgentWorkerBoot.model_validate({
        "type": "boot", "protocol_version": 1,
        "attempt_id": "00000000-0000-4000-8000-00000000000a",
        "runtime_root": str(tmp_path / "runtime"), "workspace": str(tmp_path / "ws"),
        "profile_bundle": {"config": {}}, "role": "agent",
        "case_snapshot": make_case_payload(suite_id="00000000-0000-4000-8000-000000000001"),
        "target": {"kind": "agent", "id": "writer"},
    })
    assert boot.target == {"kind": "agent", "id": "writer"}
```

- [ ] **Step 2: loop metadata 参数**。`nanobot/agent/loop.py::process_direct` 签名加 `extra_metadata: Mapping[str, Any] | None = None`；构造处：

```python
        metadata: dict[str, Any] = {}
        if not persist_user_message:
            metadata[turn_continuation.SKIP_USER_PERSIST_META] = True
        if extra_metadata:
            metadata.update(dict(extra_metadata))
```

`nanobot/nanobot.py::run` 加 `metadata: Mapping[str, Any] | None = None`，经 `build_process_direct_kwargs`（`nanobot/sdk/runtime.py` 加同名参数，`kwargs["extra_metadata"] = dict(metadata)`）透传。

- [ ] **Step 3: schemas**。`_BootBase` 加 `target: JsonObject | None = None`；`LIVE_ONLY_TOOLS` 改为：

```python
#: live 模式下允许选择的真实工具（仍在 attempt workspace 沙箱内执行）。
LIVE_ONLY_TOOLS = frozenset({
    "web_search", "web_fetch", "read_file", "write_file", "edit_file",
    "list_dir", "apply_patch", "grep", "find_files", "exec", "exec_session",
})
```

- [ ] **Step 4: 目标冻结（服务端）**。`EvaluationService.__init__` 增加 `workspace: Path | None = None`，默认 `get_workspace_path()`；新增：

```python
    _TARGET_FILE_LIMIT = 512 * 1024

    def _freeze_agent_target(self, agent_id: str) -> dict[str, Any]:
        from nanobot.agents.runtime import AgentProfileRuntime
        profile = AgentProfileRuntime(self._workspace).resolve_profile_dict(agent_id)
        # resolve_profile_dict 不存在时：用 AgentStore(workspace).load_profile(agent_id).to_dict()
        skills = self._freeze_skill_files(self._profile_skill_names(profile))
        return {"kind": "agent", "id": agent_id,
                "agent": {"profile": profile, "skills": skills}}

    def _freeze_skill_target(self, skill_name: str) -> dict[str, Any]:
        return {"kind": "skill", "id": skill_name,
                "skill": {"name": skill_name,
                          "files": self._freeze_skill_files([skill_name])}}

    def _freeze_skill_files(self, names: list[str]) -> dict[str, str]:
        root = self._workspace / "skills"
        files: dict[str, str] = {}
        total = 0
        for name in names:
            base = root / name
            if not base.is_dir():
                raise ServiceError("SKILL_NOT_FOUND", name)
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    data = path.read_text(encoding="utf-8", errors="replace")
                    rel = path.relative_to(self._workspace).as_posix()
                    total += len(data)
                    if total > self._TARGET_FILE_LIMIT:
                        raise ServiceError("TARGET_TOO_LARGE", name)
                    files[rel] = data
        return files
```

（`resolve_profile_dict` 若不存在，落地时用 `AgentStore(self._workspace).load_profile(agent_id).to_dict()`；skills 名单取 `profile["skills"]` 经 `resolve_selection` 对 `SkillsLoader.list_skills()` 求交。）`create_run` 内：

```python
        target_payload = None
        if request.target is not None:
            if request.target.kind == "agent":
                target_payload = self._freeze_agent_target(request.target.id)
            elif request.target.kind == "skill":
                target_payload = self._freeze_skill_target(request.target.id)
            else:
                target_payload = {"kind": request.target.kind, "id": request.target.id}
```

- [ ] **Step 5: worker 物化与注入**。`runtime_factory.create_attempt` 在 `paths.create()` 后：

```python
    target = getattr(boot, "target", None)
    materialized = _materialize_target_files(paths, target)
```

```python
def _materialize_target_files(paths: Any, target: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(target, Mapping):
        return []
    written: list[str] = []
    agent = target.get("agent") or {}
    profile = agent.get("profile")
    if isinstance(profile, Mapping) and profile.get("id"):
        base = paths.workspace / "agents" / "profiles"
        base.mkdir(parents=True, exist_ok=True)
        (base / f"{profile['id']}.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        written.append(f"agents/profiles/{profile['id']}.json")
    skill = target.get("skill") or {}
    for rel, content in (skill.get("files") or agent.get("skills") or {}).items():
        dest = (paths.workspace / rel).resolve()
        if not dest.is_relative_to(paths.workspace.resolve()):
            raise ValueError("TARGET_PATH_ESCAPE")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(str(content), encoding="utf-8")
        written.append(rel)
    return written
```

`create_attempt` 内 live exec 开关：

```python
    live_exec = bool(
        execution is not None and execution.tool_mode == "live"
        and "exec" in execution.allowed_tools
    )
    config = build_attempt_config(..., live_exec=live_exec)
```

`_sandbox_overlay(paths, max_iterations, live_exec=False)` 中 `"exec": {"enable": live_exec}`。

`worker.run_agent_worker` 的 turn 循环：

```python
    target = getattr(boot, "target", None) or {}
    extra_metadata = (
        {"agent_id": str(target.get("id"))} if target.get("kind") == "agent" else None
    )
    ...
    result = await runtime.bot.run(
        content, session_key=runtime.session_key, hooks=[hook],
        metadata=extra_metadata,
    )
```

- [ ] **Step 6: 通过测试 + 回归**。另加一条物化测试：

```python
def test_materialize_target_files_writes_profile_and_skills(tmp_path) -> None:
    from nanobot.evaluation.runtime_factory import _materialize_target_files
    class P:  # 最小 AttemptPaths 替身
        class workspace:  # noqa: N801 - 简单占位
            pass
    # 落地时用 attempt_paths(...) 真实构造或 pathlib.Path 替身；断言：
    # agents/profiles/writer.json 存在且内容含 prompt；skills/demo/SKILL.md 存在；
    # 相对路径逃逸（"../x"）抛 ValueError。
```

（实现时用 `attempt_paths(attempt_id=..., runtime_root=tmp_path/"runtime", workspace=tmp_path/"ws")` 真构造，占位类只示意断言目标。）
- [ ] **Step 7: 回归** — 全量 `pytest tests/evaluation -q`；`ruff check nanobot/evaluation nanobot/agent/loop.py nanobot/nanobot.py`；`basedpyright nanobot/evaluation`（不新增错误级别）。
- [ ] **Step 8: Commit** — `feat(evaluation): Agent/Skill 目标冻结与 worker 注入（agent_id metadata + live 沙箱工具）`

## WU-05 团队目标：gateway 注入 team_runner（进程内）

**Files:**
- Modify: `nanobot/evaluation/services.py`（`team_runner` 注入点 + execute 分支）、`nanobot/webui/gateway_services.py`（适配器）
- Test: `tests/evaluation/test_targets.py`

- [ ] **Step 1: 服务层失败测试**

```python
async def test_team_target_runs_through_injected_runner(repository, tmp_path) -> None:
    from nanobot.evaluation.schemas import RunCreate
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_case_payload, make_run_payload, make_suite_payload

    calls: list[dict] = []

    async def fake_runner(payload):
        calls.append(payload)
        return {"status": "succeeded", "final_answer": "团队结论 alpha", "error": None}

    service = EvaluationService(repository=repository, data_dir=tmp_path, team_runner=fake_runner)
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(RunCreate.model_validate({
        **make_run_payload([case["id"]], suite_id=suite["id"], agent_profile_id="controlled-agent"),
        "target": {"kind": "team", "id": "creative"},
    }), idempotency_key="team-1", scope="t")
    service._run_pipeline = service  # 由调度器调用；测试里直接驱动
    # 直接调用 execute（模拟调度器一次领取）：
    detail = repository.get_run(run["id"])
    attempt = detail["attempts"][0]
    outcome = await service.execute(attempt, run=detail)
    assert outcome.execution_status == "succeeded"
    assert outcome.result["final_answer"] == "团队结论 alpha"
    assert calls[0]["team_id"] == "creative"
```

（`AttemptOutcome` 字段名以 `services.py` 现有定义为准；若 `result` 装配形状不同，按 `_result_payload` 对齐。）

- [ ] **Step 2: 服务层实现**。`EvaluationService.__init__` 加 `team_runner: TeamRunner | None = None`（`TeamRunner = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]`）；`execute()` 开头：

```python
        target = (run.get("snapshot") or {}).get("target") or {}
        if target.get("kind") == "team":
            return await self._execute_team(attempt=attempt, run=run, case=case, limits=limits, errors=errors)
```

```python
    async def _execute_team(self, *, attempt, run, case, limits, errors) -> AttemptOutcome:
        if self._team_runner is None:
            return self._setup_failure([AttemptError(phase="setup", code="TEAM_RUNNER_UNAVAILABLE", message="gateway 未注入团队执行栈")])
        payload = {
            "team_id": str((run.get("snapshot", {}).get("target") or {}).get("id") or ""),
            "prompt": case.turns[-1].content,
            "timeout_s": limits.timeout_s,
        }
        started = time.monotonic()
        try:
            result = await self._team_runner(payload)
        except Exception as exc:  # noqa: BLE001
            return self._setup_failure([AttemptError(phase="execution", code="TEAM_RUN_FAILED", message=str(exc))])
        status = result.get("status") or "failed"
        final_answer = result.get("final_answer")
        outcome = AttemptOutcome(
            execution_status=status,  # type: ignore[arg-type]
            grading_status="pending",
            verdict="not_evaluated",
            result=_result_payload(execution_status=status, errors=errors,
                                   worker_result={"final_answer": final_answer, "stop_reason": None},
                                   timing={"agent_ms": int((time.monotonic() - started) * 1000)}),
            final_answer=final_answer,
        )
        return outcome
```

（字段以 `AttemptOutcome`/`_result_payload` 实际签名为准，保持与普通 attempt 同一装配出口，判分照走既有 `_grade`。）

- [ ] **Step 3: gateway 适配器**（`nanobot/webui/gateway_services.py`，在 `team_turns` 构建之后）：

```python
def build_team_eval_runner(*, team_service: Any, scheduler: Any) -> Any:
    """把评估服务的 team 目标接到 gateway 已有的团队执行栈（进程内，无独立服务）。"""
    import asyncio, time, uuid
    from nanobot.teams.models import TeamBinding

    async def run(payload: dict) -> dict:
        team_id = str(payload.get("team_id") or "")
        team = team_service.repository.get_team(team_id)
        if team is None:
            return {"status": "failed", "final_answer": None, "error": f"TEAM_NOT_FOUND:{team_id}"}
        token = uuid.uuid4().hex
        handle = await scheduler.start_run(
            binding=TeamBinding(team_id=team_id, team_revision=int(team["revision"]), team_name=str(team["name"])),
            chat_id=f"eval:{token}", session_key=f"eval:{token}",
            turn_id=token, internal_turn_id=token,
        )
        deadline = time.monotonic() + float(payload.get("timeout_s") or 1800)
        while time.monotonic() < deadline:
            row = team_service.repository.get_run(handle.run_id)
            if row is not None and row.get("status") in ("completed", "failed", "cancelled"):
                break
            await asyncio.sleep(1.0)
        row = team_service.repository.get_run(handle.run_id)
        status = str((row or {}).get("status") or "failed")
        final = None
        if status == "completed":
            final = team_service.repository.final_answer(handle.run_id)  # 若无此方法，读 run 事件/消息表取最后一条 Leader 输出
        return {"status": "succeeded" if status == "completed" else status,
                "final_answer": final, "error": None if status == "completed" else status}
    return run
```

**落地校验步骤（写码时执行）：** 读 `nanobot/teams/scheduler.py::start_run`（≈913）、`nanobot/teams/repository.py` 的 run 查询方法名与字段（`status`/最终输出所在表）。若 `final_answer` 无现成出口，用团队 run 事件流中 Leader 的最后一条 `message` 事件文本，禁止改团队域代码。装配处：

```python
    eval_team_runner = build_team_eval_runner(team_service=team_service, scheduler=team_turns.scheduler) if team_turns is not None else None
```

并把 `eval_team_runner` 传给 WU-09 的 `_start_embedded_evaluation(..., team_runner=eval_team_runner)` → `EvaluationService(team_runner=...)`。

- [ ] **Step 4: 回归 + Commit** — `pytest tests/evaluation -q`；`feat(evaluation): 团队目标经 gateway 注入 runner 进程内执行`

## WU-06 Excel 数据集：模板 / 导入 / 导出

**Files:**
- Create: `nanobot/evaluation/datasets.py`
- Modify: `nanobot/evaluation/server.py`（3 条路由）、`repository.py`（复用 create_suite/save_case）
- Test: `tests/evaluation/test_datasets.py`（新建）

- [ ] **Step 1: 失败测试**

```python
# tests/evaluation/test_datasets.py
from nanobot.evaluation.datasets import build_template_bytes, parse_workbook, export_suite_workbook


def test_template_roundtrip() -> None:
    parsed = parse_workbook(build_template_bytes())
    assert parsed["errors"] == []  # 模板自带示例行合法
    assert parsed["cases"][0]["name"] == "示例用例"


def test_rows_group_into_multiturn_case() -> None:
    data = build_template_bytes()
    # 用 openpyxl 改写：同名两行、轮次 1/2 → 一个用例 2 turns
    from openpyxl import load_workbook
    import io
    wb = load_workbook(io.BytesIO(data))
    ws = wb["用例"]
    ws.append(["多轮用例", 1, "第一问", "答案一", "", "", "", ""])
    ws.append(["多轮用例", 2, "第二问", "答案二", "", "", "", ""])
    buf = io.BytesIO(); wb.save(buf)
    parsed = parse_workbook(buf.getvalue())
    case = next(c for c in parsed["cases"] if c["name"] == "多轮用例")
    assert len(case["turns"]) == 2
    assert [t["content"] for t in case["turns"]] == ["第一问", "第二问"]


def test_missing_required_rule_is_row_error() -> None:
    from openpyxl import Workbook
    import io
    wb = Workbook(); ws = wb.active; ws.title = "用例"
    ws.append(["用例名称", "轮次", "输入", "期望包含", "期望不包含", "期望工具", "禁止工具", "标签"])
    ws.append(["空规则", 1, "hi", "", "", "", "", ""])
    buf = io.BytesIO(); wb.save(buf)
    parsed = parse_workbook(buf.getvalue())
    assert any("缺少" in e["message"] for e in parsed["errors"])
```

- [ ] **Step 2: 实现 `nanobot/evaluation/datasets.py`**

```python
"""Excel 数据集：模板、解析、导出。只服务普通对话型测试集（一行一轮）。

分组规则：同名「用例名称」的行按「轮次」升序合成一个用例；跨用例的轮次号互不影响。
断言合成：期望包含→answer_contains（换行分隔多个）；期望不包含→answer_not_contains；
期望工具→tool_called；禁止工具→tool_forbidden；四者全空 = 缺少必过规则（行错误）。
"""
from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook, load_workbook

COLUMNS = ("用例名称", "轮次", "输入", "期望包含", "期望不包含", "期望工具", "禁止工具", "标签")
MAX_ROWS = 500
MAX_BYTES = 5 * 1024 * 1024


def build_template_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "用例"
    ws.append(COLUMNS)
    ws.append(["示例用例", 1, "你好，请用一句话介绍知序。", "知序", "", "", "", "示例"])
    info = wb.create_sheet("说明")
    info["A1"] = "数据集名称"
    info["B1"] = "我的数据集"
    info["A3"] = "一行一轮；同名多行按轮次合成多轮用例。期望包含可写多行（换行分隔）。"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cell(row: tuple[Any, ...], index: int) -> str:
    value = row[index] if index < len(row) else None
    return "" if value is None else str(value).strip()


def _split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def parse_workbook(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_BYTES:
        return {"suite": None, "cases": [], "errors": [{"row": 0, "message": "文件超过 5MB"}]}
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        return {"suite": None, "cases": [], "errors": [{"row": 0, "message": f"无法解析 xlsx: {exc}"}]}
    ws = wb["用例"] if "用例" in wb.sheetnames else wb.active
    suite_name = "导入数据集"
    if "说明" in wb.sheetnames and wb["说明"]["B1"].value:
        suite_name = str(wb["说明"]["B1"].value).strip() or suite_name

    grouped: dict[str, list[tuple[int, tuple[Any, ...]]]] = {}
    errors: list[dict[str, Any]] = []
    for row_index, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if all(_cell(row, i) == "" for i in range(len(COLUMNS))):
            continue
        name = _cell(row, 0)
        if not name:
            errors.append({"row": row_index, "message": "缺少用例名称"})
            continue
        grouped.setdefault(name, []).append((row_index, row))

    cases: list[dict[str, Any]] = []
    for name, entries in grouped.items():
        entries.sort(key=lambda item: (int(_cell(item[1], 1) or 0), item[0]))
        turns: list[dict[str, str]] = []
        contains: list[str] = []
        not_contains: list[str] = []
        tools: list[str] = []
        forbidden: list[str] = []
        tags: list[str] = []
        for row_index, row in entries:
            content = _cell(row, 2)
            if not content:
                errors.append({"row": row_index, "message": "缺少输入"})
                continue
            turns.append({"content": content})
            contains += _split_lines(_cell(row, 3))
            not_contains += _split_lines(_cell(row, 4))
            tools += [t for t in _cell(row, 5).replace("，", ",").split(",") if t]
            forbidden += [t for t in _cell(row, 6).replace("，", ",").split(",") if t]
            tags += [t for t in _cell(row, 7).replace("，", ",").split(",") if t]
        if not turns:
            continue
        assertions: list[dict[str, Any]] = []
        for index, text in enumerate(dict.fromkeys(contains)):
            assertions.append({"id": f"c{index}", "kind": "answer_contains", "required": True,
                               "text": text, "case_sensitive": False})
        for index, text in enumerate(dict.fromkeys(not_contains)):
            assertions.append({"id": f"n{index}", "kind": "answer_not_contains", "required": True,
                               "text": text, "case_sensitive": False})
        for index, tool in enumerate(dict.fromkeys(tools)):
            assertions.append({"id": f"t{index}", "kind": "tool_called", "required": True, "tool": tool})
        for index, tool in enumerate(dict.fromkeys(forbidden)):
            assertions.append({"id": f"f{index}", "kind": "tool_forbidden", "required": True, "tool": tool})
        if not assertions:
            errors.append({"row": entries[0][0], "message": f"用例 {name} 缺少期望包含/期望工具等必过规则"})
            continue
        cases.append({
            "name": name[:120], "description": "", "tags": tags[:20], "enabled": True,
            "turns": turns[:20], "fixtures": [], "memory_seed": [], "clock_iso": None,
            "tool_fixtures": [],
            "execution": {"tool_mode": "live",
                          "allowed_tools": ["read_file", "write_file", "edit_file", "list_dir",
                                            "web_search", "web_fetch"],
                          "max_iterations": 12, "timeout_s": 120},
            "assertions": assertions[:50], "rubric": None, "script": [], "artifact_paths": [],
        })
    return {"suite": {"name": suite_name[:120], "description": "Excel 导入", "tags": ["dataset"], "base_revision": None},
            "cases": cases[:200], "errors": errors}


def export_suite_workbook(suite: dict[str, Any], cases: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "用例"
    ws.append(COLUMNS)
    for case in cases:
        turns = case.get("turns") or []
        contains = [a["text"] for a in case.get("assertions", []) if a.get("kind") == "answer_contains"]
        not_contains = [a["text"] for a in case.get("assertions", []) if a.get("kind") == "answer_not_contains"]
        tools = [a["tool"] for a in case.get("assertions", []) if a.get("kind") == "tool_called"]
        forbidden = [a["tool"] for a in case.get("assertions", []) if a.get("kind") == "tool_forbidden"]
        for index, turn in enumerate(turns, start=1):
            ws.append([
                case.get("name", ""), index, turn.get("content", ""),
                "\n".join(contains) if index == 1 else "",
                "\n".join(not_contains) if index == 1 else "",
                ",".join(tools) if index == 1 else "",
                ",".join(forbidden) if index == 1 else "",
                ",".join(case.get("tags", [])) if index == 1 else "",
            ])
    info = wb.create_sheet("说明")
    info["A1"] = "数据集名称"
    info["B1"] = suite.get("name", "")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

（`tool_called`/`tool_forbidden` 断言字段名以 `schemas.py:223-247` 实际字段为准，落地时对齐。）

- [ ] **Step 3: 路由**（`server.py::_routes()` 追加，`create_run` 等同层）：

```python
        r("GET", "/datasets/template", dataset_template),
        r("POST", "/datasets/import", import_dataset),
        r("GET", "/suites/{id}/export.xlsx", export_suite_xlsx),
```

```python
async def dataset_template(request: web.Request) -> web.Response:
    from nanobot.evaluation.datasets import build_template_bytes
    return web.Response(
        body=build_template_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="evaluation-dataset-template.xlsx"'},
    )


async def import_dataset(request: web.Request) -> web.Response:
    from nanobot.evaluation.datasets import parse_workbook
    context = context_of(request)
    reader = await request.multipart()
    data = b""
    while part := await reader.next():
        if part.name == "file":
            while chunk := await part.read_chunk(65536):
                data += chunk
                if len(data) > MAX_BODY_BYTES:
                    _fail("PAYLOAD_TOO_LARGE", "Excel 超过 5MB")
            break
    if not data:
        _fail("VALIDATION_ERROR", "缺少 file 字段")
    parsed = parse_workbook(data)
    if not parsed["cases"]:
        return web.json_response({"error": {"code": "VALIDATION_ERROR", "message": "没有可用用例", "rows": parsed["errors"]}}, status=422)
    suite = context.repository.create_suite(parsed["suite"])
    saved = [context.repository.save_case({**case, "suite_id": suite["id"]}) for case in parsed["cases"]]
    return web.json_response({"suite": suite, "case_count": len(saved), "errors": parsed["errors"]}, status=201)


async def export_suite_xlsx(request: web.Request) -> web.Response:
    from nanobot.evaluation.datasets import export_suite_workbook
    context = context_of(request)
    suite = context.repository.get_suite(request.match_info["id"])
    cases = context.repository.list_cases(str(suite["id"]))
    return web.Response(
        body=export_suite_workbook(suite, cases),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="dataset-{suite["id"]}.xlsx"'},
    )
```

（`repository.get_suite` 若不存在，用 `list_suites()` 过滤或补一个只读方法——只允许补只读。）

- [ ] **Step 4: 路由测试**（aiohttp TestClient，参考 `test_server.py` 的 client fixture；用例：template 200 且 content-type 正确；import 合法文件 201 且 cases 落库；非法文件 422）。
- [ ] **Step 5: 回归 + Commit** — `pytest tests/evaluation/test_datasets.py tests/evaluation/test_server.py -q`；`feat(evaluation): Excel 数据集模板/导入/导出`

## WU-07 HTML 汇总报告

**Files:**
- Modify: `nanobot/evaluation/reports.py`（`render_summary_html`）、`services.py`（`summary_report`）、`server.py`（`GET /reports/summary`）
- Test: `tests/evaluation/test_reports.py`（追加）

- [ ] **Step 1: 失败测试**

```python
def test_summary_html_is_self_contained() -> None:
    from nanobot.evaluation.reports import render_summary_html
    html = render_summary_html(
        title="评估汇总",
        generated_at="2026-10-23T00:00:00Z",
        sections=[{"heading": "通过率", "rows": [["run-1", "4/6", "0.667"]]}],
    )
    assert "<html" in html and "run-1" in html
    assert "http://" not in html and "https://" not in html  # 不引外部资源
```

- [ ] **Step 2: 实现**（reports.py 追加；表格用内联 CSS，中文状态，无 JS 依赖）：

```python
_SUMMARY_CSS = """
body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:32px;color:#111}
h1{font-size:20px} h2{font-size:16px;margin-top:24px}
table{border-collapse:collapse;width:100%;margin-top:8px}
th,td{border:1px solid #d4d4d4;padding:6px 10px;font-size:13px;text-align:left}
th{background:#f5f5f5} .meta{color:#666;font-size:12px}
"""


def render_summary_html(*, title: str, generated_at: str, sections: list[dict[str, Any]]) -> str:
    parts = [f"<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
             f"<title>{_esc(title)}</title><style>{_SUMMARY_CSS}</style></head><body>",
             f"<h1>{_esc(title)}</h1><p class=\"meta\">生成时间：{_esc(generated_at)} · 知序评估</p>"]
    for section in sections:
        parts.append(f"<h2>{_esc(section['heading'])}</h2><table><tr>"
                     + "".join(f"<th>{_esc(str(h))}</th>" for h in section["headers"])
                     + "</tr>")
        for row in section["rows"]:
            parts.append("<tr>" + "".join(f"<td>{_esc(str(cell))}</td>" for cell in row) + "</tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    return "".join(parts)
```

`services.py::summary_report(suite_id)`：取该 suite 全部 runs（`repository.list_runs(suite_id=...)`），逐 run `run_read` 的 counts/metrics + attempts 的 case 名/verdict，装配 sections：

```python
    def summary_report(self, suite_id: str) -> str:
        runs = self._repository.list_runs(suite_id=suite_id, limit=100)
        run_rows: list[list[str]] = []
        case_best: dict[str, list[str]] = {}
        for run in runs:
            read = self.run_read(str(run["id"]))
            counts = read["counts"]
            run_rows.append([read["id"][:8], read.get("status", ""), str(read["planned_count"]),
                             str(counts.get("passed", 0)), str(counts.get("failed", 0)),
                             str(counts.get("review_required", 0))])
            for attempt in read.get("attempts", []) if "attempts" in read else self._repository.list_run_attempts(str(run["id"])):
                name = str(attempt.get("case_name") or attempt.get("case_id"))
                row = case_best.setdefault(name, [name, "-", "-"])
                verdict = str(attempt.get("verdict") or "-")
                if row[1] == "-" or verdict == "failed":
                    row[1], row[2] = verdict, read["id"][:8]
        from nanobot.evaluation.reports import render_summary_html
        return render_summary_html(
            title=f"评估汇总 · {suite_id[:8]}",
            generated_at=datetime.now(timezone.utc).isoformat(),
            sections=[
                {"heading": "运行", "headers": ["运行", "状态", "用例数", "通过", "失败", "待复核"], "rows": run_rows},
                {"heading": "用例最新结论", "headers": ["用例", "结论", "最近运行"], "rows": list(case_best.values())},
            ],
        )
```

路由：

```python
        r("GET", "/reports/summary", summary_report_route),

async def summary_report_route(request: web.Request) -> web.Response:
    context = context_of(request)
    suite_id = request.query.get("suite_id", "")
    if not suite_id:
        _fail("VALIDATION_ERROR", "缺少 suite_id")
    html = context.service.summary_report(suite_id)
    return web.Response(text=html, content_type="text/html", charset="utf-8",
                        headers={"Content-Disposition": f'attachment; filename="evaluation-summary-{suite_id[:8]}.html"'})
```

- [ ] **Step 3: 回归 + Commit** — `pytest tests/evaluation/test_reports.py -q`；`feat(evaluation): 数据集维度 HTML 汇总报告`

## WU-08 gateway 内嵌启动收口（含在途改动）

**Files:**
- Modify: `nanobot/cli/gateway_runtime.py`（在途：CORS/`_start_embedded_evaluation`/生命周期）、`nanobot/webui/gateway_services.py`（team_runner 注入）
- Test: 手动 + 冒烟

- [ ] **Step 1: 汇总在途 diff** — `git status --short` + `git diff` 核对仅含 server.py CORS、gateway_runtime 内嵌、test_runtime_factory 修复（WU-01~07 的已提交内容不在此列）。
- [ ] **Step 2: 注入 profiles 与 team_runner** — `build_gateway_services` 返回前组装：

```python
    evaluation_team_runner = (
        build_team_eval_runner(team_service=team_service, scheduler=team_turns.scheduler)
        if team_turns is not None else None
    )
```

`_run_gateway` 调用处把 `evaluation_team_runner` 传入 `_start_embedded_evaluation(config, gateway_port=port, team_runner=...)` → `create_eval_app(..., team_runner=...)`（`create_eval_app` 增加 `team_runner` 透传参数）。
- [ ] **Step 3: 本机冒烟**（不提交任何临时文件）：

```bash
NETSTAT 检查 8790 空闲
.venv/Scripts/python.exe -c "from aiohttp import web; from nanobot.evaluation.server import create_eval_app; from nanobot.evaluation.profiles import build_profiles_from_config; from nanobot.config.loader import load_config; app=create_eval_app(data_dir=r'C:/Users/Huangqh/AppData/Local/Temp/zhixu-eval-gw', passphrase='smoke', allowed_origins=frozenset({'http://127.0.0.1:5173'}), profiles=(*build_profiles_from_config(load_config()),)); web.run_app(app, host='127.0.0.1', port=8790, print=None)"
# curl: GET /api/eval/v1/profiles 应返回 model:* 真实预设（不打印密钥）
```

- [ ] **Step 4: Commit** — `feat(gateway): 评估服务随 gateway 内嵌启动并注入模型 profiles 与团队 runner`

## WU-09 GROUP-1 收口

- [ ] 全量 `.venv/Scripts/python.exe -m pytest tests/evaluation -q`：除 5 条校准 WIP 外全绿。
- [ ] `ruff check nanobot/evaluation nanobot/cli/gateway_runtime.py nanobot/webui/gateway_services.py`；`basedpyright nanobot/evaluation`（不劣化基线）。
- [ ] 落盘 `verifications/2026-10-23-evaluation-webui-collective-test.md`（GROUP-1 小节）。

---

# GROUP-2 前端（屏障：GROUP-1 收口后开始）

## WU-10 API 客户端 `webui/src/lib/evaluation-api.ts`

**Files:** Create `webui/src/lib/evaluation-api.ts`；Test `webui/src/tests/evaluation-api.test.ts`

- [ ] **Step 1: 失败测试**（mock `fetch`；覆盖：自动登录→CSRF 复用；401 重登重试一次；错误体解析 `{error:{code,message}}`）

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EvaluationApiError, evaluationApi, resetEvaluationSession } from "@/lib/evaluation-api";

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

describe("evaluation-api", () => {
  beforeEach(() => {
    resetEvaluationSession();
    vi.stubGlobal("fetch", vi.fn());
    window.localStorage.setItem("zhixu-webui.bootstrap-secret", "secret-1");
  });
  afterEach(() => vi.unstubAllGlobals());

  it("logs in with the saved bootstrap secret and reuses csrf", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }, 200, { "Set-Cookie": "x" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [] }));
    await expect(evaluationApi.listSuites()).resolves.toEqual({ data: [] });
    const loginCall = fetchMock.mock.calls[0];
    expect(String(loginCall[0])).toContain("/auth/login");
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({ "X-CSRF-Token": "csrf-1" });
  });

  it("retries once after 401", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { code: "AUTH_REQUIRED", message: "" } }, 401));
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-2" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [{ id: "r1" }] }));
    await expect(evaluationApi.listRuns()).resolves.toEqual({ data: [{ id: "r1" }] });
  });

  it("maps contract error body to EvaluationApiError", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(jsonResponse({ csrf_token: "csrf-1" }));
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { code: "VALIDATION_ERROR", message: "suite_id" } }, 422));
    await expect(evaluationApi.listSuites()).rejects.toBeInstanceOf(EvaluationApiError);
  });
});
```

- [ ] **Step 2: 实现**。要点（完整文件落地时一次写全）：

```typescript
const EVAL_PORT = 8790;
function evaluationBase(): string {
  if (typeof window === "undefined") return `http://127.0.0.1:${EVAL_PORT}/api/eval/v1`;
  const host = window.location.hostname || "127.0.0.1";
  return `${window.location.protocol}//${host}:${EVAL_PORT}/api/eval/v1`;
}

let csrfToken = "";
let sessionPromise: Promise<void> | null = null;
export function resetEvaluationSession(): void { csrfToken = ""; sessionPromise = null; }

export class EvaluationApiError extends Error {
  constructor(readonly code: string, message: string, readonly status: number) { super(message || code); }
}

async function ensureSession(): Promise<void> {
  if (csrfToken) return;
  if (!sessionPromise) sessionPromise = (async () => {
    const base = evaluationBase();
    let res = await fetch(`${base}/auth/session`, { credentials: "include" });
    if (!res.ok) {
      const secret = loadSavedSecret();                      // from "@/lib/bootstrap"
      if (!secret) throw new EvaluationApiError("EVAL_OFFLINE", "evaluation-auth", 401);
      res = await fetch(`${base}/auth/login`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passphrase: secret }),
      });
      if (!res.ok) throw new EvaluationApiError("EVAL_AUTH_FAILED", "evaluation-auth", res.status);
    }
    csrfToken = ((await res.json()) as { csrf_token: string }).csrf_token;
  })().catch((err) => { sessionPromise = null; throw err; });
  return sessionPromise;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  await ensureSession();
  const res = await fetch(`${evaluationBase()}${path}`, {
    ...init, credentials: "include",
    headers: { ...(init.headers ?? {}), "X-CSRF-Token": csrfToken },
  });
  if (res.status === 401 && retry) {
    resetEvaluationSession();
    return request<T>(path, init, false);
  }
  if (!res.ok) {
    let code = "EVAL_ERROR"; let message = `HTTP ${res.status}`;
    try { const body = await res.json(); code = body?.error?.code ?? code; message = body?.error?.message || message; } catch { /* keep defaults */ }
    throw new EvaluationApiError(code, message, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
```

公开方法（类型对齐契约）：`overview`、`listRuns(filter)`、`getRun(id)`、`listAttempts(runId)`、`getAttempt(id)`、`getTrace(attemptId, afterSeq?)`、`createRun(input)`（自动生成 `Idempotency-Key: crypto.randomUUID()`）、`cancelRun(id)`、`rerunRun(runId, attemptIds)`、`listSuites()`、`listCases(suiteId)`、`listProfiles()`、`importBasicSuite()`、`downloadRunExport(runId, format)`（Blob + a.download）、`downloadDatasetTemplate()`、`importDataset(file)`（FormData，不设 Content-Type）、`exportDatasetXlsx(suiteId)`、`downloadSummaryReport(suiteId)`。写方法统一带 `Idempotency-Key`。
- [ ] **Step 3: 测试通过** — `cd webui && npx vitest run src/tests/evaluation-api.test.ts`。
- [ ] **Step 4: Commit** — `feat(webui): 评估 API 客户端（自动登录/CSRF/重试/导出）`

## WU-11 Settings 注册 + 页面（总览 / 运行 / 详情 / 新建运行）

**Files:**
- Modify: `webui/src/components/settings/contracts.ts`（union 加 `"eval"`）、`SettingsSidebar.tsx`（NAV 项，icon `ClipboardCheck`，放在 `agents` 之后）、`SettingsPage.tsx`（`case "eval"`）
- Create: `webui/src/components/settings/evaluation/EvaluationSettings.tsx`、`EvaluationRuns.tsx`、`RunDetailSheet.tsx`、`NewRunDialog.tsx`、`evaluation-status.tsx`
- Test: `webui/src/tests/evaluation-settings.test.tsx`

- [ ] **Step 1: 注册**。`contracts.ts` union 加 `"eval"`；`SettingsSidebar.SETTINGS_NAV_ITEMS` 加 `{ key: "eval", icon: ClipboardCheck, fallback: "Evaluation" }`（`standaloneSectionTitle` 无需改，走 nav fallback）；`SettingsPage.renderSection` 加：

```tsx
      case "eval":
        return <EvaluationSettings />;
```

（`EvaluationSettings` 不依赖 `settings` payload；`SettingsPage` 在 `settings` 为 null 时不渲染 section —— 若 eval 需在 settings 未加载时可用，把 `case "eval"` 提到 `renderSection` 的 `if (!settings)` 之前单独返回。）
- [ ] **Step 2: 组件**。`evaluation-status.tsx`：状态→文案/色映射（passed 绿、failed 红、review_required 琥珀、timed_out 橙、running 蓝、queued/灰色，全部走 `t("settings.evaluation.status.*")`）。
`EvaluationSettings.tsx`：`SegmentedControl` 三 Tab（overview/runs/datasets），顶部刷新按钮 + 服务在线状态点（`evaluationApi.overview()` 成功即在线）。overview：指标卡（planned/passed/failed/review_required/not_evaluated/overall_pass_rate/evaluation_coverage，来自 `/overview.metrics` 与 `status_counts`）+ 最近运行列表（点击切到运行 Tab 并打开详情）。runs：状态筛选 + 表格（suite_name、status、progress `terminal/planned`、created_at 本地化时间、行点击开 `RunDetailSheet`）+「新建运行」按钮。
`NewRunDialog.tsx`：目标类型 SegmentedControl（agent/team/skill/dataset）；agent/skill 目标为文本输入（首版，档案/技能名手输，失焦校验非空）；dataset 目标 = suite 下拉；模型下拉（`listProfiles()`，`kind` 含 agent）；judge 可选；`总时限`：开关「不限制」+ 秒数输入（默认 600，ge=5）；提交 `createRun({suite_id, case_ids, agent_profile_id, judge_profile_id, target, run_timeout_s})`，成功后关闭并打开运行详情。
`RunDetailSheet.tsx`：Sheet side="right"；头部状态 + counts + metrics 通过率；操作：取消（running 时）、重跑失败项（`attempts.filter(a=>a.verdict==="failed").map(id)` → `rerunRun`）、导出 JSON/CSV、下载 HTML 汇总（`downloadSummaryReport(run.suite_id)`）；attempts 表（case_name、execution_status、grading_status、verdict、耗时）；行展开：errors 列表、assertions 结果、`加载 Trace` 按钮 → `getTrace` 分页 `after_seq` 渲染事件列表（type + turn/iteration + payload 摘要，`tool.started/completed` 高亮工具名与参数摘要 —— 工具链视图的最小实现）。
- [ ] **Step 3: 交互测试**（mock `@/lib/evaluation-api`）：Tab 切换渲染；新建运行提交携带 target 与 run_timeout_s；运行详情展开 Trace。测试文件骨架：

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EvaluationSettings } from "@/components/settings/evaluation/EvaluationSettings";

vi.mock("@/lib/evaluation-api", () => ({
  evaluationApi: {
    overview: vi.fn().mockResolvedValue({ recent_runs: [], metrics: null, status_counts: {} }),
    listRuns: vi.fn().mockResolvedValue({ data: [] }),
    listSuites: vi.fn().mockResolvedValue({ data: [{ id: "s1", name: "数据集A", case_count: 2 }] }),
    listCases: vi.fn().mockResolvedValue({ data: [{ id: "c1", name: "用例1", enabled: true }] }),
    listProfiles: vi.fn().mockResolvedValue({ data: [{ id: "model:fast", label: "fast", provider: "openai", model: "gpt-4o-mini", kind: "both" }] }),
    createRun: vi.fn().mockResolvedValue({ id: "r9" }),
  },
  EvaluationApiError: class extends Error {},
}));
// 需要 i18n：沿用其他 settings 测试的初始化方式（参考 webui/src/tests/settings-*.test.tsx 的 setup）
```

- [ ] **Step 4: Commit** — `feat(webui): Settings 评估页（总览/运行/新建/详情/Trace）`

## WU-12 数据集 Tab + i18n + 构建验证

**Files:**
- Create: `webui/src/components/settings/evaluation/EvaluationDatasets.tsx`
- Modify: 10 个 `webui/src/i18n/locales/<loc>/common.json`
- Test: `webui/src/tests/evaluation-settings.test.tsx` 追加数据集用例

- [ ] **Step 1: 数据集组件**：suite 列表（名称/用例数/标签）+ 操作：下载模板（`downloadDatasetTemplate`）、导入（`<input type="file" accept=".xlsx">` → `importDataset(file)`，展示返回的 `case_count` 与逐行 `errors`）、导出 xlsx、查看用例（展开 `listCases`）、下载 HTML 汇总（`downloadSummaryReport(suiteId)`）。
- [ ] **Step 2: i18n**。`settings.nav.eval` + `settings.evaluation.*` 键集（en 基准，其余 9 locale 必须同形；zh-CN/zh-TW 给中文，其余 7 locale 暂用英文文案）：

```text
settings.evaluation.title / subtitle / refresh / online / offline
settings.evaluation.tab.overview / tab.runs / tab.datasets
settings.evaluation.metrics.planned / passed / failed / reviewRequired / notEvaluated
settings.evaluation.metrics.passRate / coverage / recentRuns
settings.evaluation.runs.new / runs.filterAll / runs.empty / runs.progress
settings.evaluation.newRun.title / target / targetAgent / targetTeam / targetSkill / targetDataset
settings.evaluation.newRun.model / judge / judgeOptional / dataset / cases
settings.evaluation.newRun.noLimit / timeLimitSeconds / submit / cancel
settings.evaluation.detail.attempts / errors / assertions / trace / loadTrace / exportJson / exportCsv
settings.evaluation.detail.cancelRun / rerunFailed / summaryReport / noAttempts
settings.evaluation.datasets.template / import / importing / imported / export / errors / cases / empty
settings.evaluation.status.passed / failed / reviewRequired / notEvaluated / running / queued
settings.evaluation.status.completed / cancelled / interrupted / cancelling / timedOut
settings.evaluation.error.offline / error.auth / error.request
```

en 值示例：`title: "Evaluation"`, `tab.overview: "Overview"`, `metrics.passRate: "Pass rate"`, `status.timedOut: "Timed out"`；zh-CN：`title: "评估"`, `tab.overview: "总览"`, `metrics.passRate: "通过率"`, `status.timedOut: "已超时"`。**en 与其余 locale 的键集必须完全一致（含 `settings.nav.eval`），否则 `i18n.test.tsx` 的形状校验失败。**
- [ ] **Step 3: 构建验证** — `cd webui && npx tsc -p tsconfig.build.json --noEmit && npx vitest run src/tests/evaluation-api.test.ts src/tests/evaluation-settings.test.tsx src/tests/i18n.test.tsx`；`npm run lint`（新增文件零告警）。
- [ ] **Step 4: Commit** — `feat(webui): 评估数据集 Tab 与十语言文案`

## WU-13 桌面端端到端验证

- [ ] 起服务：gateway（用户已在跑 8765 则复用；否则 `.venv/Scripts/nanobot.exe gateway`）+ `cd webui && npx vite --port 5174 --strictPort`（避开用户的 5173）。
- [ ] 浏览器（agent-browser/browser-use）打开 `http://127.0.0.1:5174/#/?bootstrapSecret=<从 ~/.nanobot/config.json 读，不打印>&section=eval`，验证：登录自动完成、总览指标、新建运行（选真实模型 + basic-v1 用例）能跑完并看到 verdict/Trace、Excel 模板可下载且导入回环成功、HTML 汇总可下载。
- [ ] 记录截图/量测到 `verifications/2026-10-23-evaluation-webui-collective-test.md`。

---

## 验证命令汇总

```bash
# 后端（工作树根目录）
.venv/Scripts/python.exe -m pytest tests/evaluation -q          # 基线 702 passed / 5 failed(校准 WIP)
.venv/Scripts/python.exe -m ruff check nanobot/evaluation nanobot/cli/gateway_runtime.py
.venv/Scripts/python.exe -m basedpyright nanobot/evaluation
# 前端
cd webui && npx tsc -p tsconfig.build.json --noEmit && npx vitest run src/tests/evaluation-api.test.ts src/tests/evaluation-settings.test.tsx src/tests/i18n.test.tsx
```

## 提交纪律

- 每个 WU 独立 commit（Angular 前缀，见 harness git-xywh）；提交前 `git status --porcelain` 重新核对，**不提交** `webui/package-lock.json` 的无关抖动与 `*-cache/` 产物。
- 不推送；push 由用户确认后执行。

## Next

**（写入后暂停，等待用户确认）**

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见（例如调整目标优先级、砍掉某 WU）
- 想并行 → 说「并行执行」，我会补 `*-dispatch.md` 后按 WU 派发
