import asyncio
import copy
import json

import httpx
import pytest

from app import benchmark as b


def fixtures():
    return (b.parse_config(b.read_json(b.ROOT / "benchmarks/candidates.json")),
            b.parse_cases(b.read_json(b.ROOT / "benchmarks/cases.json")))


class FakeProvider:
    def __init__(self, events=None, clock=None, failure=None):
        self.events = events if events is not None else [
            {"type": "delta", "text": "synthetic fiction"}, {"type": "finish", "reason": "stop"}]
        self.clock = clock
        self.failure = failure
        self.calls = 0

    async def generate(self, inp):
        self.calls += 1
        for index, event in enumerate(self.events):
            if self.clock is not None:
                self.clock[0] = [10, 20, 100, 500, 600, 700][index]
            yield event
        if self.failure:
            raise self.failure


def inp():
    return b.GenerateInput([{"role": "user", "content": "synthetic"}], "fake")


def test_fixed_config_cases_and_environment():
    config, cases = fixtures()
    assert [c["id"] for c in cases] == list(b.CASE_IDS)
    assert "[12" in cases[2]["messages"][1]["content"]
    cloud = config["candidates"][-1]
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        b.resolve_model(cloud, {})
    assert b.resolve_model(cloud, {"OPENAI_MODEL": " operator-model "}) == "operator-model"


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(warmRuns=0),
    lambda c: c.update(coldRuns=True),
    lambda c: c["generation"].update(seed=True),
    lambda c: c["candidates"][0].update(baseUrl="http://malicious"),
    lambda c: c["candidates"][0].update(optional="yes"),
    lambda c: c["candidates"][0].update(id="../escape"),
    lambda c: c["candidates"][0].update(modelEnv="OPENAI_MODEL"),
    lambda c: c["candidates"][1].update(id="gemma-1b-q4"),
    lambda c: c["candidates"][-1].update(optional=True),
    lambda c: c["candidates"].pop(0),
])
def test_config_rejects_unsafe_and_changed_contract(mutate):
    config, _ = fixtures()
    mutate(config)
    with pytest.raises(ValueError):
        b.parse_config(config)


@pytest.mark.parametrize("url", ["https://localhost", "http://example.com", "http://user:pw@localhost",
                                "http://127.0.0.1/?key=secret", "http://ollama:11434"])
def test_benchmark_restricts_loopback(url):
    with pytest.raises(ValueError):
        b.loopback_url(url)


async def test_timings_ignore_empty_delta_and_use_actual_tokens():
    clock = [0]
    events = [{"type": "delta", "text": ""}, {"type": "delta", "text": ""},
              {"type": "delta", "text": "a"}, {"type": "delta", "text": "b"},
              {"type": "usage", "promptTokens": 40, "completionTokens": 9, "decodeMs": 500, "loadMs": 50},
              {"type": "finish", "reason": "stop"}]
    result = await b.run_case(FakeProvider(events, clock), inp(), lambda: clock[0])
    assert {k: result[k] for k in ("ttftMs", "generationMs", "providerTps", "endToEndTps")} == {
        "ttftMs": 100, "generationMs": 700, "providerTps": 18, "endToEndTps": 18}
    assert result["output"] == "ab"


@pytest.mark.parametrize("events,error", [
    ([{"type": "delta", "text": "partial"}], "INCOMPLETE_OUTPUT"),
    ([{"type": "finish", "reason": "stop"}], "INCOMPLETE_OUTPUT"),
    ([{"type": "delta", "text": "partial"}, {"type": "finish", "reason": "blocked"}], "INCOMPLETE_OUTPUT"),
    ([{"type": "usage"}, {"type": "usage"}], "DUPLICATE_USAGE"),
    ([{"type": "finish", "reason": "stop"}, {"type": "delta", "text": "late"}], "EVENT_AFTER_FINISH"),
    ([{"type": "usage", "completionTokens": -1}], "INVALID_COUNTER"),
    ([{"type": "usage", "completionTokens": True}], "INVALID_COUNTER"),
])
async def test_protocol_failures(events, error):
    result = await b.run_case(FakeProvider(events), inp())
    assert result["status"] == "failed"
    assert result["error"] == error
    assert result["retryCount"] == 0


async def test_missing_usage_zero_time_and_partial_error_redaction():
    result = await b.run_case(FakeProvider(), inp(), lambda: 1)
    assert result["status"] == "completed"
    assert result["ttftMs"] == 0
    assert result["completionTokens"] is None and result["providerTps"] is None
    assert result["endToEndTps"] is None
    result = await b.run_case(FakeProvider([{"type": "delta", "text": "keep"}],
                                          failure=RuntimeError("https://server?key=secret")), inp())
    assert result["output"] == "keep" and result["error"] == "RuntimeError"


async def test_timeout_cancellation_and_reverse_clock():
    class Waiting:
        async def generate(self, _):
            await asyncio.sleep(1)
            yield {"type": "finish", "reason": "stop"}
    result = await b.run_case(Waiting(), inp(), timeout_ms=1)
    assert result["error"] == "TIMEOUT"
    task = asyncio.create_task(b.run_case(Waiting(), inp()))
    await asyncio.sleep(0)
    task.cancel()
    assert (await task)["status"] == "cancelled"
    times = iter([10, 5, 5])
    result = await b.run_case(FakeProvider(), inp(), lambda: next(times))
    assert result["error"] == "CLOCK_REVERSED"


def test_hardware_parsing_and_statistics():
    assert b.parse_nvidia_csv("4123\n") == 4123
    assert b.parse_nvidia_csv("N/A") is None
    assert b.parse_nvidia_csv("1\n2") is None
    assert b.parse_windows_memory('{"TotalVisibleMemorySize":16777216,"FreePhysicalMemory":8388608}') == 8192
    assert b.parse_windows_memory("permission denied") is None
    assert b.median([500, 100, 300, 200, 400]) == 300
    assert b.nearest_rank_p95([500, 100, 300, 200, 400]) == 500
    assert b.median([]) is None and b.nearest_rank_p95([]) is None
    with pytest.raises(ValueError):
        b.median([float("nan")])
    assert b.compare_backend_identity({}, {}) == "incomparable"


async def test_sampling_baseline_and_no_overlap():
    active = 0
    reads = 0

    async def reader():
        nonlocal active, reads
        active += 1
        assert active == 1
        await asyncio.sleep(.002)
        reads += 1
        active -= 1
        return {"atMs": reads, "gpuDeviceUsedMiB": None, "systemUsedMiB": None, "error": None}

    async def operation():
        assert reads == 1
        await asyncio.sleep(.02)
        return "done"

    result, samples = await b.sample_during(operation, reader, interval_ms=1)
    assert result == "done" and len(samples) > 1 and active == 0


def good_selection():
    trials = {case: [{"status": "completed", "ttftMs": 100, "providerTps": 20} for _ in range(5)]
              for case in b.CASE_IDS}
    items = [{"caseId": case, "scores": dict.fromkeys(b.DIMENSIONS, 4), "reviewed": True,
              "fatalViolations": []} for case in b.CASE_IDS]
    return trials, items


def test_selection_requires_real_measurements_and_human_review():
    cases, items = good_selection()
    assert b.assess_selection(cases, items)["status"] == "provisional-pass"
    items[0]["reviewed"] = False
    assert b.assess_selection(cases, items)["status"] == "insufficient-data"
    items[0]["fatalViolations"] = ["Fatal quotation: master returned alive"]
    assert b.assess_selection(cases, items)["status"] == "fail"
    cases, items = good_selection()
    cases["initial"][0]["providerTps"] = None
    assert b.assess_selection(cases, items)["status"] == "insufficient-data"
    cases["initial"][0]["status"] = "failed"
    assert b.assess_selection(cases, items)["status"] == "fail"


class FakeRuntime:
    def __init__(self):
        self.env = {"OPENAI_MODEL": "fake-cloud"}
        self.versions = {"ollama": "fake-version"}
        self.fake = FakeProvider()
        self.prepared = []

    async def preflight(self, candidates):
        self.candidates = candidates

    def provider(self, candidate):
        return self.fake

    async def prepare_cold(self, candidate):
        self.prepared.append(candidate["id"])
        return "confirmed-unloaded" if candidate["provider"] == "ollama" else "provider-managed"


async def no_hardware():
    return {"atMs": 0, "gpuDeviceUsedMiB": None, "systemUsedMiB": None, "error": "FAKE"}


async def test_full_sequential_run_artifacts_and_blind_review(tmp_path):
    config, cases = fixtures()
    runtime = FakeRuntime()
    directory = tmp_path / "fake-run"
    assert await b.execute_benchmark(config, cases, directory, runtime, hardware=no_hardware) == 0
    assert runtime.fake.calls == 54
    assert len(runtime.prepared) == 9
    assert len(list((directory / "trials").glob("*/*/*.json"))) == 54
    manifest = b.read_json(directory / "manifest.json")
    assert manifest["comparisonScope"] == "local-and-cloud"
    cloud_trial = b.read_json(next((directory / "trials/cloud-reference").glob("*/*.json")))
    assert cloud_trial["seedApplied"] is False and cloud_trial["temperatureApplied"] is False
    assert manifest["casesSourceSha256"] == b.digest((b.ROOT / "benchmarks/cases.json").read_bytes())
    review = b.read_json(directory / "review.json")
    assert len(review["items"]) == 9
    assert all(set(item) == {"reviewId", "caseId", "output", "invariants", "scores", "fatalViolations", "reviewed"}
               for item in review["items"])
    assert all(all(v is None for v in item["scores"].values()) for item in review["items"])
    snapshots = {p: p.read_bytes() for p in directory.rglob("*.json") if p.name not in {"summary.json", "review.json"}}
    for item in review["items"]:
        item["scores"] = dict.fromkeys(b.DIMENSIONS, 4)
        item["reviewed"] = True
    b.save(directory / "review.json", review)
    summary = b.write_report(directory)
    assert all(v["status"] == "insufficient-data" for v in summary["selection"].values())
    assert all(path.read_bytes() == content for path, content in snapshots.items())
    with pytest.raises(FileExistsError):
        await b.execute_benchmark(config, cases, directory, runtime, hardware=no_hardware)
    review["items"].append(copy.deepcopy(review["items"][0]))
    b.save(directory / "review.json", review)
    with pytest.raises(ValueError, match="Duplicate"):
        b.write_report(directory)


async def test_local_only_partial_comparison_and_error_continuation(tmp_path):
    config, cases = fixtures()
    runtime = FakeRuntime()
    runtime.fake.failure = RuntimeError("secret")
    directory = tmp_path / "partial"
    assert await b.execute_benchmark(config, cases, directory, runtime, local_only=True,
                                     hardware=no_hardware) == 1
    assert runtime.fake.calls == 36
    assert b.read_json(directory / "summary.json")["comparisonScope"] == "partial-local-only"


async def test_cancellation_preserves_current_trial_and_stops_remaining(tmp_path):
    config, cases = fixtures()
    runtime = FakeRuntime()
    runtime.fake.events = [{"type": "delta", "text": "partial"}]
    runtime.fake.failure = asyncio.CancelledError()
    directory = tmp_path / "cancelled"
    assert await b.execute_benchmark(config, cases, directory, runtime,
                                     local_only=True, hardware=no_hardware) == 130
    assert runtime.fake.calls == 1
    trials = list((directory / "trials").glob("*/*/*.json"))
    assert len(trials) == 1
    assert b.read_json(trials[0])["status"] == "cancelled"
    assert b.read_json(trials[0])["output"] == "partial"


async def test_failed_cold_preparation_does_not_mislabel_first_generation_as_warm(tmp_path):
    config, cases = fixtures()
    runtime = FakeRuntime()

    async def failed(_):
        raise ValueError("unload failed")

    runtime.prepare_cold = failed
    directory = tmp_path / "unload-failed"
    assert await b.execute_benchmark(config, cases, directory, runtime,
                                     local_only=True, hardware=no_hardware) == 1
    assert runtime.fake.calls == 0
    assert len(list((directory / "trials").glob("*/*/*.json"))) == 36


@pytest.mark.parametrize("score", [0, 6, 3.5, True, "4"])
def test_review_rejects_invalid_score(score):
    review, _ = b.build_blind_review("run", [{"phase": "warm", "index": 1, "trialId": "a/initial/warm-1",
                                             "caseId": "initial", "output": "fiction", "invariants": ["rule"]}])
    edited = copy.deepcopy(review)
    edited["items"][0]["scores"]["longMemory"] = score
    with pytest.raises(ValueError, match="Scores"):
        b.validate_review(edited, review)


def test_safe_cli_and_output_paths(tmp_path, monkeypatch):
    for argv in [[], ["--config", "c", "--output", "o"], ["--review", "r", "--allow-cloud"],
                 ["--unknown"], ["--config", "c", "--output", "o", "--allow-cloud", "--local-only"]]:
        with pytest.raises(SystemExit):
            b.parse_args(argv)
    with pytest.raises(ValueError):
        b.output_directory("../elsewhere", tmp_path)
    path = b.output_directory("artifacts/benchmarks/new", tmp_path)
    path.mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        b.output_directory("artifacts/benchmarks/new", tmp_path)
    monkeypatch.setattr(b, "Runtime", lambda *_: pytest.fail("Provider factory called"))
    with pytest.raises(SystemExit) as error:
        b.main(["--help"])
    assert error.value.code == 0


async def test_review_mode_never_constructs_runtime(tmp_path, monkeypatch):
    config, cases = fixtures()
    directory = tmp_path / "fake-review"
    await b.execute_benchmark(config, cases, directory, FakeRuntime(), hardware=no_hardware)
    monkeypatch.setattr(b, "output_directory", lambda *_, **__: directory)
    monkeypatch.setattr(b, "Runtime", lambda *_: pytest.fail("Provider factory called"))
    args = b.parse_args(["--review", "fake-review"])
    assert await b.async_main(args) == 0
    trial = next((directory / "trials").glob("*/*/*.json"))
    trial.write_text("{}", encoding="utf8")
    with pytest.raises(ValueError, match="integrity"):
        await b.async_main(args)


async def test_missing_cloud_model_fails_before_provider(tmp_path):
    config, cases = fixtures()
    runtime = FakeRuntime()
    runtime.env = {}
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        await b.execute_benchmark(config, cases, tmp_path / "run", runtime, hardware=no_hardware)
    assert runtime.fake.calls == 0 and not (tmp_path / "run").exists()


async def test_ollama_unload_checks_running_models(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"done": True} if request.method == "POST" else {"models": []})

    original = httpx.AsyncClient
    monkeypatch.setattr(b.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    runtime = b.Runtime({})
    assert await runtime.prepare_cold({"provider": "ollama", "resolvedModel": "fake"}) == "confirmed-unloaded"
    assert json.loads(requests[0].content)["keep_alive"] == 0
    assert requests[-1].url.path == "/api/ps"
