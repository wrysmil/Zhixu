---
artifact: execution-log
route: orchestration.dispatch
plan: .ai-runtime-artifacts/plans/2026-10-26-agent-evaluation-reliability-plan.md
created_at: 2026-10-26
status: in-progress
---

# 评估可靠性执行日志（2026-10-26）

## 1. 批次与提交

| 批次 | 任务 | 提交 | 说明 |
| --- | --- | --- | --- |
| 落盘 | T00 | 18cf4ac | 计划、契约、调研与源码核查 |
| A | T01 / T02 | 2fb9e5d 之前的历史提交 + 5d6fc4d | 基线修复、并发限制、v2 清单与迁移 |
| B | T03 / T04 | 5dcc223 | 完整 Trace、隔离团队执行、五维度判分 |
| C/D | T06–T10 / T13 | 40b48f2 | 判分、Judge 材料、指标、配对比较、独立 CLI |
| B 收尾 | T05 | 39ca493 | 限额收敛、live 工具集收紧、环境依赖明示、技能物化哈希 |
| E | T12 | 63b0d27 | 36 条分层样本、量表、14 条评分器反例、导入适配器 |
| E | T11 后端入口 | 366de69 | GET/PATCH /cases/{id}，判据保存形成 revision |
| E | T11 页面 | 19777f1 | 五个组件 + 页面挂载 + 十语言文案 + 集成测试 |

本轮未 push；工作区中的 verifications 缓存目录与历史污染目录（teams/、writing/、tmpvv5_e2h3/）均未提交。

## 2. 子单元派发与回收

| WU | 执行者 | 范围 | 状态 | 结果 |
| --- | --- | --- | --- | --- |
| WU-12 / T12 | Darwin | samples/*.json + test_reliability_samples.py | done | 36 条定义合法、14 条反例全部未判合格、41 项相关测试通过 |
| WU-11 / T11 | Tesla | 五个组件 + 10 份 locale + 测试 | partial → Leader 补齐 | 组件与文案完成；页面挂载与后端保存入口由 Leader 补齐后通过集成测试 |
| R-CODE | 第一次派发失败（高负载），已重试 | reviews/ | 进行中 | 见 reviews/ 报告 |
| R-SEC | 第一次派发失败（高负载），已重试 | reviews/ | 进行中 | 见 reviews/ 报告 |

## 3. 关键决策与取舍

1. **限额只收敛一次**：在入队冻结时把 run 级「显式设置」的限额按更严值写进 case 快照；执行期只认快照值。理由：RunLimits 的默认 12/120 是占位而不是意图，执行期再取 min 会把「用例允许 30 轮」悄悄压回 12。
2. **live 工具集移除 exec/exec_session/apply_patch**：这三个工具既在 FORBIDDEN_TOOLS 里，又出现在可选集中，等于允许入队时申请原生 shell。改为入库即拒绝（TOOL_NOT_ALLOWED）。
3. **依赖分两层**：dependencies 入库时必须由 fixtures 齐备；required_inputs 用于「故意缺输入」边界用例，在 Worker setup 阶段报 CASE_INPUT_MISSING。
4. **样本元数据不进业务模型**：reliability-v2 的 case_id/target/tier/gold 等通过 sample_cases 适配器映射为 tags、描述与依赖；gold 一律不入库，人工确认走校准通道。
5. **团队失败不兜底**：没有 team_runtime_factory 时团队目标直接 setup_error（TEAM_RUNTIME_UNAVAILABLE），不用旁路伪造通过。

## 4. 发现并修复的问题

| 问题 | 证据 | 修复 |
| --- | --- | --- |
| 新增测试污染全局 config path，导致后续 services 测试随机失败 | 全量跑 3 个 services 失败，单跑通过 | 测试加 autouse fixture 保存/恢复 loader.get_config_path() |
| 契约字段集测试与新增字段不一致 | test_validation 字段集断言失败 | 同步契约文档与字段集测试（dependencies / required_inputs） |
| 样本导入因 case 缺 suite_id 失败 | ServiceError SAMPLE_INVALID | 适配器补占位 suite_id（导入时被服务端重建） |
| 「故意缺输入」样本被依赖校验卡在入库 | CASE_DEPENDENCY_MISSING:data/missing.csv | 新增 required_inputs 通道，改为 setup 阶段报缺 |
| 复核组件与既有探索列表重复出现用例名 | evaluation-settings 单测 getByText 多匹配 | 断言改为 getAllByText，保留复核页展示 |
| 前端覆盖度字段是 Record<string, unknown> | tsc 报 string 不可赋值 | 用泛型 pick 做白名单映射，缺字段按 missing 处理 |
| 能力清单未进 Trace：技能哈希/生效白名单/生效限额只在内存 | 自查发现，worker.py 只读取 model/provider | 把 capability 写进 attempt.started，并加端到端断言 |
| Windows 260 上限：会话 checkpoint 路径 276 字符，长数据目录下 FileNotFoundError | 全量跑 3 条 services 测试随机失败，定位到 worker.log 与 attempt 错误 | 会话键改为「前 8 位 + 摘要 8 位」紧凑形式、会话根改 runs/s；同目录从 276 降到 242，并加长路径回归测试 |

## 5. 未完成与阻塞

1. **团队生产执行栈未接线**（阻断 T14 团队抽核）：gateway 未注入 team_runtime_factory，团队目标一律 setup_error。
2. **真实模型 6 attempt 冒烟未执行**（无凭据）。
3. **gold 人工确认未完成**（不得 AI 代签），校准保持 review_required。
4. **Skill 目标隔离未在 36 条回放中启用**（回放统一走 Agent 路径）。
5. 前端既有失败 7 条（useSessions、thread-viewport、add-project-dialog×2、workspace-navigation、preview-isolation、i18n-dock-parity、preview-thread）与本轮无关，未修复。
5. **独立审查未满足**：两个独立审查单元两次派发均因服务高负载失败/超时，已关闭；现由 Leader 自查产出 reviews/ 两份报告，报告首行明确标注「Leader 自查，独立审查未满足」。

## 6. 下一步建议

1. 接入 team_runtime_factory 的生产实现（复用 nanobot/teams 的 Leader 回合与成员执行），再做团队抽核。
2. 配置真实模型凭据后跑 6 attempt 冒烟（agent/skill/team × 2）。
3. 人工确认至少 10 条 gold 后运行真实 Judge 校准。
4. 在上述完成前，交付口径保持「框架与回归已通过，完整评估待验收」。
