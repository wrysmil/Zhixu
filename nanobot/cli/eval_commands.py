"""独立评估入口（契约 §7）。

两个子命令：

- nanobot eval run：无交互回归。把一次评估跑完、导出结果、返回有意义的退出码；
- nanobot eval serve：独立服务。同源静态评估页面 + API，不依赖 gateway。

退出码口径：

- 0：全部合格；
- 1：存在已知不合格（硬约束失败、任务失败）；
- 2：运行未完成、判分缺证或配置问题导致待复核——混合时 2 优先。

凭据只从服务配置进入内存与私有 IPC：命令行、清单与导出里都不出现。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

console = Console()

eval_app = typer.Typer(help="独立评估：回归运行与独立服务。")

#: 退出码（契约 §7）。
EXIT_QUALIFIED = 0
EXIT_DISQUALIFIED = 1
EXIT_REVIEW_REQUIRED = 2


def _data_dir(explicit: Optional[str]) -> Path:
    """评估数据目录：默认与 gateway 内嵌实例同源，便于共用同一批历史运行。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    from nanobot.config.paths import get_data_dir

    return get_data_dir() / "evaluation"


def _load_config(config_path: Optional[str]) -> Any:
    from nanobot.config.loader import get_config_path, load_config, set_config_path

    if config_path:
        resolved = Path(config_path).expanduser().resolve()
        set_config_path(resolved)
        if resolved.is_file():
            return load_config()
        return None
    path = get_config_path()
    if not path.is_file():
        return None
    return load_config()


def _service(data_dir: Path, config: Any) -> Any:
    """装配评估服务；模型档案来自配置，缺配置时只用受控预设。"""
    from nanobot.evaluation.database import EvaluationDatabase
    from nanobot.evaluation.profiles import build_profiles_from_config
    from nanobot.evaluation.repository import EvaluationRepository
    from nanobot.evaluation.services import EvaluationService, default_profiles

    data_dir.mkdir(parents=True, exist_ok=True)
    database = EvaluationDatabase(data_dir / "evaluation.db")
    database.init_schema()
    repository = EvaluationRepository(database)
    profiles = (
        (*build_profiles_from_config(config), *default_profiles())
        if config
        else default_profiles()
    )
    service = EvaluationService(
        repository=repository,
        data_dir=data_dir,
        profiles=profiles,
    )
    return service


def _exit_code_for(run: dict[str, Any]) -> int:
    """按 verdict 分布给退出码；待复核优先于不合格。"""
    attempts = run.get("attempts") or []
    if not attempts:
        return EXIT_REVIEW_REQUIRED
    verdicts = [str(item.get("verdict") or "not_evaluated") for item in attempts]
    if any(verdict in ("review_required", "not_evaluated") for verdict in verdicts):
        return EXIT_REVIEW_REQUIRED
    if any(verdict == "failed" for verdict in verdicts):
        return EXIT_DISQUALIFIED
    if all(verdict == "passed" for verdict in verdicts):
        return EXIT_QUALIFIED
    return EXIT_REVIEW_REQUIRED


@eval_app.command("run")
def run_eval(
    suite_id: str = typer.Option(..., "--suite-id", help="用例集 ID"),
    case_ids: Optional[str] = typer.Option(
        None, "--case-ids", help="逗号分隔的用例 ID；缺省用用例集全部用例"
    ),
    profile: str = typer.Option(..., "--profile", help="执行档案 ID"),
    judge_profile: Optional[str] = typer.Option(None, "--judge-profile", help="评分档案 ID"),
    target_kind: Optional[str] = typer.Option(None, "--target-kind", help="agent/skill/team"),
    target_id: Optional[str] = typer.Option(None, "--target-id", help="被测目标 ID"),
    repetitions: int = typer.Option(1, "--repetitions", help="重复次数（1..10）"),
    output: Optional[str] = typer.Option(None, "--output", help="导出 JSON 的输出路径"),
    data_dir: Optional[str] = typer.Option(None, "--data-dir", help="评估数据目录"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    timeout_s: int = typer.Option(900, "--timeout", help="等待运行结束的秒数"),
) -> None:
    """跑一次评估回归并返回有意义的退出码。"""
    from nanobot.evaluation.scheduler import Scheduler, SchedulerConfig
    from nanobot.evaluation.schemas import RunCreate

    root = _data_dir(data_dir)
    config = _load_config(config_path)
    service = _service(root, config)
    repository = service.repository

    selected = [item for item in (case_ids or "").split(",") if item.strip()]
    if not selected:
        listed = repository.list_cases(suite_id=suite_id)
        selected = [str(item["id"]) for item in listed]
    if not selected:
        console.print("[red]没有可运行的用例[/red]")
        raise typer.Exit(EXIT_REVIEW_REQUIRED)

    payload: dict[str, Any] = {
        "suite_id": suite_id,
        "case_ids": selected,
        "agent_profile_id": profile,
        "repetitions": repetitions,
    }
    if judge_profile:
        payload["judge_profile_id"] = judge_profile
    if target_kind and target_id:
        payload["target"] = {"kind": target_kind, "id": target_id}
    try:
        request = RunCreate.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - 配置不合法按待复核退出
        console.print(f"[red]运行参数不合法：{exc}[/red]")
        raise typer.Exit(EXIT_REVIEW_REQUIRED) from exc

    run = service.create_run(
        request, idempotency_key=f"cli-{suite_id}-{profile}", scope="cli"
    )
    scheduler = Scheduler(
        repository=repository,
        pipeline=service,
        config=SchedulerConfig(data_dir=root, concurrency=1, poll_interval_s=0.2),
    )
    exit_code = EXIT_REVIEW_REQUIRED

    async def _drive() -> None:
        nonlocal exit_code
        async with scheduler:
            deadline = asyncio.get_running_loop().time() + timeout_s
            while True:
                detail = repository.get_run(run["id"])
                if detail["status"] in (
                    "completed", "cancelled", "interrupted", "timed_out"
                ):
                    exit_code = _exit_code_for(detail)
                    return
                if asyncio.get_running_loop().time() > deadline:
                    exit_code = EXIT_REVIEW_REQUIRED
                    return
                await asyncio.sleep(0.2)

    asyncio.run(_drive())

    detail = repository.get_run(run["id"])
    if output:
        target = Path(output).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        body, _content_type, _filename = service.export_run(run["id"], fmt="json")
        target.write_text(body, encoding="utf-8")
        console.print(f"[dim]导出：{target}[/dim]")
    counts = detail.get("counts") or {}
    console.print(
        f"运行 {run['id']} 状态 {detail['status']}："
        f"通过 {counts.get('passed', 0)} / 失败 {counts.get('failed', 0)} / "
        f"待复核 {counts.get('review_required', 0)}"
    )
    raise typer.Exit(exit_code)


@eval_app.command("serve")
def serve_eval(
    host: str = typer.Option("localhost", "--host", help="监听地址；默认只听本机"),
    port: int = typer.Option(8790, "--port", help="监听端口"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="访问口令"),
    data_dir: Optional[str] = typer.Option(None, "--data-dir", help="评估数据目录"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="配置文件路径"),
) -> None:
    """独立启动评估服务（不依赖 gateway）。"""
    from aiohttp import web
    from filelock import FileLock, Timeout

    from nanobot.evaluation.server import create_eval_app

    root = _data_dir(data_dir)
    config = _load_config(config_path)
    secret = passphrase
    if not secret and config is not None:
        from nanobot.cli.webui_support import webui_bootstrap_secret

        secret = webui_bootstrap_secret(config)
    if not secret:
        console.print("[red]缺少访问口令：用 --passphrase 或在配置里设置 WebUI 密码[/red]")
        raise typer.Exit(EXIT_REVIEW_REQUIRED)

    root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(root / "service.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        console.print("[red]该数据目录已有评估服务在运行，拒绝并发写入[/red]")
        raise typer.Exit(EXIT_REVIEW_REQUIRED) from exc

    service = _service(root, config)
    app = create_eval_app(
        data_dir=root,
        passphrase=secret,
        allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
        profiles=tuple(service.profiles),
    )

    async def _serve() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        console.print(f"[green]评估服务已启动：http://{host}:{port}[/green]")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        console.print("评估服务已停止")
    finally:
        lock.release()
