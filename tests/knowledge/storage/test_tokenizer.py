import pytest

from nanobot.knowledge.tokenizer import tokenize_index, tokenize_query


def test_cjk_unigram_and_bigram():
    assert tokenize_index("创作") == "创 作 创作"
    assert tokenize_query("创作") == '"创" OR "作" OR "创作"'


def test_ascii_compact_token_hits_error_codes():
    index = tokenize_index("错误码 E-1024")
    assert "e1024" in index.split()
    assert tokenize_query("E-1024") == '"e" OR "1024" OR "e1024"'


def test_ascii_run_does_not_swallow_cjk():
    tokens = tokenize_index("E-1024错误码").split()
    assert "1024" in tokens
    assert "e1024" in tokens
    assert {"错", "误", "码", "错误", "误码"} <= set(tokens)
    query_tokens = tokenize_query("E-1024错误码").split(" OR ")
    assert '"1024"' in query_tokens
    assert '"错误"' in query_tokens


def test_query_strips_fts_operators_and_quotes():
    query = tokenize_query('foo* "bar baz" NEAR(qux) -zap')
    assert "*" not in query
    assert '"bar baz"' not in query
    assert "NEAR" not in query
    assert query.split(" OR ") == ['"foo"', '"bar"', '"baz"', '"near"', '"qux"', '"zap"']


def test_query_token_limit_is_64():
    text = "".join(chr(0x4E00 + i) for i in range(100))  # 100 个互不相同的汉字
    query = tokenize_query(text)
    assert len(query.split(" OR ")) == 64


def test_query_tokens_are_subset_of_index_tokens():
    text = "部署失败怎么回滚 E-1024"
    index_tokens = set(tokenize_index(text).split())
    for raw in tokenize_query(text).split(" OR "):
        assert raw.strip('"') in index_tokens


def test_punctuation_only_yields_empty_query():
    assert tokenize_query("！！！……？？") == ""


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_blank_text_yields_empty(text):
    assert tokenize_index(text) == ""
    assert tokenize_query(text) == ""
