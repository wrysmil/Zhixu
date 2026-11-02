---
artifact: verification-lite
route: leader-direct
skills:
  - systematic-debugging
  - source-driven-development
  - verification-before-completion
skills_evidence:
  - /Users/mima0000/.agents/skills/systematic-debugging/SKILL.md
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户要求解释情节记忆应使用提示词还是关键词拦截，并修正指挥官的 Dream 概念与 nanobot 自称
  - .ai-runtime-artifacts/specs/2026-09-17-memory-md-sqlite-derived-and-dream-button-clarification-spec.md
  - .ai-runtime-artifacts/plans/2026-09-17-memory-md-sqlite-derived-and-dream-button-plan.md
  - .ai-runtime-artifacts/specs/2026-09-29-persona-injection-and-identity-responsibility-spec.md
  - .ai-runtime-artifacts/specs/2026-10-19-zhixu-rename-phase1-low-spec.md
created_at: 2026-11-02
tier: 1
---

# 指挥官记忆说明与助手自称修正

## 范围与依据

- `nanobot/templates/agent/identity.md`：每轮共同注入的运行时提示词明确产品名为知序，保留当前 Agent 的配置名称与角色；历史文件、旧人设或聊天中的 nanobot 不是自称来源。
- 同一模板明确数据库是事实来源、MEMORY.md 是 SQLite 派生视图；Dream 是后台档案/规则维护和记忆草稿生成。普通回复用“后台记忆整理”，用户询问内部机制时可以解释 Dream。
- 普通聊天不直接编辑身份档案；未获得写入成功或已有持久记录证据，不宣称已保存、长期记住或保证下次召回。助手建议、推断和草稿不得当作已确认用户事实。
- `nanobot/memory/prompts.py`：情节摘要以用户事件为主语，去掉“用户通过 nanobot 完成了”的品牌套话。
- 指挥官出厂角色本来已叫“知序指挥官”。本次修改共同运行时模板，避免依赖重置已保存的自定义 Agent 档案。
- `prompts.py` 开始任务时已有用户原文证据等未提交修改，完整保留；未修改其他既有工作区改动、数据库、配置或用户身份文件。

## 第一问的建议边界

情节价值由抽取提示词作语义判断：保留值得后续继续的经历、关键决策及其原因、结果和可复用教训；仅新增画像事实、普通记忆确认或工具调用流水不单独创建情节。代码只检查结构、必需内容和有效空输出，不使用关键词黑名单裁决价值。本轮未实施此项抽取/持久化行为变更。

## 命令与结果

| 命令/检查 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/agent/test_identity_template.py tests/agent/test_context_builder.py tests/agent/test_context_prompt_cache.py tests/memory/test_prompts.py tests/webui/test_agents_catalog.py -q` | 152 passed in 6.38s |
| `.venv/bin/python -m ruff check nanobot/memory/prompts.py` | All checks passed |
| `git diff --check` | 通过 |
| 完整指挥官提示词渲染冒烟，默认工作区与独立项目工作区各一次 | 两个分支均包含知序自称、指挥官角色、SQLite 派生视图与保存证据规则；不含旧的 Dream 自由笔记描述；模板长度小于 4000 字符 |
| `.venv/bin/python -m ruff check nanobot/` | 5 项非本次文件的错误：cli/commands.py 导入顺序；memory/__init__.py 三项未使用导入；memory/repository.py 一项变量命名 |

## 未验证项与风险

- 未发起付费模型请求，以上验证证明提示词正确注入，不能证明模型每次都遵守。
- 未重启用户网关或修改安装包；仓库模板修复需要由实际运行进程加载，其他安装位置不自动更新。
- 未改 Python 执行逻辑或类型签名，未运行全仓类型检查。
- 未清理历史情节或重写历史聊天。旧记录中的 nanobot 不会随模板修改消失。

## Next

- 本次提示词修正完成；第一问提供筛选策略建议，未扩大为记忆抽取机制改造。
