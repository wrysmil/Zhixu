"""身份文件的规则编译：把源文件压成可注入的 ``identity/runtime/`` 产物。

纯同步、无 LLM、无网络，与 openakita 的 ``compile_all(use_llm=False)`` 同构。
编译只做机械取舍：抓「本文件拥有的章节」→ 剔除占位符与噪声行 → 去重、单行
截断 → 按字符预算封顶。不做任何语义改写，所以产物永远可以被源文件替代。

与 openakita 的一处有意差异：不按「平台职责关键词」（安全/权限/工具/记忆…）
激进剔除。知序 的平台提示层远小于 openakita，激进剔除会静默丢掉用户自己
写的行为规则——那比多留几百字符更糟。剔除只针对占位符与 Markdown 噪声。

注入侧的消费者是 :func:`read_compiled`：整体新鲜才返回产物，否则返回
``None``，调用方回退全文。这条「永不因编译状态丢内容」的约定是刻意的。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.identity.bootstrap import load_identity_template
from nanobot.identity.catalog import (
    COMPILED_SCHEMA_VERSION,
    RUNTIME_SUBDIR,
    resolve_identity_dir,
)
from nanobot.identity.store import IdentityStore, IdentityStoreError

COMPILED_AT_FILENAME = ".compiled_at"
COMPILER_VERSION_FILENAME = ".compiler_version"

# 单行上限：压掉用户写进身份文件里的整段长文，但保留一行内的完整语义。
MAX_LINE_CHARS = 240

# owned section 提取到的正文行占全量正文行的比例低于此值时，即使有标题命中也
# 降级为「收全文」。命中的往往只是导语所属的那一个标题——marker 是中英混排的
# 词表，用户的标题语言一对不上就会静默丢掉整份正文（实测：SOUL.md 用英文
# ``## Core Principles`` / ``## Execution Rules``，``# Soul`` 命中后 ``## Core
# Principles`` 不命中，产物只剩 50 字节的导语，规则全丢）。
#
# 取值刻意低于 0.5：章节收窄本就可以只留少数几节，owned 占比天然偏低；阈值
# 再高就会把「SOUL.md 里有大段非身份章节」的正常情况误判成 marker 没对上。
MIN_OWNED_COVERAGE = 0.34

# 占位符：未填写的字段不该进 system prompt——这是编译相对全文注入最大的收益。
_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "（待填",
    "(待填",
    "（待补充",
    "(待补充",
    "（待学习",
    "(待学习",
    "（待统计",
    "(待统计",
    # 派生文件的空态提示（USER.md / MEMORY.md 的「暂无记录」「暂无手工笔记」）。
    # 它们是对人说的，注入进去等于告诉模型「用户是一句占位文案」。
    "（暂无",
    "(暂无",
    # 旧版英文 USER.md 模板的占位符：老 workspace 里普遍还是这一版，
    # 不挡会把「(your name)」「(your timezone)」当成真实用户信息注入。
    "(your name)",
    "(your timezone",
    "(preferred language",
    "(your role",
    "(what you're working on)",
    "edit this file to customize",
    # 派生文件开头的说明句：对人有用，对模型是噪声。
    "帮助知序了解你",
    "information about the user to help personalize",
    "待填)",
    "此文件由",
    "最后更新:",
    "最后更新：",
    "todo",
    "tbd",
)


@dataclass(frozen=True)
class CompileTarget:
    """一个编译目标：源文件 → 产物文件，以及怎么取舍内容。"""

    key: str
    source: str  # catalog 逻辑名（白名单内）
    output: str  # runtime/ 下的文件名
    max_chars: int
    # 命任一项（大小写不敏感）的标题即该目标「拥有的章节」；空 = 不收窄，取全文。
    owned_markers: tuple[str, ...] = ()
    excluded_markers: tuple[str, ...] = _PLACEHOLDER_MARKERS
    # 源文件仍是出厂模板原文时不出产物。见 _is_factory_template 的说明。
    skip_factory_template: bool = False


COMPILE_TARGETS: tuple[CompileTarget, ...] = (
    # SOUL.md 刻意不设 skip_factory_template：出厂人格本来就该进 system prompt
    # （注入侧 _SKIPPABLE_DEFAULTS 也没有 SOUL.md），跳过它会让人格消失。
    CompileTarget(
        key="identity_core",
        source="SOUL.md",
        output="identity.core.md",
        max_chars=6000,
        owned_markers=(
            "soul",
            "身份",
            "使命",
            "性格",
            "人格",
            "核心原则",
            "执行规则",
            "价值取向",
            # P3 增补：SOUL 的三节价值取向（真正的帮助 / 诚实 / 自主与边界）。
            # marker 走子串匹配，标题里出现这些词就整节收进产物。
            "帮助",
            "诚实",
            "自主",
            "气质",
            "overview",
            # marker 走子串匹配，故单词形式即可覆盖复数与派生（rule → rules）。
            # 缺了英文这组，用户用英文标题写身份就会只剩导语——见
            # _extract_owned_sections 的降级说明。
            "principle",
            "rule",
            "guideline",
            "value",
            "personality",
            "mission",
            # 出厂 SOUL 改版后的章节（属性优先级 / 避免伤害 / 大局安全 /
            # 内在驱动 / 身份认知）。marker 必须跟着出厂模板的标题一起长，
            # 否则编译会把这些整节丢掉——出厂模板本身不会被裁，用户改过的
            # 副本却会，这比不改模板更糟。
            "属性",
            "优先级",
            "帮不上",
            "伤害",
            "边界",
            "危险",
            "安全",
            "驱动",
            "心态",
            "本能",
            "守护",
            "资源",
            "根因",
            "存在",
            "心理",
            "福祉",
            "harm",
            "safety",
            "drive",
        ),
    ),
    CompileTarget(
        key="agent_behavior",
        source="AGENT.md",
        output="agent.behavior.md",
        max_chars=6000,
        owned_markers=(
            "行为准则",
            "任务执行",
            "工具与环境",
            "沟通契约",
            "成长循环",
            "自我修复",
            # 出厂 AGENT 改版后的章节（Working Mode / 每轮自检 / 能力扩展 /
            # 工具优先序 / 验证 / 记忆 / 子 Agent / 项目 / 身份文件 / 禁止行为）。
            # 同 identity_core：marker 必须覆盖出厂模板的全部标题层级，
            # 否则子标题一落选，其正文会被整段裁掉。
            "identity",
            "working",
            "ralph",
            "铁律",
            "proactive",
            "主动",
            "自检",
            "capability",
            "tool",
            "verification",
            "记忆",
            "团队",
            "项目",
            "身份",
            "prohibited",
        ),
        skip_factory_template=True,
    ),
    CompileTarget(
        key="user_profile_core",
        source="USER.md",
        output="user.profile.core.md",
        max_chars=600,
        skip_factory_template=True,
    ),
)


def runtime_dir(workspace: Path) -> Path:
    """编译产物目录 ``identity/runtime/``（不保证存在）。"""
    return resolve_identity_dir(workspace) / RUNTIME_SUBDIR


# -- 编译流水线 ---------------------------------------------------------------


def _is_noise(stripped: str) -> bool:
    """空行、水平分隔线、没有内容的列表符号。"""
    if not stripped:
        return True
    if stripped in {"-", "*", "+"}:
        return True
    return len(stripped) >= 3 and set(stripped) <= {"-", "*", "_"}


def _payload_line_count(lines: list[str]) -> int:
    """计入覆盖率的正文行数：排除标题行与 Markdown 噪声行。

    噪声行必须排除——``picked`` 会连同 owned 章节之间的空行一起收进来，若把它们
    算进分母，一个「只有导语」的结果看上去也占了三成，覆盖率检查就形同虚设。
    """
    return sum(
        1
        for line in lines
        if not line.lstrip().startswith("#") and not _is_noise(line.strip())
    )


def _extract_owned_sections(lines: list[str], owned_markers: tuple[str, ...]) -> list[str]:
    """取本目标拥有的章节；标题对不上或收窄明显漏料时降级为「全部正文行」。

    降级有两条触发路径，两条的缺失形态不同：

    1. 一个标题都没命中——用户的 SOUL.md 未必用出厂模板的标题（「核心原则」
       「执行规则」），收窄失败时宁可全收，也不要编译出空产物。
    2. 命中了但收窄后正文覆盖率低于 :data:`MIN_OWNED_COVERAGE`——这类更隐蔽：
       导语所属的标题（「# Soul」）总能被 marker 命中，于是 ``matched_any`` 为真，
       后面所有正文因标题语言不同而全部落选，只剩导语被当成"编译成功"。
    """
    all_body = [line for line in lines if not line.lstrip().startswith("#")]

    if not owned_markers:
        return all_body

    picked: list[str] = []
    current_owned = False
    matched_any = False
    for line in lines:
        if line.lstrip().startswith("#"):
            lowered = line.lower()
            current_owned = any(marker in lowered for marker in owned_markers)
            matched_any = matched_any or current_owned
        if current_owned:
            picked.append(line)

    if not matched_any:
        return all_body

    owned_body = _payload_line_count(picked)
    total_body = _payload_line_count(all_body)
    if total_body and owned_body < total_body * MIN_OWNED_COVERAGE:
        return all_body
    return picked


def _enforce_char_budget(lines: list[str], max_chars: int) -> str:
    """逐行累加，超限时把最后一行二分切到刚好不超预算。"""
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text

    kept: list[str] = []
    for line in lines:
        if len("\n".join([*kept, line])) <= max_chars:
            kept.append(line)
            continue
        low, high = 0, len(line)
        while low < high:
            mid = (low + high + 1) // 2
            if len("\n".join([*kept, line[:mid].rstrip()])) <= max_chars:
                low = mid
            else:
                high = mid - 1
        if low > 0:
            kept.append(line[:low].rstrip())
        break
    return "\n".join(kept)


def _finish_lines(picked: list[str], target: CompileTarget) -> str:
    """噪声剔除 + 去重 + 单行截断 + 字符预算封顶。"""
    kept: list[str] = []
    seen: set[str] = set()
    for line in picked:
        stripped = line.strip()
        if _is_noise(stripped):
            continue
        # 派生文件的区块边界是 HTML 注释（AUTO/NOTE 标记），只对人有用，
        # 进 system prompt 就是噪声。
        if stripped.startswith("<!--"):
            continue
        lowered = stripped.lower()
        if any(marker in lowered for marker in target.excluded_markers):
            continue
        out = line.rstrip()
        if len(out) > MAX_LINE_CHARS:
            out = out[:MAX_LINE_CHARS].rstrip()
        key = out.strip()
        if key in seen:
            continue
        seen.add(key)
        kept.append(out)
    return _enforce_char_budget(kept, target.max_chars)


def compile_content(content: str, target: CompileTarget) -> str:
    """把一份源文件内容编译成该目标的产物正文（可能为空字符串）。

    章节收窄（``owned_markers``）只在**无损**时采用。用户在 AGENT.md 里自己加的
    章节标题多半不命中 marker，收窄会把那段正文整段丢掉——用户写的规则在系统
    提示里消失，而他什么都没收到。

    编译的职责是**压缩**（去重 + 预算封顶），不是**筛选**。所以收窄一旦会丢正文
    就改收全文，只让字符预算封顶：宁可少压一点，也不能少一条规则。
    """
    lines = content.splitlines()
    selected = _extract_owned_sections(lines, target.owned_markers)
    if _payload_line_count(selected) < _payload_line_count(lines):
        selected = lines
    return _finish_lines(selected, target)


def _is_factory_template(logical_name: str, content: str) -> bool:
    """源文件是否仍是出厂模板原文（用户一个字没改）。

    为什么必须在编译侧拦：注入侧对「出厂模板」的跳过只在**回退分支**生效
    （``_load_bootstrap_files`` 里 ``_SKIPPABLE_DEFAULTS`` 那一层）。产物分支拿到
    非空正文就直接注入，够不到那个判断。而出厂 AGENT.md / USER.md 模板并不是纯
    占位符——它们有实质句子（行为准则、字段选项提示），编译得出非空产物。于是
    「用户点一次规则编译」就会把出厂模板灌进 system prompt，且与未编译时行为不一致。

    在源头不产出，两边就一致了：未编译 → 回退分支跳过；编译过 → 无产物 → 回退分支跳过。
    """
    template = load_identity_template(logical_name)
    return template is not None and content.strip() == template.strip()


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _discard_product(path: Path) -> None:
    """删掉过期产物。

    产物集是「整体新鲜才可信」的：不写新产物却留着旧产物，注入侧会把上一版的
    正文当成本版继续注入（用户重置文件、模板升级都走这条路）。
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("identity: 删除过期产物失败 %s", path)


# -- 新鲜度 -------------------------------------------------------------------


def compiled_status(workspace: Path) -> dict[str, Any]:
    """产物集整体是否可用（存在 / schema 匹配 / 无源文件更新）。

    ``compiled`` = 曾经编译过；``fresh`` = 现在还信任这批产物。两者都为假时
    注入侧走全文。
    """
    rdir = runtime_dir(workspace)
    stamp = rdir / COMPILED_AT_FILENAME
    version_file = rdir / COMPILER_VERSION_FILENAME

    if not stamp.is_file() or not version_file.is_file():
        return {"compiled": False, "fresh": False, "reason": "not_compiled"}

    if version_file.read_text(encoding="utf-8").strip() != COMPILED_SCHEMA_VERSION:
        return {"compiled": True, "fresh": False, "reason": "schema_mismatch"}

    if not any((rdir / target.output).is_file() for target in COMPILE_TARGETS):
        return {"compiled": True, "fresh": False, "reason": "no_products"}

    stamp_ns = stamp.stat().st_mtime_ns
    store = IdentityStore(workspace)
    for target in COMPILE_TARGETS:
        source_path = store.resolve_path(target.source)
        if source_path.is_file() and source_path.stat().st_mtime_ns > stamp_ns:
            return {"compiled": True, "fresh": False, "reason": "source_newer"}

    return {"compiled": True, "fresh": True, "reason": "ok"}


def compiled_info(workspace: Path) -> dict[str, Any]:
    """编译状态 + 上次编译时间（身份编辑器展示用）。

    ``fresh=False`` 就是「有未编译改动」：源文件比产物新、schema 升级、或
    从未编译过。编辑器据此提示用户点一次编译，避免「改了文件但 prompt 缓存
    没清，下一轮不生效」。
    """
    status = compiled_status(workspace)
    stamp = runtime_dir(workspace) / COMPILED_AT_FILENAME
    compiled_at: str | None = None
    if stamp.is_file():
        try:
            compiled_at = stamp.read_text(encoding="utf-8").strip() or None
        except OSError:
            compiled_at = None
    return {**status, "compiled_at": compiled_at}


def read_compiled(workspace: Path) -> dict[str, str] | None:
    """返回 ``{target.key: 产物正文}``；产物集不新鲜或读不出时返回 ``None``。

    ``None`` 是给注入侧的回退信号——调用方据此改用源文件全文，所以任何异常
    都收敛成 ``None``，绝不让一次读盘失败把人格段落整个吞掉。
    """
    if not compiled_status(workspace)["fresh"]:
        return None

    rdir = runtime_dir(workspace)
    out: dict[str, str] = {}
    for target in COMPILE_TARGETS:
        product = rdir / target.output
        if not product.is_file():
            continue
        try:
            body = product.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning("identity: 读取编译产物失败 %s，回退全文注入", product)
            return None
        if body.strip():
            out[target.key] = body
    return out or None


def compile_identity(workspace: Path) -> dict[str, Any]:
    """跑一遍规则编译，把产物写进 ``identity/runtime/`` 并刷新时间戳。

    空产物的目标不落盘（计入 ``skipped``）——伪造一段兜底文案会让「编译过」
    与「有内容」混为一谈，注入侧的空回退反而更诚实。产物全部为空的 workspace
    仍然会写时间戳：它表示「这批源文件编译过了，结果就是没有可注入的规则」。

    出厂模板原文同样不落盘（``factory_template``）——见 :func:`_is_factory_template`。
    """
    store = IdentityStore(workspace)
    rdir = runtime_dir(workspace)
    rdir.mkdir(parents=True, exist_ok=True)

    compiled_files: list[str] = []
    skipped: list[dict[str, str]] = []
    newest_source_ns = 0

    for target in COMPILE_TARGETS:
        source_path = store.resolve_path(target.source)
        product = rdir / target.output
        try:
            content = store.read_file(target.source)
        except IdentityStoreError:
            skipped.append({"target": target.key, "reason": "source_missing"})
            _discard_product(product)
            continue

        if source_path.is_file():
            newest_source_ns = max(newest_source_ns, source_path.stat().st_mtime_ns)

        if target.skip_factory_template and _is_factory_template(target.source, content):
            skipped.append({"target": target.key, "reason": "factory_template"})
            _discard_product(product)
            continue

        body = compile_content(content, target)
        if not body.strip():
            skipped.append({"target": target.key, "reason": "empty"})
            _discard_product(product)
            continue

        _atomic_write(product, body)
        compiled_files.append(target.output)

    _atomic_write(rdir / COMPILER_VERSION_FILENAME, COMPILED_SCHEMA_VERSION)
    stamp = rdir / COMPILED_AT_FILENAME
    _atomic_write(stamp, datetime.now(timezone.utc).isoformat())

    # 把时间戳 mtime 抬到「所有源文件最新 mtime + 1ns」之上：写入发生在读源文件
    # 之后，但秒级精度文件系统（FAT/exFAT）会把两者记成同一秒，新鲜度判断随即
    # 误报过期。openakita 踩过这个坑，这里照抄。
    bump_ns = max(newest_source_ns + 1, time.time_ns())
    os.utime(stamp, ns=(bump_ns, bump_ns))

    logger.info(
        "identity: 规则编译完成，产物 {}，跳过 {}",
        compiled_files or "无",
        [entry["target"] for entry in skipped] or "无",
    )
    return {
        "status": "ok",
        "modeUsed": "rules",
        "compiledFiles": compiled_files,
        "skipped": skipped,
    }
