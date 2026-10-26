# Ghostwriter v2
## LLM 기반 소설 생성 서비스 재설계 기술 방향

**Local-first inference · Streaming · Story Memory · Observability**

- **문서 목적**: 기존 Ghostwriter 프로젝트를 프로덕션 지향 v2로 재설계하기 위한 현재 합의 방향 정리
- **프로젝트 성격**: 개인 포트폴리오 / LLM 애플리케이션 / 로컬 추론 실험
- **핵심 전환**: 단일 외부 API 호출형 MVP → 교체 가능한 LLM Provider + 로컬 추론 + 운영 지표 기반 구조
- **기준 하드웨어**: Ryzen 5 5600X / RAM 16GB / RTX 4060 Ti 8GB / 저장공간 1.59TB
- **문서 상태**: 초기 기술 방향안 — 세부 모델·백엔드·성능 목표는 벤치마크 후 확정

> **요약**  
> Ghostwriter v2의 목표는 단순히 소설 생성 기능을 다시 구현하는 것이 아니라, 제한된 개인 하드웨어에서도 체감 응답속도와 안정성을 최대한 확보하고, LLM Provider 교체·장기 문맥 관리·스트리밍·오류 대응·관측성을 갖춘 운영 가능한 생성형 AI 애플리케이션으로 재설계하는 것이다.

---

## 1. 배경과 재설계 목적

기존 Ghostwriter는 LLM API를 이용해 소설을 생성하는 MVP 성격이 강했다. 당시에는 기능 구현과 생성 결과 자체가 우선이었고, 응답 지연이나 Provider 종속성, 실패 처리, 장기 문맥 유지, 운영 지표와 같은 요소는 상대적으로 덜 중요하게 다뤄졌다.

v2에서는 실제 서비스 운영 관점에서 구조를 다시 잡는다. 핵심 질문은 **“생성이 되는가”**가 아니라 다음으로 바뀐다.

- 얼마나 빨리 첫 응답을 주는가
- 실패해도 복구 가능한가
- 모델을 교체할 수 있는가
- 장편 문맥을 효율적으로 관리하는가
- 운영 상태를 측정하고 추적할 수 있는가

### 1.1 v1 → v2 전환

| 항목 | v1 성격 | v2 방향 |
|---|---|---|
| LLM 호출 | 특정 외부 API 중심 | Provider 추상화 + Local/Cloud 선택 |
| 응답 방식 | 완료 후 전체 응답 | Streaming 우선 |
| 지연 대응 | 느리면 대기 | TTFT·TPS·총 지연 측정 및 최적화 |
| 문맥 | 프롬프트에 직접 포함 | Story Memory + Context Builder |
| 장애 처리 | 기본 수준 | Timeout / Retry / Cancel / Fallback |
| 운영성 | 기능 중심 | Metrics / Logging / Error Tracking |
| 배포 | 개발 중심 | Docker 기반 재현 가능한 환경 |

---

## 2. 기술 목표와 비목표

### 2.1 핵심 목표

1. 사용자가 생성 요청 후 가능한 한 빠르게 첫 토큰을 확인할 수 있도록 체감 지연을 줄인다.
2. LLM Provider를 애플리케이션 핵심 로직에서 분리하여 Local Gemma와 외부 API를 교체 가능하게 만든다.
3. 장편 소설에 필요한 세계관·인물·플롯·최근 문맥을 구조적으로 관리한다.
4. Timeout, Retry, Cancellation, Fallback을 포함한 실패 대응 경로를 설계한다.
5. TTFT, 전체 생성시간, tokens/sec, 실패율, 재시도 횟수 등 운영 지표를 수집한다.
6. 개인 PC의 제한된 GPU/메모리 환경에서 품질과 속도의 균형점을 실제 벤치마크로 찾는다.
7. 개인 프로젝트로 공개 가능한 형태의 코드, 문서, 데모, 성능 비교 결과를 남긴다.

### 2.2 현재 비목표

- 최초 버전부터 최대 모델·최대 컨텍스트를 사용하는 것
- 특정 LLM Provider에 프로젝트 전체를 종속시키는 것
- 모든 요청을 동일한 모델로 처리하는 것
- 성능 수치를 측정하지 않은 상태에서 모델 크기만으로 품질을 판단하는 것

---

## 3. 로컬 실행 환경과 제약

| 구성 | 현재 사양 | 설계상 의미 |
|---|---|---|
| CPU | AMD Ryzen 5 5600X (6 Core) | CPU offload 사용 시 보조 가능하나, 주 추론 성능은 GPU 의존 |
| RAM | 16 GB | 모델 offload·Docker·IDE·DB 동시 사용 시 병목 가능. 32 GB 확장은 향후 선택지 |
| GPU | NVIDIA GeForce RTX 4060 Ti 8 GB | 로컬 LLM 가능. 모델 크기와 KV Cache를 보수적으로 설계해야 함 |
| Storage | 약 1.59 TB | 여러 양자화 모델과 로그·DB 저장에는 비교적 여유 |

### 설계 원칙

**“가장 큰 모델을 억지로 실행”하는 것이 아니라, 8GB VRAM에서 실사용 가능한 TTFT·생성 속도·품질을 확보하는 방향으로 최적화한다.**

---

## 4. 목표 아키텍처

애플리케이션과 모델 실행 계층을 분리한다. 프론트엔드는 생성 요청과 스트리밍 소비에 집중하고, `Generation Orchestrator`가 문맥 조립·정책·Provider 선택·실패 처리를 담당한다.

```text
[Frontend]
    │
    │  Generate / Cancel / Stream
    ▼
[Application API]
    │
    ├── Story Service
    ├── Session / Project Service
    └── Generation Orchestrator
             │
             ├── Context Builder
             │      └── Story Memory
             │
             ├── Reliability
             │      ├── Timeout
             │      ├── Retry
             │      ├── Cancellation
             │      └── Fallback
             │
             ▼
         [LLM Gateway]
             ├── Local Gemma Adapter
             │       └── Ollama / llama.cpp / 기타 backend
             └── Cloud Adapter
                     └── Gemini 등 외부 API

[Observability]
    └── TTFT / latency / TPS / error / retry / model / request_id
```

### 4.1 LLM Gateway 책임

- 공통 `generate()` 인터페이스 제공
- Provider별 요청/응답 형식 차이 은닉
- Streaming 응답 형식 통일
- Timeout·오류 코드 정규화
- Provider / Model 메타데이터 기록
- 향후 모델 교체·A/B 비교가 가능하도록 결합도 최소화

---

## 5. LLM Provider 전략

기본 방향은 **Local-first**이다.

Gemma 계열을 로컬 모델의 우선 후보로 두되, 정확한 모델 크기·양자화 방식·추론 백엔드는 실제 벤치마크 결과를 보고 확정한다.

Cloud Provider는 제거하지 않고 다음 용도로 유지할 수 있도록 한다.

- 고품질 생성
- 로컬 모델과 비교 실험
- 로컬 서버 미가동 시 Fallback
- 특정 작업에 대한 선택적 사용

| 후보 | 주 용도 | 장점 | 주의점 |
|---|---|---|---|
| Local Gemma | 기본 생성 / 로컬 우선 | 비용 통제, 데이터 경로 통제, 추론 최적화 경험 | VRAM·RAM 제약, 모델별 품질 차이 |
| Gemini 등 Cloud | 고품질 옵션 / 비교 / Fallback | 강한 범용 성능, 관리 부담 적음 | 비용, 네트워크 지연, Provider 종속 |

### 5.1 모델 역할 분리 가능성

모든 작업을 동일한 모델에 맡기지 않는다.

```text
Generation Orchestrator
 ├─ Lightweight Model
 │   ├─ Previous Episode Summary
 │   ├─ Character State Extraction
 │   └─ Metadata / Tagging
 │
 └─ Main Generation Model
     ├─ Scene Generation
     ├─ Dialogue
     └─ Narrative Continuation
```

경량 모델은 다음 작업에 적합하다.

- 이전 회차 요약
- 등장인물 상태 추출
- 메타데이터 갱신
- 태깅
- 짧은 수정 작업

메인 생성 모델은 다음 작업에 집중한다.

- 장면 작성
- 대화 생성
- 서술
- 장기 흐름을 고려한 본문 이어쓰기

---

## 6. 응답속도 최적화

전체 생성시간만 줄이는 것보다 **사용자가 느끼는 대기시간**을 줄이는 것을 우선한다.

따라서 첫 구현부터 Streaming을 전제로 한다.

### 6.1 주요 성능 지표

| 지표 | 의미 | 활용 |
|---|---|---|
| TTFT | Time To First Token | 요청부터 첫 토큰 표시까지의 시간 |
| Generation Latency | 총 생성시간 | 마지막 토큰까지 생성되는 데 걸린 시간 |
| Tokens/sec | 생성 처리량 | 모델·양자화·Backend 간 실질 속도 비교 |
| Prompt Tokens | 입력 크기 | Context Builder 효율 평가 |
| Completion Tokens | 출력 크기 | 생성 길이와 속도 관계 파악 |
| Error Rate | 실패 비율 | Provider·모델별 안정성 비교 |
| Retry Count | 재시도 횟수 | 불안정 구간 탐지 |

### 6.2 최적화 우선순위

1. Streaming 적용
2. 모델 Warm-up / Keep-alive 검토
3. 프롬프트·Context 크기 축소
4. 양자화 수준별 품질/속도 비교
5. GPU 메모리 내 적재 범위 극대화
6. 불필요한 중복 호출 제거
7. 경량 모델과 본문 모델의 역할 분리
8. 필요 시 Speculative Decoding 등 고급 최적화 실험

---

## 7. Story Memory 및 Context 설계

장편 소설에서는 최대 컨텍스트 길이에 의존하기보다 필요한 정보를 구조화하고, 현재 장면에 필요한 정보만 `Context Builder`가 조립하도록 한다.

```text
Story Memory
 ├── World Bible
 ├── Character State
 ├── Plot State
 ├── Open Foreshadowing
 ├── Episode Summaries
 └── Recent Raw Text
          │
          ▼
    Context Builder
          │
          ├── Always-on Context
          ├── Recent Context
          └── Relevant Retrieved Context
          │
          ▼
        LLM
```

### 7.1 기본 원칙

- 세계관 핵심 규칙과 현재 인물 상태는 항상 유지
- 직전 장면·직전 회차는 상대적으로 상세하게 유지
- 오래된 회차는 요약 중심으로 압축
- 미회수 복선·현재 장면과 관련된 설정만 선택적으로 주입
- Context Builder의 결과와 토큰 수를 로그로 남겨 최적화 근거 확보

---

## 8. 안정성 및 운영 설계

### Timeout

Provider 또는 로컬 추론 서버가 무한 대기하지 않도록 요청 상한을 둔다.

### Retry

일시적 실패만 재시도하며, 동일 요청의 중복 생성 여부를 관리한다.

### Cancellation

사용자가 생성 중단을 요청하면 서버와 모델 스트림까지 가능한 범위에서 중지한다.

### Fallback

로컬 모델 장애 시 Cloud Provider로 넘길지 여부를 설정 가능하게 한다.

### Request ID

생성 요청 단위 식별자를 부여해 API·모델·DB 로그를 연결한다.

### Structured Logging

다음 항목을 구조화해서 기록한다.

```text
request_id
provider
model
prompt_tokens
completion_tokens
ttft_ms
generation_ms
tokens_per_second
status
retry_count
```

---

## 9. 벤치마크 계획

모델 선택은 사양표가 아니라 **동일한 입력 세트에 대한 실측 결과**로 결정한다.

초기에는 2~3개의 로컬 후보와 1개의 Cloud 기준 모델을 비교한다.

| 후보 | TTFT | Tokens/s | RAM/VRAM | 품질 | 결론 |
|---|---:|---:|---:|---|---|
| Local Gemma 후보 A | 측정 | 측정 | 측정 | 평가 | 보류 |
| Local Gemma 후보 B | 측정 | 측정 | 측정 | 평가 | 보류 |
| Cloud 기준 모델 | 측정 | 측정 | 측정 | 평가 | 보류 |

### 9.1 품질 평가 기준

단순 선호도 대신 소설 도메인에 맞는 기준을 사용한다.

- 캐릭터 일관성
- 설정 준수
- 문체 안정성
- 반복 표현 빈도
- 장면 연결성
- 장기 문맥 유지
- 사용자 지시 준수
- 생성 결과의 수정 필요 정도

---

## 10. 로컬 추론 Backend 검토

첫 단계에서는 설치와 양자화 모델 운용이 쉬운 **Ollama 또는 llama.cpp 계열**을 우선 검토한다.

`vLLM` 등 다른 Backend는 실제 요구 성능과 하드웨어 조건을 확인한 뒤 비교한다.

| Backend | 검토 이유 | 현재 위치 |
|---|---|---|
| Ollama | 빠른 로컬 개발, API 제공, 모델 관리 편의 | 초기 후보 |
| llama.cpp | 양자화·CPU/GPU offload 실험 유연성 | 초기 후보 |
| vLLM | 서빙 성능·운영 기능 비교 가치 | 후속 검토 |

---

## 11. 네트워크 및 보안 원칙

- 로컬 LLM 서버 포트를 인터넷에 직접 공개하지 않는다.
- 외부 접근이 필요하면 인증·HTTPS·VPN/터널 등 별도 보호 계층을 둔다.
- API Key와 Secret은 소스코드·Git 저장소에서 분리한다.
- 로그에 프롬프트 전체나 민감정보가 무조건 저장되지 않도록 로그 정책을 분리한다.
- Cloud Provider를 사용할 때는 어떤 데이터가 외부로 전송되는지 사용자가 인지할 수 있도록 설계한다.

---

## 12. 구현 단계

### Phase 0 — v1 분석

기존 기능·코드·DB·프롬프트 흐름을 정리하고 다음 네 범주로 나눈다.

- 유지
- 제거
- 리팩터링
- 신규 구현

### Phase 1 — v2 골격

- Application API
- Generation Orchestrator
- LLM Gateway 인터페이스

를 먼저 구성한다.

### Phase 2 — Streaming

- 첫 토큰부터 프론트로 전달
- 생성 중 취소
- TTFT 계측

### Phase 3 — Local Provider

- Gemma 후보 연결
- 양자화 비교
- Backend별 실측 벤치마크
- VRAM / RAM 사용량 확인

### Phase 4 — Story Memory

- 세계관
- 인물 상태
- 플롯
- 회차 요약
- 최근 원문

을 구조화하고 Context Builder를 구현한다.

### Phase 5 — Reliability

- Timeout
- Retry
- Cancellation
- Fallback
- Request ID

를 정리한다.

### Phase 6 — Observability

다음 지표를 수집하고 시각화한다.

- TTFT
- Tokens/sec
- Latency
- Error Rate
- Retry Count
- Token Usage
- Provider / Model

### Phase 7 — Packaging

- Docker
- 환경변수
- 실행 문서
- 테스트 시나리오
- 공개용 README
- 벤치마크 결과

를 정리한다.

---

## 13. 포트폴리오 관점의 목표

v2는 단순 기능 추가보다 **“왜 구조를 바꿨는가”**를 보여주는 프로젝트로 만든다.

과거 MVP를 삭제하지 않고 v1을 학습·프로토타입 버전으로 보존하고, v2에서 실무 경험을 통해 발견한 운영 문제를 어떻게 해결했는지 기록한다.

### 권장 프로젝트 설명

> 기존 LLM API 기반 소설 생성 MVP를 Local-first LLM inference, streaming, provider abstraction, long-context memory, observability와 fault handling을 갖춘 production-oriented generative AI application으로 재설계했다.

### 13.1 공개 산출물

- v1 / v2 차이를 설명하는 README
- 공개 가능한 시스템 아키텍처 다이어그램
- 로컬 모델 vs Cloud Provider 벤치마크
- TTFT / TPS / Latency 측정 결과
- 설계 의사결정 기록(ADR 또는 기술 블로그 형식)
- 실행 가능한 Docker 환경과 샘플 데이터

---

## 14. 아직 확정하지 않은 항목

| 항목 | 현재 방향 | 결정 방법 |
|---|---|---|
| 정확한 Gemma 모델 | Gemma 계열 우선 | 8GB VRAM 실측 벤치마크 |
| Quantization | 4-bit 계열 포함 검토 | 품질 / VRAM / TPS 비교 |
| Inference Backend | Ollama·llama.cpp 우선 | 동일 모델 실측 |
| Cloud Fallback | 유지 가능 | 비용·품질·사용성 평가 |
| Frontend Streaming 방식 | SSE 우선 검토 | 생성·취소 흐름 구현 후 결정 |
| Memory 저장 구조 | 구조화된 Story Memory | DB 설계 단계에서 확정 |
| RAM 업그레이드 | 필수 아님 | 실행 중 메모리 병목 발생 시 32GB 검토 |

---

## 15. 다음 작업

1. 현재 GitHub 저장소 구조와 v1 실행 상태 확인
2. 기능 목록과 기술부채를 `유지 / 제거 / 리팩터링 / 신규 구현`으로 분류
3. v2 요구사항과 최소 성공 기준 정의
4. LLM Gateway 인터페이스 초안 작성
5. Gemma 후보 2~3개를 동일 조건에서 로컬 벤치마크
6. Streaming + TTFT 계측을 최소 기능으로 먼저 구현
7. 결과를 바탕으로 최종 로컬 모델과 추론 Backend 확정

---

## 16. 의사결정 원칙

Ghostwriter v2에서는 다음 순서로 기술 결정을 내린다.

```text
요구사항
   ↓
제약조건
   ↓
아키텍처 후보
   ↓
벤치마크 / 실측
   ↓
기술 선택
   ↓
운영 지표 확인
   ↓
필요 시 재설계
```

특정 모델이나 프레임워크를 먼저 정하고 구조를 끼워 맞추지 않는다.

특히 Gemma의 정확한 모델 버전, Quantization, VRAM 요구량, 추론 Backend는 이론상 사양보다 **현재 로컬 환경에서 재현 가능한 실측 데이터**를 최종 의사결정 기준으로 사용한다.
