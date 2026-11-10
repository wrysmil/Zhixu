import pytest

from nanobot.knowledge.tokenizer import tokenize_index, tokenize_query


def test_jieba_splits_cjk_into_words():
    # 词典 + HMM：中文按词切，不再是「单字 + 二元组」的碎片。
    assert tokenize_index("创作") == "创作"
    assert tokenize_index("研究生命起源") == "研究 生命 起源"
    assert tokenize_query("创作") == '"创作"'


def test_ascii_code_is_kept_whole():
    # 文章 §4.1.4：编号/型号是向量的死区，只能靠倒排精确兜底，不能被切碎。
    # 「各段 + compact」双 token：搜完整编号走 compact（精确），只搜数字段也能命中。
    index = tokenize_index("错误码 E-1024")
    assert {"e", "1024", "e1024"} <= set(index.split())
    assert tokenize_query("E-1024") == '"e" OR "1024" OR "e1024"'


def test_ascii_run_does_not_swallow_cjk():
    tokens = tokenize_index("E-1024错误码").split()
    assert {"e", "1024", "e1024"} <= set(tokens)
    assert "错误码" in tokens
    query_tokens = tokenize_query("E-1024错误码").split(" OR ")
    assert '"e1024"' in query_tokens
    assert '"错误码"' in query_tokens


def test_legacy_unigram_bigram_mode_still_available():
    # 旧模式保留：索引重建期间可以逐库回退。
    assert tokenize_index("创作", mode="unigram_bigram") == "创 作 创作"
    assert tokenize_query("创作", mode="unigram_bigram") == '"创" OR "作" OR "创作"'


def test_legacy_bigram_mode_still_available():
    assert tokenize_index("创作", mode="bigram") == "创作"


def test_query_strips_fts_operators_and_quotes():
    query = tokenize_query('foo* "bar baz" NEAR(qux) -zap')
    assert "*" not in query
    assert '"bar baz"' not in query
    assert "NEAR" not in query
    assert query.split(" OR ") == ['"foo"', '"bar"', '"baz"', '"near"', '"qux"', '"zap"']


def test_query_token_limit_is_64():
    text = "".join(chr(0x4E00 + i) for i in range(100))  # 100 个互不相同的汉字
    query = tokenize_query(text)
    assert 1 <= len(query.split(" OR ")) <= 64


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
