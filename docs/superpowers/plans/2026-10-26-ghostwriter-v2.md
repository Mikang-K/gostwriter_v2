# Ghostwriter v2 Implementation Plan

> **2026-10-26 구조 변경:** 사용자는 Next.js 화면 + Python FastAPI + PostgreSQL을 확정했다. [ADR 0001](../../adr/0001-runtime-and-storage.md), [변경 구현 지침](../../backend-implementation.md), [현재 실행 방법](../../../README.md)이 아래 원계획의 Node API·Supabase·TypeScript Provider·pgTAP·단일 컨테이너 지침을 대체한다. 현재 실제 GPU는 RTX 4060 Laptop GPU 8GiB이며 원계획의 데스크톱 하드웨어는 과거 가정이다. 아래 상세 작업·인수 조건은 추적을 위해 보존하며 전체 완료를 선언하지 않는다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 8GB VRAM 환경에서 소설 생성·이어쓰기·중단·복구·Story Memory·운영 지표를 검증할 수 있는 Local-first v2를 구현한다.

**Architecture:** Next.js의 화면과 Node.js Route Handler를 하나의 장기 실행 애플리케이션으로 구성한다. Generation Orchestrator가 Context Builder, 교체 가능한 Provider, 원자적 저장을 연결하고, 브라우저에는 POST fetch 기반 SSE로 본문을 전달한다. Supabase Auth/PostgreSQL은 재사용하되 v1과 분리된 프로젝트에 완전한 초기 스키마를 만든다.

**Tech Stack:** Node.js 24 LTS, Next.js 16, React 19, TypeScript strict, Tailwind CSS 4, Supabase Auth/PostgreSQL, Zod, native fetch/ReadableStream/AbortController, Ollama, 선택적 Gemini REST API, Vitest, Playwright. 정확한 patch 버전은 T01에서 확인하고 lockfile에 고정한다.

**Spec:** [ghostwriter_v2_technical_direction.md](../../../ghostwriter_v2_technical_direction.md). 실측과 독립적으로 검증할 수 있는 벤치마크 도구는 [별도 구현 계획](2026-10-26-ghostwriter-v2-benchmark.md), 진행 상태는 [체크리스트](2026-10-26-ghostwriter-v2-checklist.md)에서 관리한다.

## Global Constraints

- 기준 하드웨어: Ryzen 5 5600X / RAM 16GB / RTX 4060 Ti 8GB / 저장공간 1.59TB.
- 기본 방향은 Local-first이며 정확한 Gemma 모델·Quantization·Inference Backend는 벤치마크 후 확정한다.
- 첫 구현부터 Streaming을 전제로 하고 TTFT·전체 생성시간·tokens/sec·실패율·재시도 횟수를 수집한다.
- 세계관 핵심 규칙과 현재 인물 상태는 항상 유지한다. 오래된 회차는 요약 중심으로 압축한다.
- Timeout / Retry / Cancellation / Fallback / Request ID를 구현한다.
- 로컬 LLM 서버 포트를 인터넷에 직접 공개하지 않는다.
- API Key와 Secret은 소스코드·Git 저장소에서 분리한다.
- 프롬프트 전체·민감정보를 로그에 기본 저장하지 않는다. Cloud 전송 범위를 사용자가 인지하도록 한다.
- v1은 보존하며 `../gostwriter`의 코드·DB·배포를 이 계획 실행의 변경 대상으로 삼지 않는다.
- 이 문서는 구현 계획이다. 체크 표시가 없는 작업, 성능 수치, 테스트 결과를 완료 사실로 해석하지 않는다.

---

## 1. 현재 확인된 사실과 계획의 가정

2026-10-26 기준 v2 디렉터리에는 기술 방향 문서만 있고 `.git`, 애플리케이션, 테스트, lockfile은 없다. 아래 v1 분석은 로컬 소스의 정적 확인 결과다. 원격 Supabase 스키마, 배포 상태, 모델 실행 가능 여부는 확인하지 않았다.

### 1.1 v1에서 확인한 재사용 근거

경로는 `../gostwriter/` 기준이다. README보다 실제 코드와 package.json을 우선했다.

| 근거 | 확인한 내용 | 계획 반영 |
|---|---|---|
| `frontend/package.json:5`, `:16` | Next.js 16.1.6 / React 19.2.3, dev/build/start/lint만 존재 | Next/React/Tailwind 유지, 보안 patch 갱신, 자동 검증 추가 |
| `supabase/functions/generate-novel/index.ts:255` | 실제 9개 mode, CRUD와 생성이 한 진입점에 결합 | 도메인 API와 생성 API 분리 |
| `services/aiService.ts:244`, `:295`, `:333` | Vertex `generateContent`, 전체 JSON 파싱 후 반환 | 본문 스트림과 구조화 Memory 추출을 두 단계로 분리 |
| `services/aiService.ts:100`, `index.ts:242` | CRUD에서도 AI 서비스 생성·Vertex 자격증명 요구 | 프로젝트 조회·수정은 모델이 꺼져 있어도 작동 |
| `services/promptService.ts:241`, `:667` | 기승전결, 작가 노트, 인물 불변/가변 분리 | Provider 중립 프롬프트로 재작성, 경계값 테스트 |
| `20260706000001_generation_state_rpcs.sql:26`, `:50` | 저장 시 row lock, 본문·상태·관계 원자 저장 | 짧은 transaction 원칙 유지, revision·멱등성 추가 |
| `services/dbService.ts:299` | Undo가 독립 쿼리 여러 개, 일부 오류 미확인 | episode에 연결된 immutable snapshot으로 원자 Undo |
| `supabase/migrations/`, `database.types.ts:506` | 초기 core DDL·RAG RPC 누락, 타입의 Functions가 비어 있음 | v1 migration 복사 대신 재현 가능한 v2 초기 스키마 |
| `StoryViewer.tsx:175`, `RoleplayViewer.tsx:203` | 전체 응답 대기, stream·abort 전파 없음 | draft와 확정 본문 분리, 생성 중 취소 구현 |
| `frontend/src/app/novel/create/`, `character/create/` | 디렉터리는 있으나 페이지 구현 없음 | 생성 화면을 신규 작업으로 산정 |
| `TokenChargeModal.tsx:34` | 브라우저의 모의 지갑 변경과 별도 ledger insert | v2 초기 범위에서 지갑·충전 제외 |
| `frontend/test-function.js:16` | 실제 로그인·모델 호출 수동 스크립트 | 자동 테스트로 간주하지 않고 격리된 fixture 사용 |

위 `services/`는 `supabase/functions/generate-novel/services/`, UI 파일은 `frontend/src/components/` 아래에 있다. v1의 오래된 README에 적힌 Next.js 15, Gemini Pro, `character_chat`을 실제 구현 계약으로 복사하지 않는다.

### 1.2 구현 기본안과 결정 시점

| 항목 | 이 계획의 기본안 | 근거·재검토 조건 |
|---|---|---|
| API | Next.js Node Route Handler, 단일 Node 프로세스 | TS/UI 재사용. 별도 FastAPI·Fastify·서비스 간 통신 불필요 |
| 데이터·인증 | 별도 v2 Supabase 프로젝트 | Auth/PostgreSQL 경험 재사용. **Local-first는 추론 우선 정책이며 데이터의 완전 오프라인 보관을 뜻하지 않는다** |
| 로컬 개발 DB | 개발용 hosted Supabase 또는 격리된 테스트 Supabase | RAM 16GB에서 전체 로컬 Supabase 스택 상시 실행은 기본으로 요구하지 않음 |
| 추론 연결 | 호스트 Ollama `127.0.0.1:11434` | 초기 연결을 단순화. 모델·양자화는 B05까지 실험값 |
| Cloud | Gemini REST, 기본 비활성 | 사용자 요청별 동의와 서버 설정이 모두 있을 때만 사용 |
| 스트림 | `POST /api/generations` 응답 SSE, fetch로 소비 | cookie 인증·JSON 입력·AbortSignal을 한 경로로 처리 |
| 메모리 검색 | 인물 ID·키워드·최근성에 따른 결정적 요약 선택 | 첫 버전은 외부 embedding 호출과 vector DB 없이 완성 |
| 배포 | 로컬 Node / Docker Desktop의 단일 app container | Vercel/Supabase Edge의 localhost는 사용자 PC가 아니므로 로컬 추론 기본 배포에서 제외 |
| 관측 | JSON 로그 + DB request/attempt 지표 + 읽기 전용 화면 | Prometheus/Grafana/OTel collector는 초기 필수 아님 |

DB·프레임워크 선택은 방향서에 명시된 확정 사항이 아니라 이 계획의 구현 가정이다. 완전 오프라인 저장 또는 다중 서버 운영이 필수로 바뀌면 T02 이전에 별도 ADR로 저장소·인증·작업 실행 구조를 다시 결정한다.

### 1.3 유지 / 제거 / 리팩터링 / 신규 구현

| 구분 | 범위 |
|---|---|
| 유지 | 한국어 소설 생성·이어쓰기·에필로그·완결, 작가 노트, 문체 예시 최대 3개, 인물 불변 정보, 페이싱, 회차 열람, 마지막 회차 Undo |
| 제거 | 초기 v2의 모의 토큰 충전·지갑 UI, CRUD의 AI 자격증명 의존, 전체 출력 JSON을 기다리는 생성 경로 |
| 리팩터링 | 서재·읽기 UI, auth cookie 갱신, 서버 입력 검증, 프롬프트, 원자 저장, 스냅샷, 요청 로그 |
| 신규 | 실제 생성 페이지, Provider 계약, SSE, cancel, draft 복구, revision/idempotency, 구조화 Memory, 성능 비교 도구, Docker 실행 문서 |
| 후속 범위 | 롤플레이·inventory, 공개 공유·공지·결제, 전역 캐릭터 마켓/프리셋, 자동 v1 데이터 이관, vector RAG, vLLM, speculative decoding, 복수 GPU/서버, 동시에 두 모델 상주 |

언어 필드는 `ko/en/ja`를 보존하고 프롬프트에 반영한다. 첫 릴리스의 품질 평가는 한국어를 필수로 한다. 롤플레이를 이미 지원하는 것처럼 UI나 README에 노출하지 않는다.

## 2. 완료 기준과 실행 순서

### 2.1 기능 완료 기준

1. 로그인 후 작품·인물을 만들고 첫 회차, 다음 회차, 에필로그를 생성한다. 모델이 꺼져도 목록·본문 조회·노트 수정이 된다.
2. Fake와 Local Provider 모두 첫 비어 있지 않은 본문부터 화면에 표시된다. 본문 스트림에 상태 JSON·thought가 섞이지 않는다.
3. 중단·연결 종료·deadline·프로세스 재시작에서 확정되지 않은 본문은 draft로 구분한다. 확정 회차와 Memory는 함께 저장되거나 함께 저장되지 않는다.
4. 같은 멱등 키의 재전송은 새 호출·새 회차를 만들지 않는다. 동일 키의 다른 payload는 409다.
5. 동일 작품의 동시 생성·Undo·설정 수정은 revision과 활성 요청 제약으로 충돌을 검출한다.
6. revision 0, 첫 회차 Undo, 에필로그 Undo에서 본문과 인물·관계·복선·다음 선택지·완결 상태가 일치한다.
7. Cloud 미동의 요청은 생성·요약·메타데이터 추출의 모든 단계에서 외부 LLM 호출이 0이다.
8. 사용자별 데이터 격리, 입력 상한, 비밀정보·본문 로그 미출력, 로컬 추론 포트 보호를 검증한다.
9. 새 DB를 migration과 sample seed만으로 구성하고 Docker/Node에서 실행한다. 검증 명령과 실제 실행 증거를 남긴다.
10. 2~3개 Local 후보와 Cloud 기준 모델을 동일 세트로 비교한다. 8GB에서 통과한 모델만 기본값으로 채택한다.

### 2.2 성능 기준의 지위

방향서는 확정된 SLO를 제시하지 않았다. `warm TTFT p95 ≤ 5s`, `provider decode TPS ≥ 15`는 B05의 **초기 평가 기준 제안**이다. 실제 성능 보장이나 이미 통과한 결과가 아니다. cold/warm, 입력 크기, 출력 길이, 데이터 저장·Memory 추출 시간을 나누어 보고하고, 기준 미달이면 모델·context·출력 길이를 조정한 뒤 ADR에 이유를 기록한다.

### 2.3 의존성과 검토 단위

```text
T01 골격
  T02 DB     T04 Provider 계약
  T03 인증·작품 API
  T05 Context Builder
  T06 Memory 추출
  T07 Orchestrator·원자 저장
  T10 Ollama adapter (T04 계약 이후, T08 앞에서 실행)
  T08 SSE·취소·상태 조회
  T09 화면 연결
  T11 Timeout·Retry
  T12 Gemini·Fallback
  T13 수정·완결·Undo
  T14 지표·운영 화면
  T15 통합·보안 회귀
  T16 Docker·문서·공개 산출물

T10 이후 B01~B02 준비, T14 지표 helper 완료 후 B03~B04 실측
T12 이후 B04 Cloud 비교
B05 모델·Backend 결정 후 T16 최종 인수
```

작업 번호보다 이 의존 순서를 우선한다. T10은 NDJSON fixture로 검증한 adapter를 T08의 HTTP 통합 테스트에 제공한다. T02와 T04는 파일 소유권을 나누어 병렬 작업할 수 있다. 이후 작업자는 아래 인터페이스를 먼저 읽는다. 여러 작업자가 같은 `package.json`, `types.ts`, migration을 동시에 수정하지 않는다. 작업별 RED/GREEN과 리뷰 후 통합한다. 문서만으로는 Git 초기화·commit을 실행하지 않으며, 구현 시 별도 v2 저장소가 준비되면 각 작업 끝에 제안 commit 단위를 적용한다.

## 3. 파일 구조와 공통 계약

### 3.1 파일 책임

```text
src/app/                         페이지와 얇은 Node API route
  auth/page.tsx                 이메일 로그인
  projects/page.tsx             서재
  projects/new/page.tsx         작품·초기 인물·세계관 입력
  projects/[id]/page.tsx        회차 열람과 생성
  metrics/page.tsx              내 요청 지표
  api/health/route.ts
  api/projects/route.ts
  api/projects/[id]/route.ts
  api/projects/[id]/characters/route.ts
  api/projects/[id]/undo/route.ts
  api/projects/[id]/finish/route.ts
  api/generations/route.ts
  api/generations/[id]/route.ts
  api/generations/[id]/cancel/route.ts
  api/generations/[id]/finalize/route.ts
  api/metrics/route.ts
src/features/stories/           입력 form, reader, 작가 설정
src/features/generation/        stream client, hook, draft와 상태 UI
src/server/auth.ts              검증된 사용자, cookie client, Origin 검사
src/server/config.ts            server env, model allowlist, deadline
src/server/db.ts                server-only DB client와 고정 RPC 호출
src/server/stories/             types, schemas, commands
src/server/llm/                 types, errors, fake, ollama, gemini
src/server/context/             builder, prompts
src/server/memory/              schema, extract
src/server/generation/          orchestrator, policy, runtime, sse
src/server/observability/       metrics, logging
src/shared/generation.ts        서버·브라우저 공통 요청/이벤트 계약과 schema
supabase/migrations/            전체 v2 DDL, RLS, command RPC
supabase/tests/                 pgTAP transaction, 권한, Undo
tests/unit/                    독립 로직 검증
tests/integration/             Node route와 fake upstream
tests/e2e/                     브라우저와 격리 DB
tests/fixtures/                한국어 입력, UTF-8 stream, Memory
scripts/                       DB test·demo·smoke·benchmark
benchmarks/                    비교 후보, 고정 입력, 품질 평가
docs/adr/                      결정 근거
docs/                          실행, 보안, benchmark, architecture
```

위 트리의 설명은 파일 책임만 나타낸다. 구현 문서와 UI 문구는 한국어로 작성한다. 별도 범용 repository framework, DI container, 작업 큐는 만들지 않는다. `db.ts`의 직접 함수와 Orchestrator 생성자의 의존성 인자만 사용한다.

### 3.2 Provider 계약: T04에서 정의하는 유일한 공통 타입

```ts
export type ProviderId = 'ollama' | 'gemini' | 'llamacpp';
export type Message = {
  role: 'system' | 'user' | 'assistant';
  content: string;
};
export type GenerateInput = {
  messages: Message[];
  model: string;
  maxOutputTokens: number;
  temperature: number;
  contextWindow: number;
  seed?: number;
  signal: AbortSignal;
  responseSchema?: Record<string, unknown>;
};
export type ProviderEvent =
  | { type: 'delta'; text: string }
  | { type: 'usage'; promptTokens: number | null;
      completionTokens: number | null; decodeMs: number | null;
      loadMs: number | null }
  | { type: 'finish'; reason: 'stop' | 'length' | 'blocked' };
export interface LlmProvider {
  id: ProviderId;
  generate(input: GenerateInput): AsyncIterable<ProviderEvent>;
}
```

`llamacpp`는 B02의 비교용 어댑터이며 초기 서비스 설정에는 노출하지 않는다. Provider는 HTTP/NDJSON/SSE/SDK 차이와 오류·finish reason 정규화만 담당한다. 재시도·fallback·DB 저장·사용자 동의는 Orchestrator 책임이다. `usage`와 `finish`는 각각 최대 한 번, `finish`는 필수다. finish 없는 EOF, malformed frame, empty output은 성공이 아니다. `length`는 draft를 보존하고 자동 확정을 거부한다.

### 3.3 HTTP와 SSE 계약

모든 도메인 API는 서버에서 `auth.getUser()`로 사용자를 검증한다. 쿠키 기반 mutation에는 허용된 동일 Origin을 요구한다. `owner_id`·Provider URL·SQL 함수명은 HTTP 입력으로 받지 않는다.

| API | 요청·응답 | 실패 |
|---|---|---|
| `GET/POST /api/projects` | 목록 / 제목·장르·언어·인물·세계관으로 작품과 revision 0 생성 | 400, 401, 413 |
| `GET/PATCH /api/projects/:id` | canonical snapshot / `expectedRevision` 포함 노트·문체·인물 설정 수정 | 404, 409 |
| `GET /api/projects/:id/episodes/:episodeId` | 선택한 활성 회차의 본문 조회 | 없는 회차·타인 404 |
| `POST /api/projects/:id/characters` | 안정적인 인물 UUID 생성, revision 증가 | 400, 404, 409 |
| `POST /api/generations` | 아래 GenerateRequest. 신규 실행은 `text/event-stream` | 400, 401, 404, 409, 413, 422, 429, 503 |
| `GET /api/generations/:id` | 상태·저장된 draft·확정 episode ID·metrics·`undoneAt` | 소유자 외 404 |
| `POST /api/generations/:id/cancel` | 취소 terminal 상태, 이미 성공했다면 canonical 결과 | 성공 확정과 경합 시 409 |
| `POST /api/generations/:id/finalize` | `expectedRevision`, 저장된 완성 draft의 Memory 추출·commit만 재시도. 완료 후 200 JSON `{episodeId,revision}` | 409, 422, 503 |
| `POST /api/projects/:id/undo` | `expectedRevision`, 마지막 활성 회차를 되돌린 snapshot | 활성 생성·revision 충돌 409 |
| `POST /api/projects/:id/finish` | `expectedRevision`, 생성 없이 완결 상태만 새 snapshot으로 기록 | 409 |
| `GET /api/metrics` | 내 요청·attempt 집계, 기간 최대 30일 | 401, 400 |

```ts
export type GenerateRequest = {
  projectId: string;
  expectedRevision: number;
  idempotencyKey: string; // crypto.randomUUID(), 네트워크 재전송에 동일 값
  mode: 'initial' | 'continue' | 'epilogue';
  direction: string;
  provider: 'ollama' | 'gemini';
  allowCloud: boolean;
};
export type GenerationStatus =
  | 'pending' | 'running' | 'finalizing'
  | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
export type StreamEvent =
  | { type: 'accepted'; requestId: string; generationId: string }
  | { type: 'provider'; provider: ProviderId; model: string; attempt: number }
  | { type: 'delta'; text: string }
  | { type: 'phase'; phase: 'finalizing' }
  | { type: 'committed'; episodeId: string; revision: number }
  | { type: 'error'; code: string; retryable: boolean; draftAvailable: boolean }
  | { type: 'cancelled' }
  | { type: 'done'; status: GenerationStatus };
```

각 frame은 `event: <type>\ndata: <JSON>\n\n` 형식이다. `accepted`는 첫 이벤트이며 `committed` 이전의 delta는 미확정 draft다. 스트림은 terminal 이벤트 뒤 `done` 한 번으로 끝난다. HTTP header 전에는 적절한 상태 코드와 `{error:{code,message,requestId}}`를 반환하고, header 후에는 error 이벤트로 실패를 전달한다. 내부 예외·provider 응답 전문은 반환하지 않는다.

멱등 키 재전송은 새 스트림에 붙지 않는다. 이미 존재하는 요청이면 **202 JSON** `{generationId,status,statusUrl,replayed:true}`를 반환한다. 클라이언트는 Content-Type을 확인하고 상태 API로 전환한다. 같은 키인데 canonical request hash가 다르면 409다. 최초 연결의 자동 SSE 재접속·delta replay는 구현하지 않는다. accepted 수신 전 연결 실패의 동일 키 재전송은 별도 복구 경로다.

### 3.4 데이터 불변조건

| 테이블 | 필수 필드·제약 |
|---|---|
| `projects` | UUID, owner_id, title, genre, language, notes, style_examples, settings, `revision bigint`, `head_memory_revision bigint`, archived_at. revision은 모든 확정 수정에서 증가하며 Undo로 감소하지 않음 |
| `characters` | UUID, project_id, 이름·성별·나이·성격·말투·배경의 invariant JSON. 동명 인물 허용, Memory는 UUID로 참조 |
| `memory_revisions` | `(project_id, revision)` PK, parent_revision, document JSONB, created_at. revision 0 필수. 기존 row 수정 금지 |
| `episodes` | UUID, project_id, episode_number, body, summary, `before_memory_revision`, `after_memory_revision`, generation_id UNIQUE, deleted_at. 활성 회차 번호에 partial UNIQUE |
| `generation_requests` | UUID, request_id, owner_id, project_id, idempotency_key, payload_hash, input JSONB, base_revision, run_epoch, status, draft_body, draft_complete, cancel_requested, heartbeat_at, metrics JSONB, error_code, committed_episode_id, undone_at. `(owner_id,idempotency_key)` UNIQUE |

활성 요청 partial UNIQUE는 project_id별 `pending/running/finalizing`에 적용한다. FK는 다른 작품의 인물·snapshot을 연결하지 못하도록 project_id를 포함한다. `episodes`와 `memory_revisions`의 연결은 deferrable composite FK로 transaction 끝에 검증한다. JSON 값은 Zod와 DB의 필수 구조 CHECK 양쪽에서 검증한다. 완결 여부의 단일 원본은 head Memory의 isFinished이며 project에 별도 status를 중복 저장하지 않는다. create RPC는 project head를 잠시 NULL로 두고 revision 0 생성 후 head=0을 같은 transaction에서 설정한다. Memory revision UPDATE를 거부하는 trigger를 둔다.

작가가 작성한 world bible은 `projects.settings.worldBible`에 저장한다. Memory snapshot에는 모델이 갱신하는 mutable state만 넣고 authored world bible·notes·인물 invariant를 넣지 않는다. Undo는 현재 작가 설정을 유지하고 회차 생성으로 바뀐 상태만 복원한다.

권한 경계는 한 방식으로 통일한다. 읽기는 사용자 JWT client와 owner RLS를 사용한다. 쓰기는 Node 서버가 검증한 user.id를 `p_actor_id`로 넣어 server-only service_role client의 고정 command RPC를 호출한다. command EXECUTE는 PUBLIC/anon/authenticated에서 회수하고 service_role에만 준다. 함수가 SECURITY DEFINER라면 `SET search_path = ''`와 완전 수식 테이블명을 사용한다. **service_role은 RLS를 우회하므로**, 모든 command가 project/request의 소유자와 p_actor_id를 직접 비교한다. 브라우저의 직접 DML은 금지한다.

### 3.5 원자 저장·취소·복구

1. `begin_generation`은 기존 멱등 키를 가장 먼저 조회한다. 신규면 작품 소유권·revision·미완결·활성 요청·mode를 검사하고 pending 요청을 예약한다.
2. LLM 실행 동안 DB transaction을 열어 두지 않는다. 단일 Node 프로세스에서 Ollama 활성 호출을 최대 1개로 제한한다. narrative와 해당 Memory 추출이 끝날 때까지 같은 생성이 local slot을 보유해 중간에 다른 요청이 끼어들지 않는다. 대기는 무제한 queue 대신 `429 LOCAL_BUSY`로 안내한다.
3. 본문 finish=stop 뒤 전체 draft를 저장하고 finalizing으로 전환한다. Memory 추출 결과가 검증되어야 `commit_generation`을 호출한다.
4. commit은 **project → generation** 순서로 row lock을 획득한다. expected revision·request 상태·취소 여부를 재확인하고 episode·snapshot·project revision·request 성공을 한 transaction에서 기록한다. episode 번호는 여기서 계산한다.
5. cancel RPC도 project → generation 순서로 lock을 획득한다. 먼저 잠근 쪽이 결정한다. cancel이 먼저면 commit 거부, commit이 먼저면 취소 409와 확정 결과. 이미 terminal이면 같은 상태를 반환한다.
6. 본문 일부 전달 뒤 실패·취소는 가능한 범위에서 draft를 별도 저장한다. 확정 episode와 head Memory는 변경하지 않는다. signal 중단과 DB terminal 저장에는 별도 짧은 timeout을 사용한다.
7. heartbeat는 10초 간격이다. 인증된 begin/status 처리 전에 해당 사용자·project의 `heartbeat_at < now()-60s`인 비terminal 요청을 interrupted로 정리한다. heartbeat update도 terminal 상태를 되살릴 수 없다. 재시작 직후에는 stale 판정까지 최대 60초가 필요하며 UI는 상태를 조회한다.
8. 프로세스 중단 직전의 미저장 delta는 잃을 수 있다. 첫 버전은 매 token durable 저장·분산 실행·자동 생성 재개를 보장하지 않는다. 완성 draft가 저장된 finalization 실패만 명시적으로 재처리한다. finalize 예약은 run_epoch를 증가시키고, touch/save/terminate/commit은 받은 epoch가 일치할 때만 허용한다. 구 worker가 새 시도를 확정하거나 중단시키지 못한다.
9. Undo는 마지막 활성 episode를 soft delete하고 그 episode의 `before_memory_revision`으로 head를 옮긴다. project.revision은 증가한다. 후속 생성은 증가한 revision으로 새 immutable Memory row를 만든다. request 성공 기록은 보존하고 undone_at을 남겨 같은 키로 다시 생성하지 않는다.
10. 완성 draft의 상태 저장 실패 뒤에는 “상태 저장 재시도”와 “새로 생성”을 구분한다. 새 생성은 사용자가 선택한 새 멱등 키로만 시작한다. 기존 실패 draft는 유지하되 이후 project revision이 달라지면 finalize는 409다. cancelled/length/blocked 결과에는 finalize 버튼을 제공하지 않는다.

### 3.6 테스트 fixture의 최소 계약

아래 이름은 테스트 코드에서 임의의 미구현 서비스로 해석하지 않는다. 해당 작업의 테스트/fixture 파일에서 먼저 정의하는 고정 값과 테스트 전용 함수다. 공용 테스트 framework는 추가하지 않는다.

- T03의 `tests/fixtures/story.ts`: `validProjectInput={title:'폐역의 기록',genre:'판타지',language:'ko',notes:'',worldBible:'',styleExamples:[],characters:[]}`. `directionSchema`는 T03 schemas.ts의 UTF-8 2KiB schema다.
- T04의 `tests/fixtures/provider-streams.ts`: `validGenerateInput`은 messages `[{role:'user',content:'한국어로 한 문장을 쓴다'}]`, model `fake`, maxOutputTokens 1024, temperature 0, contextWindow 4096, 새 AbortSignal이다. 각 테스트는 signal을 새로 만든다. `collect<T>(source:AsyncIterable<T>):Promise<T[]>`는 로컬 helper로 `for await`하여 배열을 반환한다.
- T05의 `oversizedCoreFixture`는 T02 ProjectSnapshot의 revision 0 샘플에서 worldBible만 `'가'.repeat(2000)`로 바꾼다. 선택 context는 비워 필수 정보 초과만 검증한다.
- T06의 `validMemoryUpdate`는 `{summary:'서윤이 상자를 발견했다',characterStates:[{characterId:'00000000-0000-4000-8000-000000000002',status:'생존',traits:[],appearance:'변화 없음',isActive:true}],plotState:'상자 조사',openForeshadowing:[],relationships:[],nextOptions:['상자를 살핀다']}`다. update는 summary와 mutable 필드만 허용하고 schemaVersion/isFinished는 서버가 넣는다.
- T07의 `createGenerationStoreFixture():GenerationStore & {episodes:Episode[];savedDraft:{body:string;complete:boolean}|null;lastError:string|null}`는 테스트 파일 내부 in-memory 저장소다. 예약된 요청 1개와 baseline snapshot을 초기화하고, saveDraft/terminate/commit을 배열·필드에 기록한다. `validRunInput`에는 그 요청의 generationId/runEpoch=1과 UUID 기반 GenerateRequest를 넣는다. `invalidMemoryProvider`는 responseSchema가 없을 때 본문과 stop을, 있을 때 잘못된 JSON과 stop을 반환한다.
- T08의 `postGeneration(request)`는 테스트 로그인 cookie/Origin을 붙여 실제 Node 테스트 서버에 fetch한다. `openTestEventReader(response)`는 같은 reader/buffer를 유지하고 `readUntil(type)`으로 해당 typed event를 돌려준다. `generationStatus(id)`는 실제 GET status를 호출한다. 테스트 서버 시작·종료는 테스트 파일의 beforeAll/afterAll에서 수행한다.
- T09의 `splitUtf8Response(text)`는 accepted·delta(text)·committed·done을 UTF-8로 인코딩하고 byte를 한 개씩 enqueue하는 Response다. 정상 형식과 중간 EOF 형식은 별도 fixture로 만든다.
- T10의 `testOptions={baseUrl:'http://127.0.0.1:11434',keepAlive:'5m'}`와 vi.stubGlobal fetch는 Ollama fixture만 반환한다. live 접속은 금지한다. T11의 `providerThatYieldsThenFails(text)`는 calls를 기록하고 delta 뒤 UNAVAILABLE을 throw하며, `policyOptions(primary)`는 cloud 비활성·deadline 300000·고정 clock/jitter를 사용한다.
- T12의 `localRunWithoutConsent`는 저장된 allowCloud=false 요청이다. `consentTestDeps`는 local 장애 fixture·cloud provider spy·fake DB를 주입하고 cloud spy는 테스트 지역 변수 `cloudFetch`로 별도 관찰한다.
- T15의 `openSeededStory/countNarrativeCalls/interruptTestServer/restartTestServer`는 Playwright 전용 fixture 함수다. 전용 사용자로 로그인하고 fake upstream의 테스트 프로세스 호출 수를 읽으며, 테스트 harness가 소유한 Node child process만 중단·재시작한다. 일반 앱 HTTP endpoint로 프로세스를 제어하지 않는다.

## 4. 단계별 구현 작업

각 작업은 테스트 파일 작성, RED 확인, 구현, GREEN 확인, 변경 검토의 순서다. 아래 테스트 코드는 핵심 assertion이며 fixture와 import는 해당 작업의 Files/Interfaces에서 정의한다. 런타임 구현은 명시된 계약 전체를 만족해야 한다. 아직 없는 파일의 명령은 **미래 실행 명령**이다.

### T01. 실행 골격과 검증 명령을 만든다

**Files:** Create `package.json`, `package-lock.json`, `tsconfig.json`, `next.config.ts`, `eslint.config.mjs`, `vitest.config.ts`, `playwright.config.ts`, `.gitignore`, `.env.example`, `src/app/layout.tsx`, `src/app/page.tsx`, `src/app/globals.css`, `src/app/api/health/route.ts`, `tests/unit/health.test.ts`, `docs/adr/0001-runtime-and-storage.md`.

**Interfaces:** `GET /api/health` → `{status:'ok'}`. 앱 기본 포트 3000. unit은 Fake를 의존성으로 주입하고 integration/E2E는 Ollama adapter가 테스트용 fake upstream을 호출한다. 테스트에서 실제 LLM API key를 요구하지 않는다.

- [ ] **1.** v2 경로와 새 저장소 경계를 확인한다. `node --version`, `npm.cmd --version`, `git rev-parse --show-toplevel`을 기록한다. `.git`이 없다면 구현 담당자가 v2 독립 저장소를 준비한다. 상위·v1 저장소에 파일을 추가하지 않는다.
- [ ] **2.** Node 24 LTS에서 package를 초기화하고 필요한 의존성만 설치한다. Next 16/React 19의 지원되는 patch를 확인해 lockfile에 고정한다.

```powershell
npm.cmd init -y
npm.cmd install next@16 react@19 react-dom@19 @supabase/ssr @supabase/supabase-js zod server-only
npm.cmd install -D typescript @types/node@24 @types/react@19 @types/react-dom@19 tailwindcss@4 @tailwindcss/postcss@4 eslint eslint-config-next@16 vitest @playwright/test tsx supabase
```

- [ ] **3.** 아래 assertion을 먼저 작성하고 `npm.cmd run test:unit -- tests/unit/health.test.ts`가 미구현 route 때문에 실패하는지 확인한다. 설정·CSS 같은 단순 파일을 위한 별도 단위 테스트는 만들지 않는다.

```ts
import { expect, it } from 'vitest';
import { GET } from '../../src/app/api/health/route';
it('returns health without model credentials', async () => {
  expect(await (await GET()).json()).toEqual({ status: 'ok' });
});
```

- [ ] **4.** `export const runtime = 'nodejs'`, `Response.json({status:'ok'})`, strict TS, `@/*` alias와 다음 scripts를 구현한다. test:db는 T02, test:integration은 T03, test:e2e는 T09, benchmark는 B04에서 연결한다. 그 전에는 없는 script를 성공했다고 보고하지 않는다. Tailwind용 `postcss.config.mjs`도 이 작업에서 만들고 Vitest/Playwright의 파일 glob을 분리한다.

```json
{"scripts":{"dev":"next dev --hostname 127.0.0.1","build":"next build","start":"next start --hostname 127.0.0.1","lint":"eslint .","typecheck":"tsc --noEmit","test:unit":"vitest run tests/unit","test:integration":"vitest run tests/integration","test:db":"tsx scripts/test-db.ts","test:e2e":"playwright test","benchmark":"tsx scripts/benchmark.ts"}}
```

- [ ] **5.** `.env.example`에 이름과 비밀이 아닌 기본값만 적는다. `APP_ORIGIN=http://localhost:3000`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `OLLAMA_BASE_URL=http://127.0.0.1:11434`, `OLLAMA_MODEL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `ENABLE_CLOUD=false`. dotenv 실파일·artifacts·모델 파일을 ignore한다. 실제 key는 이 작업에서 발급·복사하지 않는다.
- [ ] **6.** unit/typecheck/lint/build를 실행한다. ADR에 저장소·runtime 가정과 “hosted DB에 작품이 저장된다”는 데이터 경로를 적는다. 제안 commit: `chore: scaffold local node application`.

### T02. 재현 가능한 DB·RLS·baseline snapshot을 만든다

**Files:** Create `supabase/config.toml`, `supabase/migrations/202610260001_core.sql`, `supabase/migrations/202610260002_commands.sql`, `supabase/seed.sql`, `supabase/tests/core.test.sql`, `supabase/tests/security.test.sql`, `scripts/test-db.ts`, `src/server/stories/types.ts`, `src/server/db.ts`.

**Interfaces:** `ProjectSnapshot { projectId:string; revision:number; memoryRevision:number; title:string; genre:string; language:'ko'|'en'|'ja'; notes:string; worldBible:string; styleExamples:string[]; targetEpisodes:number|null; characters:Character[]; memory:MemoryDocument; episodes:Episode[] }`. `Character {id:string;name:string;gender:string;ageGroup:string;personality:string;speechStyle:string;background:string}`. `Episode {id:string;episodeNumber:number;body:string|null;summary:string;beforeMemoryRevision:number;afterMemoryRevision:number}`. MemoryDocument와 하위 상태 타입은 T06의 정확한 shape로 **이 작업에서** stories/types.ts에 정의하고 T06은 검증 schema만 추가한다. `create_project`, `begin_generation`, `save_generation_draft`, `touch_generation`, `commit_generation`, `cancel_generation`, `recover_generations`, `reserve_finalization`, `undo_episode`, `update_project`, `finish_project`는 고정 named RPC다. 입력은 `p_actor_id`와 해당 command 데이터만 받는다. `begin_generation` 반환은 `{kind:'created'|'existing',generationId,status,runEpoch}`다.

- [ ] **1.** `core.test.sql`에 새 DB에서 작품 생성 뒤 revision 0과 head=0이 존재하는 pgTAP 테스트를 작성한다. `security.test.sql`에는 A/B 사용자 읽기 격리와 authenticated의 command 직접 호출 거부를 작성한다.

```sql
select plan(3);
select has_table('public', 'generation_requests');
select col_is_unique('public', 'generation_requests',
  array['owner_id','idempotency_key']);
select is((select count(*)::int from public.memory_revisions
           where project_id='00000000-0000-4000-8000-000000000001' and revision=0),
          1, 'project has immutable baseline');
select * from finish();
```

- [ ] **2.** `test-db.ts`는 명시적 `TEST_DATABASE_URL`에만 `psql`을 실행한다. `TEST_DATABASE_PROJECT_REF`와 URL host가 일치하는지 확인하고 앱/v1 운영 DB와 같으면 즉시 실패한다. seed에는 A/B 테스트 사용자와 고정 project UUID `00000000-0000-4000-8000-000000000001`을 만든다. psql exit=0만으로 통과하지 않고 TAP의 not ok, plan 불일치, bailout을 검사해 nonzero로 종료한다. runner 자체는 실패 TAP fixture로 검증한다. 빈 DB RED 후 §3.4의 DDL·FK·index·RLS·권한과 `create_project`를 구현한다.
- [ ] **3.** 다음 제약을 구현한다. child 테이블 SELECT policy는 소유 project 존재를 확인한다. `generation_requests`의 raw input·draft도 소유자만 조회한다. 함수의 lock 순서는 모두 project → generation이다.

```sql
create unique index one_active_generation_per_project
on public.generation_requests(project_id)
where status in ('pending','running','finalizing');
create unique index one_live_episode_number
on public.episodes(project_id, episode_number)
where deleted_at is null;
-- 모든 command의 전체 signature에 같은 권한 규칙을 적용한다.
revoke all on function public.create_project(uuid,jsonb) from public, anon, authenticated;
grant execute on function public.create_project(uuid,jsonb) to service_role;
```

- [ ] **4.** `createProject(actorId,input)`, `getProject(actorClient,id)`와 command별 함수만 `db.ts`에 노출한다. server-only import를 적용한다. `getProject`는 사용자 client/RLS, command는 service_role client/actor 검증을 사용한다. raw SQL·RPC명을 HTTP에서 전달받는 범용 실행기는 만들지 않는다.
- [ ] **5.** `npm.cmd run test:db`로 fresh schema→seed→RLS 검증을 실행한다. revision 0의 존재뿐 아니라 UPDATE 거부도 확인한다. 로컬 Supabase를 쓰는 경우 `npx.cmd supabase start` / `npx.cmd supabase db reset --local`은 폐기 가능한 v2 테스트 인스턴스에서만 수행한다. hosted DB에서는 reset 대신 전용 테스트 DB에 migration을 적용한다.
- [ ] **6.** 아직 구현하지 않은 generation/Undo RPC 본문은 이후 T07/T13에서 추가한다. 빈 성공 함수나 가짜 transaction은 넣지 않는다. 제안 commit: `feat: add isolated story schema and access rules`.

### T03. 인증·서재·작품 생성 API를 연결한다

**Files:** Create `src/server/auth.ts`, `src/server/stories/schemas.ts`, `src/server/stories/commands.ts`, `src/proxy.ts`, `src/app/auth/page.tsx`, `src/app/api/projects/route.ts`, `src/app/api/projects/[id]/route.ts`, `src/app/api/projects/[id]/episodes/[episodeId]/route.ts`, `src/app/api/projects/[id]/characters/route.ts`, `tests/unit/story-input.test.ts`, `tests/integration/projects.test.ts`, `tests/fixtures/story.ts`.

**Interfaces:** `requireUser(request): Promise<{id:string}>`, `assertSameOrigin(request):void`, `createProjectInput.parse(unknown)`, `getProjectSnapshot(userId,projectId):Promise<ProjectSnapshot>`. 없는 project와 타인 소유 project는 같은 404를 반환한다.

- [ ] **1.** 입력 schema의 RED를 작성한다. validProjectInput fixture는 아래 입력 계약에 맞춘 한국어 샘플로 정의한다.

```ts
it('rejects extra owner and byte overflow', () => {
  expect(createProjectInput.safeParse({ ...validProjectInput,
    owner_id: 'attacker' }).success).toBe(false);
  expect(directionSchema.safeParse('가'.repeat(683)).success).toBe(false);
});
```

- [ ] **2.** Zod strict object와 UTF-8 byte refinement를 구현한다. 전체 body 64KiB, 제목 200B, 장르 2KiB, direction 2KiB, notes/world bible 각각 10KiB, 스타일 5KiB×3, 인물 최대 10명·인물당 4KiB. 빈 제목·잘못된 UUID·음수 revision·지원하지 않는 mode/provider는 400, body 상한은 413이다.
- [ ] **3.** `../gostwriter/frontend/src/utils/server.ts`의 cookie 흐름만 옮기고 현재 Next 16의 proxy 규칙에 맞춘다. 서버 API는 proxy 통과 여부와 관계없이 getUser를 호출한다. `getSession()`의 로컬 값만으로 권한을 결정하지 않는다.
- [ ] **4.** POST는 Origin을 APP_ORIGIN과 비교하고 검증된 user.id만 command로 전달한다. 읽기 client와 쓰기 client를 분리한다. 모델 env를 요구하는 코드를 import 단계에서 실행하지 않는다. snapshot은 최근 2회차 본문과 전체 회차 ID/요약만 읽고, 오래된 body는 null로 돌려준다. reader가 선택한 회차 본문은 개별 API로 읽어 전체 원고를 매 요청 다시 가져오지 않는다.
- [ ] **5.** `npm.cmd run test:unit -- tests/unit/story-input.test.ts`, `npm.cmd run test:integration -- tests/integration/projects.test.ts`, `npm.cmd run test:db`를 실행한다. 401·타인 404·잘못된 Origin 403·모델 key 없는 CRUD·빈 신규 생성 경로를 확인한다.
- [ ] **6.** 제안 commit: `feat: add authenticated project commands`.

### T04. Provider 계약과 결정적 Fake를 만든다

**Files:** Create `src/server/llm/types.ts`, `src/server/llm/errors.ts`, `src/server/llm/fake.ts`, `src/server/config.ts`, `src/shared/generation.ts`, `tests/unit/provider-contract.test.ts`, `tests/fixtures/provider-streams.ts`.

**Interfaces:** §3.2의 타입과 `ProviderError {code, retryable, retryAfterMs, provider}`.code는 `UNAVAILABLE/RATE_LIMITED/TIMEOUT/INVALID_OUTPUT/CONTEXT_LIMIT/AUTH/BLOCKED/CANCELLED`로 고정한다. `createFakeProvider(events,delayMs):LlmProvider`는 테스트용이다.

- [ ] **1.** 정상/분할/empty/finish 없음/length/blocked/Abort fixture를 작성하고 RED를 확인한다.

```ts
it('stops fake output when caller aborts', async () => {
  const controller = new AbortController();
  const provider = createFakeProvider([{type:'delta',text:'가'}], 20);
  controller.abort();
  await expect(collect(provider.generate({ ...validGenerateInput,
    signal: controller.signal }))).rejects.toMatchObject({code:'CANCELLED'});
});
```

- [ ] **2.** `collect`는 이 테스트 파일 내부의 AsyncIterable 수집 helper로 정의한다. `validGenerateInput`은 고정 메시지·모델 `fake`·1024 output·4096 context·temperature 0을 가진 fixture다. Fake의 delay는 signal로 해제한다.
- [ ] **3.** config는 선택한 provider의 필수 env만 검사한다. 서버 설정의 provider/model allowlist 외 입력을 거부한다. 최초 예시는 `gemma3:4b-it-q4_K_M`이지만 **bench 전 개발 후보**라고 표시한다. Gemini는 env로 설정한 모델만 사용한다. shared/generation.ts에 §3.3의 요청·상태·이벤트 타입과 strict streamEventSchema를 정의한다. 서버와 브라우저는 같은 schema를 사용하고 server-only 모듈을 브라우저 bundle에 넣지 않는다.
- [ ] **4.** `npm.cmd run test:unit -- tests/unit/provider-contract.test.ts`와 typecheck를 실행한다. Fake가 production cloud/local 선택지로 노출되지 않는지 확인한다.
- [ ] **5.** 제안 commit: `feat: define provider stream contract`.

### T05. 예산이 있는 Context Builder와 Provider 중립 프롬프트를 만든다

**Files:** Create `src/server/context/builder.ts`, `src/server/context/prompts.ts`, `tests/unit/context-builder.test.ts`, `docs/adr/0002-context-budget.md`. Modify `tests/fixtures/story.ts`.

**Interfaces:** `buildContext(input:{snapshot:ProjectSnapshot;direction:string;mode:GenerateRequest['mode'];contextWindow:number;maxOutputTokens:number}):BuiltContext`. `BuiltContext {messages:Message[];estimatedPromptTokens:number;estimation:'utf8-conservative';includedIds:string[];droppedIds:string[];sectionBytes:Record<string,number>}`. `narrativePhase(episode:number,target:number|null):'ki'|'seung'|'jeon'|'gyeol'`.

- [ ] **1.** world bible·현재 인물 상태·작가 노트가 유지되고 오래된 summary부터 제거되는 RED를 작성한다. 예산보다 필수 context가 큰 경우 조용히 잘라내지 않는다.

```ts
it('keeps core rules and rejects an impossible budget', () => {
  expect(() => buildContext({snapshot:oversizedCoreFixture,
    direction:'문을 연다',mode:'continue',contextWindow:4096,
    maxOutputTokens:1024})).toThrow('CONTEXT_BUDGET_EXCEEDED');
  expect(narrativePhase(6,10)).toBe('seung');
  expect(narrativePhase(9,10)).toBe('jeon');
});
```

- [ ] **2.** context를 system 지시, 사용자 불변 규칙, world/현재 인물·plot·미회수 복선, 최근 원문, 최근 요약, 관련 옛 요약, 현재 direction 순으로 조립한다. 사용자/과거 소설 텍스트는 별도 데이터 구획이며 시스템 권한을 갖지 않는다.
- [ ] **3.** 최근 원문은 우선 최근 2회차의 끝부분, 요약은 최근 5개를 고려한다. 장면에 언급된 안정적 인물 ID/이름과 direction 키워드의 교집합으로 옛 요약 최대 3개를 정렬한다. 점수가 같으면 최근 회차 우선이다. 중복 회차는 한 번만 포함한다. vector 검색이나 cloud embedding은 호출하지 않는다.
- [ ] **4.** 초기는 context 4096/output 1024/template margin 512를 사용한다. UTF-8 bytes를 보수적 token 추정치로 쓰고, 선택 context를 순서대로 제거한다. 이 값은 **정확한 tokenizer 계수나 모든 모델의 hard guarantee가 아니다**. 필수 context만으로 초과하면 422와 축소할 필드를 안내한다. Provider의 context 오류를 정규화하고 실제 prompt_tokens를 기록해 B05에서 추정 오차·한국어 사용성을 평가한다.
- [ ] **5.** v1의 ≤20%/≤60%/≤90% 페이싱과 target 없음, epilogue 별도 지시, 언어·문체·반복 억제를 테스트한다. 인물 invariant는 프롬프트만으로 보장된다고 주장하지 않고 T06의 구조화 update allowlist도 적용한다.
- [ ] **6.** `npm.cmd run test:unit -- tests/unit/context-builder.test.ts`를 실행하고 ADR에 필수/선택 context, 제거 순서, token 추정 한계를 기록한다. 제안 commit: `feat: build bounded story context`.

### T06. Story Memory 스키마와 별도 추출 단계를 만든다

**Files:** Create `src/server/memory/schema.ts`, `src/server/memory/extract.ts`, `tests/unit/memory.test.ts`, `tests/fixtures/memory.ts`.

**Interfaces:** `MemoryDocument {schemaVersion:1;characterStates:CharacterState[];plotState:string;openForeshadowing:Foreshadowing[];relationships:Relationship[];nextOptions:string[];isFinished:boolean}`. 상태는 characterId를 사용하고 인물 불변 정보는 포함하지 않는다. `extractMemory(input:{provider:LlmProvider;model:string;previous:MemoryDocument;worldBible:string;characters:Character[];body:string;mode:GenerateRequest['mode'];signal:AbortSignal;contextWindow:number}):Promise<{memory:MemoryDocument;summary:string}>`. 모델 출력 검증은 `memoryUpdateSchema`, 최종 snapshot 검증은 `memoryDocumentSchema`를 사용한다.

- [ ] **1.** 알 수 없는 인물 ID·invariant 수정·범위 밖 친밀도·잘못된 JSON·코드펜스에 대한 실패 테스트를 작성한다.

```ts
it('does not accept a model changing a fixed name', () => {
  expect(memoryUpdateSchema.safeParse({ ...validMemoryUpdate,
    characterStates:[{...validMemoryUpdate.characterStates[0],name:'바뀐 이름'}]
  }).success).toBe(false);
});
```

- [ ] **2.** `CharacterState {characterId:string;status:string;traits:string[];appearance:string;isActive:boolean}`, `Relationship {subjectId:string;objectId:string;tag:string;intimacy:number;description:string}`, `Foreshadowing {id:string;text:string;status:'open'|'resolved';characterIds:string[]}`를 strict schema로 정의한다. 실제 메타 출력 이름은 `characterStates`로 통일한다. 친밀도 -100~100, summary 2KiB, 선택지 최대 3개, 문서 전체 64KiB로 제한한다. 모든 참조 인물은 해당 프로젝트의 ID여야 한다.
- [ ] **3.** 본문 생성과 다른 호출에서 structured JSON을 요청한다. schema는 Provider의 `responseSchema`로 전달하되 서버 검증을 반드시 수행한다. 본문 전체+필수 이전 Memory가 예산을 넘으면 paragraph 단위로 순차 추출하고 요약·state를 단계별 병합한다. 자동 재귀나 동시에 여러 모델 호출을 만들지 않는다.
- [ ] **4.** 모델 출력에 worldBible·인물 invariant 필드가 있으면 거부한다. 프로젝트에 저장된 작가 설정은 변경하지 않고 mutable state만 병합한다. 한국어 서사 속 실제 규칙 위반을 코드로 완전히 탐지할 수 없으므로 B05의 인간 품질 평가로 검증한다. epilogue의 isFinished는 모델이 아니라 mode로 결정한다.
- [ ] **5.** 추출 실패는 `INVALID_MEMORY`이며 T07이 완성 draft를 보존한다. 자동 cloud 전환·가짜 summary·이전 Memory로 무조건 성공 처리하지 않는다. 경량 전용 모델은 벤치마크가 전환 이득을 입증할 때만 도입하고, 최초에는 같은 모델을 순차 사용한다.
- [ ] **6.** `npm.cmd run test:unit -- tests/unit/memory.test.ts`를 실행한다. 제안 commit: `feat: validate structured story memory`.

### T07. Orchestrator와 원자 commit·멱등성을 구현한다

**Files:** Create `src/server/generation/orchestrator.ts`, `src/server/generation/runtime.ts`, `supabase/migrations/202610260003_generation_commands.sql`, `supabase/tests/generation.test.sql`, `tests/unit/orchestrator.test.ts`. Modify `src/server/db.ts`.

**Interfaces:** `reserveGeneration(input:{actorId:string;requestId:string;request:GenerateRequest},deps:{db:GenerationStore}):Promise<Reservation>`은 begin RPC만 호출한다. `Reservation {kind:'created'|'existing';generationId:string;status:GenerationStatus;runEpoch:number}`. `runGeneration(input:{actorId:string;requestId:string;generationId:string;runEpoch:number;request:GenerateRequest;signal:AbortSignal},deps:{provider:LlmProvider;fallback?:LlmProvider;db:GenerationStore;clock:()=>number}):AsyncIterable<StreamEvent>`은 **예약된 요청만 실행**한다. runGeneration 내부에서 begin을 다시 호출하지 않는다. GenerationStore는 다음 signature를 제공한다.

```ts
interface GenerationStore {
  begin(actorId:string, requestId:string, request:GenerateRequest,
    payloadHash:string):Promise<Reservation>;
  loadSnapshot(actorId:string, projectId:string):Promise<ProjectSnapshot>;
  saveDraft(actorId:string, generationId:string, runEpoch:number,
    body:string, complete:boolean):Promise<void>;
  commit(input:{actorId:string;generationId:string;runEpoch:number;
    expectedRevision:number;body:string;summary:string;memory:MemoryDocument})
    :Promise<{episodeId:string;revision:number}>;
  terminate(input:{actorId:string;generationId:string;runEpoch:number;
    status:'failed'|'cancelled'|'interrupted';errorCode:string|null}):Promise<void>;
  touch(actorId:string,generationId:string,runEpoch:number):Promise<boolean>;
  recover(actorId:string,projectId:string):Promise<number>;
}
```

- [ ] **1.** Fake provider+fake store에서 “별도 preflight 예약 → 본문 → Memory → commit” 순서를 테스트한다. begin 호출 횟수는 1회여야 한다. runtime은 active generation ID→AbortController와 Ollama semaphore 1개만 보유한다.

```ts
it('never commits narrative when metadata is invalid', async () => {
  const store = createGenerationStoreFixture();
  await collect(runGeneration(validRunInput,
    {provider:invalidMemoryProvider,db:store,clock:()=>100}));
  expect(store.episodes).toHaveLength(0);
  expect(store.savedDraft?.complete).toBe(true);
  expect(store.lastError).toBe('INVALID_MEMORY');
});
```

- [ ] **2.** canonical hash에 projectId·expectedRevision·mode·direction·provider·allowCloud를 포함한다. key·requestId·cookie는 제외한다. JSON key 순서를 고정해 SHA-256을 계산한다. 동일 key의 기존 요청은 revision 검사보다 먼저 반환한다. 빈 key는 허용하지 않는다. begin은 사용자별 신규 생성 최대 10회/60초를 DB에서 판정한다. replay는 제외하며 rate-limit DB 오류 때 생성은 503으로 거부한다.
- [ ] **3.** §3.5의 command RPC를 구현한다. commit 내부에서 episode 번호와 after revision을 계산하고 새로운 Memory와 summary를 함께 저장한다. 모든 예외에서 rollback한다. 본문 insert 뒤 의도적 제약 오류로 나머지 저장을 실패시키는 DB 테스트를 작성한다. begin은 epoch=1을 반환하고 touch/save/terminate/commit은 generationId와 epoch를 함께 받는다.
- [ ] **4.** memory 실패·length·blocked·finish 없는 EOF·DB 실패를 각각 terminal code로 남긴다. body finish=stop이며 draft_complete=true인 failed/interrupted 요청만 finalize 재시도를 허용한다. reserve_finalization은 원래 allowCloud·base revision·활성 요청을 검사하고 run_epoch 증가와 finalizing 전환을 원자 수행한다. 새 narrative 호출은 하지 않는다. 구 epoch의 touch/save/terminate/commit이 모두 거부되는 테스트를 추가한다.
- [ ] **5.** `npm.cmd run test:unit -- tests/unit/orchestrator.test.ts`, `npm.cmd run test:db`를 실행한다. 동일 key 2회·다른 key 동시 요청·cancel/commit 경합·DB fail injection·첫 episode 번호·완결 작품 재생성을 확인한다. 원격 timeout 뒤 commit 결과를 모르면 새 commit을 만들지 않고 요청 ID로 재조회한다.
- [ ] **6.** 제안 commit: `feat: commit generations atomically with idempotency`.

### T08. SSE API·취소 전파·상태 복구를 구현한다

**Files:** Create `src/server/generation/sse.ts`, `src/app/api/generations/route.ts`, `src/app/api/generations/[id]/route.ts`, `src/app/api/generations/[id]/cancel/route.ts`, `src/app/api/generations/[id]/finalize/route.ts`, `tests/integration/generation-stream.test.ts`, `scripts/fake-upstream.ts`.

**Interfaces:** `encodeSse(event:StreamEvent):Uint8Array`. route runtime은 nodejs. headers는 `Content-Type:text/event-stream; charset=utf-8`, `Cache-Control:no-cache, no-transform`, `X-Accel-Buffering:no`, `X-Request-ID`다.

- [ ] **1.** fake upstream이 `가`와 `나`를 지연 전송하고 첫 chunk가 전체 완료 전에 도착하는 integration RED를 만든다.

```ts
it('flushes a visible delta before commit', async () => {
  const response = await postGeneration(validRequest);
  const stream = openTestEventReader(response);
  const accepted = await stream.readUntil('accepted');
  const firstDelta = await stream.readUntil('delta');
  expect(firstDelta.text).toBe('가');
  expect(await generationStatus(accepted.generationId)).not.toBe('succeeded');
});
```

- [ ] **2.** POST body/schema/auth/Origin 검증 후 서버가 requestId UUID를 새로 만들고 reserveGeneration을 header 전 한 번 호출한다. created의 generationId/runEpoch를 runGeneration에 전달한다. 기존 key는 202 JSON 계약을 따른다. 예약 뒤 local slot 확보에 실패하면 그 신규 요청을 LOCAL_BUSY로 terminal 처리하고 429를 반환해 pending이 남지 않게 한다. iterator 종료와 ReadableStream cancel·request.signal을 하나의 runtime AbortController에 연결한다. SSE comment ping과 callback을 본문 delta로 해석하지 않는다.
- [ ] **3.** cancel endpoint는 소유권 확인→cancel RPC→해당 runtime controller.abort 순으로 처리한다. DB에서 취소가 확정되면 본문·Memory commit은 반드시 거절한다. 브라우저가 닫혀 cancel endpoint를 호출할 수 없을 때도 request abort와 stale recovery가 동작해야 한다.
- [ ] **4.** 15초 간격 `: ping\n\n` comment를 전송한다. upstream 작업이 없어진 뒤 timer·heartbeat·reader를 finally에서 정리한다. GET status는 저장된 draft와 canonical episode를 구분하고 토큰·secret·내부 예외를 응답에 넣지 않는다.
- [ ] **5.** `npm.cmd run test:integration -- tests/integration/generation-stream.test.ts`를 실행한다. 실제 Node HTTP 연결 종료에서 fake upstream socket이 닫히는지 확인한다. 함수 mock의 abort 호출만으로 전파를 통과 처리하지 않는다. late delta·heartbeat 후 terminal 부활·double cancel·스트림 내부 error를 확인한다.
- [ ] **6.** 제안 commit: `feat: stream generation and propagate cancellation`.

### T09. 생성 화면·stream client·draft UX를 구현한다

**Files:** Create `src/features/generation/client.ts`, `src/features/generation/useGeneration.ts`, `src/features/generation/GenerationPanel.tsx`, `src/features/stories/ProjectForm.tsx`, `src/features/stories/StoryReader.tsx`, `src/app/projects/page.tsx`, `src/app/projects/new/page.tsx`, `src/app/projects/[id]/page.tsx`, `tests/unit/sse-client.test.ts`, `tests/e2e/generation.spec.ts`, `tests/fixtures/sse.ts`.

**Interfaces:** `consumeGeneration(response:Response,onEvent:(e:StreamEvent)=>void,signal:AbortSignal):Promise<void>`. hook 상태는 `idle/connecting/streaming/finalizing/committed/cancelled/failed`다. idempotencyKey와 원래 GenerateRequest는 **POST 전에** project별 sessionStorage에 보관하고 accepted 수신 후 generationId를 추가한다. 본문 canonical 저장소로 localStorage를 사용하지 않는다.

- [ ] **1.** SSE parser를 UTF-8 byte 중간, JSON 중간, 여러 frame 병합, CRLF, ping, 마지막 done 없음으로 테스트한다.

```ts
it('decodes Korean text across arbitrary byte boundaries', async () => {
  const events: StreamEvent[] = [];
  await consumeGeneration(splitUtf8Response('가나다'), e=>events.push(e),
    new AbortController().signal);
  expect(events.filter(e=>e.type==='delta').map(e=>e.text).join('')).toBe('가나다');
});
```

- [ ] **2.** TextDecoder streaming mode와 frame buffer를 사용하고 각 event를 schema 검증한다. header가 JSON이면 replay 응답 처리로 전환한다. terminal 없는 EOF는 실패 표시 후 status 재조회한다. EventSource 자동 재접속이나 JSON 문자열 부분 추출을 사용하지 않는다.
- [ ] **3.** 누락되어 있던 작품 생성 form과 수동 인물 입력을 구현한다. v1의 카드·텍스트 reader 스타일만 골라 옮긴다. 오래된 회차는 선택 시 개별 API로 읽는다. delta는 React text node로 렌더링한다. 확정 회차 목록에 draft를 추가하지 않는다.
- [ ] **4.** 생성 중 취소 버튼, “본문 생성 완료·상태 정리 중”, “미확정 초안”, “상태 저장 재시도”를 표시한다. committed 뒤 canonical snapshot을 다시 읽는다. 실패 시 direction 입력을 복구한다. 재진입 시 generationId가 있으면 상태 조회한다. accepted 전에 끊겨 ID가 없으면 원래 payload/key로 재전송해 202 결과에서 ID를 회수한다. 복구 중 status를 2초 간격으로 최대 360초 조회하고 이후 수동 재조회 버튼을 표시한다. sessionStorage의 사용자·project 소속을 검증하고 terminal 확인 뒤 원래 payload를 지운다. 새 key로 자동 재생성하지 않는다.
- [ ] **5.** 버튼 중복 제출 방지·IME `isComposing`·키보드 취소·focus·`role=status`를 구현한다. token마다 전체 본문을 aria-live로 낭독하지 않고 상태 변경만 알린다. Cloud 전송 범위 확인은 T12에서 연결한다.
- [ ] **6.** `npm.cmd run test:unit -- tests/unit/sse-client.test.ts`, `npm.cmd run test:e2e -- tests/e2e/generation.spec.ts`를 실행한다. desktop/mobile 폭, 첫 delta 표시, 취소, 연결 종료, reload, finalize 실패, double click을 확인한다. 제안 commit: `feat: add streaming story editor`.

### T10. Ollama Local Provider를 연결한다

**Files:** Create `src/server/llm/ollama.ts`, `tests/unit/ollama.test.ts`, `tests/fixtures/ollama.ndjson`, `docs/local-inference.md`. Modify `src/server/config.ts`, `src/server/generation/orchestrator.ts`의 Provider 선택.

**Interfaces:** `createOllamaProvider({baseUrl,keepAlive}):LlmProvider`. 모델·context·출력 길이는 GenerateInput에서 받는다. `/api/chat` NDJSON을 §3.2 이벤트로 변환한다.

- [ ] **1.** 분할 JSON, 마지막 usage, thought/content 분리, 비정상 EOF, abort를 fixture로 RED 확인한다.

```ts
it('converts nanosecond usage and excludes thinking', async () => {
  const events = await collect(createOllamaProvider(testOptions)
    .generate(validGenerateInput));
  expect(events.filter(e=>e.type==='delta')).toEqual([{type:'delta',text:'본문'}]);
  expect(events).toContainEqual({type:'usage',promptTokens:10,
    completionTokens:20,decodeMs:2000,loadMs:500});
});
```

- [ ] **2.** native fetch로 `{model,messages,stream:true,keep_alive:'5m',options:{num_ctx:contextWindow,num_predict:maxOutputTokens,temperature,seed}}`를 전송한다. structured 추출일 때 `format`에 responseSchema를 전달한다. thinking 전용 필드·빈 content는 UI delta로 보내지 않는다.
- [ ] **3.** `prompt_eval_count/eval_count/eval_duration/load_duration`을 사용한다. ns는 1e6으로 나눠 ms로 바꾼다. `done_reason=length`는 length, 정상 종료만 stop이다. 404 모델 없음·OOM·context limit·접속 실패를 분리한다. response body는 finally에서 닫는다. adapter가 connect/header 10초 timer를 관리하고 header 수신 즉시 해제한다. 이 timer가 전체 body 스트림을 10초에 중단시키지 않도록 테스트한다.
- [ ] **4.** 기본 baseUrl은 loopback만 허용한다. Docker의 host.docker.internal은 운영자가 server env에 설정한 경우에만 허용한다. request body로 URL을 바꾸지 못하게 한다. 자동 model pull·warm-up을 일반 HTTP 요청 경로에 넣지 않는다.
- [ ] **5.** `npm.cmd run test:unit -- tests/unit/ollama.test.ts` 후 운영자가 선택 모델을 설치한 환경에서만 local smoke를 실행한다. 모델이 없으면 `LOCAL_MODEL_MISSING`으로 명확히 보고하고 통과로 기록하지 않는다. 설치·VRAM 측정은 B01~B05에서 수행한다.
- [ ] **6.** 문서에 host 실행·keep-alive·unload·context 변경·메모리 부족 대응을 작성한다. 제안 commit: `feat: add local ollama inference`.

### T11. Deadline·Retry·중단 정책을 구현한다

**Files:** Create `src/server/generation/policy.ts`, `tests/unit/reliability.test.ts`. Modify `orchestrator.ts`, `config.ts`, `runtime.ts`.

**Interfaces:** `runWithPolicy(input:GenerateInput,options:{primary:LlmProvider;fallback?:LlmProvider;fallbackModel?:string;task:'narrative'|'memory';allowCloud:boolean;deadlineAt:number;onAttempt:(a:AttemptRecord)=>void;clock?:()=>number;random?:()=>number;sleep?:(ms:number,signal:AbortSignal)=>Promise<void>}):AsyncIterable<ProviderEvent>`. clock/random/sleep은 단위 테스트에서만 교체하고 기본값은 platform monotonic clock/random/abort 가능한 timer다. `AttemptRecord {provider:ProviderId;model:string;task:'narrative'|'memory';attempt:number;status:string;errorCode:string|null;retryCount:number;metrics:Record<string,number|null>}`. metrics 계산은 T14의 정의를 사용한다. fallback 실행에는 primary model이 아니라 명시된 fallbackModel을 넣는다. T12에서 Orchestrator의 optional fallback과 설정된 model을 연결한다.

- [ ] **1.** fake timer로 첫 token 전 503은 재시도하고 첫 delta 이후에는 재시도하지 않는 RED를 확인한다.

```ts
it('never regenerates after a visible token', async () => {
  const primary = providerThatYieldsThenFails('가');
  await expect(collect(runWithPolicy(validGenerateInput,
    policyOptions(primary)))).rejects.toMatchObject({code:'UNAVAILABLE'});
  expect(primary.calls).toBe(1);
});
```

- [ ] **2.** 기본 total deadline 300s, adapter의 connect/header 10s, policy의 first-visible-token 60s, token idle 30s, Memory 합계 120s를 구현한다. 각각의 time budget은 total remaining보다 커질 수 없다. 추출 paragraph 수가 많아져도 total deadline은 늘어나지 않는다. iterator.next가 멈춰 있어도 timer가 child AbortController를 중단시키도록 하고 모든 timer/listener는 finally에서 해제한다.
- [ ] **3.** 재시도 대상은 연결 실패·429·502/503/504다. narrative는 primary 최대 2 attempts와 fallback 최대 1회로 제한한다. 각 Memory paragraph도 primary 2/fallback 1의 동일 상한을 적용하며 전체 Memory 누적 호출은 12회까지다. paragraph 수 때문에 초과하면 draft를 보존하고 실패한다. validation/auth/context/blocked/OOM은 반복하지 않는다. 모든 호출은 같은 총 deadline 안에 기록한다.
- [ ] **4.** 첫 본문 delta 전만 narrative 재시도/fallback을 허용한다. exponential backoff 500ms·1000ms 상한과 jitter를 적용하고 Retry-After가 remaining보다 길면 즉시 실패한다. jitter·clock·delay를 테스트에서 주입한다. 취소는 backoff·reader·추출까지 전파한다.
- [ ] **5.** `npm.cmd run test:unit -- tests/unit/reliability.test.ts`를 실행한다. first-token 지연/idle hang/429 Retry-After/사용자 cancel/전체 deadline/첫 delta 이후 실패를 검증한다. header나 ping을 첫 token으로 계측하지 않는다.
- [ ] **6.** 제안 commit: `feat: bound generation retries and deadlines`.

### T12. Gemini 선택과 동의 기반 Fallback을 구현한다

**Files:** Create `src/server/llm/gemini.ts`, `tests/unit/gemini.test.ts`, `tests/unit/cloud-consent.test.ts`, `tests/fixtures/gemini.sse`. Modify `config.ts`, `policy.ts`, `GenerationPanel.tsx`, `ProjectForm.tsx`.

**Interfaces:** `createGeminiProvider({apiKey,baseUrl}):LlmProvider`. baseUrl은 고정된 Google endpoint를 server env/test에서만 설정한다. SDK wrapper와 Vertex OAuth 구현은 새로 추가하지 않는다.

- [ ] **1.** Gemini SSE에서 여러 text part를 순서대로 합치고 thought를 제외하는 RED를 작성한다. usage가 없으면 null이며 chunk 수를 token 수로 간주하지 않는다.

```ts
it('makes zero cloud calls without persisted consent', async () => {
  await collect(runGeneration(localRunWithoutConsent, consentTestDeps));
  expect(cloudFetch).not.toHaveBeenCalled();
});
```

- [ ] **2.** `models/{model}:streamGenerateContent?alt=sse`에 API key를 header로 전달한다. system/user 메시지·생성 설정·JSON schema를 REST 형식으로 변환한다. 정상 finish/길이 제한/safety block/429/usage를 공통 타입으로 매핑한다. T10과 같은 connect/header timer 경계를 적용한다. 인증 key가 URL·예외·로그에 남지 않도록 한다.
- [ ] **3.** Gemini 모델은 GEMINI_MODEL의 명시적 값만 사용한다. 시작 전 provider 선택 시 availability를 확인하고 지원 중단된 모델이면 설정 오류를 반환한다. v1의 model 문자열을 그대로 기본값으로 옮기지 않는다.
- [ ] **4.** UI에 작품 설정·인물·최근 원문·요약·현재 지시·생성 본문이 Cloud 생성/추출에 전달될 수 있음을 표시한다. 요청별 allowCloud는 기본 false다. `provider=gemini && !allowCloud`는 400. fallback은 ENABLE_CLOUD와 저장된 allowCloud가 모두 true일 때만 허용한다. 이 동의는 hosted Supabase 저장 경로 안내와 별도로 표시한다.
- [ ] **5.** 로컬 장애 전 첫 delta가 없을 때만 fallback한다. Provider/model 전환 이벤트를 UI에 표시한다. Memory 추출에서 외부 전송도 같은 동의를 재검사한다. 키가 없고 cloud 비활성이어도 Ollama/CRUD는 정상 동작해야 한다.
- [ ] **6.** `npm.cmd run test:unit -- tests/unit/gemini.test.ts tests/unit/cloud-consent.test.ts`를 실행한다. 실제 유료 호출은 B04 명시적 실행에서만 수행한다. 제안 commit: `feat: add consented cloud fallback`.

### T13. 노트·인물 수정·완결·원자 Undo를 구현한다

**Files:** Create `supabase/migrations/202610260004_story_commands.sql`, `supabase/tests/undo.test.sql`, `src/app/api/projects/[id]/undo/route.ts`, `src/app/api/projects/[id]/finish/route.ts`, `src/features/stories/AuthorSettings.tsx`, `tests/e2e/undo.spec.ts`. Modify `db.ts`, `commands.ts`, project PATCH/characters API, StoryReader.

**Interfaces:** `updateProject(actorId,projectId,expectedRevision,patch)`, `finishProject(actorId,projectId,expectedRevision)`, `undoEpisode(actorId,projectId,expectedRevision):Promise<ProjectSnapshot>`.모두 짧은 transaction과 project lock을 사용한다.

- [ ] **1.** 각 테스트 transaction 안에서 seed의 고정 project UUID `00000000-0000-4000-8000-000000000001`에 begin_generation→commit_generation→undo_episode를 실행한 뒤 아래를 assert한다. 테스트마다 rollback하여 동일 baseline에서 시작한다. 프로젝트 생성 자체는 T02에서 별도 검증한다. revision 0→1화→Undo와 동명 인물 두 명의 서로 다른 상태 복원 DB RED를 작성한다. 에필로그 후 Undo에서는 isFinished=false가 복원되어야 한다.

```sql
select is((select head_memory_revision from public.projects
           where id='00000000-0000-4000-8000-000000000001'),
          0::bigint, 'first undo returns to baseline');
select is((select count(*)::int from public.episodes
           where project_id='00000000-0000-4000-8000-000000000001' and deleted_at is null),
          0, 'undone episode is not canonical');
```

- [ ] **2.** active 요청이 있으면 설정 수정/Undo/finish를 409로 거부한다. expectedRevision을 확인하고 현재 Memory head를 기준으로 새 revision을 만든다. 캐릭터 invariant 변경은 작성자의 명시적 수정만 허용한다.
- [ ] **3.** Undo는 마지막 활성 episode의 before snapshot을 복원한다. 생성 후 바뀐 캐릭터 mutable state·관계·plot·복선·선택지·isFinished를 함께 복원한다. notes/world/invariant는 project/characters의 현재 값을 유지한다. 설정 수정은 project.revision을 증가시키되 기존 Memory row를 변경하지 않는다. 생성→세계관 수정→Undo 순서에서도 최신 작가 설정이 유지되는 회귀 테스트를 추가한다. UI에서 Undo 대상 회차와 복원할 상태를 안내한다.
- [ ] **4.** soft-deleted episode와 원래 generation request는 보존한다. 같은 키를 다시 보내면 undoneAt이 있는 기존 결과만 반환한다. 복구 불가능한 영구 삭제·v1 데이터 이관은 이 작업에 추가하지 않는다. 서재 삭제 동작은 archived_at으로 숨기고 되돌릴 수 있게 한다.
- [ ] **5.** `npm.cmd run test:db`, `npm.cmd run test:e2e -- tests/e2e/undo.spec.ts`를 실행한다. 첫 회차/일반 회차/에필로그/완결만 처리한 상태/Undo 뒤 같은 번호 재생성/설정 수정과 Undo 충돌을 확인한다. `updated_at`·이름 기준 snapshot 선택을 사용하지 않는다.
- [ ] **6.** 제안 commit: `feat: restore story state atomically on undo`.

### T14. 요청·attempt 지표와 운영 화면을 구현한다

**Files:** Create `src/server/observability/metrics.ts`, `src/server/observability/logging.ts`, `src/app/api/metrics/route.ts`, `src/app/metrics/page.tsx`, `tests/unit/metrics.test.ts`, `tests/unit/logging.test.ts`. Modify `orchestrator.ts`, `policy.ts`, generation client, request metrics 저장.

**Interfaces:** `summarizeTiming({startedAt,firstTokenAt,lastTokenAt,completedAt,completionTokens,decodeMs}):{ttftMs:number|null;generationMs:number;endToEndTps:number|null;providerTps:number|null}`. 시간은 monotonic ms다. nullable timestamp/token은 측정 실패를 뜻하며 0으로 위장하지 않는다.

- [ ] **1.** first token·마지막 token·commit 시간과 Provider decode 시간을 분리하는 RED를 작성한다.

```ts
it('uses measured tokens rather than chunk count', () => {
  expect(summarizeTiming({startedAt:0,firstTokenAt:1000,lastTokenAt:3000,
    completedAt:3500,completionTokens:40,decodeMs:2000}))
    .toEqual({ttftMs:1000,generationMs:3500,endToEndTps:40/3,
      providerTps:20});
});
```

- [ ] **2.** server TTFT는 요청 처리 시작→첫 비어 있지 않은 visible delta, generationMs는 시작→Memory/DB terminal, endToEndTps는 completionTokens/(lastTokenAt-startedAt), providerTps는 completionTokens/decodeMs로 정의한다. duration=0 또는 token unknown이면 null이다. UI TTFT는 클릭→첫 paint로 별도 보고하며 server TTFT와 합치지 않는다.
- [ ] **3.** JSON 로그는 request_id/generation_id/provider/model/task/attempt/status/error_code/retry_count/prompt_tokens/completion_tokens/ttft_ms/generation_ms/tokens_per_second를 allowlist로 기록한다. Context는 included/dropped IDs와 section byte/token 추정치만 남긴다. 본문·prompt·notes·keys·cookie는 기본 출력 금지다.
- [ ] **4.** request 집계와 attempt 집계를 나눈다. 실패율 분모는 실제 실행 요청, idempotent replay는 제외한다. cancelled/interrupted/fallback 성공을 각각 표시한다. 기간과 sample 수를 함께 표시하고 적은 sample의 p95를 SLA로 표현하지 않는다.
- [ ] **5.** 로그에 심은 sentinel secret/prompt가 없는지, missing usage/null/zero duration/cancel/retry/fallback 계수가 맞는지 unit 검증한다. metrics route는 내 기록만 반환하고 raw draft를 포함하지 않는다. 화면은 표·간단한 SVG 분포만 사용하며 chart 의존성을 추가하지 않는다.
- [ ] **6.** `npm.cmd run test:unit -- tests/unit/metrics.test.ts tests/unit/logging.test.ts`를 실행한다. 제안 commit: `feat: expose generation metrics without prompt logging`.

### T15. 실제 경계를 통과하는 회귀 검증을 완성한다

**Files:** Create `tests/integration/recovery.test.ts`, `tests/integration/authorization.test.ts`, `tests/e2e/cloud-policy.spec.ts`, `tests/e2e/accessibility.spec.ts`, `scripts/seed-demo.ts`, `docs/validation.md`. Modify Playwright 설정.

**Interfaces:** CI의 모든 기본 테스트는 fake upstream+전용 DB다. fake upstream은 Ollama `/api/chat` 계약과 structured 추출 응답까지 제공한다. 실제 Ollama/Gemini 호출은 별도 live 검증으로 구분한다. 테스트 사용자 A/B와 seed는 테스트 인스턴스에만 만든다.

- [ ] **1.** 아래 시나리오별로 실패를 재현하는 테스트를 먼저 작성한다. 동일 요구를 구현한 unit 테스트를 단순 복제하지 않고 route·DB·Browser 사이를 검증한다.

```ts
test('reload does not regenerate an interrupted request', async ({page}) => {
  await openSeededStory(page);
  await page.getByRole('button',{name:'다음 회차 생성'}).click();
  await expect(page.getByText('미확정 초안')).toBeVisible();
  await interruptTestServer();
  await restartTestServer();
  await page.reload();
  await expect(page.getByText('생성이 중단되었습니다')).toBeVisible();
  expect(await countNarrativeCalls()).toBe(1);
});
```

- [ ] **2.** double submit·같은 키 다른 payload·동시 탭·취소/commit 경합·비정상 EOF·length·잘못된 metadata·저장 timeout 뒤 재조회·완성 draft finalize·프로세스 재시작을 검증한다. 재시작 테스트는 fake clock으로 heartbeat 만료 60초를 진행시킨 후 interrupted를 확인한다. runtime test helper는 테스트 전용이며 production route에 fault injection parameter를 넣지 않는다.
- [ ] **3.** A의 project/generation/metrics에 B 접근, 인증 없는 mutation, 잘못된 Origin, raw RPC/DML 거부, provider URL 입력, 초대형 JSON, script 태그 본문 안전 렌더링, cloud 미동의의 생성/추출 외부 요청 0을 검증한다.
- [ ] **4.** 키보드만으로 생성·취소·오류 복구, modal focus/Escape, 모바일 가로 overflow, 한글 조합 Enter를 확인한다. 스크린리더 수동 확인을 하지 않았다면 그 한계를 validation 문서에 기록한다.
- [ ] **5.** typecheck/lint/unit/DB/integration/Chromium E2E/build를 한 번씩 실행하고 실행일·commit·환경·결과 파일을 남긴다. 실패를 skip으로 바꿔 통과시키지 않는다. 새 실패나 코드 변경 없이 동일 전체 검증을 반복하지 않는다.
- [ ] **6.** 제안 commit: `test: cover generation recovery and access boundaries`.

### T16. Docker·실행 문서·공개 산출물을 완성한다

**Files:** Create `Dockerfile`, `.dockerignore`, `compose.yaml`, `scripts/smoke.ts`, `README.md`, `docs/runbook.md`, `docs/security.md`, `docs/architecture.md`, `docs/adr/0003-model-selection.md`. Modify `next.config.ts`, `package.json`, `docs/benchmarks.md` (B04에서 생성).

**Interfaces:** `output:'standalone'`, app port 3000. compose는 `127.0.0.1:3000:3000`으로 공개하고 Ollama는 호스트에서 실행한다. DB는 별도 v2 Supabase다. `npm.cmd run smoke -- --base-url http://localhost:3000`은 health·로그인·Fake 생성을 확인한다. package.json에 `"smoke":"tsx scripts/smoke.ts"`를 추가한다.

- [ ] **1.** `smoke.ts`는 health JSON, 테스트 사용자 인증, first delta, committed episode와 DB 재조회가 일치하는지 assert한다. GPU 없는 container 검증에서는 OLLAMA_BASE_URL을 harness가 소유한 fake upstream으로 지정한다. 앱에 production Fake 선택지나 테스트 전용 endpoint는 추가하지 않는다. 테스트 프로세스의 외부 LLM 접속은 실패시키는 guard를 둔다. 스모크가 먼저 실패하는지 확인한다.
- [ ] **2.** Node 24 multi-stage build와 non-root runtime을 구성한다. `.next/standalone`, static, public만 배포하고 .env/key/artifacts/model을 image에 넣지 않는다. Supabase 공개 URL/anon key는 Next build 인자, service role/Gemini key는 runtime secret이다. 공개 env가 image build 시 고정된다는 점을 문서화한다.

```yaml
services:
  app:
    build:
      context: .
      args:
        NEXT_PUBLIC_SUPABASE_URL: ${NEXT_PUBLIC_SUPABASE_URL}
        NEXT_PUBLIC_SUPABASE_ANON_KEY: ${NEXT_PUBLIC_SUPABASE_ANON_KEY}
    ports: ["127.0.0.1:3000:3000"]
    env_file: .env.local
    environment:
      HOSTNAME: 0.0.0.0
      OLLAMA_BASE_URL: http://host.docker.internal:11434
```

- [ ] **3.** host에서 loopback으로만 열려 있는 Ollama에 Docker Desktop container가 접속 가능한지 실제 점검한다. 접속되지 않으면 권한·대상 인터페이스·방화벽 범위를 확인한 뒤 문서화한다. 작동 확인 없이 Ollama를 0.0.0.0으로 열라는 명령을 기본 안내로 넣지 않는다. 우선 완주 경로는 native Node이며 Docker의 GPU 직접 passthrough는 초기 필수 범위가 아니다.
- [ ] **4.** README는 설치→환경변수→실행→생성/취소/복구→문제 해결 순서로 작성한다. hosted DB 데이터 경로, Cloud 동의, 로컬 추론 포트, 단일 프로세스 한계, draft 유실 범위, 실제 지원 기능을 명시한다. 개발 검증 명령은 별도 docs/validation.md에 둔다.
- [ ] **5.** architecture.md에 Browser→Node API→Context/Memory→Provider→DB 흐름과 cancel/fallback 경계를 설명한다. runbook에는 모델 미실행/모델 없음/OOM/context 초과/429/추출 실패/DB 실패/중단 복구를 code별로 연결한다. v1 DB에 연결·migration하지 않는다.
- [ ] **6.** B05 실측 결과와 ADR을 반영하고 local/cloud 비교·TTFT/TPS/latency·RAM/VRAM·품질·재현 명령을 공개 문서에 넣는다. API key/사용자 원고/로그의 민감정보를 제거한다. 모델 가중치는 재배포하지 않고 원출처·license 확인 절차를 적는다.
- [ ] **7.** `docker compose config`, `docker compose build`, `docker compose up -d`, smoke와 real local inference smoke를 실행한다. Docker stream flush·취소·restart·로그 노출을 확인한다. B05와 전체 gate가 통과해야 릴리스 완료다. 제안 commit: `docs: package reproducible local inference demo`.

## 5. 검증 실행 계약

각 명령은 구현 완료 후 v2 루트에서 실행한다. Windows에서는 `npm.cmd`, `npx.cmd`를 사용한다.

```powershell
npm.cmd ci
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run test:unit
npm.cmd run test:db
npm.cmd run test:integration
npx.cmd playwright install chromium
npm.cmd run test:e2e
npm.cmd run build
```

DB가 없는 환경의 unit 통과는 DB·RLS·복구 통과를 뜻하지 않는다. Fake 통과는 실제 Gemma의 속도·품질·GPU 중단을 보증하지 않는다. Cloud 비교를 실행하지 못했다면 인증·비용·서비스 가용성 제약을 기록하고 비교 미완료로 남긴다.

벤치마크는 별도 계획의 `npm.cmd run benchmark -- --config benchmarks/candidates.json --output artifacts/benchmarks/run-id`로 실행한다. 실측 전 임의 성능 수치를 README에 넣지 않는다. iteration마다 새 run-id를 사용하고 trial별 raw JSON·환경 manifest·blind 품질표·결정 ADR을 함께 보존한다.

## 6. 설계 근거와 외부 문서

아래는 2026-10-26에 확인한 1차 자료다. 실행자는 설치 시점에 API/버전 호환성을 다시 확인한다. 제품 동작과 성능은 문서가 아니라 테스트·실측으로 판정한다.

- [Node.js release schedule](https://nodejs.org/en/about/previous-releases): Node 24 LTS를 runtime 기본안으로 선택한 근거.
- [Next.js self-hosting](https://nextjs.org/docs/app/guides/self-hosting): Node/Docker 배포와 streaming 경로를 구성할 때 참고. proxy buffering 유무는 실제 배포에서 검증한다.
- [Ollama streaming](https://docs.ollama.com/api/streaming), [chat API](https://docs.ollama.com/api/chat): NDJSON, message content와 마지막 usage 형식의 근거.
- [Ollama FAQ](https://docs.ollama.com/faq): num_ctx·keep-alive·호스트/메모리 설정을 검토할 근거. 모델 파일 크기를 VRAM 총량으로 치환하지 않는다.
- [Gemini text generation](https://ai.google.dev/gemini-api/docs/text-generation): streaming 생성 형식의 근거. 사용 모델의 schema/usage 지원은 adapter fixture와 live smoke에서 확인한다.
- [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security): 사용자 읽기 격리와 service role의 권한 차이에 대한 근거.

## 7. 계획 검토 기록과 실행 인계

작성 후 방향서 §1~16을 다시 읽고 체크리스트의 요구사항 매핑으로 누락을 검토한다. placeholder·인터페이스 이름·DB lock 순서·멱등 재전송·token 계측·Cloud 동의가 서로 일치하는지 확인한다. 수정한 내용과 검토 결과는 체크리스트에 남긴다.

2026-10-26 계획 자체 검토에서 예약/실행 분리, epoch fencing, 작가 세계관과 mutable Memory 분리, 논리 호출별 retry, accepted 유실 복구, TAP 실패 판정, 실제 characterStates 검증, SSE event의 ID 사용을 수정했다. 별도 검토자에게 DB·UI·벤치마크 계약을 확인받고 자체 검토도 수행했다. 문서 3개의 상대 링크 9개, code fence, JSON 예시, UTF-8, 작업 21개, 요구사항 32개와 방향서 §1~16 매핑을 정적으로 점검했다. 애플리케이션·DB·GPU·Docker 테스트를 실행한 결과는 아니다.

실행 방식은 작업별 subagent 구현과 중앙 통합 검증을 권장한다. 대안은 `superpowers:executing-plans`로 같은 작업을 순차 수행하는 방식이다. 이번 요청의 산출물은 계획 문서까지이며, 다음 구현 지시에서 T01부터 시작한다.
