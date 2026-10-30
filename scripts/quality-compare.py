"""Compare context revisions on installed Ollama models using synthetic Korean scenes only."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.context import build_context
from app.errors import AppError
from app.providers import GenerateInput, Provider


def cases():
    story = {"title": "비 오는 역", "genre": "미스터리", "language": "ko",
             "notes": "하린의 3인칭 제한적 시점. 감정을 설명하기보다 행동으로 드러낸다.",
             "worldBible": "현실 세계. 폐역에는 전기가 없다. 죽은 사람은 돌아오지 않는다.",
             "characters": [{"name": "하린", "personality": "신중하고 관찰력이 좋다"},
                            {"name": "도윤", "personality": "불안하면 소매를 만진다"}],
             "memory": {"plotState": "하린은 봉투를 찾고 있다. 도윤은 행방을 숨긴다."},
             "styleExamples": [("하린은 젖은 표를 뒤집었다. 잉크가 엄지에 묻었다.\n"
                               "“마지막 열차는?”\n도윤이 소매를 내렸다.\n“어제 끊겼어.”\n"
                               "벽시계의 초침은 같은 자리에 서 있었다.")], "episodes": []}
    direction = ("하린이 도윤의 거짓말을 눈치채는 장면. 하린의 3인칭 제한적 시점으로 "
                 "행동과 대사를 중심으로 250~400자. 도윤의 속마음은 쓰지 않는다. "
                 "거짓말이라는 단어로 설명하지 말고 모순되는 말이나 행동으로 보여준다. "
                 "하린이 추궁하기 직전에 끝낸다. 봉투는 아직 찾지 못한다.")
    yield "initial", story, "initial", direction
    latest = {"id": "e1", "episodeNumber": 1, "summary": "폐역에 도착해 젖은 표를 확인했다.",
              "body": "하린은 역무실 문고리에 손을 얹었다. 도윤이 빈 창구 앞을 가로막았다. “거긴 아무것도 없어.”"}
    yield "continue", {**story, "episodes": [latest]}, "continue", direction
    history = [{"id": f"e{i}", "episodeNumber": i, "summary": "폐역에서 봉투를 찾았다가 잃어버렸다.",
                "body": "하린은 젖은 승강장을 걸었다. " * 100} for i in range(1, 6)]
    history.append({**latest, "id": "e6", "episodeNumber": 6})
    yield "long-history", {**story, "episodes": history}, "continue", direction


async def main(args):
    output = Path(args.output)
    if output.exists():
        raise ValueError("Use a new output directory; previous runs must not be overwritten")
    baseline_path = Path(args.baseline_file).resolve()
    spec = importlib.util.spec_from_file_location("app.baseline_context", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    async with httpx.AsyncClient(timeout=10) as client:
        version = (await client.get("http://127.0.0.1:11434/api/version")).raise_for_status().json()
        installed = (await client.get("http://127.0.0.1:11434/api/tags")).raise_for_status().json()["models"]
    if set(args.models) - {m["name"] for m in installed}:
        raise ValueError("Requested model is not installed; no models are downloaded")
    output.mkdir(parents=True)
    manifest = {"startedUtc": datetime.now(UTC).isoformat(), "ollama": version,
                "models": [m for m in installed if m["name"] in args.models],
                "contextWindow": 8192, "maxOutputTokens": 1024, "temperature": .7,
                "seeds": args.seeds, "cloudCalls": 0,
                "baselineSha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                "updatedSha256": hashlib.sha256((ROOT / "backend/app/context.py").read_bytes()).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
    (output / "cases.json").write_text(json.dumps(list(cases()), ensure_ascii=False, indent=2), encoding="utf8")
    provider = Provider("ollama", "http://127.0.0.1:11434", header_timeout=120)
    results = []
    for model in args.models:
        for case_id, snapshot, mode, direction in cases():
            for seed in args.seeds:
                # Alternate order so the updated prompt does not always receive a warm model.
                variants = [("baseline", baseline.build_context), ("updated", build_context)]
                if seed % 2:
                    variants.reverse()
                for variant, builder in variants:
                    context = builder(snapshot, direction, mode, 8192, 1024)
                    assert context["estimatedPromptTokens"] <= 8192 - 1024 - 512
                    result = {"model": model, "case": case_id, "seed": seed, "variant": variant,
                              "context": context, "body": "", "finishReason": None, "humanScores": None}
                    start = time.perf_counter()
                    try:
                        async with asyncio.timeout(300):
                            async for event in provider.generate(GenerateInput(
                                    context["messages"], model, context_window=8192, seed=seed)):
                                if event["type"] == "delta":
                                    result["body"] += event["text"]
                                elif event["type"] == "usage":
                                    result["usage"] = event
                                elif event["type"] == "finish":
                                    result["finishReason"] = event["reason"]
                    except (AppError, httpx.HTTPError, TimeoutError) as error:
                        result["error"] = type(error).__name__ + ": " + str(error)
                    result["elapsedSeconds"] = round(time.perf_counter() - start, 3)
                    results.append(result)
                    with (output / "results.jsonl").open("a", encoding="utf8") as stream:
                        stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                    print(f"{model} {case_id} seed={seed} {variant}: "
                          f"{result['finishReason']} {len(result['body'])} chars", flush=True)
    comparison = ["# Synthetic local context comparison", "", "Human scores are unassigned.", ""]
    for result in results:
        comparison.extend([f"## {result['model']} / {result['case']} / seed {result['seed']} / {result['variant']}",
                           "", f"Finish: {result['finishReason']}; elapsed: {result['elapsedSeconds']} s", "",
                           result["body"], ""])
    (output / "comparison.md").write_text("\n".join(comparison), encoding="utf8")
    assert all(r["finishReason"] == "stop" and r["body"] and "error" not in r for r in results), \
        "Some outputs failed or were truncated; inspect results.jsonl"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--models", nargs="+", default=["gemma3:1b-it-q4_K_M", "gemma3:4b-it-q4_K_M"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43])
    arguments = parser.parse_args()
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(main(arguments))
