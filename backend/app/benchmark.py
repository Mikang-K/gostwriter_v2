"""Explicit, sequential benchmark CLI. Importing this module performs no I/O."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import re
import signal
import statistics
import sys
import time
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .metrics import summarize_timing
from .providers import GenerateInput, Provider

ROOT = Path(__file__).resolve().parents[2]
CASE_IDS = ("initial", "continuation", "long-memory")
DIMENSIONS = ("characterConsistency", "worldCompliance", "styleStability", "repetition",
              "sceneContinuity", "longMemory", "instructionFollowing", "editReadiness")
GENERATION = {"maxOutputTokens": 512, "temperature": 0.7, "contextWindow": 8192,
              "seed": 42, "timeoutMs": 120000}
ARTIFACT_KEYS = {"ggufSha256", "quantization", "chatTemplateSha256", "runtimeVersion"}


def strict_object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Invalid object keys")


def text(value):
    return isinstance(value, str) and bool(value.strip())


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def parse_config(value):
    strict_object(value, {"coldRuns", "warmRuns", "generation", "candidates"})
    if type(value["coldRuns"]) is not int or value["coldRuns"] != 1:
        raise ValueError("coldRuns must be 1")
    if type(value["warmRuns"]) is not int or value["warmRuns"] != 5:
        raise ValueError("warmRuns must be 5")
    generation = value["generation"]
    strict_object(generation, GENERATION)
    if generation != GENERATION or any(type(generation[k]) is not type(v) for k, v in GENERATION.items()):
        raise ValueError("Fixed generation settings required")
    candidates = value["candidates"]
    if not isinstance(candidates, list):
        raise ValueError("candidates must be an array")
    ids = set()
    for candidate in candidates:
        strict_object(candidate, {"id", "provider", "artifact"}, {"model", "modelEnv", "optional"})
        if not text(candidate["id"]) or not re.fullmatch(r"[a-zA-Z0-9_-]+", candidate["id"]):
            raise ValueError("Invalid candidate id")
        if candidate["id"] in ids or candidate["provider"] not in {"ollama", "llamacpp", "openai"}:
            raise ValueError("Duplicate candidate or invalid provider")
        ids.add(candidate["id"])
        if "optional" in candidate and type(candidate["optional"]) is not bool:
            raise ValueError("optional must be boolean")
        if ("model" in candidate) == ("modelEnv" in candidate):
            raise ValueError("Exactly one model source required")
        if "modelEnv" in candidate and (candidate["modelEnv"] != "OPENAI_MODEL" or candidate["provider"] != "openai"):
            raise ValueError("Only OPENAI_MODEL is supported")
        if "model" in candidate and not text(candidate["model"]):
            raise ValueError("Invalid model")
        artifact = candidate["artifact"]
        strict_object(artifact, ARTIFACT_KEYS)
        for key, entry in artifact.items():
            if entry is not None and not text(entry):
                raise ValueError("Invalid artifact identity")
            if key.endswith("Sha256") and entry is not None and not re.fullmatch(r"[a-fA-F0-9]{64}", entry):
                raise ValueError("Invalid SHA-256")
    local = [c for c in candidates if c["provider"] != "openai"]
    cloud = [c for c in candidates if c["provider"] == "openai"]
    if not 2 <= len(local) <= 3 or len([c for c in local if not c.get("optional")]) < 2:
        raise ValueError("Two mandatory local candidates required; at most three local candidates")
    if len(cloud) != 1 or cloud[0].get("optional") or cloud[0].get("modelEnv") != "OPENAI_MODEL":
        raise ValueError("One mandatory OPENAI_MODEL cloud reference required")
    return value


def parse_cases(value):
    if not isinstance(value, list) or [c.get("id") if isinstance(c, dict) else None for c in value] != list(CASE_IDS):
        raise ValueError("Three ordered benchmark cases required")
    for case in value:
        strict_object(case, {"id", "messages", "invariants"})
        if not isinstance(case["messages"], list) or len(case["messages"]) < 2:
            raise ValueError("Case messages required")
        for message in case["messages"]:
            strict_object(message, {"role", "content"})
            if message["role"] not in {"system", "user", "assistant"} or not text(message["content"]):
                raise ValueError("Invalid message")
        if not isinstance(case["invariants"], list) or not case["invariants"] or not all(map(text, case["invariants"])):
            raise ValueError("Case invariants required")
    if len({c["messages"][0]["content"] for c in value}) != 1 or value[0]["messages"][0]["role"] != "system":
        raise ValueError("Cases must share system rules")
    return value


def resolve_model(candidate, env):
    model = candidate.get("model", env.get(candidate.get("modelEnv", "")))
    if not text(model):
        raise ValueError("Missing model: " + candidate.get("modelEnv", candidate["id"]))
    return model.strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf8")


def save(path, value, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb" if exclusive else "wb") as handle:
        handle.write(encoded(value))


def read_json(path):
    return json.loads(path.read_text(encoding="utf8"))


async def run_case(provider, inp, clock=lambda: time.monotonic() * 1000, timeout_ms=120000):
    started = previous = clock()
    first = last = None
    output = ""
    finish = None
    usage = {"promptTokens": None, "completionTokens": None, "decodeMs": None, "loadMs": None}
    seen_usage = False
    status, error = "completed", None
    try:
        async with asyncio.timeout(timeout_ms / 1000), aclosing(provider.generate(inp)) as events:
            async for event in events:
                now = clock()
                if now < previous:
                    raise ValueError("CLOCK_REVERSED")
                previous = now
                if finish is not None:
                    raise ValueError("EVENT_AFTER_FINISH")
                if event.get("type") == "delta":
                    if not isinstance(event.get("text"), str):
                        raise ValueError("INVALID_DELTA")
                    if event["text"]:
                        first = now if first is None else first
                        last = now
                        output += event["text"]
                elif event.get("type") == "usage":
                    if seen_usage:
                        raise ValueError("DUPLICATE_USAGE")
                    seen_usage = True
                    for key in usage:
                        counter = event.get(key)
                        if counter is not None and (not finite(counter) or (key.endswith("Tokens") and type(counter) is not int)):
                            raise ValueError("INVALID_COUNTER")
                        usage[key] = counter
                elif event.get("type") == "finish" and event.get("reason") in {"stop", "length", "blocked"}:
                    finish = event["reason"]
                else:
                    raise ValueError("INVALID_EVENT")
        if not finish or first is None or finish == "blocked":
            raise ValueError("INCOMPLETE_OUTPUT")
    except asyncio.CancelledError:
        status, error = "cancelled", "CANCELLED"
    except TimeoutError:
        status, error = "failed", "TIMEOUT"
    except Exception as exc:
        # Arbitrary provider messages can contain keys and URLs; save only a bounded error code.
        code = getattr(exc, "code", "")
        if not code and isinstance(exc, ValueError):
            code = str(exc)
        error = code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,64}", code) else type(exc).__name__
        status = "failed"
    completed = clock()
    if completed < previous:
        status, error = "failed", "CLOCK_REVERSED"
        completed = previous
    timing = summarize_timing(started, first, last, completed, usage["completionTokens"], usage["decodeMs"])
    return {"output": output, **timing, **usage, "finishReason": finish, "status": status,
            "error": error, "retryCount": 0}


def parse_nvidia_csv(value):
    try:
        values = [float(line.strip()) for line in value.strip().splitlines()]
        return values[0] if len(values) == 1 and finite(values[0]) else None
    except ValueError:
        return None


def parse_windows_memory(value):
    try:
        data = json.loads(value)
        total, free = data["TotalVisibleMemorySize"], data["FreePhysicalMemory"]
        return (total - free) / 1024 if finite(total) and finite(free) and free <= total else None
    except (ValueError, TypeError, KeyError):
        return None


async def command(*args):
    process = None
    try:
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(process.communicate(), 2)
        if process.returncode:
            raise ValueError("hardware command failed")
        return out.decode("utf8")
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


async def read_hardware():
    script = ("Get-CimInstance Win32_OperatingSystem | Select-Object "
              "TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json -Compress")
    values = await asyncio.gather(
        command("nvidia-smi", "--id=0", "--query-gpu=memory.used", "--format=csv,noheader,nounits"),
        command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script),
        return_exceptions=True)
    gpu = parse_nvidia_csv(values[0]) if isinstance(values[0], str) else None
    ram = parse_windows_memory(values[1]) if isinstance(values[1], str) else None
    return {"atMs": time.monotonic() * 1000, "gpuDeviceUsedMiB": gpu, "systemUsedMiB": ram,
            "error": None if gpu is not None and ram is not None else "COUNTER_UNAVAILABLE"}


async def sample_during(operation, reader=read_hardware, interval_ms=250):
    samples = [await reader()]
    stop = asyncio.Event()

    async def sample():
        while True:
            try:
                await asyncio.wait_for(stop.wait(), interval_ms / 1000)
                return
            except TimeoutError:
                samples.append(await reader())

    task = asyncio.create_task(sample())
    try:
        result = await operation()
    finally:
        stop.set()
        await task
    return result, samples


def median(values):
    if not all(map(finite, values)):
        raise ValueError("Invalid statistics input")
    return statistics.median(values) if values else None


def nearest_rank_p95(values):
    if not all(map(finite, values)):
        raise ValueError("Invalid statistics input")
    return sorted(values)[math.ceil(len(values) * .95) - 1] if values else None


def compare_backend_identity(a, b):
    keys = ("ggufSha256", "quantization", "chatTemplateSha256", "contextWindow",
            "maxOutputTokens", "temperature", "seed")
    return "comparable" if all(a.get(k) is not None and a.get(k) == b.get(k) for k in keys) else "incomparable"


def build_blind_review(run_id, trials):
    items, key = [], {}
    for trial in trials:
        if trial["phase"] != "warm" or trial["index"] != 1:
            continue
        review_id = digest((run_id + ":" + trial["trialId"]).encode())
        key[review_id] = trial["trialId"]
        items.append({"reviewId": review_id, "caseId": trial["caseId"], "output": trial["output"],
                      "invariants": trial["invariants"], "scores": dict.fromkeys(DIMENSIONS),
                      "fatalViolations": [], "reviewed": False})
    return {"items": sorted(items, key=lambda item: item["reviewId"])}, key


def validate_review(review, expected):
    strict_object(review, {"items"})
    if not isinstance(review["items"], list):
        raise ValueError("Invalid review")
    expected_items = {item["reviewId"]: item for item in expected["items"]}
    seen = set()
    for item in review["items"]:
        strict_object(item, {"reviewId", "caseId", "output", "invariants", "scores", "fatalViolations", "reviewed"})
        ident = item["reviewId"]
        if not isinstance(ident, str) or ident in seen or ident not in expected_items:
            raise ValueError("Duplicate or unknown review item")
        seen.add(ident)
        if any(item[k] != expected_items[ident][k] for k in ("caseId", "output", "invariants")):
            raise ValueError("Review source content changed")
        strict_object(item["scores"], DIMENSIONS)
        if any(v is not None and (type(v) is not int or not 1 <= v <= 5) for v in item["scores"].values()):
            raise ValueError("Scores must be null or integers 1..5")
        if type(item["reviewed"]) is not bool or not isinstance(item["fatalViolations"], list):
            raise ValueError("Invalid human review")
        if not all(map(text, item["fatalViolations"])):
            raise ValueError("Fatal violations require notes and quotations")
    if seen != expected_items.keys():
        raise ValueError("Missing review item")


def assess_selection(cases, items):
    failed, missing = [], []
    for case_id in CASE_IDS:
        trials = cases.get(case_id, [])
        if len(trials) != 5:
            missing.append(case_id + ": five warm trials required")
        if any(t["status"] != "completed" for t in trials):
            failed.append(case_id + ": warm failures")
        for metric, threshold, statistic, exceeds in (
                ("ttftMs", 5000, nearest_rank_p95, True), ("providerTps", 15, median, False)):
            values = [t.get(metric) for t in trials if t["status"] == "completed" and finite(t.get(metric))]
            if len(values) != 5:
                missing.append(case_id + ": missing " + metric)
            elif (statistic(values) > threshold if exceeds else statistic(values) < threshold):
                failed.append(case_id + ": provisional " + metric + " criterion failed")
    if len(items) != 3 or {i["caseId"] for i in items} != set(CASE_IDS):
        missing.append("Three human reviews required")
    scores = []
    for item in items:
        if item["fatalViolations"]:
            failed.append("Fatal invariant violation")
        if not item["reviewed"] or any(v is None for v in item["scores"].values()):
            missing.append("Human review incomplete")
        else:
            scores.extend(item["scores"].values())
    if scores and (min(scores) < 3 or statistics.mean(scores) < 3.5):
        failed.append("Provisional quality criterion failed")
    return {"status": "fail" if failed else "insufficient-data" if missing else "provisional-pass",
            "reasons": failed + missing}


def write_report(directory):
    manifest = read_json(directory / "manifest.json")
    trials = [read_json(p) for p in sorted((directory / "trials").glob("*/*/*.json"))]
    expected, key = build_blind_review(manifest["runId"], trials)
    review_path = directory / "review.json"
    if review_path.exists():
        review = read_json(review_path)
        validate_review(review, expected)
    else:
        review = expected
        save(review_path, review, exclusive=True)
    key_path = directory / "review-key.json"
    if key_path.exists():
        if read_json(key_path) != key:
            raise ValueError("Review key mismatch")
    else:
        save(key_path, key, exclusive=True)
    summaries, verdicts = [], {}
    for candidate in manifest["candidates"]:
        candidate_trials = [t for t in trials if t["candidateId"] == candidate["id"]]
        for case_id in CASE_IDS:
            for phase in ("cold", "warm"):
                group = [t for t in candidate_trials if t["caseId"] == case_id and t["phase"] == phase]
                successful = [t for t in group if t["status"] == "completed"]
                row = {"candidateId": candidate["id"], "caseId": case_id, "phase": phase,
                       "attempts": len(group), "completed": len(successful),
                       "failed": sum(t["status"] == "failed" for t in group),
                       "cancelled": sum(t["status"] == "cancelled" for t in group),
                       "truncated": sum(t["finishReason"] == "length" for t in group),
                       "errorRate": sum(t["status"] != "completed" for t in group) / len(group) if group else None,
                       "coldStates": sorted({t["coldState"] for t in group})}
                for metric in ("ttftMs", "generationMs", "providerTps", "endToEndTps", "promptTokens", "completionTokens"):
                    values = [t[metric] for t in successful if finite(t.get(metric))]
                    row[metric] = {"n": len(values), "median": median(values), "p95": nearest_rank_p95(values)}
                for counter in ("gpuDeviceUsedMiB", "systemUsedMiB"):
                    values = [s[counter] for t in group for s in t["hardware"] if finite(s.get(counter))]
                    baseline = [t["hardware"][0][counter] for t in group if t["hardware"] and finite(t["hardware"][0].get(counter))]
                    row[counter] = {"baselineMedian": median(baseline), "sampledPeak": max(values) if values else None,
                                    "n": len(values)}
                summaries.append(row)
        trial_ids = {t["trialId"] for t in candidate_trials}
        candidate_reviews = [i for i in review["items"] if key[i["reviewId"]] in trial_ids]
        verdicts[candidate["id"]] = assess_selection(
            {c: [t for t in candidate_trials if t["caseId"] == c and t["phase"] == "warm"] for c in CASE_IDS},
            candidate_reviews)
    comparisons = []
    for a in manifest["candidates"]:
        for b in manifest["candidates"]:
            if a["provider"] == "ollama" and b["provider"] == "llamacpp":
                comparisons.append({"a": a["id"], "b": b["id"], "status": compare_backend_identity(
                    {**a["artifact"], **manifest["generation"]}, {**b["artifact"], **manifest["generation"]})})
    summary = {"comparisonScope": manifest["comparisonScope"], "provisionalCriteria": True,
               "rows": summaries, "selection": verdicts, "backendComparisons": comparisons}
    save(directory / "summary.json", summary)
    lines = ["# Benchmark evidence", "", "Scope: " + manifest["comparisonScope"], "",
             "All criteria are provisional. n=5 nearest-rank p95 is the maximum; this is not an SLA.",
             "Missing counters remain null. Hardware values are device/system-wide sampled peaks, include other processes,",
             "and can miss peaks between 250ms samples. Cold rows with unverified/provider-managed state are not hardware cold evidence.",
             "", "| Candidate | Case | Phase | Success/attempts | TTFT median/p95 ms | Decode median tokens/s |", "|---|---|---|---|---|---|"]
    for row in summaries:
        lines.append(f"| {row['candidateId']} | {row['caseId']} | {row['phase']} | {row['completed']}/{row['attempts']} | "
                     f"{row['ttftMs']['median']}/{row['ttftMs']['p95']} | {row['providerTps']['median']} |")
    lines += ["", "Human evaluation and provisional selection:"]
    lines += [f"- {candidate}: {v['status']}; " + "; ".join(v["reasons"]) for candidate, v in verdicts.items()]
    lines += ["", "See summary.json for valid n, failures, cancellations, truncation, tokens and baseline/peak samples.",
              "Share review.json for blind scoring; keep review-key.json and raw timing artifacts separate.",
              "No model is automatically selected. If local candidates fail, change one evidenced context/quant/offload/load/RAM factor and use a new run-id."]
    if not comparisons:
        lines += ["Backend comparison unavailable: no matched cross-backend artifact evidence."]
    else:
        lines += [f"Backend {c['a']} / {c['b']}: {c['status']}" for c in comparisons]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf8")
    return summary


def loopback_url(value):
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Benchmark server must be loopback HTTP")
    return value.rstrip("/")


def output_directory(value, root=ROOT, *, new=True):
    path = (root / value).resolve()
    base = (root / "artifacts/benchmarks").resolve()
    if path == base or not path.is_relative_to(base):
        raise ValueError("Output must be a run directory under artifacts/benchmarks")
    if new and path.exists():
        raise ValueError("Output directory already exists")
    if not new and not path.is_dir():
        raise ValueError("Review directory does not exist")
    return path


class Runtime:
    def __init__(self, env):
        self.env = env
        self.urls = {"ollama": loopback_url(env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")),
                     "llamacpp": loopback_url(env.get("LLAMACPP_BASE_URL", "http://127.0.0.1:8080"))}
        self.versions = {}
        self.hardware_info = {}

    def provider(self, candidate):
        return Provider(candidate["provider"], self.urls.get(candidate["provider"], ""),
                        self.env.get("OPENAI_API_KEY", "") if candidate["provider"] == "openai" else "")

    async def preflight(self, candidates):
        async with httpx.AsyncClient(timeout=10) as client:
            for candidate in candidates:
                kind = candidate["provider"]
                if kind == "ollama":
                    response = await client.get(self.urls[kind] + "/api/tags")
                    response.raise_for_status()
                    if candidate["resolvedModel"] not in {m["name"] for m in response.json()["models"]}:
                        raise ValueError("Local model missing; download separately")
                    response = await client.get(self.urls[kind] + "/api/version")
                    response.raise_for_status()
                    self.versions[kind] = response.json().get("version")
                elif kind == "llamacpp":
                    response = await client.get(self.urls[kind] + "/props")
                    response.raise_for_status()
                    props = response.json()
                    if props.get("default_generation_settings", {}).get("n_ctx") != GENERATION["contextWindow"]:
                        raise ValueError("llama.cpp server context must be verified as 8192")
                    if not candidate["artifact"]["runtimeVersion"]:
                        raise ValueError("Record installed llama.cpp runtimeVersion in artifact")
                    self.versions[kind] = candidate["artifact"]["runtimeVersion"]
        descriptions = await asyncio.gather(
            command("nvidia-smi", "--id=0", "--query-gpu=name,driver_version,memory.total",
                    "--format=csv,noheader,nounits"),
            command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory | ConvertTo-Json -Compress"),
            return_exceptions=True)
        self.hardware_info = {"gpuNameDriverTotalMiB": descriptions[0].strip() if isinstance(descriptions[0], str) else None,
                              "systemPhysicalMemory": None}
        if isinstance(descriptions[1], str):
            try:
                self.hardware_info["systemPhysicalMemory"] = json.loads(descriptions[1]).get("TotalPhysicalMemory")
            except (ValueError, AttributeError):
                pass

    async def prepare_cold(self, candidate):
        if candidate["provider"] == "openai":
            return "provider-managed"
        if candidate["provider"] == "llamacpp":
            if sys.stdin.isatty():
                answer = await asyncio.to_thread(input, "Restart llama.cpp for this case; enter restarted to confirm: ")
                return "external-restart" if answer == "restarted" else "unverified"
            return "unverified"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(self.urls["ollama"] + "/api/generate",
                                         json={"model": candidate["resolvedModel"], "keep_alive": 0, "stream": False})
            response.raise_for_status()
            if response.json().get("done") is not True:
                raise ValueError("Ollama unload not confirmed")
            response = await client.get(self.urls["ollama"] + "/api/ps")
            response.raise_for_status()
            if candidate["resolvedModel"] in {m["name"] for m in response.json()["models"]}:
                raise ValueError("Ollama model still loaded")
        return "confirmed-unloaded"


async def execute_benchmark(config, cases, directory, runtime, *, local_only=False,
                            include_optional=False, clock=lambda: time.monotonic() * 1000,
                            hardware=read_hardware):
    candidates = [{**c, "resolvedModel": resolve_model(c, runtime.env)} for c in config["candidates"]
                  if (include_optional or not c.get("optional")) and (not local_only or c["provider"] != "openai")]
    await runtime.preflight(candidates)
    directory.mkdir(parents=True, exist_ok=False)
    source = (ROOT / "benchmarks/cases.json").read_bytes()
    manifest = {"runId": directory.name, "startedUtc": datetime.now(timezone.utc).isoformat(),
                "comparisonScope": "partial-local-only" if local_only else "local-and-cloud",
                "candidates": candidates, "cases": cases, "casesSourceSha256": digest(source),
                "casesSourceBytes": len(source), "configSha256": digest(encoded(config)),
                "generation": config["generation"], "coldRuns": 1, "warmRuns": 5,
                "python": platform.python_version(), "os": platform.platform(),
                "cpu": platform.processor(), "runtimeVersions": runtime.versions,
                "hardwareDescription": getattr(runtime, "hardware_info", {}),
                "operatorNotes": runtime.env.get("BENCHMARK_NOTES", "Not recorded"),
                "samplingIntervalMs": 250, "samplingScope": "device-wide GPU0; OS-wide RAM; sampled peak only",
                "randomness": "seed support does not guarantee reproducibility"}
    save(directory / "manifest.json", manifest, exclusive=True)
    hashes = {}
    cancelled = False
    for candidate in candidates:
        provider = runtime.provider(candidate)
        for case in cases:
            cold_state = "unverified"
            preparation_error = None
            for index in range(6):
                phase = "cold" if index == 0 else "warm"
                if index == 0:
                    try:
                        cold_state = await runtime.prepare_cold(candidate)
                    except asyncio.CancelledError:
                        cancelled = True
                        break
                    except Exception:
                        preparation_error = "COLD_PREPARATION_FAILED"
                inp = GenerateInput(messages=case["messages"], model=candidate["resolvedModel"],
                                    max_output_tokens=512, temperature=.7, context_window=8192, seed=42)
                started_utc = datetime.now(timezone.utc).isoformat()
                if preparation_error:
                    result = {"output": "", "status": "failed", "error": preparation_error, "retryCount": 0,
                              "finishReason": None, **dict.fromkeys(("ttftMs", "generationMs", "promptTokens",
                              "completionTokens", "decodeMs", "loadMs", "providerTps", "endToEndTps"))}
                    samples = []
                else:
                    result, samples = await sample_during(lambda: run_case(provider, inp, clock), hardware)
                trial_id = f"{candidate['id']}/{case['id']}/{phase}-{max(1, index)}"
                trial = {**result, "trialId": trial_id, "candidateId": candidate["id"], "caseId": case["id"],
                         "provider": candidate["provider"], "model": candidate["resolvedModel"],
                         "phase": phase, "index": max(1, index), "coldState": cold_state if index == 0 else "warm",
                         "invariants": case["invariants"], "caseSha256": digest(encoded(case)),
                         "generation": config["generation"], "seedApplied": candidate["provider"] != "openai",
                         "temperatureApplied": candidate["provider"] != "openai",
                         "contextWindowApplied": candidate["provider"] == "ollama", "startedUtc": started_utc,
                         "hardware": samples}
                relative = "trials/" + trial_id + ".json"
                save(directory / relative, trial, exclusive=True)
                hashes[relative] = digest((directory / relative).read_bytes())
                if result["status"] == "cancelled":
                    cancelled = True
                    break
            if cancelled:
                break
        if cancelled:
            break
    save(directory / "trial-hashes.json", hashes, exclusive=True)
    summary = write_report(directory)
    failures = sum(row["failed"] + row["cancelled"] for row in summary["rows"])
    print(f"Failures/cancellations: {failures}; report: {directory / 'report.md'}")
    return 130 if cancelled else 1 if failures else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Explicit synthetic Korean benchmark. Cloud sends inputs externally and may charge for 18 requests. No downloads.")
    parser.add_argument("--config")
    parser.add_argument("--output")
    parser.add_argument("--review", help="Rerender existing artifacts only; no provider calls")
    parser.add_argument("--local-only", action="store_true", help="Partial comparison; excludes cloud")
    parser.add_argument("--allow-cloud", action="store_true", help="Explicitly permit 18 paid cloud requests")
    parser.add_argument("--include-optional", action="store_true")
    args = parser.parse_args(argv)
    if args.review:
        if args.config or args.output or args.local_only or args.allow_cloud or args.include_optional:
            parser.error("--review cannot be combined with run arguments")
    elif not args.config or not args.output or args.local_only == args.allow_cloud:
        parser.error("run requires --config, --output and exactly one of --local-only / --allow-cloud")
    return args


async def async_main(args):
    if args.review:
        directory = output_directory(args.review, new=False)
        hashes_path = directory / "trial-hashes.json"
        if hashes_path.exists():
            for relative, sha in read_json(hashes_path).items():
                path = (directory / relative).resolve()
                if not path.is_relative_to((directory / "trials").resolve()) or digest(path.read_bytes()) != sha:
                    raise ValueError("Raw trial integrity failure")
        write_report(directory)
        print("Existing results re-evaluated; no providers created.")
        return 0
    directory = output_directory(args.output)
    config = parse_config(read_json(ROOT / args.config))
    cases = parse_cases(read_json(ROOT / "benchmarks/cases.json"))
    if args.allow_cloud and not os.environ.get("OPENAI_API_KEY", "").strip():
        raise ValueError("OPENAI_API_KEY required for explicit cloud run")
    for c in config["candidates"]:
        if (args.include_optional or not c.get("optional")) and (not args.local_only or c["provider"] != "openai"):
            resolve_model(c, os.environ)
    selected = [c for c in config["candidates"] if (args.include_optional or not c.get("optional"))
                and (not args.local_only or c["provider"] != "openai")]
    print(json.dumps({"cases": list(CASE_IDS), "generation": GENERATION, "candidates": [c["id"] for c in selected],
                      "expectedRequests": len(selected) * 18, "cloudRequests": 18 if args.allow_cloud else 0,
                      "scope": "partial-local-only" if args.local_only else "local-and-cloud"}))
    runtime = Runtime(os.environ)
    task = asyncio.current_task()
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda *_: task.cancel())
    try:
        return await execute_benchmark(config, cases, directory, runtime, local_only=args.local_only,
                                       include_optional=args.include_optional)
    finally:
        signal.signal(signal.SIGINT, previous)


def main(argv=None):
    args = parse_args(argv)
    try:
        return asyncio.run(async_main(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 130
    except Exception as exc:
        # Never print provider response bodies, credentials, or arbitrary URLs.
        print("Benchmark failed: " + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
