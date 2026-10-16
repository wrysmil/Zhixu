"""T10 产物下载：只认登记行、再过一次目录包含判定，响应头恒为 attachment + nosniff。

这里用仓储直接造 attempt 行（不起 HTTP），因为要验证的是「登记数据被篡改后
下载层还能不能兜住」——那种场景在正常写入路径上根本构造不出来。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.evaluation import artifacts as artifact_module
from nanobot.evaluation.artifacts import (
    ArtifactError,
    attachment_headers,
    media_type_for,
    resolve_artifact,
)
from nanobot.evaluation.errors import ERROR_STATUS, status_for
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.schemas import RunCreate

from .conftest import make_case_payload, make_run_payload, make_suite_payload


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


@pytest.fixture
def attempt_id(repository: EvaluationRepository) -> str:
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"], agent_profile_id="agent-1")
    )
    run = repository.create_run(
        scope="local-user",
        idempotency_key="artifact-test-1",
        request=request,
        agent={"id": "agent-1", "provider": "controlled", "model": "eval-model"},
        judge=None,
        rules_version="rules-v1",
        grader_version="grader-v1",
    )
    return str(run["attempts"][0]["id"])


def _workspace(runtime_root: Path, attempt_id: str) -> Path:
    workspace = runtime_root / attempt_id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _register(
    repository: EvaluationRepository,
    attempt_id: str,
    *,
    relative_path: str,
    media_type: str = "text/plain; charset=utf-8",
    size_bytes: int = 4,
) -> str:
    row = repository.append_artifact(
        attempt_id=attempt_id,
        relative_path=relative_path,
        media_type=media_type,
        size_bytes=size_bytes,
        sha256="a" * 64,
    )
    return str(row["id"])


# ------------------------------------------------------------------ 媒体类型


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("out.txt", "text/plain; charset=utf-8"),
        ("result.JSON", "application/json; charset=utf-8"),
        ("notes.md", "text/markdown; charset=utf-8"),
        ("table.csv", "text/csv; charset=utf-8"),
        ("payload.html", "application/octet-stream"),
        ("no_suffix", "application/octet-stream"),
    ],
)
def test_media_type_falls_back_to_octet_stream(name: str, expected: str) -> None:
    """可执行/可渲染的类型一律降级为字节流：浏览器不得把产物当页面跑。"""
    assert media_type_for(name) == expected


# -------------------------------------------------------------------- 解析


def test_resolve_reads_the_registered_file_with_actual_size(
    repository: EvaluationRepository, attempt_id: str, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    (_workspace(runtime_root, attempt_id) / "output.txt").write_text("内容", encoding="utf-8")
    artifact_id = _register(repository, attempt_id, relative_path="output.txt", size_bytes=999)
    resolved = resolve_artifact(repository, artifact_id, runtime_root=runtime_root)
    assert resolved.path.name == "output.txt"
    assert resolved.size_bytes == 6  # 以磁盘实际字节为准，登记值不可信
    assert resolved.filename == "output.txt"


def test_resolve_handles_nested_relative_path(
    repository: EvaluationRepository, attempt_id: str, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    workspace = _workspace(runtime_root, attempt_id)
    (workspace / "reports").mkdir()
    (workspace / "reports" / "trace.md").write_text("# 轨迹", encoding="utf-8")
    artifact_id = _register(
        repository, attempt_id, relative_path="reports/trace.md",
        media_type="text/markdown; charset=utf-8",
    )
    resolved = resolve_artifact(repository, artifact_id, runtime_root=runtime_root)
    assert resolved.filename == "trace.md"
    assert resolved.path == (workspace / "reports" / "trace.md").resolve()


def test_unknown_artifact_id_is_not_found(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    with pytest.raises(ArtifactError) as exc:
        resolve_artifact(
            repository, "00000000-0000-4000-8000-000000000000",
            runtime_root=tmp_path / "runtime",
        )
    assert exc.value.code == "ARTIFACT_NOT_FOUND"


@pytest.mark.parametrize(
    "tampered",
    ["../../outside.txt", "..\\outside.txt", "/etc/passwd", "C:/Windows/win.ini", "a/../../b"],
)
def test_tampered_relative_path_escapes_the_workspace(
    repository: EvaluationRepository, attempt_id: str, tmp_path: Path, tampered: str
) -> None:
    """登记行本身被改坏（库损坏/外部导入）时，下载层仍不得读出工作区之外的文件。"""
    runtime_root = tmp_path / "runtime"
    (_workspace(runtime_root, attempt_id) / "output.txt").write_text("内容", encoding="utf-8")
    outside = runtime_root.parent / "outside.txt"
    outside.write_text("机密", encoding="utf-8")
    artifact_id = _register(repository, attempt_id, relative_path="output.txt")
    with repository._db.connect() as conn:  # noqa: SLF001 - 正常写入造不出篡改态
        conn.execute(
            "UPDATE artifacts SET relative_path = ? WHERE id = ?", (tampered, artifact_id)
        )
    with pytest.raises(ArtifactError) as exc:
        resolve_artifact(repository, artifact_id, runtime_root=runtime_root)
    assert exc.value.code == "PATH_OUTSIDE_ROOT"
    assert outside.read_text(encoding="utf-8") == "机密"


def test_missing_file_on_disk_is_not_found(
    repository: EvaluationRepository, attempt_id: str, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    _workspace(runtime_root, attempt_id)
    artifact_id = _register(repository, attempt_id, relative_path="output.txt")
    with pytest.raises(ArtifactError) as exc:
        resolve_artifact(repository, artifact_id, runtime_root=runtime_root)
    assert exc.value.code == "ARTIFACT_NOT_FOUND"


def test_oversized_artifact_is_refused(
    repository: EvaluationRepository, attempt_id: str, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    (_workspace(runtime_root, attempt_id) / "huge.txt").write_text("0123456789", encoding="utf-8")
    artifact_id = _register(repository, attempt_id, relative_path="huge.txt", size_bytes=10)
    monkeypatch.setattr(artifact_module, "MAX_ARTIFACT_BYTES", 4)
    with pytest.raises(ArtifactError) as exc:
        resolve_artifact(repository, artifact_id, runtime_root=runtime_root)
    assert exc.value.code == "ARTIFACT_TOO_LARGE"


# ------------------------------------------------------------------ 响应头


def test_attachment_headers_force_download_and_disable_sniffing(
    repository: EvaluationRepository, attempt_id: str, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    (_workspace(runtime_root, attempt_id) / "report.csv").write_bytes(b"a,b\n")
    artifact_id = _register(
        repository, attempt_id, relative_path="report.csv", media_type="text/csv; charset=utf-8"
    )
    resolved = resolve_artifact(repository, artifact_id, runtime_root=runtime_root)
    headers = attachment_headers(resolved)
    assert headers["Content-Disposition"] == 'attachment; filename="report.csv"'
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Length"] == "4"


def test_filename_cannot_inject_headers_or_break_disposition() -> None:
    """文件名里的引号与换行会被剥掉：否则一个产物名就能往响应头里塞字段。"""
    resolved = artifact_module.ResolvedArtifact(
        path=Path("whatever"),
        media_type="text/plain; charset=utf-8",
        filename="x\"\r\nSet-Cookie: session=stolen",
        size_bytes=2,
    )
    disposition = attachment_headers(resolved)["Content-Disposition"]
    assert "\r" not in disposition and "\n" not in disposition



# ------------------------------------------------------------- 错误码登记


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("ARTIFACT_NOT_FOUND", 404),
        ("PATH_OUTSIDE_ROOT", 422),
        ("ARTIFACT_TOO_LARGE", 413),
        ("PAYLOAD_TOO_LARGE", 413),
        ("METHOD_NOT_ALLOWED", 405),
        ("NOT_FOUND", 404),
        ("VALIDATION_ERROR", 422),
    ],
)
def test_artifact_and_http_error_codes_are_registered(code: str, status: int) -> None:
    """未登记的码会退化成 500：等于把「预期的拒绝」伪装成服务故障。"""
    assert code in ERROR_STATUS
    assert status_for(code) == status


def test_resolve_error_codes_match_the_status_table(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    """ArtifactError 用的码必须都在码表里，否则路由层会把它变成 INTERNAL_ERROR。"""
    with pytest.raises(ArtifactError) as exc:
        resolve_artifact(repository, "not-an-uuid", runtime_root=tmp_path)
    assert exc.value.code in ERROR_STATUS


def test_repository_rejects_absolute_paths_at_registration(
    repository: EvaluationRepository, attempt_id: str
) -> None:
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        repository.append_artifact(
            attempt_id=attempt_id, relative_path="/etc/passwd",
            media_type="text/plain", size_bytes=1, sha256="b" * 64,
        )
