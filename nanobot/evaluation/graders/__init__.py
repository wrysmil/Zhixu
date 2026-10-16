"""判分侧（契约 §6 的 graders 模块边界）。

- ``rules`` 确定性断言：只读契约 §6.1 的证据成员，不执行用例代码、不访问外网。
- ``verdict`` 状态合并：把执行状态、必过规则、Judge 状态与 Trace 完整性合成 verdict。
- ``judge`` 无工具评分请求与量表校准消费。
- ``calibration`` judge 指纹、gold 样本一致率与校准结论消费。
"""
