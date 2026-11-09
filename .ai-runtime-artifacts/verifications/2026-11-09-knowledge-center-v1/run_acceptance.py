#!/usr/bin/env python3
"""知识中心 v1 CLI 端到端冒烟（离线、临时目录，不写 ~/.nanobot）。

用法：
    .venv/bin/python .ai-runtime-artifacts/verifications/2026-11-09-knowledge-center-v1/run_acceptance.py

做的事：临时 config（embedding.backend=none，databasePath/vectorPath 指向临时目录）
→ 建库 → 入库两篇 md → search（命中 / 空结果 / 错误码）→ stats → reindex
→ rm-doc / rm → 校验源文件仍在、state.db 权限 0600。
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG_NAME = "config.json"


def run(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "nanobot", *args, "--config", str(workspace / CONFIG_NAME)]
    print(f"\n$ nanobot {' '.join(args)}")
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    print(completed.stdout.rstrip())
    if completed.stderr.strip():
        print(completed.stderr.rstrip())
    print(f"[exit={completed.returncode}]")
    return completed


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="zhixu-knowledge-accept.") as raw:
        workspace = Path(raw)
        data = workspace / "data"
        data.mkdir()
        (workspace / CONFIG_NAME).write_text(
            json.dumps(
                {
                    "knowledge": {
                        "databasePath": str(data / "state.db"),
                        "vectorPath": str(data / "chromadb"),
                        "embedding": {"backend": "none"},
                        "chunking": {"chunkSize": 400, "chunkOverlap": 80},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        deploy = workspace / "deploy.md"
        deploy.write_text(
            "# 部署与回滚\n\n发布前先在预发环境验证，部署失败时执行 kubectl rollout undo "
            "回滚到上一个稳定版本。\n回滚完成后需要在变更单记录原因，并通知值班同学复核。\n",
            encoding="utf-8",
        )
        api = workspace / "api.md"
        api.write_text(
            "# API 错误码\n\n错误码 E-1024 表示对象存储写入超时。请检查网络连通性与桶配额，\n"
            "重试三次仍失败时提交工单，附带请求 ID 与时间戳。\n",
            encoding="utf-8",
        )

        assert run(workspace, "knowledge", "create", "产品手册", "--global").returncode == 0
        assert run(workspace, "knowledge", "add", str(deploy), "--kb", "产品手册").returncode == 0
        assert run(workspace, "knowledge", "add", str(api), "--kb", "产品手册").returncode == 0

        hit = run(workspace, "knowledge", "search", "部署失败怎么回滚")
        assert hit.returncode == 0 and "mode=keyword" in hit.stdout
        assert "回滚" in hit.stdout

        error_code = run(workspace, "knowledge", "search", "E-1024")
        assert error_code.returncode == 0 and "E-1024" in error_code.stdout

        empty = run(workspace, "knowledge", "search", "zzzzqqqq")
        assert empty.returncode == 0 and "没有检索到" in empty.stdout

        stats = run(workspace, "knowledge", "stats")
        payload = json.loads(stats.stdout.strip().splitlines()[-1])
        assert payload["documents"] == 2 and payload["chunks"] == 2

        documents = json.loads(
            run(workspace, "knowledge", "docs", "--json").stdout.strip().splitlines()[-1]
        )
        doc_id = documents[0]["id"]
        assert run(workspace, "knowledge", "reindex", doc_id, "--wait").returncode == 0
        assert run(workspace, "knowledge", "doc", doc_id).returncode == 0

        assert run(workspace, "knowledge", "rm-doc", doc_id).returncode == 2  # 缺 --yes
        assert run(workspace, "knowledge", "rm-doc", doc_id, "--yes").returncode == 0
        assert run(workspace, "knowledge", "rm", "产品手册", "--yes").returncode == 0
        assert run(workspace, "knowledge", "list").returncode == 0

        assert deploy.is_file() and api.is_file(), "external 源文件被误删"
        mode = stat.S_IMODE((data / "state.db").stat().st_mode)
        assert mode == 0o600, f"state.db 权限 {oct(mode)} != 0o600"
        print(f"\nOK: 源文件保留、state.db 权限 {oct(mode)}、临时目录 {workspace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
