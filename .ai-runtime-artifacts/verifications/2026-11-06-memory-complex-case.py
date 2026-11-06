"""复杂案例：真实模型 + 隔离 HOME/workspace 验证记忆能力。

案例：
1. 记住 aigc_platform_back 项目约定（Maven / Docker Compose / 环境变量 key）
2. 记录一次线上失败（数据库连接超时 -> 健康检查依赖 -> 重试成功）
3. 用 memory_search 找回该经验
4. 问项目构建/部署方式，验证 MEMORY.md 注入

全程使用临时 HOME / workspace / config，不碰真实 ~/.nanobot 数据。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(__file__).resolve().parent / "2026-11-06-memory-complex-case.md"


TURNS = [
    (
        "记住项目约定",
        "这是 aigc_platform_back 项目。请记住这些项目约定：后端 Java/Spring Boot，"
        "构建用 Maven（mvn -s settings.xml），部署用 Docker Compose，"
        "模型 key 从环境变量读取，不要写进代码。请用 memory_write 写进项目长期记忆。",
    ),
    (
        "记录线上失败",
        "上线时容器启动失败，日志显示数据库连接超时。根因是 docker compose 没有等待 "
        "MySQL 健康检查；我们加上 depends_on 的 service_healthy 条件后重试成功，"
        "并验证了 docker compose up -d 正常。请把这次失败和解决方式记到经验库。",
    ),
    (
        "检索经验",
        "如果以后再遇到容器启动时数据库连接超时，应该先检查什么？"
        "请用 memory_search 查一下经验库再回答。",
    ),
    (
        "验证规则注入",
        "我们 aigc_platform_back 项目构建用什么？部署用什么？",
    ),
]


def _load_base_config() -> dict:
    path = Path.home() / ".nanobot" / "config.json"
    if not path.is_file():
        raise SystemExit(f"找不到真实配置：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_config(path: Path, workspace: Path) -> dict:
    config = _load_base_config()
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    defaults["workspace"] = str(workspace)
    defaults["memoryEnabled"] = True
    defaults["memoryFileMode"] = True
    defaults["memorySearchBackend"] = "fts5"
    defaults["memoryExtractIntervalMinutes"] = 9999
    defaults["maxToolIterations"] = 20
    defaults.setdefault("dream", {})["enabled"] = False
    defaults.setdefault("consolidate", {})["enabled"] = False
    config.setdefault("channels", {}).setdefault("websocket", {})["enabled"] = False
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return defaults


def _run(message: str, config: Path, workspace: Path, home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        "-m",
        "nanobot",
        "agent",
        "-m",
        message,
        "-s",
        "memory-complex",
        "-c",
        str(config),
        "-w",
        str(workspace),
        "--no-markdown",
        "--no-logs",
    ]
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def _db_rows(workspace: Path) -> tuple[int, int, str]:
    db_path = workspace / "memory" / "state.db"
    if not db_path.exists():
        return 0, 0, ""
    conn = sqlite3.connect(db_path)
    try:
        experience = conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE source='experience'"
        ).fetchone()[0]
        memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        summary = conn.execute(
            "SELECT summary FROM episodes WHERE source='experience' ORDER BY ended_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return int(experience), int(memories), str(summary[0] if summary else "")


def _memory_docs(home: Path) -> tuple[str, str]:
    global_dir = home / ".nanobot" / "memory"
    project_files = list((home / ".nanobot" / "projects").glob("*/memory/MEMORY.md"))
    global_text = (global_dir / "MEMORY.md").read_text(encoding="utf-8") if (global_dir / "MEMORY.md").exists() else ""
    project_text = project_files[0].read_text(encoding="utf-8") if project_files else ""
    return global_text, project_text


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="zhixu-memory-complex-", ignore_cleanup_errors=True) as tmp:
        run_dir = Path(tmp)
        home = run_dir / "home"
        workspace = run_dir / "workspace"
        home.mkdir()
        workspace.mkdir()
        config = run_dir / "config.json"
        defaults = _write_config(config, workspace)
        print(f"provider={defaults.get('provider')} model={defaults.get('model')}")

        outputs: list[str] = []
        codes: list[int] = []
        for label, message in TURNS:
            print(f"\n=== {label} ===")
            result = _run(message, config, workspace, home)
            outputs.append(result.stdout)
            codes.append(result.returncode)
            print(result.stdout[-2500:])
            if result.returncode != 0:
                print("stderr:", result.stderr[-1500:])

        experience_rows, memory_rows, experience_summary = _db_rows(workspace)
        global_text, project_text = _memory_docs(home)
        combined_md = global_text + "\n" + project_text
        combined_out = "\n".join(outputs)

        checks = {
            "turn_exit_codes": all(code == 0 for code in codes),
            "memory_write_called": "memory_write" in combined_out,
            "experience_record_called": "experience_record" in combined_out,
            "memory_search_called": "memory_search" in combined_out,
            "project_rule_written": any(
                word in combined_md for word in ("Maven", "mvn", "Docker", "环境变量")
            ),
            "experience_written": experience_rows >= 1,
            "experience_has_root_cause": any(
                word in experience_summary for word in ("数据库", "超时", "健康", "depends_on")
            ),
            "no_dual_write": memory_rows == 0,
            "build_answer_uses_rule": "maven" in outputs[-1].lower() or "mvn" in outputs[-1].lower(),
            "deploy_answer_uses_rule": "docker" in outputs[-1].lower(),
        }

        print("\n=== checks ===")
        for key, ok in checks.items():
            print(f"{key}: {'PASS' if ok else 'FAIL'}")
        print(f"experience_rows={experience_rows} memory_rows={memory_rows}")
        print("MEMORY.md preview:")
        print(combined_md[:1500])

        REPORT.write_text(
            "# 复杂案例：真实模型记忆能力验证（2026-11-06）\n\n"
            f"- provider/model: `{defaults.get('provider')}` / `{defaults.get('model')}`\n"
            "- 环境：临时 HOME / workspace / config，未碰真实 `~/.nanobot` 数据\n\n"
            "## 场景\n\n"
            + "\n".join(f"{index + 1}. **{label}**：{message}" for index, (label, message) in enumerate(TURNS))
            + "\n\n## 结果\n\n"
            + "\n".join(f"- {key}: {'PASS' if ok else 'FAIL'}" for key, ok in checks.items())
            + f"\n- experience_rows={experience_rows}, memory_rows={memory_rows}\n\n"
            "## MEMORY.md\n\n```markdown\n"
            + combined_md[:4000]
            + "\n```\n\n## 最后一轮输出\n\n```text\n"
            + outputs[-1][-3000:]
            + "\n```\n",
            encoding="utf-8",
        )
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
