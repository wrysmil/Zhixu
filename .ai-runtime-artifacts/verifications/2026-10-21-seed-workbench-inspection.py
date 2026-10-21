"""为仓库已有检查环境添加标注清楚的 UI 观察会话；不调用模型。"""
from pathlib import Path

from nanobot.config.loader import load_config, set_config_path
from nanobot.session.manager import SessionManager
from nanobot.webui.transcript import read_transcript_lines, write_session_messages_as_transcript

root = Path(__file__).resolve().parent / "nanobot-ui-check"
config_path = root / "config.json"
set_config_path(config_path)
config = load_config(config_path)
assert config.workspace_path.resolve().is_relative_to(root.resolve())
manager = SessionManager(config.workspace_path, sessions_root=root / "sessions")
session = manager.get_or_create("websocket:workbench-inspection-20261021")
if not session.messages:
    session.metadata["title"] = "工作台侧栏检查（示例）"
    session.add_message(
        "user",
        "我想优化当前会话右侧的工作台。除了样式，希望能看到任务进展、当前计划、"
        "运行中的子 Agent、工作记忆，以及 Skill / MCP 的使用情况。",
    )
    session.add_message(
        "assistant",
        "这是用于检查实际界面的示例会话，没有调用模型。\n\n"
        "当前工作台包含会话摘要、环境信息、技能与 MCP、产出和来源。"
        "任务、计划、子 Agent 和记忆还没有进入这个侧栏的数据模型。\n\n"
        "这次先观察实际侧栏的宽度、间距、信息层级和折叠行为，再制作对应的侧栏原型。",
    )
    manager.save(session)
if not read_transcript_lines(session.key):
    write_session_messages_as_transcript(session.key, session.messages)
empty_session = manager.get_or_create("websocket:workbench-empty-20261021")
if not empty_session.messages:
    empty_session.metadata["title"] = "空会话布局检查（示例）"
    # 仅用于会话索引定位，故意不生成显示 transcript，以检查空显示历史的布局。
    empty_session.add_message("user", "空显示历史布局检查，无模型调用。")
    manager.save(empty_session)
print("界面观察会话已就绪，仅写入 .ai-runtime-artifacts 内的既有检查环境。")
