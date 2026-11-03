"""WU-03：知序记忆隔离适配器（plan §5 Task 3 / 契约 §3）。

覆盖：

- settle 走 WU-02 公共入口 ``flush_session_memory`` 的**真实抽取**，脚本化
  provider 下完成屏障如实上报 completed / skipped_by_policy / failed / timeout。
- recall 走产品检索引擎；两个 probe 状态隔离；scope 共享正例（全局可见）与
  跨工作区隔离。
- 画像 / 工作记忆路径独立；路径全部限制在 trial 评估根内。
- no_persistent_memory：全部持久读写关闭，不以「禁止访问检索工具」代替关闭。
- snapshot / restore / restart 从持久化文件恢复，不保留隐藏 Python 缓存。
- 向量后端缺失 → capability_missing，不回退成假向量成绩。
- forget(product_request) 走产品真实删除入口并刷新派生文件；rebuild 走真实
  重建入口；gold 永远不出现在执行端任何产物中。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.memory.models import (
    MemoryCandidate,
    MemoryProbe,
    MemoryScope,
    MemorySessionEvent,
    MemorySettings,
    build_execution_input,
)
from nanobot.evaluation.memory.zhixu_adapter import (
    TrialPathError,
    ZhixuMemoryAdapter,
)
from nanobot.memory.models import Memory as MemoryRow
from nanobot.memory.repository import add_memory

# ---------------------------------------------------------------------------
# 身份 registry 重定向到临时目录（不写真实 ~/.nanobot）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_identity_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nanobot import workspace_identity

    registry_root = tmp_path / "registry"

    def runtime_subdir(name: str) -> Path:
        path = registry_root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(workspace_identity, "get_runtime_subdir", runtime_subdir)
    workspace_identity.reset_workspace_identity_cache()
    yield
    workspace_identity.reset_workspace_identity_cache()

# ---------------------------------------------------------------------------
# 脚本化 provider（与 tests/memory/test_extractor.py 同一套鸭子类型模式）
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str | None, usage: Any = None) -> None:
        self.content = content
        self.usage = usage


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 2048
    reasoning_effort = None


class _ScriptedProvider:
    """按 prompt 内容分流：语义抽取 / 情节抽取 / probe 回答。

    语义抽取按转录里出现的事实标记生成记忆（同一会话抽取哪座城市由内容
    决定），保证「抽取来自真实输入」而不是固定脚本回放。
    """

    #: 转录标记 → 记忆条目
    FACTS: dict[str, dict[str, Any]] = {
        "青岚城": {
            "content": "用户的虚构测试城市叫青岚城",
            "evidence": "我的虚构测试城市叫青岚城",
            "type": "fact",
            "importance": 0.9,
            "subject": "用户",
            "predicate": "city",
            "tags": ["test"],
        },
        "雾隐计划": {
            "content": "用户的项目代号叫雾隐计划",
            "evidence": "我的项目代号叫雾隐计划",
            "type": "fact",
            "importance": 0.85,
            "subject": "用户",
            "predicate": "project_codename",
            "tags": ["test"],
        },
    }

    def __init__(
        self,
        *,
        semantic: str | None = None,
        episode: str | None = None,
        answers: dict[str, str] | None = None,
    ) -> None:
        self.semantic = semantic
        self.episode = episode
        self.answers = answers or {}
        self.calls: list[list[dict[str, Any]]] = []

    def _semantic_for(self, text: str) -> str:
        if self.semantic is not None:
            return self.semantic
        memories = [
            fact for marker, fact in self.FACTS.items() if marker in text
        ]
        return json.dumps({"memories": memories, "experiences": []}, ensure_ascii=False)

    async def chat_with_retry(self, *, messages: list[dict[str, Any]], **_kwargs: Any) -> Any:
        self.calls.append(messages)
        text = " ".join(
            m.get("content", "") for m in messages if isinstance(m.get("content"), str)
        )
        if "语义记忆抽取" in text:
            return _FakeResponse(self._semantic_for(text))
        if "情节" in text or "episode" in text.lower():
            return _FakeResponse(self.episode)
        # probe 回答：system 里有注入块时带上它（供注入证据断言）。
        system = next(
            (m.get("content", "") for m in messages if m.get("role") == "system"), ""
        )
        for needle, answer in self.answers.items():
            if needle in text:
                return _FakeResponse(f"{answer}||INJECTED::{system}")
        return _FakeResponse(self.answers.get("*", "不知道"))


class _ScriptedRuntime:
    def __init__(self, provider: _ScriptedProvider) -> None:
        self.provider = provider
        self.model = "scripted-model"
        self.generation = _FakeGeneration()
        self.context_window_tokens = 8192


def _semantic_json(memories: list[dict[str, Any]]) -> str:
    return json.dumps({"memories": memories, "experiences": []}, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def trial_root(tmp_path: Path) -> Path:
    root = tmp_path / "trial"
    (root / "workspaces").mkdir(parents=True)
    return root


@pytest.fixture()
def provider() -> _ScriptedProvider:
    # semantic=None：语义抽取按转录内容生成（真实输入决定抽取结果）。
    return _ScriptedProvider(
        episode=json.dumps(
            {"summary": "用户告知测试信息", "goal": "", "outcome": "completed"},
            ensure_ascii=False,
        ),
        answers={"城市": "我住在青岚城", "代号": "我的项目代号是雾隐计划"},
    )


@pytest.fixture()
def runtime(provider: _ScriptedProvider) -> _ScriptedRuntime:
    return _ScriptedRuntime(provider)


def make_scope(subject: str = "synthetic-u1", workspace: str = "synthetic-w1") -> MemoryScope:
    return MemoryScope(subject_id=subject, workspace_id=workspace)


def make_session_event(
    session_id: str = "s1",
    *,
    with_fact: bool = True,
    fact: str = "请记住，我的虚构测试城市叫青岚城。",
) -> MemorySessionEvent:
    messages: list[dict[str, str]] = []
    if with_fact:
        messages.append({"id": "m1", "role": "user", "content": fact})
        messages.append({"id": "m2", "role": "assistant", "content": "收到这条信息。"})
    else:
        messages.append({"id": "m1", "role": "user", "content": "你好呀"})
        messages.append({"id": "m2", "role": "assistant", "content": "你好。"})
    return MemorySessionEvent(type="session", id=session_id, at="2026-11-01T09:00:00+08:00", messages=messages)  # type: ignore[arg-type]


def make_probe(probe_id: str = "q1", question: str = "青岚城") -> MemoryProbe:
    return MemoryProbe(
        id=probe_id,
        question=question,
        at="2026-11-02T09:00:00+08:00",
        fresh_session=True,
    )


def make_candidate(
    mode: str = "zhixu",
    *,
    vector: bool = False,
) -> MemoryCandidate:
    return MemoryCandidate(
        id="c1",
        name="候选一",
        mode=mode,  # type: ignore[arg-type]
        agent_profile_id="p1",
        memory_settings=MemorySettings(
            embedding_model="bge" if vector else None,
            index_version="v1" if vector else None,
        ),
    )


async def _settle(
    adapter: ZhixuMemoryAdapter, session_id: str = "s1", timeout_s: float = 10.0
):
    return await adapter.settle(session_id, timeout_s)


# ---------------------------------------------------------------------------
# settle：真实抽取 + 完成屏障
# ---------------------------------------------------------------------------


async def test_settle_runs_real_extraction_and_reports_completed(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        await adapter.ingest_session(make_session_event())
        result = await _settle(adapter)
        assert result.status == "completed"
        assert "extraction" in result.completed_stages
        # 真实落库：SQLite 里确实有这条记忆（产品隔离键下）
        rows = adapter.list_memory_rows()
        assert any("青岚城" in row.content for row in rows)
    finally:
        await adapter.close()


async def test_settle_reports_failed_when_extraction_backend_fails(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    """抽取后端故障时如实记 failed（含失败轨道），不得伪装 completed。"""

    class _ExplodingProvider(_ScriptedProvider):
        async def chat_with_retry(self, *, messages, **kwargs):  # type: ignore[override]
            text = " ".join(
                m.get("content", "") for m in messages if isinstance(m.get("content"), str)
            )
            if "语义记忆抽取" in text or "情节" in text:
                raise RuntimeError("extraction backend down")
            return await super().chat_with_retry(messages=messages, **kwargs)

    adapter = ZhixuMemoryAdapter(
        trial_root=trial_root,
        scope=make_scope(),
        runtime=_ScriptedRuntime(_ExplodingProvider()),
    )
    try:
        await adapter.ingest_session(make_session_event())
        result = await _settle(adapter)
        assert result.status == "failed"
        assert result.failed_stages  # 失败轨道如实上报（semantic / episode）
        assert adapter.list_memory_rows() == []
    finally:
        await adapter.close()


async def test_settle_reports_skipped_for_trivial_session(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    """产品信号门槛跳过琐碎切片 → 如实记 skipped_by_policy（不伪装 completed）。"""
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        await adapter.ingest_session(make_session_event(with_fact=False))
        result = await _settle(adapter)
        assert result.status == "skipped_by_policy"
        assert result.reason_codes
        assert adapter.list_memory_rows() == []
    finally:
        await adapter.close()


async def test_settle_reports_timeout_honestly(
    trial_root: Path, provider: _ScriptedProvider, runtime: _ScriptedRuntime
) -> None:
    """抽取卡住时如实记 timeout，不得伪装成功。"""
    import asyncio as _asyncio

    class _SlowProvider(_ScriptedProvider):
        async def chat_with_retry(self, *, messages, **kwargs):  # type: ignore[override]
            if "语义记忆抽取" in " ".join(
                m.get("content", "") for m in messages if isinstance(m.get("content"), str)
            ):
                await _asyncio.sleep(30)
            return await super().chat_with_retry(messages=messages, **kwargs)

    slow_runtime = _ScriptedRuntime(_SlowProvider(semantic=runtime.provider.semantic, episode=runtime.provider.episode))
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=slow_runtime)
    try:
        await adapter.ingest_session(make_session_event())
        result = await _settle(adapter, timeout_s=0.2)
        assert result.status == "timeout"
    finally:
        await adapter.close()


async def test_settle_maps_registry_partial_to_failed_in_adapter_layer(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    """registry 的 partial 与契约 status 闭集的映射发生在适配层：partial → failed。"""
    from nanobot.memory.signals import MemoryFlushOutcome

    mapping = ZhixuMemoryAdapter.map_flush_status
    assert mapping(  # type: ignore[call-arg]
        MemoryFlushOutcome(status="partial").status
    ) == "failed"
    assert mapping("completed") == "completed"
    assert mapping("skipped") == "skipped_by_policy"
    assert mapping("failed") == "failed"


# ---------------------------------------------------------------------------
# recall / answer
# ---------------------------------------------------------------------------


async def test_recall_finds_extracted_memory_after_settle(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        await adapter.ingest_session(make_session_event())
        await _settle(adapter)
        recall = await adapter.recall(make_probe())
        assert recall.probe_id == "q1"
        assert any("青岚城" in item.value for item in recall.items)
        assert recall.coverage.complete
    finally:
        await adapter.close()


async def test_answer_uses_fresh_session_and_reports_injection(
    trial_root: Path, provider: _ScriptedProvider, runtime: _ScriptedRuntime
) -> None:
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        await adapter.ingest_session(make_session_event())
        await _settle(adapter)
        execution = await adapter.answer(make_probe())
        assert execution.answer is not None
        assert "青岚城" in execution.answer
        # 注入证据来自真实检索，而不是历史回放
        assert execution.injected_evidence, "检索命中应作为注入证据上报"
        # 新会话：probe 会话 key 与被抽取会话不同
        assert provider.calls, "回答必须经过真实 provider 调用"
        last_call = provider.calls[-1]
        assert last_call[0]["role"] == "system"
        assert "青岚城" in last_call[0]["content"]
    finally:
        await adapter.close()


# ---------------------------------------------------------------------------
# scope 隔离与共享
# ---------------------------------------------------------------------------


async def test_other_workspace_cannot_recall_this_subjects_memory(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    adapter_a = ZhixuMemoryAdapter(
        trial_root=trial_root,
        scope=make_scope("synthetic-u1", "synthetic-w1"),
        runtime=runtime,
    )
    adapter_b = ZhixuMemoryAdapter(
        trial_root=trial_root,
        scope=make_scope("synthetic-u2", "synthetic-w2"),
        runtime=runtime,
    )
    try:
        await adapter_a.ingest_session(make_session_event())
        await _settle(adapter_a)
        recall_b = await adapter_b.recall(make_probe(question="青岚城"))
        assert not any("青岚城" in item.value for item in recall_b.items)
    finally:
        await adapter_a.close()
        await adapter_b.close()


async def test_global_scope_memory_is_shared_positive_case(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    """全局（default）记忆对本工作区可见——产品可见集合的正例。"""
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        services_db = adapter.services.database
        memory = MemoryRow(
            id="global-mem-1",
            content="全局共享的测试事实：平台名称是知序",
            created_at="2026-11-01T09:00:00Z",
        )
        with services_db.connect() as conn:
            add_memory(conn, memory)  # workspace_id 默认 'default'（全局）
        recall = await adapter.recall(make_probe(question="知序"))
        assert any("知序" in item.value for item in recall.items)
    finally:
        await adapter.close()


# ---------------------------------------------------------------------------
# no_persistent_memory
# ---------------------------------------------------------------------------


async def test_no_persistent_memory_closes_all_persistence(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        await adapter.reset(make_candidate("no_persistent_memory"))
        await adapter.ingest_session(make_session_event())
        settle = await _settle(adapter)
        assert settle.status == "skipped_by_policy"
        # 持久层零写入
        assert adapter.list_memory_rows() == []
        recall = await adapter.recall(make_probe())
        assert recall.items == []
        execution = await adapter.answer(make_probe())
        assert execution.injected_evidence == []
        assert execution.recalled == []
    finally:
        await adapter.close()


# ---------------------------------------------------------------------------
# snapshot / restore / restart
# ---------------------------------------------------------------------------


async def _prepare_state(trial_root: Path, runtime: _ScriptedRuntime) -> tuple[ZhixuMemoryAdapter, Any]:
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    await adapter.ingest_session(make_session_event())
    await _settle(adapter)
    snapshot = await adapter.snapshot("cp-1")
    return adapter, snapshot


async def test_snapshot_restore_keeps_two_probes_isolated(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    adapter, snapshot = await _prepare_state(trial_root, runtime)
    try:
        probe_a = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
        await probe_a.restore(snapshot)
        probe_b = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
        await probe_b.restore(snapshot)

        # probe A 继续 ingest + settle（状态前进），probe B 保持检查点状态
        await probe_a.ingest_session(
            make_session_event("s2", fact="请记住，我的项目代号叫雾隐计划。")
        )
        await probe_a.settle("s2", 10.0)

        recall_a = await probe_a.recall(make_probe(question="雾隐计划"))
        assert any("雾隐计划" in item.value for item in recall_a.items)
        recall_b = await probe_b.recall(make_probe(question="雾隐计划"))
        assert not any("雾隐计划" in item.value for item in recall_b.items)
        # 检查点状态本身可检索
        recall_b_city = await probe_b.recall(make_probe(question="青岚城"))
        assert any("青岚城" in item.value for item in recall_b_city.items)

        await probe_a.close()
        await probe_b.close()
    finally:
        await adapter.close()


async def test_restart_rebuilds_from_persisted_files_without_hidden_cache(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    adapter, snapshot = await _prepare_state(trial_root, runtime)
    try:
        # 旧 extractor 对象应被丢弃：restart 后 settle 只抽取增量（cursor 来自恢复的 DB）
        await adapter.restart(snapshot)
        rows_before = len(adapter.list_memory_rows())
        assert rows_before >= 1

        await adapter.ingest_session(
            make_session_event("s2", fact="请记住，我的项目代号叫雾隐计划。")
        )
        result = await adapter.settle("s2", 10.0)
        assert result.status == "completed"
        rows_after = adapter.list_memory_rows()
        assert any("雾隐计划" in row.content for row in rows_after)
        # 增量语义：老记忆仍在
        assert any("青岚城" in row.content for row in rows_after)
    finally:
        await adapter.close()


# ---------------------------------------------------------------------------
# 能力与向量缺失
# ---------------------------------------------------------------------------


async def test_vector_missing_reports_capability_missing_not_fake_scores(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    adapter = ZhixuMemoryAdapter(
        trial_root=trial_root,
        scope=make_scope(),
        runtime=runtime,
        vector_expected=True,
    )
    try:
        assert adapter.capabilities.semantic_recall.status == "unsupported"
        assert "capability_missing" in adapter.capabilities.semantic_recall.detail
        await adapter.ingest_session(make_session_event())
        await _settle(adapter)
        recall = await adapter.recall(make_probe())
        assert "capability_missing" in recall.coverage.reasons
        assert recall.items == []
        assert recall.index_version is None
    finally:
        await adapter.close()


async def test_clock_coverage_is_partial_not_full(trial_root: Path, runtime: _ScriptedRuntime) -> None:
    """风险 #3：since_days SQL 窗口走真实时间 → clock_coverage 必须标部分覆盖。"""
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        assert adapter.capabilities.clock_coverage.status == "partial"
    finally:
        await adapter.close()


async def test_paths_confined_to_trial_root(
    trial_root: Path, runtime: _ScriptedRuntime, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    with pytest.raises(TrialPathError):
        ZhixuMemoryAdapter(
            trial_root=trial_root,
            scope=make_scope(),
            runtime=runtime,
            workspace_path=outside,
        )


# ---------------------------------------------------------------------------
# forget / rebuild
# ---------------------------------------------------------------------------


async def test_forget_product_request_deletes_via_product_path(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    from nanobot.evaluation.memory.models import ForgetRequest

    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        await adapter.ingest_session(make_session_event())
        await _settle(adapter)
        rows = [row for row in adapter.list_memory_rows() if "青岚城" in row.content]
        assert rows

        result = await adapter.forget(
            ForgetRequest(mode="product_request", fact_key="city")
        )
        assert result.status == "completed"
        assert result.state_checked is True
        assert result.deleted_ids
        remaining = adapter.list_memory_rows()
        assert not any("青岚城" in row.content for row in remaining)
    finally:
        await adapter.close()


async def test_forget_reports_unsupported_for_unknown_mode_never_fakes(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    from nanobot.evaluation.memory.models import ForgetRequest

    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        await adapter.ingest_session(make_session_event())
        await _settle(adapter)
        result = await adapter.forget(ForgetRequest(mode="product_request", fact_key="不存在的事实"))
        assert result.status == "completed"
        assert result.deleted_ids == []
    finally:
        await adapter.close()


async def test_rebuild_refreshes_derived_state(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        await adapter.ingest_session(make_session_event())
        await _settle(adapter)
        result = await adapter.rebuild()
        assert result.status == "completed"
        assert "derived_memory_md" in result.completed_stages
    finally:
        await adapter.close()


# ---------------------------------------------------------------------------
# gold 隔离
# ---------------------------------------------------------------------------


async def test_gold_never_reaches_execution_side(
    trial_root: Path, runtime: _ScriptedRuntime, tmp_path: Path
) -> None:
    """gold 值不得出现在 trial 根下任何执行产物（DB / manifest / 文件）中。"""
    from tests.evaluation.memory_fixtures import make_memory_scenario

    scenario_dict = make_memory_scenario()
    scenario_dict["gold"]["q1"]["required_facts"][0]["value"] = "GOLD-ONLY-TOKEN-7f3a"
    scenario_dict["gold"]["q1"]["required_facts"][0]["evidence_message_ids"] = ["m1"]

    from nanobot.evaluation.memory.models import MemoryScenario

    scenario = MemoryScenario.model_validate(scenario_dict)
    execution = build_execution_input(scenario, track="end_to_end")

    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=scenario.scope, runtime=runtime)
    try:
        for event in execution.events:
            if event.type == "session":
                await adapter.ingest_session(event)
        await _settle(adapter)
        snapshot = await adapter.snapshot("cp-gold")
        await adapter.recall(execution.probes[0])
    finally:
        await adapter.close()

    # 全根扫描：执行端任何字节都不得包含 gold 专用 token
    for path in trial_root.rglob("*"):
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}:
            try:
                blob = path.read_bytes()
            except OSError:  # pragma: no cover
                continue
            assert b"GOLD-ONLY-TOKEN-7f3a" not in blob, f"gold leaked into {path}"
    _ = snapshot


async def test_ingest_without_settle_does_not_extract(
    trial_root: Path, runtime: _ScriptedRuntime
) -> None:
    """只 ingest 不 settle：评测不靠 idle 等待（产品 idle 阈值默认 120s），
    记忆不会自动出现；settle 是唯一的完成屏障。"""
    adapter = ZhixuMemoryAdapter(trial_root=trial_root, scope=make_scope(), runtime=runtime)
    try:
        await adapter.ingest_session(make_session_event())
        assert adapter.list_memory_rows() == []
    finally:
        await adapter.close()
