"""`nanobot knowledge` 子命令：本地知识库的建库、入库、检索、修复与统计。

契约见 `.ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md` §12：

- 退出码：`0` 成功、`1` 运行期失败（解析/入库/IO）、`2` 用法或参数错误（含 `--yes` 缺失）；
- `add` 默认同步入库并打印最终状态，`--no-wait` 只落库为 `pending`（需 gateway 运行）；
- 删除类命令必须显式 `--yes`，且永不删除 `external` 用户源文件；
- `repair` 默认全做，显式 `--purge` 需 `--yes`。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape

if TYPE_CHECKING:
    from nanobot.config.schema import Config
    from nanobot.knowledge.fusion import SearchResult
    from nanobot.knowledge.service import KnowledgeService

knowledge_app = typer.Typer(
    help="管理本地知识库（存储、检索、修复）",
    no_args_is_help=True,
)
console = Console()

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2

_SEARCH_MODES = frozenset({"hybrid", "semantic", "keyword"})


# ============================================================================
# 基础设施
# ============================================================================


def _load_config(config: str | None = None) -> Config:
    from nanobot.config.loader import load_config

    return load_config(Path(config).expanduser() if config else None)


def _service_factory(config: Config) -> KnowledgeService:
    from nanobot.knowledge.service import get_knowledge_service

    return get_knowledge_service(config.knowledge)


def _service(config: str | None = None) -> KnowledgeService:
    return _service_factory(_load_config(config))


def _abort(message: str, code: int = EXIT_USAGE) -> NoReturn:
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(code)


def _print_json(payload: Any) -> None:
    # 机器可读输出走 typer.echo，避免 Rich 按终端宽度折行破坏 JSON。
    typer.echo(json.dumps(payload, ensure_ascii=False))


def _resolve_kb(service: KnowledgeService, value: str, workspace_id: str) -> str:
    """按 id 或名称解析可见知识库；不存在/重名/不可见统一退出码 2。"""
    visible = service.list_kbs(workspace_ids=[workspace_id])
    if value.startswith("kb_"):
        if any(kb.id == value for kb in visible):
            return value
        _abort(f"知识库不存在或不可见：{value}")
    matches = [kb for kb in visible if kb.name == value]
    if len(matches) != 1:
        _abort(f"知识库不存在或重名：{value}")
    return matches[0].id


def _print_search_header(result: SearchResult) -> None:
    routes = ",".join(result.routes) or "-"
    degraded = ",".join(result.degraded) or "-"
    console.print(f"mode={result.mode_used} routes={routes} degraded={degraded}")
    if result.reason:
        console.print(f"reason={result.reason}")


def _ingest_many(service: KnowledgeService, doc_ids: list[str]) -> None:
    failures: list[str] = []
    for doc_id in doc_ids:
        claimed = service.ingest_now(doc_id)
        document = service.get_document(doc_id)
        status = document.parse_status if document is not None else "missing"
        chunks = document.chunk_count if document is not None else 0
        console.print(f"{status} {doc_id} chunks={chunks}")
        if document is not None and document.error_message:
            console.print(f"[red]{escape(document.error_message)}[/red]")
        if not claimed or document is None or document.parse_status != "ready":
            failures.append(doc_id)
    if failures:
        _abort(
            f"有 {len(failures)} 篇文档未完成重建：{', '.join(failures)}",
            EXIT_RUNTIME,
        )


# ============================================================================
# 知识库
# ============================================================================


@knowledge_app.command("list")
def list_kbs(
    workspace_id: str = typer.Option("default", "--workspace-id", "--workspace"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """列出可见知识库。"""
    kbs = _service(config).list_kbs(workspace_ids=[workspace_id])
    if not kbs:
        console.print("没有知识库")
        return
    for kb in kbs:
        console.print(
            f"{kb.id}\t{escape(kb.name)}\t{kb.visibility}\t{kb.embedding_backend}"
        )


@knowledge_app.command("create")
def create_kb(
    name: str = typer.Argument(..., help="知识库名称"),
    description: str = typer.Option("", "--desc", help="描述"),
    is_global: bool = typer.Option(False, "--global", help="创建为全局可见知识库"),
    workspace_id: str = typer.Option("default", "--workspace-id", "--workspace"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """创建知识库。"""
    kb = _service(config).create_kb(
        name=name,
        description=description,
        workspace_id=workspace_id,
        visibility="global" if is_global else "workspace",
    )
    console.print(f"created {kb.id} {escape(name)}")


@knowledge_app.command("rm")
def rm_kb(
    kb: str = typer.Argument(..., help="知识库 id 或名称"),
    yes: bool = typer.Option(False, "--yes", help="确认删除"),
    workspace_id: str = typer.Option("default", "--workspace-id", "--workspace"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """删除知识库（不删除用户源文件）。"""
    if not yes:
        _abort("需要 --yes 确认")
    service = _service(config)
    kb_id = _resolve_kb(service, kb, workspace_id)
    service.delete_kb(kb_id)
    console.print(f"deleted {kb_id}（用户源文件未被删除）")


# ============================================================================
# 文档
# ============================================================================


@knowledge_app.command("add")
def add_file(
    path: Path = typer.Argument(..., help="单个存在的本地文件"),
    kb: str = typer.Option(..., "--kb", help="知识库 id 或名称"),
    title: str = typer.Option("", "--title", help="文档标题（默认取文件名）"),
    force: bool = typer.Option(False, "--force", help="同内容也新建一条记录"),
    no_wait: bool = typer.Option(False, "--no-wait", help="只入队，等待 gateway 处理"),
    workspace_id: str = typer.Option("default", "--workspace-id", "--workspace"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """添加本地文件；默认等待处理完成。"""
    if not path.is_file():
        _abort(f"只接受单个存在的普通文件：{path}")
    service = _service(config)
    kb_id = _resolve_kb(service, kb, workspace_id)

    from nanobot.knowledge.extractor import KnowledgeExtractionError

    try:
        outcome = service.add_file(
            path,
            kb_id=kb_id,
            workspace_id=workspace_id,
            title=title or None,
            force=force,
        )
    except KeyError:
        _abort(f"知识库不存在：{kb_id}")
    except KnowledgeExtractionError as exc:
        _abort(str(exc))

    if outcome.duplicate:
        console.print(f"duplicate {outcome.id}")
        return
    if no_wait:
        service.enqueue_document(outcome.id)
        console.print(f"queued {outcome.id} status=pending（需要 gateway 运行）")
        return

    claimed = service.ingest_now(outcome.id)
    document = service.get_document(outcome.id)
    if document is None:
        _abort(f"文档在入库后消失：{outcome.id}", EXIT_RUNTIME)
    console.print(f"{document.parse_status} {outcome.id} chunks={document.chunk_count}")
    if document.parse_status == "failed":
        if document.error_message:
            console.print(f"[red]{escape(document.error_message)}[/red]")
        raise typer.Exit(EXIT_RUNTIME)
    if document.parse_status != "ready":
        if not claimed:
            _abort(f"无法认领文档（可能正被 gateway 处理）：{outcome.id}", EXIT_RUNTIME)
        _abort(f"文档未完成入库：{outcome.id} status={document.parse_status}", EXIT_RUNTIME)


@knowledge_app.command("docs")
def docs(
    kb: str | None = typer.Option(None, "--kb", help="只列该知识库"),
    status: str | None = typer.Option(None, "--status", help="按入库状态过滤"),
    limit: int = typer.Option(100, "--limit", min=1),
    offset: int = typer.Option(0, "--offset", min=0),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str = typer.Option("default", "--workspace-id", "--workspace"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """列出文档。"""
    service = _service(config)
    kb_id = _resolve_kb(service, kb, workspace_id) if kb else None
    documents = service.list_documents(
        kb_id=kb_id,
        workspace_ids=[workspace_id],
        status=status,
        offset=offset,
        limit=limit,
    )
    if as_json:
        _print_json([dataclasses.asdict(document) for document in documents])
        return
    if not documents:
        console.print("没有文档")
        return
    for document in documents:
        console.print(
            f"{document.id}\t{document.parse_status}\t"
            f"chunks={document.chunk_count}\t{escape(document.title)}"
        )


@knowledge_app.command("doc")
def doc_detail(
    doc_id: str = typer.Argument(..., help="文档 id"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """查看文档状态与前 3 个块预览。"""
    service = _service(config)
    document = service.get_document(doc_id)
    if document is None:
        _abort(f"文档不存在：{doc_id}")
    console.print(
        f"{document.id}\t{document.parse_status}\t"
        f"chunks={document.chunk_count}\t{escape(document.title)}"
    )
    if document.error_message:
        console.print(f"[red]{escape(document.error_message)}[/red]")
    if document.parse_status != "ready":
        return
    for chunk in service.list_chunks(doc_id, limit=3):
        console.print(f"[{chunk.chunk_index}] {escape(chunk.content[:120])}")


@knowledge_app.command("rm-doc")
def rm_doc(
    doc_id: str = typer.Argument(..., help="文档 id"),
    yes: bool = typer.Option(False, "--yes", help="确认删除"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """删除文档（不删除用户源文件）。"""
    if not yes:
        _abort("需要 --yes 确认")
    service = _service(config)
    if service.get_document(doc_id) is None:
        _abort(f"文档不存在：{doc_id}")
    service.delete_document(doc_id)
    console.print(f"deleted {doc_id}（用户源文件未被删除）")


# ============================================================================
# 检索
# ============================================================================


@knowledge_app.command("search")
def search(
    query: str = typer.Argument(..., help="检索词"),
    kb: str | None = typer.Option(None, "--kb", help="只搜该知识库"),
    mode: str = typer.Option("hybrid", "--mode", help="hybrid / semantic / keyword"),
    limit: int = typer.Option(8, "--limit", min=1),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str = typer.Option("default", "--workspace-id", "--workspace"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """检索预览；空结果退出码仍为 0，运行期失败为 1。"""
    normalized_mode = mode.strip().lower()
    if normalized_mode not in _SEARCH_MODES:
        _abort(f"不支持的检索模式：{mode}（hybrid / semantic / keyword）")

    service = _service(config)
    kb_ids = [_resolve_kb(service, kb, workspace_id)] if kb else None

    from nanobot.knowledge.retriever import KnowledgeSearchError

    try:
        result = service.search(
            query,
            workspace_ids=[workspace_id],
            kb_ids=kb_ids,
            mode=normalized_mode,
            limit=limit,
        )
    except KnowledgeSearchError as exc:
        _abort(str(exc), EXIT_RUNTIME)
    except ValueError as exc:
        _abort(str(exc))

    if as_json:
        _print_json([dataclasses.asdict(hit) for hit in result.hits])
        return

    _print_search_header(result)
    if not result.hits:
        console.print("没有检索到")
        return
    for hit in result.hits:
        console.print(
            f"{hit.score:.3f}\t{hit.chunk_id}\t{hit.doc_id}\t{escape(hit.content[:80])}"
        )


# ============================================================================
# 重建 / 修复 / 统计
# ============================================================================


@knowledge_app.command("reindex")
def reindex(
    target: str = typer.Argument(..., help="知识库 id/名称或文档 doc_ id"),
    wait: bool = typer.Option(False, "--wait", help="同步等待重建完成"),
    workspace_id: str = typer.Option("default", "--workspace-id", "--workspace"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """重建单个文档或整个知识库。"""
    service = _service(config)
    if target.startswith("doc_"):
        document = service.get_document(target)
        if document is None:
            _abort(f"文档不存在：{target}")
        if service.reindex_document(target):
            console.print(f"requeued {target}")
        else:
            console.print(f"skip {target} status={document.parse_status}")
        if wait:
            _ingest_many(service, [target])
        return

    kb_id = _resolve_kb(service, target, workspace_id)
    doc_ids = service.reindex_kb(kb_id)
    console.print(f"requeued {len(doc_ids)} documents")
    if wait and doc_ids:
        _ingest_many(service, doc_ids)


@knowledge_app.command("repair")
def repair(
    fts: bool = typer.Option(False, "--fts", help="重建 FTS 索引"),
    vectors: bool = typer.Option(False, "--vectors", help="扫描并修复向量"),
    purge: bool = typer.Option(False, "--purge", help="清理孤儿向量/托管文件"),
    yes: bool = typer.Option(False, "--yes", help="确认 --purge"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """修复 FTS / 向量 / 孤儿文件；不指定则全做。"""
    if purge and not yes:
        _abort("--purge 需要 --yes 确认")
    all_flags = not (fts or vectors or purge)
    report = _service(config).repair(
        fts=fts or all_flags,
        vectors=vectors or all_flags,
        purge=purge or all_flags,
    )
    _print_json(report)


@knowledge_app.command("stats")
def stats(
    workspace_id: str = typer.Option("default", "--workspace-id", "--workspace"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """输出统计信息。"""
    _print_json(_service(config).stats(workspace_ids=[workspace_id]))
