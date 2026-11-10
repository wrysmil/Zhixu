"""知识检索评测的 HTTP 路由（契约 §2 / 知识检索评测台契约）。

职责边界与记忆专项一致：

- 路由表由 :func:`knowledge_route_defs` 返回，挂进 ``server._routes()`` 的**同一份**
  清单，从而继承 Host/Origin guard、CSRF、限流与未匹配方法的 405 兜底——不另建
  一套前缀相同但不受准入保护的路由表。
- 错误响应只经 ``server._fail`` / ``server._error`` 产出：错误码 → 状态码的唯一
  映射点在 :mod:`nanobot.evaluation.errors`，这里不自己拼 ``{"error": ...}``。
- 请求体在**边界解析一次**：检索参数白名单先于模型校验（未知字段要报
  ``KNOWLEDGE_CONFIG_UNSUPPORTED``，不是通用的 ``VALIDATION_ERROR``），之后由
  服务层按严格 Pydantic 模型收口。
- ``POST /knowledge/runs`` 只入队（202 + Location），**请求内不执行检索**；真正
  的检索由服务层起的后台 asyncio 任务执行。
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from nanobot.evaluation.knowledge.metrics import METRIC_IDS, METRIC_SPECS
from nanobot.evaluation.schemas import EmptyBody

# 分页游标语义只有一份实现（记忆专项）：这里复用它的 ``_limit`` / ``_page``，
# 不另写一套游标——两套游标迟早会漂移。
from nanobot.evaluation.memory.routes import _limit, _page
from nanobot.evaluation.server import (
    API_PREFIX,
    _fail,
    _json_body,
    _json_object,
    context_of,
)

#: 与既有记忆专项共用同一前缀（``nanobot/evaluation/server.py`` 的 ``API_PREFIX``）。
KNOWLEDGE_API_PREFIX = "/api/eval/v1"


# --------------------------------------------------------------------------- 能力与口径


async def knowledge_capabilities(request: web.Request) -> web.Response:
    """``GET /knowledge/capabilities``：知识域是否可用、可见知识库与判定档案。

    没有 ``config.knowledge`` 时返回 ``enabled=false`` + ``reason="NO_CONFIG"``——
    **不是 503**：页面要能渲染出「为什么不可用」，而不是拿到一个错误页。
    """
    context = context_of(request)
    return web.json_response(context.service.knowledge_capabilities(context.config))


async def knowledge_metrics(request: web.Request) -> web.Response:
    """``GET /knowledge/metrics``：指标口径下发（公式的唯一真相源）。

    首页公式区渲染这里下发的内容；前端**不得**在 i18n 里另抄一份公式。
    """
    metrics: list[dict[str, Any]] = []
    for metric_id in METRIC_IDS:
        spec = METRIC_SPECS[metric_id]
        metrics.append(
            {
                "metricId": spec.id,
                "label": spec.label,
                "formula": spec.formula,
                "numerator": spec.numerator,
                "denominator": spec.denominator,
                "direction": spec.direction,
                "description": spec.description,
            }
        )
    return web.json_response({"metrics": metrics})


# --------------------------------------------------------------------------- 数据集


async def list_knowledge_datasets(request: web.Request) -> web.Response:
    """``GET /knowledge/datasets``：数据集列表（分页）。"""
    context = context_of(request)
    limit = _limit(request)
    datasets = context.service.knowledge_list_datasets()
    return web.json_response(
        _page(
            request,
            datasets,
            key_of=lambda item: [str(item.get("created_at") or ""), str(item["id"])],
            scope="knowledge.datasets",
            filters={},
            limit=limit,
        )
    )


async def create_knowledge_dataset(request: web.Request) -> web.Response:
    """``POST /knowledge/datasets``：建数据集（201）。"""
    context = context_of(request)
    payload = await _json_object(request)
    dataset = context.service.knowledge_create_dataset(payload)
    return web.json_response(
        dataset,
        status=201,
        headers={"Location": f"{API_PREFIX}/knowledge/datasets/{dataset['id']}"},
    )


async def get_knowledge_dataset(request: web.Request) -> web.Response:
    """``GET /knowledge/datasets/{id}``：数据集详情（含用例表）。"""
    context = context_of(request)
    return web.json_response(
        context.service.knowledge_get_dataset(request.match_info["id"])
    )


async def update_knowledge_dataset(request: web.Request) -> web.Response:
    """``PATCH /knowledge/datasets/{id}``：局部更新；版本过期 → 409。"""
    context = context_of(request)
    payload = await _json_object(request)
    dataset = context.service.knowledge_update_dataset(
        request.match_info["id"], payload
    )
    return web.json_response(dataset)


async def delete_knowledge_dataset(request: web.Request) -> web.Response:
    """``DELETE /knowledge/datasets/{id}``：幂等删除（连带删掉名下运行与结果）。"""
    context = context_of(request)
    result = context.service.knowledge_delete_dataset(request.match_info["id"])
    return web.json_response(result)


# --------------------------------------------------------------------------- 运行


async def create_knowledge_run(request: web.Request) -> web.Response:
    """``POST /knowledge/runs``：入队即 202，**请求内不执行检索**。"""
    context = context_of(request)
    payload = await _json_object(request)
    run = context.service.knowledge_create_run(payload, config=context.config)
    return web.json_response(
        run,
        status=202,
        headers={"Location": f"{API_PREFIX}/knowledge/runs/{run['id']}"},
    )


async def list_knowledge_runs(request: web.Request) -> web.Response:
    """``GET /knowledge/runs``：运行列表（分页，可按数据集过滤）。"""
    context = context_of(request)
    limit = _limit(request)
    dataset_id = request.query.get("dataset_id")
    runs = context.service.knowledge_list_runs(dataset_id)
    return web.json_response(
        _page(
            request,
            runs,
            key_of=lambda item: [str(item.get("created_at") or ""), str(item["id"])],
            scope="knowledge.runs",
            filters={"dataset_id": dataset_id},
            limit=limit,
        )
    )


async def get_knowledge_run(request: web.Request) -> web.Response:
    """``GET /knowledge/runs/{id}``：运行状态、进度与配置快照。"""
    context = context_of(request)
    return web.json_response(context.service.knowledge_get_run(request.match_info["id"]))


async def get_knowledge_run_report(request: web.Request) -> web.Response:
    """``GET /knowledge/runs/{id}/report``：聚合段 + 逐题段，**两段缺一不可**。

    只有聚合数字视为实现缺失——前端必须能逐题看到「哪条查询在哪个配置下变差」。
    """
    context = context_of(request)
    return web.json_response(
        context.service.knowledge_run_report(request.match_info["id"])
    )


async def cancel_knowledge_run(request: web.Request) -> web.Response:
    """``POST /knowledge/runs/{id}/cancel``：终态重复取消 200，其余 202。"""
    context = context_of(request)
    await _json_body(request, EmptyBody)
    detail, changed = await context.service.knowledge_cancel_run(
        request.match_info["id"]
    )
    return web.json_response(detail, status=202 if changed else 200)


async def compare_knowledge_runs(request: web.Request) -> web.Response:
    """``GET /knowledge/compare?base=&candidate=``：逐指标差 + 退化查询清单。

    ``a`` / ``b`` 是同义别名（契约早期草案用的字母参数），两者都接受。
    """
    context = context_of(request)
    base = request.query.get("base") or request.query.get("a") or ""
    candidate = request.query.get("candidate") or request.query.get("b") or ""
    if not base or not candidate:
        _fail("VALIDATION_ERROR", "缺少 base / candidate 运行 id")
    return web.json_response(context.service.knowledge_compare(base, candidate))


# --------------------------------------------------------------------------- 挂载


def knowledge_route_defs(prefix: str = KNOWLEDGE_API_PREFIX) -> list[web.RouteDef]:
    """契约 §2 的路由清单。

    形状约束：路径统一在 ``{prefix}/knowledge`` 之下，复数名词、无动词后缀；
    动作语义交给 HTTP 方法。
    """

    def r(method: str, path: str, handler: Any) -> web.RouteDef:
        return web.route(method, f"{prefix}{path}", handler)

    return [
        r("GET", "/knowledge/capabilities", knowledge_capabilities),
        r("GET", "/knowledge/metrics", knowledge_metrics),
        r("GET", "/knowledge/datasets", list_knowledge_datasets),
        r("POST", "/knowledge/datasets", create_knowledge_dataset),
        r("GET", "/knowledge/datasets/{id}", get_knowledge_dataset),
        r("PATCH", "/knowledge/datasets/{id}", update_knowledge_dataset),
        r("DELETE", "/knowledge/datasets/{id}", delete_knowledge_dataset),
        r("POST", "/knowledge/runs", create_knowledge_run),
        r("GET", "/knowledge/runs", list_knowledge_runs),
        r("GET", "/knowledge/runs/{id}", get_knowledge_run),
        r("GET", "/knowledge/runs/{id}/report", get_knowledge_run_report),
        r("POST", "/knowledge/runs/{id}/cancel", cancel_knowledge_run),
        r("GET", "/knowledge/compare", compare_knowledge_runs),
    ]


__all__ = ["KNOWLEDGE_API_PREFIX", "knowledge_route_defs"]
