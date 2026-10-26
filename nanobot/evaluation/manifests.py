"""运行清单（manifest）：把一次评估的输入、目标、环境与判分固化成可比较的摘要。

契约 §1。清单里只放**摘要与白名单字段**：凭据、绝对路径、随机 attempt 路径
都不能进摘要——否则同一份输入在两台机器上会算出不同摘要，比较就失去意义。

各摘要的边界：

- dataset_digest：用例集合（case_id + revision + 定义），与数据集名称无关。
- target_digest：被测对象本身（档案、提示词、技能文件、团队成员档案）。
- environment_digest：运行环境（fixture、记忆种子、时钟、工具白名单、限额）。
- grading_digest：判分口径（规则版本、量表、评分器配置）。
- candidate_digest：被测变量 = 目标快照 + 生效的模型参数。

任一摘要算不出来时必须显式为 null 并带原因，不允许用空串或 0 假装已知。
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

MANIFEST_VERSION = 2

#: 摘要里绝对不能出现的键名：凭据、令牌、口令、私钥。
_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|apikey|access[_-]?key|secret|token|password|passwd"
    r"|credential|authorization|private[_-]?key|session[_-]?key)"
)

#: 绝对路径（Windows 盘符、UNC、POSIX、~ 展开）在摘要里一律折叠成占位符。
_ABS_PATH_RE = re.compile(
    r"""(?<!\w)[A-Za-z]:[\\/][^\s"'<>|]+"""
    r"""|(?:\\[^\s"'<>|]+)"""
    r"""|(?:~[\\/][^\s"'<>|]+)"""
    r"""|(?<![\w:/])(?:/)(?:[^\s"'<>|/\\]+/)+[^\s"'<>|/\\]*"""
)

_PATH_PLACEHOLDER = "<abs-path>"


def canonical_json(value: Any) -> str:
    """稳定序列化：键排序、无多余空白、非 ASCII 原样保留。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_of(value: Any) -> str:
    """内容摘要；调用方需先决定是否脱敏。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def redact(value: Any) -> Any:
    """递归去掉凭据与绝对路径。

    键命中敏感词直接丢弃——不是替换成假值，假值会让人误以为存在凭据；
    字符串里的绝对路径折叠成占位符，避免随机 attempt 目录影响摘要。
    """
    if isinstance(value, Mapping):
        mapping = cast("Mapping[str, Any]", value)
        out: dict[str, Any] = {}
        for key, item in mapping.items():
            name = str(key)
            if _SECRET_KEY_RE.search(name):
                continue
            out[name] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        items = cast("Sequence[Any]", value)
        return [redact(item) for item in items]
    if isinstance(value, str):
        return _ABS_PATH_RE.sub(_PATH_PLACEHOLDER, value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return str(value)


def _case_index(case_snapshots: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "case_id": str(item["case_id"]),
            "revision": int(item["case_revision"]),
            "definition_digest": digest_of(redact(item["case_snapshot"])),
        }
        for item in case_snapshots
    ]
    rows.sort(key=lambda row: (row["case_id"], row["revision"]))
    return rows


def _environment_of(case_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    execution = cast("Mapping[str, Any]", case_snapshot.get("execution") or {})
    raw_fixtures = cast("Sequence[Mapping[str, Any]]", case_snapshot.get("fixtures") or [])
    fixtures = [
        {
            "path": str(fixture.get("path")),
            "sha256": digest_of(fixture.get("content", "")),
        }
        for fixture in raw_fixtures
    ]
    fixtures.sort(key=lambda row: row["path"])
    allowed = cast("Sequence[Any]", execution.get("allowed_tools") or [])
    return {
        "fixtures": fixtures,
        "memory_seed": redact(case_snapshot.get("memory_seed") or []),
        "clock_iso": case_snapshot.get("clock_iso"),
        "allowed_tools": sorted(str(name) for name in allowed),
        "max_iterations": execution.get("max_iterations"),
        "timeout_s": execution.get("timeout_s"),
    }


def _string_map(value: Any) -> dict[str, str]:
    """把任意映射压成字符串字典；未知来源按空处理，不猜。"""
    if not isinstance(value, Mapping):
        return {}
    mapping = cast("Mapping[str, Any]", value)
    return {str(k): str(v) for k, v in sorted(mapping.items())}


def _rubric_digests(case_snapshots: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for item in case_snapshots:
        frozen = cast("Mapping[str, Any]", item["case_snapshot"] or {})
        rubric = frozen.get("rubric")
        if rubric:
            digests[str(item["case_id"])] = digest_of(redact(rubric))
    return digests


def build_manifest(
    *,
    case_snapshots: Sequence[Mapping[str, Any]],
    model_mode: str,
    tool_modes: Sequence[str],
    limits: Mapping[str, Any],
    target_snapshot: Mapping[str, Any] | None,
    agent_profile: Mapping[str, Any],
    judge_profile: Mapping[str, Any] | None,
    rules_version: str,
    grader_version: str,
    generation: Mapping[str, Any] | None = None,
    source_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 v2 运行清单。

    case_snapshots 必须是入队时冻结的用例快照（带 case_id/case_revision），
    不能现读用例表——现读会让「冻结后改源不影响已入队运行」这条契约失效。
    """
    index = _case_index(case_snapshots)
    environment = {
        "cases": [
            {
                "case_id": str(item["case_id"]),
                "environment": _environment_of(item["case_snapshot"]),
            }
            for item in sorted(
                case_snapshots,
                key=lambda row: (str(row["case_id"]), int(row["case_revision"])),
            )
        ],
        "tool_modes": sorted({str(mode) for mode in tool_modes}),
        "limits": redact(dict(limits)),
    }
    grading = {
        "rules_version": rules_version,
        "grader_version": grader_version,
        "judge": redact(dict(judge_profile)) if judge_profile else None,
        "rubrics": _rubric_digests(case_snapshots),
    }
    candidate = {
        "target": redact(dict(target_snapshot)) if target_snapshot else None,
        "agent_profile": redact(dict(agent_profile)),
        "generation": redact(dict(generation)) if generation else None,
    }
    target_kind = str((target_snapshot or {}).get("kind") or "")
    target_id = str((target_snapshot or {}).get("id") or "")
    source = dict(source_state or {})
    source_revision = source.get("source_revision")
    source_dirty_digest = source.get("source_dirty_digest")
    return {
        "version": MANIFEST_VERSION,
        "snapshot_schema_version": MANIFEST_VERSION,
        "model_mode": model_mode,
        "tool_modes": sorted({str(mode) for mode in tool_modes}),
        "dataset_digest": digest_of(index),
        "dataset_case_count": len(index),
        "target_kind": target_kind or None,
        "target_id": target_id or None,
        "target_digest": digest_of(candidate["target"]),
        "environment_digest": digest_of(environment),
        "grading_digest": digest_of(grading),
        "candidate_digest": digest_of(candidate),
        "source_revision": source_revision,
        "source_revision_reason": source.get("source_revision_reason")
        or (None if source_revision else "NOT_COLLECTED"),
        "source_dirty_digest": source_dirty_digest,
        "source_dirty_digest_reason": source.get("source_dirty_digest_reason")
        or (None if source_dirty_digest else "NOT_COLLECTED"),
        "package_versions": {
            "python": platform.python_version(),
            **_string_map(source.get("package_versions")),
        },
    }


def repo_root() -> Path:
    """源码仓库根（nanobot/evaluation/manifests.py 上溯三层）。"""
    return Path(__file__).resolve().parents[2]


def collect_source_state(source_root: str | Path | None = None) -> dict[str, Any]:
    """收集被测代码的版本与脏摘要。

    只看受控源码目录（默认 nanobot/），不读工作区、文章库、配置等敏感文件。
    拿不到 git 信息时返回 null + 原因，不伪造。
    """
    root = Path(source_root) if source_root is not None else repo_root()

    def _git(*args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    revision_result = _git("rev-parse", "HEAD")
    if revision_result is None or revision_result.returncode != 0:
        return {
            "source_revision": None,
            "source_revision_reason": "GIT_UNAVAILABLE",
            "source_dirty_digest": None,
            "source_dirty_digest_reason": "GIT_UNAVAILABLE",
        }
    revision = revision_result.stdout.strip() or None
    status_result = _git("status", "--porcelain", "--", "nanobot")
    if status_result is None or status_result.returncode != 0:
        dirty_digest, dirty_reason = None, "GIT_STATUS_UNAVAILABLE"
    else:
        entries = [line for line in status_result.stdout.splitlines() if line.strip()]
        dirty_digest = digest_of(sorted(entries))
        dirty_reason = None
    return {
        "source_revision": revision,
        "source_revision_reason": None if revision else "GIT_REV_EMPTY",
        "source_dirty_digest": dirty_digest,
        "source_dirty_digest_reason": dirty_reason,
    }
