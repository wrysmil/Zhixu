# tests/memory/retrieval/test_decomposer.py
"""D10：查询准备只做确定性处理——无模型改写、无结果缓存、无中文碎片切词。"""
import pytest

from nanobot.memory.retrieval.decomposer import QueryDecomposer


class _RecordingBrain:
    """若被调用就把模型词写进结果；用于证明模型路径已删除。"""

    def __init__(self) -> None:
        self.calls = 0

    async def call_compiler(self, prompt: str) -> str:
        self.calls += 1
        return '{"keywords": ["from-model"], "intent": "general"}'


@pytest.mark.asyncio
async def test_brain_injected_but_never_called():
    brain = _RecordingBrain()
    result = await QueryDecomposer(brain=brain).decompose(
        "我之前写的那个 Python 爬虫脚本还能用吗", recent=[]
    )
    assert brain.calls == 0
    assert result.used_llm is False
    assert "from-model" not in result.keywords


@pytest.mark.asyncio
async def test_original_sentence_is_first_keyword():
    query = "我之前写的那个 Python 爬虫脚本还能用吗"
    result = await QueryDecomposer().decompose(query, recent=[])
    assert result.keywords[0] == query
    assert "Python" in result.keywords


@pytest.mark.asyncio
async def test_chinese_query_not_cut_into_fragments():
    """原句之外不再产出「健身」「身视」这类 2~4 字跨词碎片。"""
    query = "那个健身视频的剪辑脚本"
    result = await QueryDecomposer().decompose(query, recent=[])
    assert result.keywords == [query]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["知序", "健身", "进度"])
async def test_short_chinese_substring_query_kept_whole(query):
    result = await QueryDecomposer().decompose(query, recent=[])
    assert result.keywords == [query]


@pytest.mark.asyncio
async def test_path_and_filename_kept_whole():
    query = r"帮我找 C:\proj\zhixu\main.py 那个脚本"
    result = await QueryDecomposer().decompose(query, recent=[])
    assert r"C:\proj\zhixu\main.py" in result.keywords
    assert "main.py" in result.keywords


@pytest.mark.asyncio
async def test_identifiers_kept_whole():
    result = await QueryDecomposer().decompose(
        "nanobot 和 Zhixu 的 memory_search 有啥区别", recent=[]
    )
    assert {"nanobot", "Zhixu", "memory_search"} <= set(result.keywords)


@pytest.mark.asyncio
async def test_anaphoric_query_completed_from_recent_project():
    """「那个项目」这类不自足查询补一个最近已确认的项目名。"""
    recent = [
        {"role": "user", "content": "我把 Zhixu 项目的部署脚本放到桌面了"},
        {"role": "assistant", "content": "好的"},
    ]
    result = await QueryDecomposer().decompose("那个项目现在还能跑吗", recent=recent)
    assert result.keywords == ["那个项目现在还能跑吗", "Zhixu"]


@pytest.mark.asyncio
async def test_chinese_project_name_completed_from_recent():
    recent = [{"role": "user", "content": "项目叫知序，是个 agent 框架"}]
    result = await QueryDecomposer().decompose("上次那件事进展如何", recent=recent)
    assert result.keywords == ["上次那件事进展如何", "知序"]


@pytest.mark.asyncio
async def test_anaphoric_query_without_context_stays_uncertain():
    """无法确定对象时保持不确定：不把历史内容拼进检索输入。"""
    recent = [{"role": "user", "content": "今天天气不错"}]
    result = await QueryDecomposer().decompose("上次那件事进展如何", recent=recent)
    assert result.keywords == ["上次那件事进展如何"]


@pytest.mark.asyncio
async def test_demonstrative_phrase_is_not_mistaken_for_project_name():
    """最近对话里只有「这个项目」时仍算不确定，不把「这个」当项目名补进去。"""
    recent = [{"role": "user", "content": "这个项目下周要上线了"}]
    result = await QueryDecomposer().decompose("那个项目现在还能跑吗", recent=recent)
    assert result.keywords == ["那个项目现在还能跑吗"]


@pytest.mark.asyncio
async def test_self_contained_query_does_not_pull_recent_project():
    """原句已点名对象时不补全，避免把旧话题混进本次检索。"""
    recent = [{"role": "user", "content": "nanobot 项目的部署已经完成"}]
    result = await QueryDecomposer().decompose("那个 Zhixu 项目还能跑吗", recent=recent)
    assert "Zhixu" in result.keywords
    assert "nanobot" not in result.keywords


@pytest.mark.asyncio
async def test_empty_query_yields_no_keywords():
    result = await QueryDecomposer().decompose("   ", recent=[])
    assert result.keywords == []
    assert result.intent == "general"


@pytest.mark.asyncio
async def test_intent_kept_for_compat_but_always_general():
    """``intent`` 的唯一消费者 attachments 通道已删除（D04），字段只剩兼容值。"""
    result = await QueryDecomposer().decompose("找一下之前的图片", recent=[])
    assert result.intent == "general"
    assert result.used_llm is False
