# nanobot/memory/retrieval/decomposer.py
"""查询拆解：LLM 主路径 + 规则降级 + LRU 缓存（500 条）。"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from loguru import logger

_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_FILE_EXT_RE = re.compile(r"\.\w{1,5}\b")
_STOPWORDS = frozenset({
    "的", "了", "吗", "呢", "吧", "是", "我", "你", "他", "她", "它",
    "我们", "你们", "他们", "这", "那", "这个", "那个", "一个",
    "在", "有", "和", "或", "但", "就", "都", "也", "还", "再",
    "上", "下", "里", "外", "前", "后", "中", "间",
    "做", "做一下", "用", "能", "可以", "会", "想", "需要", "要",
})
_MEDIA_HINTS = ("图片", "照片", "视频", "文件", "音频", "pdf", "PDF")
_CACHE_MAX = 500


@dataclass
class DecompositionResult:
    keywords: list[str]
    intent: str
    used_llm: bool


class QueryDecomposer:
    """LLM 拆解关键词 + 规则降级（路径/扩展名/停用词/媒体意图）。"""

    def __init__(self, brain=None, *, cache_max: int = _CACHE_MAX):
        self._brain = brain
        self._cache: OrderedDict[str, DecompositionResult] = OrderedDict()
        self._cache_max = cache_max

    async def decompose(self, query: str, recent: list | None) -> DecompositionResult:
        cache_key = query.strip()
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]
        result = await self._do_decompose(query, recent or [])
        self._cache[cache_key] = result
        if len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return result

    async def _do_decompose(self, query: str, recent: list) -> DecompositionResult:
        if self._brain is not None:
            try:
                payload = await self._brain.call_compiler(self._build_prompt(query))
                parsed = self._parse_payload(payload)
                if parsed is not None:
                    return DecompositionResult(
                        keywords=parsed["keywords"],
                        intent=parsed["intent"],
                        used_llm=True,
                    )
            except Exception as exc:  # noqa: BLE001 - 拆解失败退化为规则，不打断检索
                # 降级是允许的，但「这次检索的关键词是规则拆的、不是模型拆的」
                # 必须可见——否则召回质量下降时无从判断是不是拆解退化造成的。
                logger.warning(
                    "query decomposition via llm failed, degrading to rules: {}: {!r}",
                    type(exc).__name__,
                    exc,
                )
        return self._rule_decompose(query)

    def _build_prompt(self, query: str) -> str:
        return f"decompose: {query}"

    def _parse_payload(self, payload: str) -> dict | None:
        import json
        try:
            data = json.loads(payload)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        kws = data.get("keywords") or []
        if not isinstance(kws, list):
            return None
        return {
            "keywords": [str(k) for k in kws if k],
            "intent": str(data.get("intent", "general")),
        }

    def _rule_decompose(self, query: str) -> DecompositionResult:
        tokens: list[str] = []
        tokens.extend(_PATH_RE.findall(query))
        tokens.extend(m.group(0) for m in _FILE_EXT_RE.finditer(query))
        # 保留完整英文单词，单独匹配中文 2-4 字词
        for word in re.findall(r"[a-zA-Z]+|[一-鿿]{2,4}", query):
                if word not in _STOPWORDS and word not in tokens:
                    tokens.append(word)
        intent = "search_file" if any(h in query for h in _MEDIA_HINTS) else "general"
        return DecompositionResult(keywords=tokens[:8], intent=intent, used_llm=False)
