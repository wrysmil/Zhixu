"""CJK 索引/查询分词。

unicode61 会把整段中文当成一个 token，因此索引前必须自己切分。
索引与查询共用本模块；任何分词行为变化都要递增 index_version 的 tok 段并全量重建。
"""

from __future__ import annotations

import unicodedata

_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)

_ASCII_SEPARATORS = frozenset("-_.:/#@")


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _cjk_run_tokens(run: str, mode: str) -> list[str]:
    if not run:
        return []
    if mode == "unigram_bigram":
        return list(run) + [run[i : i + 2] for i in range(len(run) - 1)]
    bigrams = [run[i : i + 2] for i in range(len(run) - 1)]
    return bigrams or [run]


def tokenize_index(text: str, *, mode: str = "unigram_bigram") -> str:
    """把正文转成空格分隔的索引 token；ASCII 保持 alnum run。"""
    normalized = unicodedata.normalize("NFKC", text)
    tokens: list[str] = []
    i, n = 0, len(normalized)
    while i < n:
        ch = normalized[i]
        if _is_cjk(ch):
            j = i
            while j < n and _is_cjk(normalized[j]):
                j += 1
            tokens.extend(_cjk_run_tokens(normalized[i:j], mode))
            i = j
            continue
        if ch.isalnum():
            parts: list[str] = []
            j = i
            while j < n and (
                (normalized[j].isalnum() and not _is_cjk(normalized[j]))
                or normalized[j] in _ASCII_SEPARATORS
            ):
                if normalized[j].isalnum() and not _is_cjk(normalized[j]):
                    k = j
                    while k < n and normalized[k].isalnum() and not _is_cjk(normalized[k]):
                        k += 1
                    parts.append(normalized[j:k].lower())
                    j = k
                else:
                    j += 1
            tokens.extend(parts)
            compact = "".join(parts)
            if len(parts) > 1 and 2 <= len(compact) <= 32:
                tokens.append(compact)
            i = j
            continue
        i += 1
    return " ".join(tokens)


def tokenize_query(text: str, *, mode: str = "unigram_bigram", limit: int = 64) -> str:
    """构造 FTS MATCH 表达式：token 白名单 + 双引号 + 显式 OR。"""
    tokens = tokenize_index(text, mode=mode).split()
    seen: list[str] = []
    for token in tokens:
        cleaned = "".join(ch for ch in token if ch.isalnum() or _is_cjk(ch))
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return " OR ".join(f'"{token}"' for token in seen)
