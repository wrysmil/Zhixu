"""T10 产物下载：按 artifact id 解析到已登记文件。

安全边界（契约 §3 ``GET /artifacts/{id}``）：

- **只认登记行**。请求里没有任何 path 参数可解析，浏览器无法指定要读哪个文件。
- 落盘位置再过一次 :func:`resolve_inside` 目录包含判定：登记行本身被篡改
  （数据库损坏、导入外部数据）也不会读到工作区之外。
- 一律 ``Content-Disposition: attachment`` + ``nosniff``：产物是数据不是页面，
  浏览器不得把它当 HTML/SVG 执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nanobot.evaluation.repository import EvaluationRepository, RecordNotFound
from nanobot.evaluation.validation import PATH_OUTSIDE_ROOT, resolve_inside

#: 契约 §3 的上传/导入上限在这里兜底：下载侧同样拒绝超大文件，避免把内存吃光。
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024

MEDIA_TYPES: dict[str, str] = {
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
}
FALLBACK_MEDIA_TYPE = "application/octet-stream"


class ArtifactError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ResolvedArtifact:
    path: Path
    media_type: str
    filename: str
    size_bytes: int


def media_type_for(name: str) -> str:
    return MEDIA_TYPES.get(Path(name).suffix.lower(), FALLBACK_MEDIA_TYPE)


def resolve_artifact(
    repository: EvaluationRepository, artifact_id: str, *, runtime_root: Path
) -> ResolvedArtifact:
    """按登记行解析产物；路径越界与文件缺失都收敛成明确错误码。

    登记的 ``relative_path`` 是相对 **该 attempt 的 workspace**，不是相对 runtime
    根：基准弄错会让所有产物都 404，或者更糟——读到别的 attempt 的同名文件。
    """
    try:
        row = repository.get_artifact(artifact_id)
    except RecordNotFound as exc:
        raise ArtifactError("ARTIFACT_NOT_FOUND") from exc
    relative = str(row["relative_path"])
    workspace = Path(runtime_root) / str(row["attempt_id"]) / "workspace"
    try:
        target = resolve_inside(workspace, relative)
    except ValueError as exc:
        # 登记行指向工作区之外：这是数据被篡改，不是一个正常的 404。
        raise ArtifactError(PATH_OUTSIDE_ROOT) from exc
    if not target.is_file():
        raise ArtifactError("ARTIFACT_NOT_FOUND")
    size = target.stat().st_size
    if size > MAX_ARTIFACT_BYTES:
        raise ArtifactError("ARTIFACT_TOO_LARGE")
    filename = Path(relative).name or "artifact"
    return ResolvedArtifact(
        path=target,
        media_type=str(row["media_type"]) or media_type_for(filename),
        filename=filename,
        size_bytes=size,
    )


def attachment_headers(artifact: ResolvedArtifact) -> dict[str, str]:
    """attachment + nosniff：产物永远当下载，不当页面渲染。"""
    safe_name = artifact.filename.replace('"', "").replace("\r", "").replace("\n", "")
    return {
        "Content-Type": artifact.media_type,
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Length": str(artifact.size_bytes),
    }
