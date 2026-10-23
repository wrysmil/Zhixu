"""gateway 配置 → 评估可选模型 Profile。

bundle 里带整份 providers/presets：worker 的 Config() 默认值没有用户凭据，
合并这份 config 后 make_provider 的解析路径与 gateway 完全一致（含 auto）。
凭据只存在于内存中的 profile.bundle 与 boot 管道，不落库、不进快照（WU-01）。

只遍历 ``config.model_presets`` 里的命名预设（见 tests/evaluation/test_profiles.py）：

- 隐式 ``default`` 预设（由 agents.defaults 字段合成）不算可选预设：无命名预设
  时返回空元组，评估 UI 不出现任何真实模型条目；
- ``"default"`` 是 Config 的保留名（``Config._validate_model_preset`` 直接拒绝），
  bundle 的 ``modelPresets`` 里出现它会让 worker 侧 ``Config.model_validate``
  （runtime_factory.build_attempt_config）启动即失败。

bundle 全部用 camelCase：worker 侧合并的 base 是
``Config.model_dump(mode="json", by_alias=True)``，snake key 不会覆盖 base 的
camel key（pydantic populate_by_name 下别名优先），凭据等新值会被旧值吞掉。
选预设写 ``agents.defaults.modelPreset``（``resolve_preset`` 的选择器）；
``agents.defaults.model`` 按计划一并写入，仅作无预设时的回退模型串。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.evaluation.services import Profile

if TYPE_CHECKING:
    from nanobot.config.schema import Config


def build_profiles_from_config(config: Config) -> tuple[Profile, ...]:
    """把 gateway 配置里的命名模型预设映射为 ``model:<name>`` 评估 Profile。"""
    if not config.model_presets:
        return ()
    providers_payload: dict[str, Any] = config.providers.model_dump(mode="json", by_alias=True)
    profiles: list[Profile] = []
    for name, preset in config.model_presets.items():
        bundle_config = {
            "agents": {"defaults": {"model": name, "modelPreset": name}},
            "modelPresets": {name: preset.model_dump(mode="json", by_alias=True)},
            "providers": providers_payload,
        }
        profiles.append(
            Profile(
                id=f"model:{name}",
                label=name,
                provider=preset.provider,
                model=preset.model,
                kind="both",
                bundle={"config": bundle_config},
            )
        )
    return tuple(profiles)
