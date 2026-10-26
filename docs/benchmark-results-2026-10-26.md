# 2026-10-26 로컬 모델 측정 결과

범위는 로컬 부분 비교다. Cloud 기준 모델, llama.cpp, 선택 Q8은 실행하지 않았다. 기본 모델의 최종 채택은 보류한다.

## 환경과 실행

- GPU: NVIDIA RTX 4060 Laptop GPU, 8188MiB. 최초 계획의 데스크톱 RTX 4060 Ti와 다르다.
- Windows, Python 3.13.14, Ollama. 정확한 OS·CPU·driver·Ollama 버전과 모델 digest는 로컬 manifest에 기록했다.
- `gemma3:1b-it-q4_K_M`, `gemma3:4b-it-q4_K_M`. context 8192, 출력 한도 512 tokens, temperature 0.7, seed 42.
- 초기·이어쓰기·장기 기억 각각 cold 1회와 warm 5회, 후보당 18회, 합계 36회. CLI 종료 코드 0.
- 실행 중 IDE·Next 빌드와 브라우저 검증이 병행되었다. 자원 사용량은 장치 전체 값이며 순수 추론 프로세스 사용량이 아니다. 시스템 RAM counter는 CIM 접근 제한으로 null이다.

```powershell
$env:PYTHONPATH = "backend"
backend/.venv/Scripts/python.exe -m app.benchmark --config benchmarks/candidates.json --output artifacts/benchmarks/2026-10-26-local --local-only
```

이 run-id는 이미 존재하므로 재실행할 때 새 이름을 사용한다. [원자료 보고서](../artifacts/benchmarks/2026-10-26-local/report.md), [manifest](../artifacts/benchmarks/2026-10-26-local/manifest.json), [집계 JSON](../artifacts/benchmarks/2026-10-26-local/summary.json)은 Git에서 제외한 로컬 검증 산출물이다.

## Warm 관측값

| 후보 | 사례 | 유효 n | TTFT p95 ms | Provider decode TPS median | GPU 관측 peak MiB |
|---|---|---:|---:|---:|---:|
| Gemma 1B Q4 | 초기 | 5 | 772.3 | 161.1 | 1792 |
| Gemma 1B Q4 | 이어쓰기 | 5 | 784.2 | 162.1 | 1792 |
| Gemma 1B Q4 | 장기 기억 | 5 | 862.5 | 161.2 | 1792 |
| Gemma 4B Q4 | 초기 | 5 | 1217.6 | 70.1 | 5787 |
| Gemma 4B Q4 | 이어쓰기 | 5 | 1258.0 | 64.4 | 5787 |
| Gemma 4B Q4 | 장기 기억 | 5 | 1652.6 | 63.3 | 5789 |

n=5 nearest-rank p95는 다섯 관측값 중 최대다. TPS는 Ollama의 실제 completion token·decode duration으로 계산했으며 브라우저 표시 속도가 아니다. cold 초기 TTFT는 1B 89.7초, 4B 22.4초였다. 이 결과로 앱의 기본 first-token 제한 60초가 모든 cold 시작을 수용한다고 주장할 수 없다.

## 판정

36회 모두 정상 스트림 종료와 token counter를 얻었지만 **36회 모두 512-token 출력 제한으로 절단**되었다. 따라서 호출 완료율을 완성된 소설 성공률로 해석하지 않는다. 앱은 같은 length 종료를 미확정 초안으로 남기며 자동 확정하지 않는다.

수동 blind 점수는 모두 null이며 fatal 위반도 아직 판정하지 않았다. 품질표를 채우고, 완결성을 측정할 출력 한도 또는 분량 지시를 별도 실험으로 조정한 뒤 새 run-id로 비교해야 한다. 기존 결과와 조건을 섞거나 자동 점수를 만들지 않는다. Cloud 기준 비교와 동일 GGUF·template의 backend 효과 비교도 미완료다.

별도의 짧은 합성 소설 smoke에서는 실제 Gemma 4B의 본문과 structured 상태 추출이 모두 완료되어 PostgreSQL에 1회차로 원자 저장되었다. 첫 실행은 본문 49 tokens·TTFT 약 1.04초·전체 약 4.31초였으며, transport deadline 수정 후 최종 재검증은 55 tokens·TTFT 약 8.07초·전체 약 11.29초였다. 두 실행 모두 원문·회차 재조회 일치를 확인했다. 이는 짧은 사례의 기능 검증이며 장편 품질 평가가 아니다. [최종 로컬 smoke 원자료](../artifacts/model-smoke.json).
