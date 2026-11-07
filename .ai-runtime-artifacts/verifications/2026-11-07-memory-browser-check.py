"""只读核对隔离浏览器测试的存储与工具轨迹。"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

RUN = Path(__file__).with_name("2026-11-07-memory-browser")


def main() -> None:
    label = sys.argv[1]
    with sqlite3.connect(f"file:{RUN / 'workspace/memory/state.db'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        episodes = []
        for row in db.execute("SELECT * FROM episodes ORDER BY ended_at"):
            entry = dict(row)
            entry["metadata"] = json.loads(entry["metadata"])
            episodes.append(entry)
        memories = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    sessions = []
    for path in sorted((RUN / "runtime/sessions").rglob("*.jsonl")):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        if not rows or rows[0].get("_type") != "metadata":
            continue
        messages = []
        for row in rows[1:]:
            item = {key: row[key] for key in ("role", "content", "tool_calls", "tool_call_id", "name", "timestamp") if key in row}
            messages.append(item)
        sessions.append({"key": rows[0]["key"], "messages": messages})
    docs = {str(path.relative_to(RUN)): path.read_text(encoding="utf-8") for path in (RUN / "home/.nanobot/projects").glob("*/memory/MEMORY.md")}
    result = {"label": label, "memories_count": memories, "kinds": dict(Counter(row["metadata"].get("kind") for row in episodes)), "episodes": episodes, "project_memory_docs": docs, "sessions": sessions}
    output = RUN / f"{label}.verify.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"label": label, "memories_count": memories, "kinds": result["kinds"], "episode_ids": [row["id"] for row in episodes]}, ensure_ascii=False))
    for session in sessions:
        calls = [call.get("function", {}).get("name") for row in session["messages"] for call in row.get("tool_calls", [])]
        print(json.dumps({"session": session["key"], "user_turns": sum(row.get("role") == "user" for row in session["messages"]), "tools": dict(Counter(calls))}, ensure_ascii=False))
    print(f"Evidence: {output}")


if __name__ == "__main__":
    main()
