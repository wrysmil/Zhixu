"""Run a Zhixu agent over a Langfuse dataset and record a dataset run."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any

import httpx

from langfuse import Langfuse
from langfuse.experiment import Evaluation

from nanobot.nanobot import Nanobot


def _provider() -> tuple[str, str, str]:
    path = os.path.expanduser("~/.nanobot/config.json")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    provider = cfg["providers"]["custom-command-ai"]
    model = os.environ.get("JUDGE_MODEL") or str(cfg["agents"]["defaults"]["model"])
    return str(provider["apiBase"]).rstrip("/"), str(provider["apiKey"]), model


def _parse_score(content: str) -> tuple[float, str]:
    match = re.search(r"\{.*\}", content, re.S)
    if match:
        try:
            payload = json.loads(match.group(0))
            return (
                max(0.0, min(1.0, float(payload["score"]))),
                str(payload.get("comment") or ""),
            )
        except (ValueError, KeyError, TypeError):
            pass
    numbers = re.findall(r"0?\.\d+|1\.0|0|1", content)
    if numbers:
        return max(0.0, min(1.0, float(numbers[0]))), content.strip()[:80]
    return 0.0, content.strip()[:80]


async def task(*, item: Any, **_: Any) -> str:
    question = item.input if isinstance(item.input, str) else str(item.input)
    async with Nanobot.from_config() as bot:
        result = await bot.run(
            question,
            session_key=f"langfuse-exp:{item.id}",
            channel="langfuse-experiment",
            chat_id=item.id,
            sender_id="experiment",
            ephemeral=True,
        )
    return result.content


def exact_match(
    *,
    input: Any,
    output: Any,
    expected_output: Any,
    metadata: Any = None,
    **_: Any,
) -> Evaluation:
    value = (
        1.0
        if expected_output is not None
        and str(output).strip() == str(expected_output).strip()
        else 0.0
    )
    return Evaluation(name="exact_match", value=value)


async def semantic_match(
    *,
    input: Any,
    output: Any,
    expected_output: Any,
    metadata: Any = None,
    **_: Any,
) -> Evaluation:
    if not expected_output:
        return Evaluation(name="semantic_match", value=0.0, comment="no expected output")
    api_base, api_key, model = _provider()
    prompt = (
        "判断候选回答与参考回答在语义上是否等价（允许措辞不同）。\n"
        "不要输出推理过程，只输出一行 JSON："
        "{\"score\": <0 到 1 的小数>, \"comment\": \"<简短中文理由>\"}\n\n"
        f"用户问题：\n{input}\n\n参考回答：\n{expected_output}\n\n候选回答：\n{output}"
    )
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            f"{api_base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1024,
                "temperature": 0,
            },
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        content = str(message.get("content") or "").strip()
    if not content:
        return Evaluation(
            name="semantic_match",
            value=0.0,
            comment="judge returned no content",
        )
    score, comment = _parse_score(content)
    return Evaluation(name="semantic_match", value=score, comment=comment[:200])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default=os.environ.get("LANGFUSE_DATASET", "zhixu-evals"),
    )
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args()

    langfuse = Langfuse()
    dataset = langfuse.get_dataset(args.dataset)
    result = dataset.run_experiment(
        name="zhixu-baseline",
        run_name=args.run_name,
        task=task,
        evaluators=[exact_match, semantic_match],
        max_concurrency=1,
    )
    print(result.format())
    langfuse.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
