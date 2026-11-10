"""知识中心配置层（Task 16 config slice）测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from nanobot.config.paths import get_knowledge_dir
from nanobot.config.schema import (
    Config,
    KnowledgeChunkingConfig,
    KnowledgeConfig,
    KnowledgeEmbeddingConfig,
    KnowledgeQuestionGenerationConfig,
    KnowledgeRetrievalConfig,
)

CAMEL_PAYLOAD: dict[str, object] = {
    "enabled": True,
    "databasePath": "/tmp/kb/state.db",
    "vectorPath": "/tmp/kb/chromadb",
    "chunking": {
        "chunkSize": 500,
        "chunkOverlap": 50,
        "maxFileMb": 50,
        "tokenizerMode": "bigram",
        "paragraphMode": "line",
    },
    "embedding": {
        "backend": "remote",
        "model": "text-embedding-3-small",
        "dimensions": 1024,
        "device": "cuda",
        "batchSize": 64,
        "baseUrl": "https://example.test/v1",
        "apiKey": "sk-test",
        "apiKeyEnv": "EMBEDDING_KEY",
        "localModelDir": "/models/bge",
        "downloadSource": "modelscope",
        "timeoutSeconds": 10.0,
    },
    "retrieval": {
        "vectorWeight": 0.5,
        "keywordWeight": 0.5,
        "rrfK": 30,
        "poolSize": 20,
        "keywordPoolSize": 40,
        "defaultLimit": 5,
        "maxLimit": 10,
        "mmrLambda": 0.6,
        "rerankTopK": 12,
        "parentMaxChars": 2000,
        "parentMinChars": 500,
        "shortChunkChars": 150,
        "relevanceBand": 0.2,
        "adjacentSimilarityFloor": 0.3,
        "dedupContainment": 0.9,
        "dedupUniqueRatio": 0.05,
        "dedupJaccard": 0.5,
        "dedupLengthRatio": 3.0,
        "toolOutputChars": 6000,
    },
    "rerank": {
        "enabled": False,
        "backend": "remote",
        "baseUrl": "",
        "apiKey": "",
        "apiKeyEnv": "",
        "model": "",
        "device": "cpu",
        "timeoutSeconds": 30.0,
        "batchSize": 32,
        "maxConcurrency": 4,
        "maxCandidates": 200,
        "maxPassageChars": 1200,
        "maxRequestChars": 6000,
        "threshold": 0.3,
        "fallbackMinScore": 0.15,
        "modelWeight": 0.6,
        "baseWeight": 0.3,
        "sourceWeight": 0.1,
        "scoreTransform": "probability",
    },
    "questionGeneration": {
        "enabled": False,
        "baseUrl": "",
        "apiKey": "",
        "apiKeyEnv": "",
        "model": "",
        "questionCount": 3,
        "maxTokens": 2048,
        "temperature": 0.7,
        "maxContextChars": 1200,
        "timeoutSeconds": 30.0,
    },
}


def test_chunking_defaults() -> None:
    config = KnowledgeChunkingConfig()
    assert config.chunk_size == 800
    assert config.chunk_overlap == 120
    assert config.max_file_mb == 50
    assert config.tokenizer_mode == "jieba"
    assert config.paragraph_mode == "blank_line"


def test_embedding_defaults() -> None:
    config = KnowledgeEmbeddingConfig()
    assert config.backend == "auto"
    assert config.model == "BAAI/bge-small-zh-v1.5"
    assert config.dimensions == 512
    assert config.device == "cpu"
    assert config.batch_size == 16
    assert config.base_url == ""
    assert config.api_key == ""
    assert config.api_key_env == ""
    assert config.local_model_dir == ""
    assert config.download_source == "auto"
    assert config.timeout_seconds == 30.0


def test_retrieval_defaults() -> None:
    config = KnowledgeRetrievalConfig()
    assert config.vector_weight == 0.7
    assert config.keyword_weight == 0.3
    assert config.rrf_k == 60
    assert config.pool_size == 50
    assert config.keyword_pool_size == 100
    assert config.default_limit == 8
    assert config.max_limit == 20
    assert config.mmr_lambda == 0.7
    assert config.rerank_top_k == 10
    assert config.parent_max_chars == 2400
    assert config.parent_min_chars == 600
    assert config.short_chunk_chars == 200
    # 已废弃：仅保留字段以兼容旧配置，不再参与判定。
    assert config.relevance_band == 0.15
    assert config.adjacent_similarity_floor == 0.25
    assert config.dedup_containment == 0.85
    assert config.dedup_unique_ratio == 0.02
    assert config.dedup_jaccard == 0.6
    assert config.dedup_length_ratio == 3.0
    assert config.tool_output_chars == 6000


def test_question_generation_defaults() -> None:
    config = KnowledgeQuestionGenerationConfig()
    assert config.enabled is False
    assert config.question_count == 3
    assert config.max_tokens == 2048
    assert config.temperature == 0.7
    assert config.max_context_chars == 1200
    assert config.api_key == ""


def test_knowledge_defaults_nest_subconfigs() -> None:
    config = KnowledgeConfig()
    assert config.enabled is True
    assert config.database_path == ""
    assert config.vector_path == ""
    assert isinstance(config.chunking, KnowledgeChunkingConfig)
    assert isinstance(config.embedding, KnowledgeEmbeddingConfig)
    assert isinstance(config.retrieval, KnowledgeRetrievalConfig)
    assert isinstance(
        config.question_generation, KnowledgeQuestionGenerationConfig
    )
    assert config.chunking.chunk_size == 800
    assert config.embedding.backend == "auto"
    assert config.retrieval.dedup_length_ratio == 3.0


def test_camel_case_dict_round_trips() -> None:
    config = KnowledgeConfig.model_validate(CAMEL_PAYLOAD)
    assert config.database_path == "/tmp/kb/state.db"
    assert config.chunking.tokenizer_mode == "bigram"
    assert config.embedding.batch_size == 64
    assert config.retrieval.keyword_pool_size == 40
    assert config.retrieval.dedup_length_ratio == 3.0

    dumped = config.model_dump(by_alias=True)
    assert dumped == CAMEL_PAYLOAD
    assert KnowledgeConfig.model_validate(dumped) == config


def test_camel_case_json_round_trips() -> None:
    raw = json.dumps(CAMEL_PAYLOAD)
    config = KnowledgeConfig.model_validate_json(raw)
    assert config.embedding.download_source == "modelscope"
    assert config.chunking.paragraph_mode == "line"
    assert json.loads(config.model_dump_json(by_alias=True)) == CAMEL_PAYLOAD


def test_config_includes_knowledge_with_nested_defaults() -> None:
    config = Config()
    assert isinstance(config.knowledge, KnowledgeConfig)
    assert config.knowledge.database_path == ""
    assert config.knowledge.chunking.chunk_size == 800
    assert config.knowledge.embedding.batch_size == 16
    assert config.knowledge.retrieval.default_limit == 8
    assert config.knowledge.retrieval.dedup_length_ratio == 3.0

    encoded = config.model_dump(mode="json", by_alias=True)["knowledge"]
    assert encoded["chunking"]["chunkSize"] == 800
    assert encoded["retrieval"]["keywordPoolSize"] == 100
    assert encoded["retrieval"]["dedupLengthRatio"] == 3.0


def test_config_accepts_camel_case_knowledge_override() -> None:
    config = Config(
        knowledge={
            "chunking": {"chunkSize": 600},
            "retrieval": {"defaultLimit": 3, "maxLimit": 6},
        }
    )
    assert config.knowledge.chunking.chunk_size == 600
    assert config.knowledge.retrieval.default_limit == 3
    assert config.knowledge.retrieval.max_limit == 6
    # 未覆盖的嵌套字段仍取默认值
    assert config.knowledge.embedding.model == "BAAI/bge-small-zh-v1.5"


def test_invalid_tokenizer_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeConfig.model_validate({"chunking": {"tokenizerMode": "char"}})
    with pytest.raises(ValidationError):
        Config(knowledge={"chunking": {"tokenizerMode": "char"}})


def test_invalid_download_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeConfig.model_validate({"embedding": {"downloadSource": "openai"}})
    with pytest.raises(ValidationError):
        Config(knowledge={"embedding": {"downloadSource": "openai"}})


def test_get_knowledge_dir_follows_config_path_without_creating_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "instance-a" / "config.json"
    monkeypatch.setattr("nanobot.config.paths.get_config_path", lambda: config_file)

    knowledge_dir = get_knowledge_dir()

    assert knowledge_dir == config_file.parent / "knowledge"
    assert not knowledge_dir.exists()
