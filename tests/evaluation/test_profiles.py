# tests/evaluation/test_profiles.py
"""WU-02：gateway config → 评估 Profile 构建函数。

关键约束（读 nanobot/config/schema.py 与 runtime_factory.build_attempt_config 后确认）：
- bundle 一律 camelCase（``apiKey`` / ``modelPreset`` / ``modelPresets``）：
  worker 侧 _merge 的 base 是 ``Config.model_dump(by_alias=True)``，snake key 会与
  base 的 camel key 并存，pydantic 别名优先会吞掉 bundle 里的新值。
- worker 选预设走 ``agents.defaults.modelPreset``（resolve_preset 语义），
  ``agents.defaults.model`` 只是它的回退模型串，故两者都写入。
- "default" 是 Config 保留名：modelPresets 里出现它会让 worker 的
  Config.model_validate 直接报错，因此只遍历命名预设。
"""
from nanobot.config.schema import Config, ModelPresetConfig
from nanobot.evaluation.profiles import build_profiles_from_config


def test_profiles_from_config_maps_presets_with_credential_bundle() -> None:
    config = Config()
    config.providers.openai.api_key = "sk-live"
    config.model_presets["fast"] = ModelPresetConfig(model="gpt-4o-mini", provider="openai")
    profiles = build_profiles_from_config(config)
    by_id = {p.id: p for p in profiles}
    assert by_id["model:fast"].model == "gpt-4o-mini"
    assert by_id["model:fast"].provider == "openai"
    assert by_id["model:fast"].kind == "both"
    bundle = by_id["model:fast"].bundle
    assert bundle["config"]["providers"]["openai"]["apiKey"] == "sk-live"
    assert bundle["config"]["agents"]["defaults"]["model"] == "fast"
    assert bundle["config"]["agents"]["defaults"]["modelPreset"] == "fast"
    assert bundle["config"]["modelPresets"]["fast"]["model"] == "gpt-4o-mini"
    # bundle 的 key 必须与 base dump（by_alias=True）同形，_merge 才能覆盖而不是并存
    base_defaults = Config().model_dump(mode="json", by_alias=True)["agents"]["defaults"]
    assert "modelPreset" in base_defaults
    # worker 合并终点是 Config.model_validate：bundle 单独即可构成合法 Config，
    # 且 make_provider 的解析路径（resolve_preset）能选中该预设。
    merged = Config.model_validate(bundle["config"])
    assert merged.resolve_preset().model == "gpt-4o-mini"
    assert merged.resolve_preset().provider == "openai"


def test_profiles_never_contains_secret_outside_bundle() -> None:
    config = Config()
    config.providers.openai.api_key = "sk-live"
    config.model_presets["fast"] = ModelPresetConfig(model="gpt-4o-mini", provider="openai")
    profiles = build_profiles_from_config(config)
    assert len(profiles) == 1
    profile = profiles[0]
    read = str(profile.to_read())
    assert "sk-live" not in read
    assert "api_key" not in read and "apiKey" not in read
    # 凭据唯一的持有位置是内存里的 bundle（S7：不落库、不进快照由 WU-01 保证）。
    assert profile.bundle is not None
    assert "sk-live" in str(profile.bundle)


def test_profiles_empty_when_config_has_no_named_presets() -> None:
    # 隐式 default 预设（agents.defaults 字段合成）不算可选预设。
    assert build_profiles_from_config(Config()) == ()
