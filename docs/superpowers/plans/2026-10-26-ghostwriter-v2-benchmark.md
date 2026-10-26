# Ghostwriter v2 Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 동일한 한국어 소설 입력으로 로컬 Gemma 후보 2~3개와 Cloud 기준 모델 1개의 속도·메모리·품질을 재현 가능하게 비교하고, 측정 근거가 있는 모델 선택 기록을 만든다.

**Architecture:** [주 구현 계획](./2026-10-26-ghostwriter-v2.md)의 Provider 계약을 재사용하는 독립 CLI를 만든다. 실제 모델을 호출하는 부분과 시간 계산·통계·보고서 생성을 분리하여 GPU와 네트워크 없이 검증한다. llama.cpp는 이 비교용 어댑터로만 추가하며, 앱의 기본 Provider 선택은 이 계획이 직접 바꾸지 않는다.

**Tech Stack:** Node.js, TypeScript, `tsx`, Vitest, Node 표준 라이브러리, 기존 Ollama/Gemini Provider, 선택적 llama.cpp HTTP server, Windows PowerShell 및 `nvidia-smi`.

**Spec:** [ghostwriter_v2_technical_direction.md](../../../ghostwriter_v2_technical_direction.md)의 3·5·6·9·10·11·14·16절. 앱 공통 계약과 테스트 환경은 [주 구현 계획](./2026-10-26-ghostwriter-v2.md)을 함께 읽는다.

## Global Constraints

- 기준 하드웨어: Ryzen 5 5600X / RAM 16GB / RTX 4060 Ti 8GB / 저장공간 1.59TB.
- 초기에는 2~3개의 로컬 후보와 1개의 Cloud 기준 모델을 비교한다.
- 정확한 Gemma 모델, Quantization, Inference Backend, 성능 목표는 벤치마크 후 확정한다.
- 최초 버전부터 최대 모델·최대 컨텍스트를 사용하지 않는다.
- 로컬 LLM 서버 포트를 인터넷에 직접 공개하지 않는다.
- API Key와 Secret은 소스코드·Git 저장소에서 분리한다.
- Cloud 실행은 명시적으로 CLI를 실행할 때만 허용하며, 합성 입력의 외부 전송과 비용 발생을 실행 문서에 명시한다.
- 이 문서 작성 단계에서는 모델 다운로드, 추론 서버 시작, 모델 호출, Cloud 과금 호출을 실행하지 않는다.
- 실행 코드는 v1의 실제 사용자 데이터·DB·계정을 읽지 않는다. Hosted v2 Supabase도 이 CLI의 의존성이 아니다.
- 후보 이름은 2026-10-26 확인한 [Ollama 공식 Gemma 3 tags](https://ollama.com/library/gemma3/tags)를 근거로 한다. `gemma3:1b-it-q4_K_M`, `gemma3:4b-it-q4_K_M`, `gemma3:4b-it-q8_0`가 등록되어 있다. 공개 파일 크기는 실행 VRAM 요구량의 증거로 사용하지 않는다.
- `warm TTFT p95 ≤ 5,000ms`, `decode ≥ 15 tokens/sec` 및 아래 품질 기준은 **제안하는 임시 선택 기준**이다. 확정 요구사항이나 SLA로 기재하지 않는다.

---

## 파일 구조와 선행 계약

| 파일 | 책임 |
|---|---|
| `benchmarks/candidates.json` | 후보·고정 생성 조건·반복 횟수 |
| `benchmarks/cases.json` | 초기 생성·이어쓰기·장기 기억의 합성 한국어 입력 |
| `benchmarks/quality-rubric.md` | 8개 품질 차원, 1~5점 기준, 치명적 위반 정의 |
| `src/server/benchmark/config.ts` | 설정·사례 타입 및 엄격한 로드/검증 |
| `src/server/llm/llamacpp.ts` | benchmark 전용 OpenAI 호환 SSE Provider |
| `src/server/benchmark/runner.ts` | 단일 사례 실행·시간/usage 계측 |
| `src/server/benchmark/hardware.ts` | 선택적 GPU/Windows RAM 표본 수집 |
| `src/server/benchmark/report.ts` | 통계·blind 평가 묶음·선택 판단 |
| `scripts/benchmark.ts` | 인자·환경변수·직렬 실행·산출물 저장 |
| `docs/benchmarks.md` | 사전 준비, 측정 한계, 수동 평가, 재실행 방법 |
| `tests/unit/benchmark/*.test.ts` | 외부 추론 없이 검증하는 harness 테스트 |
| `tests/fixtures/benchmark/llamacpp.sse` | 경계가 나뉜 llama.cpp 응답 fixture |
| `artifacts/benchmarks/<run-id>/` | 실행 당시만 생성하는 원자료·보고서; Git 제외 |

주 계획에서 먼저 만드는 `src/server/llm/types.ts`를 그대로 import한다. 이 문서에서 이름이나 event 모양을 변경하지 않는다.

```ts
export type ProviderId = 'ollama' | 'gemini' | 'llamacpp';
export interface GenerateInput {
  messages: { role: 'system' | 'user' | 'assistant'; content: string }[];
  model: string;
  maxOutputTokens: number;
  temperature: number;
  contextWindow: number;
  seed?: number;
  signal: AbortSignal;
  responseSchema?: Record<string, unknown>;
}
export type ProviderEvent =
  | { type: 'delta'; text: string }
  | { type: 'usage'; promptTokens: number | null;
      completionTokens: number | null; decodeMs: number | null; loadMs: number | null }
  | { type: 'finish'; reason: 'stop' | 'length' | 'blocked' };
export interface LlmProvider {
  id: ProviderId;
  generate(input: GenerateInput): AsyncIterable<ProviderEvent>;
}
```

Ollama/Gemini는 주 계획 T10의 `createOllamaProvider({baseUrl,keepAlive})`와 T12의 `createGeminiProvider({apiKey,baseUrl})`로 만든 객체를 `runCase()`에 주입한다. 부모 계획의 Provider 테스트 완료 후 B02~B04를 연결하고, B03은 T14의 순수 계산 helper `summarizeTiming()` 완료에도 의존한다. CLI의 benchmark 실행에는 자동 retry와 Cloud fallback을 적용하지 않으며 `retryCount=0`을 원자료에 남긴다. 실패를 다른 모델의 성공으로 숨기지 않는다.

## 측정·선택 규칙

1. 후보별 각 사례를 `cold 1회 → warm 5회` 직렬 실행한다. 후보 3개라면 54회, 후보 4개라면 72회 호출이다. Cloud 기준 모델은 18회이며 비용이 발생할 수 있다. 한 번에 하나만 생성하여 로컬 GPU 경쟁을 피한다.
2. 설정은 모든 후보에서 `maxOutputTokens=512`, `temperature=0.7`, `contextWindow=8192`, `seed=42`, `timeoutMs=120000`으로 고정한다. seed를 지원하지 않는 Provider는 `seedApplied=false`로 기록한다. Cloud의 contextWindow는 비교용 입력 예산이며 해당 모델의 서버 context 크기를 변경했다는 뜻이 아니다. 실제 prompt/completion token 수는 Provider usage가 있을 때만 기록한다.
3. Ollama cold는 해당 모델의 unload 완료를 확인한 뒤 호출한다. llama.cpp cold는 실행자가 서버를 재시작하고 확인한 경우만 인정한다. Cloud의 첫 요청은 `provider-managed`로 표기하고 하드웨어 cold로 부르지 않는다. 확인할 수 없는 경우 `unverified`이며 cold 비교 대상에서 제외한다. warm은 같은 프로세스/모델에서 중간 unload 없이 바로 실행한 결과다.
4. `t0`는 `provider.generate()` 순회를 시작하기 직전의 monotonic time, `tFirst`는 `text.length > 0`인 첫 visible delta 수신, `tLast`는 마지막 비어 있지 않은 visible delta 수신, `tDone`은 Provider finish를 확인한 뒤 iterator가 정상 종료한 시간이다. usage·finish·빈 delta·thought는 첫 토큰으로 세지 않는다. 공통 `summarizeTiming()`을 재사용하여 TTFT=`tFirst-t0`, generationMs=`tDone-t0`으로 계산한다. CLI의 completedAt은 Provider 종료이며, 앱의 completedAt은 Memory/DB terminal까지 포함한다. CLI TTFT에는 브라우저 렌더링 시간도 없으므로 앱 화면 표시 TTFT와 별도로 이름 붙인다.
5. `providerTps=completionTokens*1000/decodeMs`와 `endToEndTps=completionTokens*1000/(tLast-t0)`는 token counter가 있고 분모가 양수일 때만 계산한다. 둘 다 공통 `summarizeTiming()`의 반환값을 사용한다. endToEndTps에는 첫 응답 대기가 포함되며, Provider decoder 속도로 해석하지 않는다. delta 개수·글자 수를 token 수로 바꾸지 않는다. Provider가 counters를 주지 않으면 `null`이다.
6. warm 통계는 성공 표본의 median과 nearest-rank p95를 사용한다. 정렬 값의 `ceil(0.95*n)-1` 인덱스이므로 n=5의 p95는 최대값이다. 오류율은 warm 전체 시도 수를 분모로 쓴다. 성공 5개 미만, 첫 delta 없음, usage 없음은 숨기지 않으며 해당 선택 기준은 `insufficient-data`가 될 수 있다. n=5는 설명 통계이며 운영 SLA 증거가 아니다.
7. GPU는 250ms 간격의 `nvidia-smi` 장치 전체 사용량 표본이다. Windows RAM은 OS 전체 사용량 표본이다. 다른 프로세스가 포함되며 sampling 사이의 실제 최고점과 개별 추론 프로세스의 정확한 peak를 보장하지 않는다. baseline/관측 최고값/수집 오류/간격을 기록하고 누락 counter는 `null`로 둔다.
8. Ollama와 llama.cpp backend 성능을 직접 비교하려면 동일 GGUF SHA-256, quantization, contextWindow, chat template, 생성 조건을 확인한다. 하나라도 미확인/불일치면 `incomparable`로 표시한다. Ollama manifest digest만으로 GGUF SHA-256이 같다고 판단하지 않는다. 서로 다른 모델 간의 실제 운용 후보 비교는 가능하되 backend 자체 효과로 해석하지 않는다.
9. 세 사례의 warm 첫 번째 출력을 모델명이 없는 ID로 blind 평가한다. 점수는 사람이 입력하기 전까지 `null`이다. 캐릭터의 확정된 사망을 무시한 재등장과 세계관의 명시적 불가능 규칙 위반은 fatal이며 평균 점수로 상쇄하지 않는다.
10. 로컬 기본 모델의 임시 통과 조건은 모든 사례 warm 성공 5/5, TTFT p95 ≤5,000ms, providerTps median ≥15, fatal=0, 8차원별 3점 이상 및 전체 평균 3.5점 이상이다. decode counter 부재는 속도 통과로 간주하지 않는다. 실패 시 context 축소·다른 quant·경량 작업 전용·RAM 확장 선택지를 측정 근거와 함께 ADR에 기록하고 재측정한다. 가장 큰 모델을 자동 선택하지 않는다.

### Task B01: 후보 설정과 한국어 고정 입력을 검증한다

**Files:**

- Create: `benchmarks/candidates.json`
- Create: `benchmarks/cases.json`
- Create: `src/server/benchmark/config.ts`
- Test: `tests/unit/benchmark/config.test.ts`

**Interfaces:**

- Consumes: `GenerateInput`, `ProviderId` from `src/server/llm/types.ts`.
- Produces: `BenchmarkCandidate`, `BenchmarkCase`, `BenchmarkConfig`, `parseBenchmarkConfig(value: unknown): BenchmarkConfig`, `parseBenchmarkCases(value: unknown): BenchmarkCase[]`, `resolveModel(candidate: BenchmarkCandidate, env: Record<string, string | undefined>): string`.

```ts
export interface BenchmarkCandidate {
  id: string;
  provider: ProviderId;
  model?: string;
  modelEnv?: 'GEMINI_MODEL';
  optional?: boolean;
  artifact: { ggufSha256: string | null; quantization: string | null;
    chatTemplateSha256: string | null; runtimeVersion: string | null };
}
export interface BenchmarkCase {
  id: 'initial' | 'continuation' | 'long-memory';
  messages: GenerateInput['messages'];
  invariants: string[];
}
export interface BenchmarkConfig {
  coldRuns: 1;
  warmRuns: 5;
  generation: { maxOutputTokens: 512; temperature: 0.7;
    contextWindow: 8192; seed: 42; timeoutMs: 120000 };
  candidates: BenchmarkCandidate[];
}
```

- [ ] **Step 1: 실패하는 설정 검증 테스트를 작성한다.**

```ts
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { parseBenchmarkConfig, parseBenchmarkCases, resolveModel } from
  '../../../src/server/benchmark/config';

describe('benchmark configuration', () => {
  it('loads three Korean cases and a required cloud model from environment', () => {
    const config = parseBenchmarkConfig(JSON.parse(readFileSync('benchmarks/candidates.json', 'utf8')));
    const cases = parseBenchmarkCases(JSON.parse(readFileSync('benchmarks/cases.json', 'utf8')));
    expect(cases.map(c => c.id)).toEqual(['initial', 'continuation', 'long-memory']);
    const cloud = config.candidates.find(c => c.provider === 'gemini')!;
    expect(() => resolveModel(cloud, {})).toThrow('GEMINI_MODEL');
    expect(resolveModel(cloud, { GEMINI_MODEL: 'chosen-by-operator' })).toBe('chosen-by-operator');
    expect(() => parseBenchmarkConfig({ ...config, warmRuns: 0 })).toThrow();
    expect(() => parseBenchmarkCases([{ id: 'initial', messages: [], invariants: [] }])).toThrow();
  });
});
```

- [ ] **Step 2: RED를 확인한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark/config.test.ts`

Expected: 아직 없는 `config.ts` import 또는 fixture 누락으로 FAIL. 의도하지 않은 TypeScript 설정 오류라면 주 계획의 테스트 골격을 먼저 완료한다.

- [ ] **Step 3: 타입과 고정 fixture를 만든다.**

```json
{
  "coldRuns": 1,
  "warmRuns": 5,
  "generation": { "maxOutputTokens": 512, "temperature": 0.7, "contextWindow": 8192, "seed": 42, "timeoutMs": 120000 },
  "candidates": [
    { "id": "gemma-1b-q4", "provider": "ollama", "model": "gemma3:1b-it-q4_K_M", "artifact": { "ggufSha256": null, "quantization": "Q4_K_M", "chatTemplateSha256": null, "runtimeVersion": null } },
    { "id": "gemma-4b-q4", "provider": "ollama", "model": "gemma3:4b-it-q4_K_M", "artifact": { "ggufSha256": null, "quantization": "Q4_K_M", "chatTemplateSha256": null, "runtimeVersion": null } },
    { "id": "gemma-4b-q8", "provider": "ollama", "model": "gemma3:4b-it-q8_0", "optional": true, "artifact": { "ggufSha256": null, "quantization": "Q8_0", "chatTemplateSha256": null, "runtimeVersion": null } },
    { "id": "cloud-reference", "provider": "gemini", "modelEnv": "GEMINI_MODEL", "artifact": { "ggufSha256": null, "quantization": null, "chatTemplateSha256": null, "runtimeVersion": null } }
  ]
}
```

`parseBenchmarkConfig()`는 object/배열/문자열의 실제 타입, 후보 ID 중복, provider enum, 모델 literal와 modelEnv의 배타성, 로컬 2~3개·Cloud 1개, 고정 생성 조건을 검증한다. `modelEnv` 문자열을 모델 ID로 직접 보내지 않는다. optional 후보를 config에서 제외할 때도 로컬 최소 2개를 유지한다. 임의 URL·명령·실행 파일 경로를 이 JSON에 허용하지 않는다.

사례 파일에는 다음 JSON을 그대로 저장하며, 세 사례 모두 같은 system 규칙을 사용한다. 장기 기억은 고정 episode 문장 12개를 포함한다. 생성 시점에 LLM으로 사례를 만들지 않는다.

```json
[
  { "id": "initial", "messages": [
    { "role": "system", "content": "한국어 소설 본문만 작성한다. 서윤은 소리를 듣지 못하고 진동과 필담으로 소통한다. 이 세계에서는 죽은 사람이 되살아날 수 없다. 사용자가 정한 사실을 바꾸지 않는다." },
    { "role": "user", "content": "비가 내리는 폐역에서 기록관 서윤이 진동하는 봉인 상자를 발견한다. 경비 태오는 상자를 열지 말라고 필담으로 경고한다. 서윤의 판단과 두 사람의 긴장을 감각적으로 보여 주되 상자는 아직 열지 않는다." }
  ], "invariants": ["서윤은 소리를 듣지 못한다", "상자는 아직 열리지 않는다", "죽은 자는 부활할 수 없다"] },
  { "id": "continuation", "messages": [
    { "role": "system", "content": "한국어 소설 본문만 작성한다. 서윤은 소리를 듣지 못하고 진동과 필담으로 소통한다. 이 세계에서는 죽은 사람이 되살아날 수 없다. 사용자가 정한 사실을 바꾸지 않는다." },
    { "role": "user", "content": "직전 장면: 서윤은 왼쪽 발목을 다쳐 난간을 짚고 있다. 태오는 봉인 열쇠를 서윤에게 건넸고 지금은 빈손이다. 두 사람은 폐역 지하실 입구에 있다. 이어쓰기 지시: 태오가 필담으로 자신의 거짓말을 고백한다. 서윤이 다친 상태를 유지하고 열쇠 소유자를 바꾸지 않은 채 대화를 진행한다." }
  ], "invariants": ["서윤의 왼쪽 발목 부상 유지", "열쇠는 서윤 소유", "서윤은 소리를 듣지 못한다"] },
  { "id": "long-memory", "messages": [
    { "role": "system", "content": "한국어 소설 본문만 작성한다. 서윤은 소리를 듣지 못하고 진동과 필담으로 소통한다. 이 세계에서는 죽은 사람이 되살아날 수 없다. 사용자가 정한 사실을 바꾸지 않는다." },
    { "role": "user", "content": "장기 기억: [1화] 봉인 상자는 두 개의 열쇠가 있어야 열린다. [2화] 붉은 열쇠는 태오가 보관한다. [3화] 서윤의 스승 도하는 사망했고 유해를 화장했다. [4화] 서윤은 파란 열쇠를 얻었다. [5화] 수도는 북쪽 강 건너에 있다. [6화] 태오는 왼손잡이임을 밝혔다. [7화] 봉인 문양은 달빛에서만 보인다. [8화] 서윤은 기록 보존을 위해 왕실의 제안을 거절했다. [9화] 다리가 끊겨 남쪽 길은 막혔다. [10화] 서윤의 발목은 치료되어 정상 보행한다. [11화] 태오는 붉은 열쇠를 잃었고 아직 되찾지 못했다. [12화] 서윤과 태오는 달이 없는 밤에 북쪽 나루터에 도착했다. 현재 지시: 상자를 열지 못하는 현실적 장애물을 확인하고 다음 행동을 의논한다. 도하의 기록을 떠올릴 수 있지만 도하 본인이 살아 돌아오지 않는다. 해결책을 갑자기 만들어 내지 않는다." }
  ], "invariants": ["도하는 사망하여 재등장할 수 없다", "붉은 열쇠 분실 상태", "달이 없어 문양을 볼 수 없다", "발목은 회복됨"] }
]
```

장기 기억 사례는 오래된 사실과 최신 변경이 동시에 필요한 입력이다. 더 긴 context stress를 추가하려면 이 고정 입력과 별도 case/version으로 다루며, 현재 세 사례의 길이를 실행마다 바꾸지 않는다. 세 사례의 source SHA-256과 UTF-8 byte 수를 manifest에 기록한다.

```ts
export function resolveModel(c: BenchmarkCandidate, env: Record<string, string | undefined>): string {
  const model = c.model ?? (c.modelEnv ? env[c.modelEnv] : undefined);
  if (!model?.trim()) throw new Error(`Missing model: ${c.modelEnv ?? c.id}`);
  return model.trim();
}
```

- [ ] **Step 4: GREEN 및 타입 검사를 실행한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark/config.test.ts`

Run: `npm.cmd run typecheck`

Expected: 세 사례, 고정 조건, 모델 환경변수 검증 PASS. 네트워크 호출 0회.

- [ ] **Step 5: 작업 범위만 커밋한다.**

```powershell
git add benchmarks/candidates.json benchmarks/cases.json src/server/benchmark/config.ts tests/unit/benchmark/config.test.ts
git commit -m "feat: add fixed benchmark candidates and Korean cases"
```

### Task B02: llama.cpp 비교용 스트리밍 어댑터를 만든다

**Files:**

- Create: `src/server/llm/llamacpp.ts`
- Create: `tests/fixtures/benchmark/llamacpp.sse`
- Test: `tests/unit/benchmark/llamacpp.test.ts`

**Interfaces:**

- Consumes: `LlmProvider`, `GenerateInput`, `ProviderEvent` from `src/server/llm/types.ts`; 주 계획의 공통 SSE parser가 있으면 해당 export를 재사용한다.
- Produces: `createLlamaCppProvider(baseUrl: string, fetchImpl: typeof fetch = fetch): LlmProvider`.
- `id='llamacpp'`; 앱의 생성 요청에서 선택 가능한 기본 Provider 목록에는 등록하지 않는다.

- [ ] **Step 1: chunk 경계·usage·취소에 관한 실패 테스트를 쓴다.**

```ts
import { expect, it, vi } from 'vitest';
import { createLlamaCppProvider } from '../../../src/server/llm/llamacpp';

it('maps OpenAI-compatible streaming text and usage without counting role chunks', async () => {
  const text = [
    'data: {"choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}\n\n',
    'data: {"choices":[{"delta":{"content":"서윤은"},"finish_reason":null}]}\n\n',
    'data: {"choices":[{"delta":{"content":" 멈췄다."},"finish_reason":"stop"}]}\n\n',
    'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":8}}\n\n',
    'data: [DONE]\n\n'
  ].join('');
  const bytes = new TextEncoder().encode(text);
  const fetchMock = vi.fn(async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(bytes.slice(0, 29));
      controller.enqueue(bytes.slice(29)); controller.close(); }
  }), { headers: { 'Content-Type': 'text/event-stream' } })) as unknown as typeof fetch;
  const signal = new AbortController().signal;
  const events = [];
  for await (const e of createLlamaCppProvider('http://127.0.0.1:8080', fetchMock).generate({
    messages: [{ role: 'user', content: '이어 써라' }], model: 'gemma-local',
    maxOutputTokens: 512, temperature: 0.7, contextWindow: 8192, seed: 42, signal
  })) events.push(e);
  expect(events.filter(e => e.type === 'delta')).toEqual([
    { type: 'delta', text: '서윤은' }, { type: 'delta', text: ' 멈췄다.' }
  ]);
  expect(events).toContainEqual({ type: 'usage', promptTokens: 100,
    completionTokens: 8, decodeMs: null, loadMs: null });
  expect(events.at(-1)).toEqual({ type: 'finish', reason: 'stop' });
  expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ signal }));
});
```

같은 파일에 HTTP 503, malformed JSON, `[DONE]` 이전 EOF, 사전 abort를 각각 검사한다. fixture의 UTF-8 한글 byte 중간을 나눈 stream도 복원해야 한다. 공통 parser와 중복되는 테스트는 어댑터가 호출/전달하는 계약만 검증한다.

- [ ] **Step 2: RED를 확인한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark/llamacpp.test.ts`

Expected: `createLlamaCppProvider` 없음으로 FAIL.

- [ ] **Step 3: OpenAI 호환 endpoint를 최소 매핑한다.**

```ts
const body = {
  model: input.model, messages: input.messages, stream: true,
  stream_options: { include_usage: true }, max_tokens: input.maxOutputTokens,
  temperature: input.temperature, seed: input.seed
};
const response = await fetchImpl(new URL('/v1/chat/completions', baseUrl), {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body), signal: input.signal
});
```

`createLlamaCppProvider()`는 loopback HTTP URL만 허용하고 사용자 자격증명을 받지 않는다. `contextWindow`는 llama.cpp 서버 시작 설정이므로 요청에 임의 필드를 추가하지 않는다. manifest의 서버 context 크기가 입력값과 일치하지 않으면 CLI preflight를 실패시킨다. benchmark는 text generation만 하므로 `responseSchema`가 전달되면 지원하지 않는다는 명확한 오류를 반환한다.

`choices[].delta.content`가 문자열일 때 delta, 마지막 usage가 있을 때 usage를 낸다. 명시적이고 단위가 확인된 runtime timing이 없는 경우 `decodeMs`·`loadMs`는 `null`이다. stop/length/content_filter를 공통 finish reason에 매핑하고 최종 usage 이후 finish를 한 번만 내보낸다. 서버 오류, parsing 오류, 종료 event 없이 연결이 끊긴 응답은 성공 finish를 만들지 않는다. 사용한 llama.cpp 버전과 응답 fixture를 함께 고정하며 endpoint 계약은 [llama.cpp 공식 server 문서](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)를 실행 시 해당 설치 버전의 문서와 대조한다.

- [ ] **Step 4: GREEN을 확인한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark/llamacpp.test.ts`

Expected: 정상/role-only/UTF-8 분할/usage/오류/abort 테스트 PASS. 실제 llama.cpp 서버는 필요 없다.

- [ ] **Step 5: 커밋한다.**

```powershell
git add src/server/llm/llamacpp.ts tests/fixtures/benchmark/llamacpp.sse tests/unit/benchmark/llamacpp.test.ts
git commit -m "feat: add benchmark-only llama.cpp stream adapter"
```

### Task B03: 가짜 시간으로 검증 가능한 계측과 하드웨어 표본 수집을 만든다

**Files:**

- Create: `src/server/benchmark/runner.ts`
- Create: `src/server/benchmark/hardware.ts`
- Test: `tests/unit/benchmark/runner.test.ts`
- Test: `tests/unit/benchmark/hardware.test.ts`

**Interfaces:**

- Consumes: `LlmProvider`, `GenerateInput`, `ProviderEvent`; B01의 `BenchmarkCandidate`, `BenchmarkCase`; 주 계획 T14의 `summarizeTiming()` from `src/server/observability/metrics.ts`.
- Produces: `Clock { now(): number }`, `RunResult`, `runCase(provider: LlmProvider, input: GenerateInput, clock: Clock): Promise<RunResult>`.
- Produces: `HardwareSample`, `parseNvidiaCsv(text: string): number | null`, `parseWindowsMemory(text: string): number | null`, `startHardwareSampling(read: () => Promise<HardwareSample>, intervalMs: number): { stop(): Promise<HardwareSample[]> }`.

```ts
export interface Clock { now(): number }
export interface RunResult {
  output: string;
  ttftMs: number | null;
  generationMs: number;
  promptTokens: number | null;
  completionTokens: number | null;
  decodeMs: number | null;
  loadMs: number | null;
  providerTps: number | null;
  endToEndTps: number | null;
  finishReason: 'stop' | 'length' | 'blocked' | null;
  status: 'completed' | 'failed' | 'cancelled';
  error: string | null;
  retryCount: 0;
}
export interface HardwareSample {
  atMs: number;
  gpuDeviceUsedMiB: number | null;
  systemUsedMiB: number | null;
  error: string | null;
}
```

- [ ] **Step 1: token과 delta의 차이, counter 누락을 검사하는 실패 테스트를 쓴다.**

```ts
import { expect, it } from 'vitest';
import { runCase } from '../../../src/server/benchmark/runner';
import type { GenerateInput, LlmProvider } from '../../../src/server/llm/types';

it('ignores usage/empty deltas for TTFT and uses provider tokens for decode speed', async () => {
  let time = 0;
  const provider: LlmProvider = { id: 'ollama', async *generate() {
    time = 10; yield { type: 'delta', text: '' };
    time = 20; yield { type: 'delta', text: '' };
    time = 100; yield { type: 'delta', text: '서윤은' };
    time = 500; yield { type: 'delta', text: ' 멈췄다.' };
    time = 600; yield { type: 'usage', promptTokens: 40, completionTokens: 9, decodeMs: 500, loadMs: 50 };
    time = 700; yield { type: 'finish', reason: 'stop' };
  }};
  const input: GenerateInput = { messages: [], model: 'fake', maxOutputTokens: 512,
    temperature: 0.7, contextWindow: 8192, signal: new AbortController().signal };
  const result = await runCase(provider, input, { now: () => time });
  expect(result).toMatchObject({ ttftMs: 100, generationMs: 700,
    completionTokens: 9, providerTps: 18, endToEndTps: 18, retryCount: 0 });
});
```

추가 테스트: usage 없는 정상 종료는 token/TPS `null`; usage와 finish만 있는 출력은 TTFT `null` 및 실패; finish 이전 EOF는 실패; `signal.aborted`는 cancelled; `tLast=t0`이면 endToEndTps `null`; 실패 전 받은 partial output은 보존한다. 이것은 공개 benchmark 합성 입력의 진단용이며 앱의 저장 정책을 바꾸지 않는다.

```ts
expect(parseNvidiaCsv('4123\n')).toBe(4123);
expect(parseNvidiaCsv('N/A')).toBeNull();
expect(parseWindowsMemory('{"TotalVisibleMemorySize":16777216,"FreePhysicalMemory":8388608}'))
  .toBe(8192);
expect(parseWindowsMemory('permission denied')).toBeNull();
```

- [ ] **Step 2: RED를 확인한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark/runner.test.ts tests/unit/benchmark/hardware.test.ts`

Expected: runner/hardware import 누락으로 FAIL.

- [ ] **Step 3: 계측과 표본 수집을 구현한다.**

```ts
import { summarizeTiming } from '../observability/metrics';

const timing = summarizeTiming({
  startedAt, firstTokenAt: first, lastTokenAt: last,
  completedAt: clock.now(), completionTokens, decodeMs
});
```

`runCase()`는 공통 Provider 계약에 따라 usage와 finish를 각각 최대 한 번 허용한다. 중복 usage/finish는 protocol 오류로 기록한다. monotonic clock이 역행하면 측정 실패로 기록한다. Provider 오류 메시지는 secret/URL query를 제거하여 저장한다. `blocked`는 failed, `length`는 completed지만 `finishReason='length'`를 유지하여 품질 평가에서 출력 절단을 확인할 수 있게 한다. 정상 finish 뒤 추가 delta는 protocol 오류로 처리한다. 오류/취소 때도 종료 관측 시간을 completedAt으로 넣되 status를 별도로 보존한다.

하드웨어 읽기는 `execFile`로 실행하며 shell interpolation을 쓰지 않는다.

```ts
const gpuArgs = ['--id=0', '--query-gpu=memory.used', '--format=csv,noheader,nounits'];
const memoryScript = 'Get-CimInstance Win32_OperatingSystem | ' +
  'Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json -Compress';
```

`nvidia-smi` 및 `powershell.exe -NoProfile -NonInteractive -Command <memoryScript>`는 각각 2초 timeout을 둔다. 둘 중 하나가 실패해도 다른 값은 보존한다. 첫 read를 생성 직전에 실행하여 baseline으로 기록한다. 표본 timer는 이전 read 완료 전에 새 read를 겹쳐 실행하지 않고 `stop()`에서 진행 중 read 종료 후 반환한다. 원자료에는 표본 배열을 저장하고 GPU peak는 `max(valid samples)`로만 표현한다. 읽기 실패가 모델 생성 성공을 실패로 바꾸지는 않는다.

- [ ] **Step 4: GREEN을 확인한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark/runner.test.ts tests/unit/benchmark/hardware.test.ts`

Expected: fake Provider/clock/process reader로 모든 계산 PASS. 테스트가 실제 `nvidia-smi`, PowerShell, GPU를 실행하지 않는다.

- [ ] **Step 5: 커밋한다.**

```powershell
git add src/server/benchmark/runner.ts src/server/benchmark/hardware.ts tests/unit/benchmark/runner.test.ts tests/unit/benchmark/hardware.test.ts
git commit -m "feat: measure benchmark stream timings and sampled hardware usage"
```

### Task B04: 명시적 실행 CLI와 재현 가능한 원자료를 연결한다

**Files:**

- Create: `scripts/benchmark.ts`
- Create: `docs/benchmarks.md`
- Modify: `package.json`의 scripts만 추가
- Modify: `.gitignore`에 `artifacts/benchmarks/` 추가
- Test: `tests/unit/benchmark/cli.test.ts`

**Interfaces:**

- Consumes: B01 `parseBenchmarkConfig`, `parseBenchmarkCases`, `resolveModel`; B02 `createLlamaCppProvider`; B03 `runCase`, `startHardwareSampling`; 주 계획 T10의 `createOllamaProvider({baseUrl,keepAlive})`와 T12의 `createGeminiProvider({apiKey,baseUrl})`.
- Produces: `parseBenchmarkArgs(argv: string[]): { config: string; output: string }`, `executeBenchmark(config: BenchmarkConfig, cases: BenchmarkCase[], deps: BenchmarkDeps): Promise<void>` from `scripts/benchmark.ts`.
- `BenchmarkDeps`는 `getProvider(candidate): LlmProvider`, `prepareCold(candidate): Promise<'confirmed-unloaded' | 'external-restart' | 'unverified' | 'provider-managed'>`, `clock: Clock`, `write(relativePath, value): Promise<void>`, `readHardware(): Promise<HardwareSample>`, `env: Record<string, string | undefined>`를 가진다. 객체는 이 파일에서 export하며 테스트의 유일한 실행 경계다.

```ts
export interface BenchmarkDeps {
  getProvider(candidate: BenchmarkCandidate): LlmProvider;
  prepareCold(candidate: BenchmarkCandidate): Promise<
    'confirmed-unloaded' | 'external-restart' | 'unverified' | 'provider-managed'>;
  clock: Clock;
  write(relativePath: string, value: unknown): Promise<void>;
  readHardware(): Promise<HardwareSample>;
  env: Record<string, string | undefined>;
}
```

- [ ] **Step 1: 호출 횟수·중단·중복 출력 경로 테스트를 쓴다.**

```ts
import { expect, it, vi } from 'vitest';
import { executeBenchmark } from '../../../scripts/benchmark';

it('runs cold once then five warm trials per case without fallback or retries', async () => {
  const generate = vi.fn(async function* () {
    yield { type: 'delta' as const, text: '합성 소설' };
    yield { type: 'finish' as const, reason: 'stop' as const };
  });
  const config = JSON.parse(await (await import('node:fs/promises')).readFile('benchmarks/candidates.json', 'utf8'));
  const cases = JSON.parse(await (await import('node:fs/promises')).readFile('benchmarks/cases.json', 'utf8'));
  const write = vi.fn(async () => {});
  await executeBenchmark(config, cases, {
    getProvider: c => ({ id: c.provider, generate }),
    prepareCold: async c => c.provider === 'gemini' ? 'provider-managed' : 'unverified',
    clock: { now: () => 1 }, write,
    readHardware: async () => ({ atMs: 1, gpuDeviceUsedMiB: null, systemUsedMiB: null, error: null }),
    env: { GEMINI_MODEL: 'fake-cloud-model' }
  });
  expect(generate).toHaveBeenCalledTimes(config.candidates.length * cases.length * 6);
  expect(write).toHaveBeenCalledWith('manifest.json', expect.any(Object));
});
```

추가 테스트는 missing GEMINI_MODEL에서 첫 호출 전 실패, 단일 trial 오류 후 다음 trial 진행, 기존 output 디렉터리 거부, root 밖 output 거부, SIGINT 시 현재 signal abort 및 잔여 trial 미실행, import만으로 실행되지 않음을 확인한다. mock 시간=1이면 TTFT 0은 가능하고 TPS는 null이며 이것을 실제 성능 수치로 저장하지 않는다.

- [ ] **Step 2: RED를 확인한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark/cli.test.ts`

Expected: CLI export 누락으로 FAIL.

- [ ] **Step 3: CLI·preflight·원자료 저장을 구현한다.**

```json
{ "scripts": { "benchmark": "node --import tsx scripts/benchmark.ts" } }
```

기존 scripts를 보존하며 위 항목만 병합한다. `tsx`와 Vitest는 주 계획 테스트 환경을 재사용한다. CLI import가 호출을 실행하지 않도록 `pathToFileURL(resolve(process.argv[1])).href === import.meta.url`일 때만 main을 호출한다. 사용자 제공 output은 `artifacts/benchmarks/` 아래로 resolve하고 새 디렉터리만 `mkdir()`한다. output이 있으면 실패하며 덮어쓰거나 삭제하지 않는다.

실행 인자 형태는 다음 두 개뿐이며 알 수 없는 인자를 거부한다.

```powershell
npm.cmd run benchmark -- --config benchmarks/candidates.json --output artifacts/benchmarks/run-id
node --import tsx scripts/benchmark.ts --config benchmarks/candidates.json --output artifacts/benchmarks/run-id
```

실제 호출 전 모든 모델명을 resolve하고 합성 사례/조건/예상 호출 수/Cloud 전송 여부를 stdout에 출력한다. 후보 ID·source hash·backend 버전·Node/OS·설정·seed 지원 여부·GPU/CPU/RAM 설명·artifact identity·cold 확인 방법을 `manifest.json`에 저장한다. 환경변수 전체를 dump하지 않는다. `.env.local`의 암묵적 전체 로드는 하지 않으며 필요한 값만 실행 환경에서 읽는다.

각 candidate/case의 cold trial 직전에 `prepareCold(candidate)`를 호출한다. Ollama cold는 공식 unload 요청에 맞춰 `POST /api/generate`에 `{ model, keep_alive: 0 }`을 전송하고 정상 완료를 확인한다. 모델이 없으면 다운로드하지 않고 preflight 실패로 종료한다. llama.cpp 서버의 재시작과 GGUF 경로·SHA-256·template 확인은 실행자가 각 cold 전에 수행하고 기록한다. 자동으로 다른 프로세스를 종료하지 않으며, 재시작 확인 없는 cold는 unverified로 유지한다. [Ollama 공식 FAQ](https://docs.ollama.com/faq)의 unload/keep-alive 동작과 설치 버전을 실행 문서에서 대조한다.

llama.cpp의 concrete `prepareCold()`는 TTY에서 `node:readline/promises`로 해당 사례의 서버 재시작을 확인하고 입력이 `restarted`일 때만 `external-restart`를 반환한다. 그 외 입력과 non-TTY 실행은 `unverified`로 기록한다. 이것은 과금 승인 절차가 아니라 cold 측정 조건의 기록이며, API 호출 자체는 사용자가 실행한 CLI 범위에서 진행한다.

llama.cpp backend 비교 run에서는 optional q8 후보를 동일한 4b Q4_K_M의 `provider='llamacpp'` 항목으로 교체한 별도 config를 사용한다. 로컬 모델 설정 2개와 비교 backend 1개, Cloud 1개의 형태를 유지한다. 양쪽의 실제 GGUF·template SHA-256이 확인되지 않으면 해당 run도 backend 비교 불가로 보고한다.

각 trial은 `trials/<candidate>/<case>/<cold|warm>-<index>.json`으로 즉시 저장한다. 내용은 `RunResult`, model/provider, case source hash, 생성 조건, coldState, hardware samples, 시작 UTC 시간이다. 최종 실패율에 오류 trial도 포함한다. CLI timeout과 SIGINT는 실제 `GenerateInput.signal`에 전달한다. 오류 trial은 재호출하지 않으며, 마지막에 실패 건수와 보고서 경로를 출력한다. SIGINT는 종료 코드 130, config/preflight 오류는 1, 전체 정상 완료는 0으로 구분한다.

`docs/benchmarks.md`에는 loopback 서버 준비, 다운로드·서버 실행이 별도 수동 단계임, 외부 비용, 18회 Cloud 호출, cold 의미, 250ms sampling 한계, 측정 중 IDE/Docker/다른 GPU 작업 기록, run-id 중복 금지, 재평가 명령을 적는다. CPU offload·context·quant 변경은 새 run-id/manifest로 비교한다.

- [ ] **Step 4: GREEN과 안전한 도움말을 확인한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark/cli.test.ts`

Run: `npm.cmd run benchmark -- --help`

Expected: tests PASS, help는 호출·파일 생성 없이 명령/Cloud 비용 정보를 출력. 이 단계에서는 실제 benchmark 명령을 실행하지 않는다.

- [ ] **Step 5: 커밋한다.**

```powershell
git add scripts/benchmark.ts docs/benchmarks.md package.json .gitignore tests/unit/benchmark/cli.test.ts
git commit -m "feat: add explicit benchmark CLI and reproducible raw artifacts"
```

### Task B05: 설명 통계·blind 품질 평가·모델 선택 보고서를 완성한다

**Files:**

- Create: `src/server/benchmark/report.ts`
- Create: `benchmarks/quality-rubric.md`
- Modify: `scripts/benchmark.ts`의 완료 시 보고서 생성과 review 인자 처리
- Modify: `docs/benchmarks.md`의 수동 품질 평가 및 선택 기록 절차
- Test: `tests/unit/benchmark/report.test.ts`

**Interfaces:**

- Consumes: B01 `BenchmarkCandidate`, `BenchmarkCase`; B03 `RunResult`; B04의 manifest/trial JSON.
- Produces: `median(values: number[]): number | null`, `nearestRankP95(values: number[]): number | null`, `compareBackendIdentity(a: BackendIdentity, b: BackendIdentity): 'comparable' | 'incomparable'`, `buildBlindReview(runId: string, outputs: ReviewOutput[]): BlindReview`, `assessSelection(results: SelectionInput): SelectionVerdict`, `writeBenchmarkReport(runDir: string): Promise<void>`.
- `BackendIdentity`는 `ggufSha256`, `quantization`, `chatTemplateSha256`, `contextWindow`, `maxOutputTokens`, `temperature`, `seed`를 포함한다. null identity는 comparable이 아니다.
- `ReviewOutput={trialId:string; candidateId:string; caseId:string; output:string; invariants:string[]}`; `BlindReview={items:{reviewId:string;caseId:string;output:string;invariants:string[];scores:QualityScores;fatalViolations:string[];reviewed:boolean}[]; key:Record<string,string>}`.
- `QualityScores`는 아래 8개 영문 key를 가지며 값은 `1 | 2 | 3 | 4 | 5 | null`이다. `SelectionInput`은 사례별 warm `RunResult[]`와 사람이 완료한 `BlindReview`를 가진다. `SelectionVerdict={status:'provisional-pass'|'fail'|'insufficient-data';reasons:string[]}`.

```ts
export interface BackendIdentity {
  ggufSha256: string | null;
  quantization: string | null;
  chatTemplateSha256: string | null;
  contextWindow: number;
  maxOutputTokens: number;
  temperature: number;
  seed: number | null;
}
export type QualityDimension = 'characterConsistency' | 'worldCompliance' |
  'styleStability' | 'repetition' | 'sceneContinuity' | 'longMemory' |
  'instructionFollowing' | 'editReadiness';
export type QualityScores = Record<QualityDimension, 1 | 2 | 3 | 4 | 5 | null>;
export interface SelectionInput {
  cases: Partial<Record<BenchmarkCase['id'], RunResult[]>>;
  review: BlindReview;
}
export interface SelectionVerdict {
  status: 'provisional-pass' | 'fail' | 'insufficient-data';
  reasons: string[];
}
```

`assessSelection()`에는 한 후보의 세 사례와 해당 후보의 review 항목 세 개만 전달한다. 호출자는 manifest와 review-key를 사용하여 후보를 묶는다. 세 출력의 24개 점수가 모두 3점 이상이고 24개 전체 평균이 3.5 이상이어야 임시 품질 기준을 통과한다.

- [ ] **Step 1: 통계와 허위 통과를 막는 테스트를 쓴다.**

```ts
import { expect, it } from 'vitest';
import { median, nearestRankP95, compareBackendIdentity, assessSelection }
  from '../../../src/server/benchmark/report';

it('reports nearest-rank p95 for n=5 and keeps missing data unavailable', () => {
  expect(median([500, 100, 300, 200, 400])).toBe(300);
  expect(nearestRankP95([500, 100, 300, 200, 400])).toBe(500);
  expect(median([])).toBeNull();
  expect(nearestRankP95([])).toBeNull();
});
it('never claims backend comparability from two unknown hashes', () => {
  const identity = { ggufSha256: null, quantization: 'Q4_K_M', chatTemplateSha256: null,
    contextWindow: 8192, maxOutputTokens: 512, temperature: 0.7, seed: 42 };
  expect(compareBackendIdentity(identity, identity)).toBe('incomparable');
});
it('does not select a model before any human review or complete measurements', () => {
  expect(assessSelection({ cases: {}, review: { items: [], key: {} } }).status)
    .toBe('insufficient-data');
});
```

추가 테스트: reviewed=false나 null 점수로 통과 불가; 4/5 성공으로 통과 불가; fatal 1개는 고득점이어도 fail; decodeMs=null이면 insufficient-data; blind items에 model/candidateId가 없고 별도 key에만 trialId 존재; invalid 숫자와 중복 reviewer 항목은 거부.

- [ ] **Step 2: RED를 확인한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark/report.test.ts`

Expected: 보고서 export 누락으로 FAIL.

- [ ] **Step 3: 통계·평가 양식·선택 보고서를 구현한다.**

```ts
export function nearestRankP95(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.ceil(0.95 * sorted.length) - 1];
}
export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}
```

집계 입력에서는 finite이며 0 이상인 값만 인정한다. invalid raw counter는 원자료의 오류로 명시하고 몰래 0으로 대체하지 않는다. `summary.json`과 `report.md`는 candidate/case별 성공·실패·취소·절단 횟수, warm n, TTFT median/p95, generationMs median/p95, providerTps/endToEndTps와 각각의 유효 n, prompt/completion tokens, baseline/관측 GPU·RAM 최고 표본을 포함한다. cold는 별도 행으로 두며 warm 통계에 섞지 않는다.

`buildBlindReview()`는 사례별 warm-1 출력을 선택한다. `sha256(runId + ':' + trialId)`로 reviewId를 만들고 hash 순서로 정렬한다. `review.json`에는 모델명과 timing을 빼고 `review-key.json`에만 연결한다. 각 점수는 null, reviewed=false, fatalViolations=[]로 시작한다. 파일을 자동 작성했다고 평가가 완료된 것으로 간주하지 않는다.

| 품질 차원 / key | 1점 | 2점 | 3점 | 4점 | 5점 |
|---|---|---|---|---|---|
| 캐릭터 일관성 / `characterConsistency` | 핵심 인물 사실 파괴 | 성격·상태 오류 여러 개 | 작은 불일치 1개, 수정 가능 | 핵심·상태 모두 유지 | 사실 유지와 설득력 있는 반응 모두 우수 |
| 설정 준수 / `worldCompliance` | 불가능 규칙 위반 | 핵심 설정 오해 | 사소한 설정 오류 1개 | 모든 명시 설정 준수 | 설정 제약이 장면의 원인으로 자연스럽게 작용 |
| 문체 안정성 / `styleStability` | 시점·언어 붕괴 | 문체 급변 반복 | 대체로 일관, 거친 부분 있음 | 시점·어조 안정 | 안정된 어조와 장면에 맞는 리듬 |
| 반복 표현 빈도 / `repetition` | 같은 구절·전개 반복 지배 | 반복 여러 개로 읽기 방해 | 반복 1~2개, 수정 가능 | 눈에 띄는 기계적 반복 없음 | 어휘·문장·전개가 다양하고 자연스러움 |
| 장면 연결성 / `sceneContinuity` | 직전 상황과 단절 | 위치·행동 연결 오류 여러 개 | 연결되지만 전환 설명 부족 | 직전 장면에서 매끄럽게 연결 | 인과·공간·정서 연결이 모두 명확 |
| 장기 문맥 유지 / `longMemory` | 과거 핵심 사건 뒤집음 | 기억 누락이 전개를 손상 | 중요한 사실 대부분 유지 | 과거와 최신 상태 모두 유지 | 오래된 사실과 최신 변경을 함께 정확히 사용 |
| 사용자 지시 준수 / `instructionFollowing` | 핵심 요청 무시 | 금지/필수 조건 여러 개 누락 | 핵심 수행, 작은 누락 있음 | 모든 명시 요청 수행 | 요청을 정확히 수행하며 서사도 자연스러움 |
| 수정 필요 정도 / `editReadiness` | 전면 재작성 | 절반 이상 재작성 | 몇 문단 수정 후 사용 가능 | 문장 수준 수정만 필요 | 최소 교정으로 사용 가능 |

`longMemory`도 initial/continuation에서 제공된 사실 유지 정도로 평가하고, long-memory 사례를 별도로 반드시 보고한다. fatal은 worldCompliance 또는 characterConsistency 저점과 중복 기록할 수 있으며 삭제하지 않는다. 사람이 스승의 생환, 청각 회복 등 확정 사실 위반을 출력 인용과 함께 기록한다. 단순히 사망자를 회상하거나 기록에서 인용한 것은 부활 위반이 아니다.

점수 입력 후 다음 명령으로 **기존 결과만** 다시 집계할 수 있게 CLI에 mode를 추가한다.

```powershell
npm.cmd run benchmark -- --review artifacts/benchmarks/run-id
```

이때 parseBenchmarkArgs의 반환 타입을 `{ mode:'run'; config:string; output:string } | { mode:'review'; directory:string }`로 좁혀 B04의 테스트도 함께 갱신한다. review mode는 Provider factory를 생성하지 않고 `writeBenchmarkReport()`만 호출한다. reviewer가 수정하는 파일은 `review.json`이며 원래 trial JSON과 manifest는 바꾸지 않는다.

선택 보고서에는 임시 기준의 결과와 근거를 쓰되 모델 자동 지정은 하지 않는다. 표본이 없으면 표에 `미측정`/`null`, 평가 전이면 `미평가`, 정보가 다르면 `backend 비교 불가`로 표시한다. 모든 로컬 후보가 기준을 못 맞추면 context/quant/offload·동시 작업·RAM 병목 중 실제 측정이 보여 준 항목을 하나씩 바꿔 새 run-id로 재실행하도록 안내한다. Cloud로 조용히 대체하거나 8GB 적재 가능을 선언하지 않는다.

- [ ] **Step 4: 전체 harness GREEN 및 타입 검사를 확인한다.**

Run: `npm.cmd exec vitest -- run tests/unit/benchmark`

Run: `npm.cmd run typecheck`

Expected: GPU·다운로드·외부 API 없이 B01~B05 테스트 PASS. 생성한 fake 결과는 테스트 임시 디렉터리에서만 사용하고 공개 성능 결과로 옮기지 않는다.

- [ ] **Step 5: 커밋하고 주 계획의 모델 선택 gate에 연결한다.**

```powershell
git add src/server/benchmark/report.ts benchmarks/quality-rubric.md scripts/benchmark.ts docs/benchmarks.md tests/unit/benchmark/report.test.ts tests/unit/benchmark/cli.test.ts
git commit -m "feat: report benchmark statistics and blinded model selection evidence"
```

## 완료 조건과 실행 인계

- B01~B05 harness 및 타입 검사 PASS는 benchmark 도구 구현 완료를 뜻한다. 모델 성능 검증 완료를 뜻하지 않는다.
- 실제 측정은 실행자가 로컬 모델·서버를 준비하고 GEMINI_MODEL 및 필요한 자격증명을 정한 다음 명시적으로 benchmark 명령을 실행하는 별도 단계다.
- 실제 결과와 blind human 평가가 모두 있어야 주 계획의 기본 로컬 모델·backend·context·경량 모델 역할을 확정한다. 결과가 기준을 못 맞추면 제약/기준 조정 근거를 ADR로 남긴다.
- 공개할 때 합성 입력, 설치 버전, 실행 설정, 유효 n, missing counters, sampling 한계, Cloud 모델명, 측정 날짜를 함께 제공한다. API key, raw 환경변수, 실제 사용자 원문은 포함하지 않는다.
- 다음 실행 선택지는 주 계획 전체와 함께 결정한다: `superpowers:subagent-driven-development`로 작업별 실행하거나 `superpowers:executing-plans`로 동일 세션에서 순서대로 실행한다. 이 문서 작성 요청은 추론/다운로드/Cloud 과금 실행 권한으로 확대하지 않는다.
