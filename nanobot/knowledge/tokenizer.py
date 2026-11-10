"""CJK 索引/查询分词。

unicode61 会把整段中文当成一个 token，因此索引前必须自己切分。
索引与查询共用本模块；任何分词行为变化都要递增 index_version 的 tok 段并全量重建。

三种模式：

- ``jieba``（默认）：词典 + HMM 的中文分析器。词典优先命中，未登录词（人名、型号）
  交给 HMM 判断能否成词——与 WeKnora 用 Lindera 的「词典优先」路线一致
  （Lindera-py 无 Windows wheel，本机装不了，改用同为纯 Python 的 jieba）。
- ``unigram_bigram``：单字 + 相邻二元组，召回宽、噪声多。
- ``bigram``：只出二元组。
"""

from __future__ import annotations

import re
import threading
import unicodedata

_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)

_ASCII_SEPARATORS = frozenset("-_.:/#@")

#: ASCII 编号/型号串（`E1024`、`XJ-9003`、`err_1042`）：**整段保留，不参与中文切词**。
#: 文章 §4.1.4：编号、型号、错误码是向量的死区，只能靠倒排精确兜底；一旦被切成
#: `e` + `1024`，倒排也兜不住了（「有 E1024 的文档」与「有 1024 的文档」混为一谈）。
_ASCII_RUN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+#\-]*")

#: ASCII 段之间的分隔符（`XJ-9003` → `xj` + `9003` + compact `xj9003`）。
_ASCII_SPLIT_RE = re.compile(r"[_.+#\-]+")


def _ascii_tokens(run: str) -> list[str]:
    """ASCII 编号/型号的 token：各段 + 无分隔符的 compact 形式。

    同时给 `E-1024` 产出 `e` / `1024` / `e1024` 三个 token：
    搜完整编号走 compact（精确），只搜数字段也能命中（召回）。
    compact 形态是**单个** token，不会被 FTS5 当成短语查询
    （FTS 表是 detail=default，短语查询不被支持）。
    """
    parts = [part.lower() for part in _ASCII_SPLIT_RE.split(run) if part]
    if not parts:
        return []
    tokens = list(parts)
    compact = "".join(parts)
    if len(parts) > 1 and 2 <= len(compact) <= 32:
        tokens.append(compact)
    return tokens

#: jieba 首次加载要读词典（约 0.5 s）并写缓存文件；懒加载 + 锁，避免并发初始化。
_JIEBA_LOCK = threading.Lock()
_JIEBA_MODULE: object | None = None


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _jieba_cut(text: str) -> list[str]:
    """词典 + HMM 切分；jieba 不可用时退化为单字 + 二元组。"""
    global _JIEBA_MODULE
    with _JIEBA_LOCK:
        if _JIEBA_MODULE is None:
            try:
                import jieba

                jieba.setLogLevel(60)
                _JIEBA_MODULE = jieba
            except Exception:  # noqa: BLE001 - 依赖缺失时退化，不让检索整体不可用
                _JIEBA_MODULE = False
    if _JIEBA_MODULE is False:
        return list(text) + [text[i : i + 2] for i in range(len(text) - 1)]
    words = _JIEBA_MODULE.lcut(text)  # type: ignore[attr-defined]
    return [word for word in words if word.strip()]


def _cjk_run_tokens(run: str, mode: str) -> list[str]:
    if not run:
        return []
    if mode == "unigram_bigram":
        return list(run) + [run[i : i + 2] for i in range(len(run) - 1)]
    bigrams = [run[i : i + 2] for i in range(len(run) - 1)]
    return bigrams or [run]


def _jieba_index_tokens(text: str) -> list[str]:
    """jieba 模式下的索引 token：中文按词切，ASCII 编号/型号整段保留。

    先把 ASCII 段摘出来（不参与切词），剩下的中文段交给 jieba；顺序按原文位置，
    便于查询侧用同一套切分得到相同的 key。
    """
    tokens: list[str] = []
    cursor = 0
    for match in _ASCII_RUN_RE.finditer(text):
        tokens.extend(_jieba_cut(text[cursor : match.start()]))
        tokens.extend(_ascii_tokens(match.group(0)))
        cursor = match.end()
    tokens.extend(_jieba_cut(text[cursor:]))
    cleaned: list[str] = []
    for token in tokens:
        piece = token.strip()
        if not piece:
            continue
        if any(_is_cjk(ch) for ch in piece):
            cleaned.append(piece)
        elif piece.isalnum():
            cleaned.append(piece.lower())
    return cleaned


def tokenize_index(text: str, *, mode: str = "jieba") -> str:
    """把正文转成空格分隔的索引 token；ASCII 保持 alnum run。"""
    normalized = unicodedata.normalize("NFKC", text)
    if mode == "jieba":
        return " ".join(_jieba_index_tokens(normalized))
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


def tokenize_query(text: str, *, mode: str = "jieba", limit: int = 64) -> str:
    """构造 FTS MATCH 表达式：token 白名单 + 双引号 + 显式 OR。

    索引与查询必须用同一个分词器：建索引切成 `研究/生命/起源`、查询切成
    `研究/生命/起源`，key 才对得上；不一致时索引里有数据、查询也不报错，
    就是零命中——最难查的一类静默失败。
    """
    tokens = tokenize_index(text, mode=mode).split()
    seen: list[str] = []
    for token in tokens:
        cleaned = _clean_query_token(token)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return " OR ".join(f'"{token}"' for token in seen)


def _clean_query_token(token: str) -> str:
    """FTS 查询 token：中文词原样；ASCII 编号分段与 compact 原样（小写）。

    索引侧 ASCII 已拆成「各段 + compact」，查询侧用同一套拆法，两边 key 一致；
    每个 token 单独成项，OR 连接，不会触发 FTS5 的短语查询限制。
    """
    if any(_is_cjk(ch) for ch in token):
        return token
    if token.isalnum():
        return token.lower()
    return ""
