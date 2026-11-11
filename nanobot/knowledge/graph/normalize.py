"""实体名归一：NFKC → 去首尾空白 → 连续空白折叠 → casefold。

展示名保留最早入库的原文；归一值只用于合并与匹配。
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize_entity_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "")
    return _WHITESPACE.sub(" ", text.strip()).casefold()
