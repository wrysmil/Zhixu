"""受控路径解析：所有客户端可控的相对路径都必须落在声明过的根目录内。

信任边界（契约 §2 / spec §8）：fixture 路径、产物路径、导入文件路径都由外部提交，
因此一律按「语法先拦截、解析后再判定目录包含」两段处理，任何一步失败都抛
``ValueError("PATH_OUTSIDE_ROOT")``——错误码而非文案，调用方（WU-05 路由层）直接映射
到 422/400，不需要再解析消息文本。
"""

from __future__ import annotations

import re
from pathlib import Path

PATH_OUTSIDE_ROOT = "PATH_OUTSIDE_ROOT"

# 盘符形态：'C:' 开头（含 'C:foo' 这类盘符相对路径）与 'C:\' / 'C:/' 绝对形式。
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
# 控制字符与 NUL：路径截断与 shell 注入的经典入口，任何形态都不允许出现。
_CONTROL_RE = re.compile(r"[\x00-\x1f]")


def _reject() -> None:
    raise ValueError(PATH_OUTSIDE_ROOT)


def validate_relative_path(relative: str) -> str:
    """无根目录时的语法层拦截：用于模型字段（保存用例时工作区尚未创建）。

    拒绝：空值、绝对路径、盘符、UNC、反斜线、控制字符、NUL、``.``/``..`` 段与空段。
    非字符串由调用方（StrictStr 字段）先拒掉，这里不再重复 isinstance 判断。
    """
    if not relative.strip():
        _reject()
    if "\\" in relative:
        _reject()
    if _CONTROL_RE.search(relative):
        _reject()
    if relative.startswith("/") or _DRIVE_RE.match(relative):
        _reject()
    if Path(relative).is_absolute():  # POSIX 绝对路径兜底（含 Windows 上的 '/' 形式）
        _reject()
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _reject()
    return relative


def resolve_inside(root: Path, relative: str) -> Path:
    """把相对路径解析到 ``root`` 内，并拒绝任何指向 root 外的结果。

    ``Path.resolve()`` 会展开已存在的 symlink / Windows junction（reparse point），
    因此「链接指向 root 外」在解析后由目录包含判定捕获；不存在的中间目录也允许解析
    （调用方随后自行创建），但一旦某层已存在且逃逸即拒绝。
    """
    base = Path(root).resolve()
    validate_relative_path(relative)
    target = (base / Path(relative)).resolve()
    if target == base or not target.is_relative_to(base):
        _reject()
    return target
