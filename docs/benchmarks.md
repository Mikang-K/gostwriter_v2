# 모델 비교 실행

Python CLI는 앱의 `Provider`와 시간 계산 함수를 재사용한다. 2026-10-26 로컬 Q4 모델 2개를 36회 측정했다. [실측 결과](benchmark-results-2026-10-26.md)를 참고한다. 기본 로컬 모델은 실제 결과와 사람이 완료한 품질 평가를 함께 보고 결정한다. 모든 선택 기준은 임시 기준이며 SLA가 아니다.

저장소 루트 PowerShell에서 다음처럼 실행한다. `PYTHONPATH`는 Python이 backend 패키지를 찾게 한다.

```powershell
$env:PYTHONPATH = "backend"
backend/.venv/Scripts/python.exe -m app.benchmark --help
backend/.venv/Scripts/python.exe -m app.benchmark --config benchmarks/candidates.json --output artifacts/benchmarks/local-20261026 --local-only
```

`--local-only`는 Cloud를 호출하지 않는 **부분 비교**다. 전체 비교는 로컬 후보 두 개 이상과 Gemini 한 개가 필요하다. 선택 후보인 4b Q8까지 측정하려면 `--include-optional`을 추가한다. help와 review는 모델을 생성하지 않는다. import만으로도 실행되지 않는다.

## 준비와 비용

Ollama 설치, 서버 실행, 모델 다운로드는 별도 수동 단계다. CLI는 모델을 내려받거나 서버를 시작하지 않는다. 준비된 Ollama의 `/api/tags`에 아래 모델이 있어야 한다.

- `gemma3:1b-it-q4_K_M`
- `gemma3:4b-it-q4_K_M`
- 선택: `gemma3:4b-it-q8_0`

Ollama 기본 주소는 `http://127.0.0.1:11434`다. `OLLAMA_BASE_URL`로 loopback HTTP 주소만 바꿀 수 있다. 외부 주소, 사용자 정보, query와 JSON 내 URL/실행 명령은 허용하지 않는다. 앱 DB, v1 데이터, Supabase는 사용하지 않는다.

Cloud를 포함하려면 실행 환경에 `GEMINI_MODEL`, `GEMINI_API_KEY`를 설정하고 `--allow-cloud`를 명시한다. `.env.local`은 자동으로 읽지 않는다. 모델은 운영자가 지정하며 키를 명령 인자나 출력물에 기록하지 않는다.

```powershell
backend/.venv/Scripts/python.exe -m app.benchmark --config benchmarks/candidates.json --output artifacts/benchmarks/full-20261026 --allow-cloud
```

전체 기본 실행은 54회, 선택 로컬 후보 포함 시 72회다. 이 중 **Cloud 18회는 합성 한국어 입력을 외부로 전송하며 비용이 발생할 수 있다**. CLI는 retry나 Cloud fallback을 하지 않는다. `--allow-cloud` 또는 `--local-only` 중 정확히 하나가 필요하다. 새로운 run-id만 허용하며 기존 디렉터리와 trial을 덮어쓰지 않는다.

## 측정 조건

초기 생성, 이어쓰기, 장기 기억의 고정 한국어 사례별로 cold 1회 후 warm 5회를 직렬 실행한다. 최대 출력 512 tokens, temperature 0.7, contextWindow 8192, seed 42, timeout 120초를 고정한다. Gemini의 contextWindow는 입력 예산 의미이며 서버 설정을 바꾸지 않는다. Gemini는 seed 미적용으로 기록한다. seed 적용도 출력 재현성을 보장하지 않는다.

Ollama cold 직전에 `POST /api/generate`의 `keep_alive:0` 완료와 `/api/ps`에서 모델 부재를 확인한다. 실패하면 해당 사례의 여섯 trial은 호출하지 않고 실패로 남긴다. 다른 사례는 계속한다. Cloud 첫 요청은 `provider-managed`이며 하드웨어 cold 근거가 아니다.

TTFT는 Provider 순회 직전부터 첫 번째 비어 있지 않은 본문 delta까지다. 브라우저 표시 시간과 구분한다. generationMs는 정상 iterator 종료까지다. decode TPS는 Provider가 준 completionTokens/decodeMs, endToEndTps는 completionTokens/(마지막 본문 delta 시각-시작 시각)이다. token counter를 글자나 delta 개수로 추정하지 않는다. missing counter는 null이다. 출력 제한 종료는 length로 남기고 절단 횟수를 보고한다.

GPU0 `nvidia-smi`와 PowerShell CIM을 shell 없이 각각 최대 2초 동안 읽는다. 생성 직전 baseline, 이후 250ms 대기마다 읽으며 앞선 읽기와 겹치지 않는다. 실제 간격은 subprocess 실행 시간만큼 길어질 수 있다. GPU는 **장치 전체**, RAM은 **OS 전체** 사용량이다. 다른 프로세스를 포함하며 표본 사이 peak와 개별 추론 프로세스의 정확한 최대치를 보장하지 않는다. unavailable 값과 읽기 실패는 null/오류로 남기고 모델 성공과 구분한다.

측정 전 IDE, Docker, 다른 GPU 작업 상태와 offload 설정을 기록한다. `BENCHMARK_NOTES`에 비밀정보 없는 환경 메모를 넣을 수 있다. 하드웨어와 OS/Python/백엔드 버전, 설정·사례 hash, 사례 UTF-8 bytes, artifact identity, seed 적용, cold 확인, 원자료 checksum을 저장한다. 원시 환경변수 전체는 저장하지 않는다.

SIGINT는 진행 중 async Provider를 취소하고 이후 trial을 중단한다. 취소 trial과 부분 출력은 남긴다. 종료 코드는 정상 0, 오류/실패 1, 취소 130이다. 실제 서버 종료나 모델 전역 취소를 별도로 호출하지 않는다.

## llama.cpp 비교

앱 설정은 바꾸지 않고 별도 config에서 선택 Q8 후보를 `provider: "llamacpp"`인 Q4 후보로 교체한다. 기본 `http://127.0.0.1:8080` 또는 `LLAMACPP_BASE_URL`의 loopback HTTP 서버를 사용한다. 설치 버전의 server API 문서를 확인하고 `/props.default_generation_settings.n_ctx`가 8192가 되게 시작한다. `artifact.runtimeVersion`에 설치 버전을 기록한다.

각 사례 cold 전에 실행자가 서버를 재시작하고 TTY 안내에 `restarted`를 입력한 경우만 `external-restart`로 남긴다. 그 외에는 `unverified`이며 cold 비교에서 해석을 제한한다. CLI가 다른 프로세스를 종료하지 않는다.

동일 backend 효과라고 해석하려면 실제 GGUF SHA-256, quantization, chat template SHA-256, context와 생성 조건이 모두 같아야 한다. config의 artifact에 확인된 값을 기록한다. Ollama manifest digest만으로 GGUF가 같다고 간주하지 않는다. null 또는 불일치는 `incomparable`다. 서로 다른 모델의 실제 사용 후보 비교는 가능하다.

## Blind 평가와 재집계

결과는 `artifacts/benchmarks/<run-id>/`에 저장한다. trial JSON과 manifest는 보존하며, 각 사례의 warm-1 출력으로 만든 `review.json`만 사람이 수정한다. `review-key.json`에는 원자료 연결이 있으므로 평가자에게 함께 주지 않는다. review 항목에는 모델명과 timing 메타데이터를 넣지 않는다. 내용 자체의 자기소개까지 익명화하지는 않는다.

`benchmarks/quality-rubric.md`를 따라 8개 차원의 null 값을 1~5 정수로 바꾸고 `reviewed:true`로 표시한다. 위반이 있으면 `fatalViolations`에 출력 인용과 이유를 적는다. 사망자의 회상은 부활 위반이 아니다. 평가 전 점수는 자동으로 만들지 않는다. 식별자, 본문, 사례, 불변 규칙은 수정하지 않는다.

```powershell
backend/.venv/Scripts/python.exe -m app.benchmark --review artifacts/benchmarks/full-20261026
```

재집계는 Provider를 만들지 않고 기존 결과만 읽는다. raw trial checksum을 확인하며 manifest와 trial은 바꾸지 않는다. `summary.json`, `report.md`만 다시 작성한다. 점수 범위, 중복/누락 항목과 본문 변경을 거부한다.

임시 통과 조건은 모든 사례 warm 5/5 성공, TTFT p95 ≤5000ms, providerTps median ≥15, fatal=0, 24개 점수 모두 ≥3 및 평균 ≥3.5다. 누락된 측정과 미평가 출력은 `insufficient-data`이며 높은 품질 점수로 fatal 위반을 상쇄할 수 없다. n=5 nearest-rank p95는 최대값이다. cold는 별도 행이며 warm에 섞지 않는다. 보고서/summary는 성공·실패·취소·절단, metric별 유효 n, token 수, baseline/관측 peak를 제공한다.

자동 기본 모델 지정은 하지 않는다. 모든 로컬 후보가 실패하면 실제 근거가 있는 context, quantization, offload, 동시 작업, RAM 병목 중 한 항목씩 바꾸고 새 run-id로 재실행한다. 아직 측정하지 않은 8GB 적재 가능성이나 성능은 주장하지 않는다.

## 오프라인 검증

```powershell
backend/.venv/Scripts/python.exe -m pytest -c backend/pyproject.toml backend/tests/test_benchmark.py
```

테스트는 fake Provider/clock/process reader와 임시 디렉터리를 사용한다. 실제 모델, GPU, PowerShell 하드웨어 명령, 외부 API, DB와 다운로드를 실행하지 않는다. 이 테스트 통과는 harness 검증이며 모델 성능 검증은 아니다.
