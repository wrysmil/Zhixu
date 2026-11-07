"""VectorSettings 与 pydantic 配置的映射契约。"""
from __future__ import annotations

from nanobot.config.schema import AgentDefaults
from nanobot.memory.vector.settings import VectorSettings


def test_default_backend_is_chromadb_and_vector_enabled():
    """2026-11-07 起默认开启向量（覆盖原 D5/R6 契约）。"""
    d = AgentDefaults()
    assert d.memory_search_backend == "chromadb"
    s = d.memory_vector.to_vector_settings(enabled=(d.memory_search_backend == "chromadb"))
    assert isinstance(s, VectorSettings)
    assert s.enabled is True


def test_explicit_fts5_keeps_vector_disabled():
    """显式回退到 FTS5 时仍是纯词法路径。"""
    d = AgentDefaults(memorySearchBackend="fts5")
    s = d.memory_vector.to_vector_settings(enabled=(d.memory_search_backend == "chromadb"))
    assert s.enabled is False


def test_enabling_chromadb_backend_enables_vector():
    d = AgentDefaults(memorySearchBackend="chromadb")
    s = d.memory_vector.to_vector_settings(enabled=(d.memory_search_backend == "chromadb"))
    assert s.enabled is True
    assert s.dimensions == 512
    assert s.device == "cpu"
    assert s.max_candidates == 45


def test_camel_case_alias_accepted():
    d = AgentDefaults(memorySearchBackend="chromadb", memoryVector={"embeddingDimensions": 768})
    assert d.memory_vector.embedding_dimensions == 768


def test_unknown_backend_degrades_to_fts5():
    """历史 "api_embedding" 与未知值不再静默映射为可用后端：配置值如实保留，
    派生开关按 FTS5 降级路径关闭向量层（统计口径报告实际后端 fts5）。"""
    for backend in ("api_embedding", "dashscope", "totally_unknown"):
        d = AgentDefaults(memorySearchBackend=backend)
        assert d.memory_search_backend == backend
        s = d.memory_vector.to_vector_settings(
            enabled=(d.memory_search_backend == "chromadb")
        )
        assert s.enabled is False
