# ChatGPT 구독 사용량 연동 검토

검토일: 2026-10-30 · 대상: Ghostwriter v2 개인 로컬 실행 · 상태: **설계 검토, 미구현**

## 1. 결론

**개인 로컬용이라면 공식 `Sign in with ChatGPT / ChatGPT plan usage` 연동을 조건부로 추진할 수 있다.**
현재 FastAPI의 HTTP/SSE 처리를 활용하는 직접 Responses 연동을 우선 후보로 권한다.
다만 실제 계정의 자격·모델 접근·메모리 추출을 확인하기 전에는 구현 가능성 검토와 동작 검증을 구분해야 한다.

- 현재 `OPENAI_API_KEY` 경로는 API 과금이다. Codex 구독 잔여량을 자동으로 사용하는 구현이 아니다.
- 구독 연동은 앱이 별도 OAuth 동의를 받아 적격 요청에 ChatGPT 플랜을 사용하는 공식 경로다.
  기존 Codex 인증 파일을 복사하거나 남는 토큰을 API 크레딧으로 변환하는 방식이 아니다.
- 공식 안내는 적격 Plus·Pro 계정과 개인 로컬 프로젝트 등을 대상으로 한다. 유료·원격 제공은 별도 접근 신청이 필요하다.
- **계정에 실제 남은 이용량, 이 앱에 부여되는 한도, 원하는 모델의 접근 가능 여부는 확인하지 않았다.**
  이전 대화의 “Codex 쪽 토큰 사용”은 정확히는 “승인된 앱에서 ChatGPT 구독 사용량 활용”으로 표현한다.

근거: [Codex 인증·과금](https://learn.chatgpt.com/docs/auth),
[공식 로컬 앱 연동 안내](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt),
[ChatGPT plan usage 개요](https://developers.openai.com/siwc/token-sharing-open-source).

## 2. 비용과 연동 방식 비교

아래의 구현 부담과 우선순위는 현재 저장소를 기준으로 한 판단이다.

| 방식 | 사용량·비용 경로 | 현재 앱과의 관계 | 판단 |
|---|---|---|---|
| 현재 OpenAI API 키 | 연결된 API 조직의 크레딧·과금 | 이미 구현됨 | API 비용을 허용할 때 사용 |
| 구독 혜택의 API 크레딧 | 적격 Pro·Business가 별도 신청하는 API 크레딧 | 인증 구조를 유지할 수 있음 | 해당 혜택이 있으면 먼저 확인할 대안 |
| ChatGPT plan usage 직접 연동 | OAuth로 승인한 ChatGPT 플랜·허용 크레딧 | 인증·요청 형식·오류 처리 추가 필요 | 남는 구독 이용량 활용이라는 목적에 가장 적합 |
| Codex app-server 연동 | 앱이 취득한 구독 OAuth 토큰으로 Responses 호출 | 별도 프로세스·RPC·갱신 시 재시작 관리 필요 | 현재 소설 생성 기능에는 직접 HTTP보다 부담이 큼 |

API 크레딧 혜택은 Codex 잔여량 이전과 다르다. 공식 문서상 유료 Pro·Business 유지 7일 이상,
구독과 API 조직의 소유자 조건, 같은 이메일 등의 자격과 활성화 절차가 있다. 이 문서는 사용자의 자격이나
신청 완료를 확인하지 않았다. 혜택은 Responses API를 포함하지만 API 키로 쓰는 Codex 이용에는 적용되지 않는다.
[API 크레딧 혜택](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)

구독 경로도 무제한·추가 비용 없음으로 표시하면 안 된다. 플랜·앱별 설정과 크레딧 사용 허용 여부를 확인해야 한다.
Plus의 5시간 한도는 해당 플랜을 쓰는 앱 사이에 공유되며 앱마다 별도 한도가 생기는 것은 아니다.
Pro에는 이 5시간 한도가 적용되지 않는다고 명시되어 있지만, 이를 모든 제약이 없는 것으로 해석하지 않는다.
[계정·사용량 관리](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)

## 3. 현재 코드와의 차이

공식 구독 경로는 `POST https://api.openai.com/v1/responses`와 OAuth access token을 사용한다.
모델 목록도 같은 계정의 `GET /v1/models`로 확인한다. 문서의 예시 모델명을 기본 모델로 채택하거나
목록 조회만으로 추론 성공을 판정하지 않는다. 실제 완료 이벤트까지 받아야 한다.
[모델·추론 규격](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)

| 항목 | 확인한 현재 코드 | 구독 경로 검토 결과 |
|---|---|---|
| 설정·공급자 | [config.py](../backend/app/config.py)의 API 키·모델 설정, [generation.py](../backend/app/generation.py)의 `_provider()` | 구독 자격 증명과 API 키를 별도 관리. 구독 경로가 API 키를 요구하지 않도록 분기 필요 |
| 요청·동의 | [models.py](../backend/app/models.py)의 `GenerateRequest`는 `ollama/openai`, `allow_cloud` 사용 | 구독 경로를 식별할 값 추가 필요. OAuth 동의와 이번 원고의 외부 전송 동의를 각각 검사 |
| 요청 본문 | [providers.py](../backend/app/providers.py)의 OpenAI 경로는 `stream:true`, `store:false`, `max_output_tokens` 전송 | 앞의 두 설정은 맞지만 `max_output_tokens`는 구독 경로에서 제외해야 함 |
| 지시 메시지 | [context.py](../backend/app/context.py)와 [memory.py](../backend/app/memory.py)가 `role:system` 구성 | 구독 전송 경계에서 `instructions` 또는 developer 메시지로 변환. 로컬 프롬프트까지 일괄 변경할 필요 없음 |
| 상태 추출 | `finalize_memory()`는 본문과 같은 공급자·모델로 JSON Schema 추출 | 본문 성공만으로 적용 완료 판정 불가. 메모리 추출과 Pydantic 검증까지 확인 필요 |
| 오류 | 공급자 HTTP 429는 `RATE_LIMITED`, SSE `response.failed`는 `INVALID_OUTPUT`로 처리 | 구독 한도·권한·일시 장애를 구분할 오류 매핑 필요 |
| 로그인 | [main.py](../backend/app/main.py)의 앱 암호·세션 쿠키·owner 식별 | 앱 로그인은 유지하고 ChatGPT 계정 연결을 별도로 추가. ChatGPT 계정 변경으로 원고 소유자를 바꾸지 않음 |
| 화면·기록 | [작품 화면](../src/app/projects/%5Bid%5D/page.tsx)의 모델 선택, [api.ts](../src/features/stories/api.ts)의 오류 문구, 생성 metrics의 provider | 구독/API 과금 경로를 화면·작업 기록에서 구별. 한도 오류를 단순 “잠시 후 재시도”로 안내하지 않음 |

구독 프리뷰에서는 `max_output_tokens`, `temperature`, `top_p` 등이 지원되지 않으며
system 지시는 `instructions` 또는 developer 메시지로 전달해야 한다. HTTP 요청은 필요한 문맥을 매번 보내고
`store:false`, `stream:true`를 사용한다. 현재 본문과 메모리 요청 모두에 이 어댑터가 필요하다.
[프리뷰 제약](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)

**출력 상한이 달라진다.** 현재 `max_output_tokens=1024`에 의존하는 공급자 출력 제한을 그대로 보장할 수 없다.
설계상 분량 지시와 기존 시간 제한을 유지하고, 앱의 수신량 제한을 별도로 정해야 한다. 제한 도달 시 스트림을
닫고 초안으로 보존하며 자동 확정하지 않는 것이 제안이다. 클라이언트 취소로 공급자 측 생성·사용량까지
즉시 중단되는지는 미검증이다. “1024 tokens 제한 유지”로 문서화해서는 안 된다.

`text.format` JSON Schema와 현재 `MemoryUpdate` 스키마의 실제 호환성은 확인하지 않았다.
지원되지 않는 항목 목록에 없다는 이유만으로 지원을 보증하지 않는다. 본문 성공 후 메모리만 실패해도
현재 구조상 회차가 확정되지 않으므로 중요한 도입 조건이다. 임의 JSON 정리나 무제한 재시도로 통과시키지 않는다.

## 4. 제안하는 최소 구현 범위

이 절은 구현 제안이며 현재 반영된 기능이 아니다.

1. 기존 Ollama·API 키 경로를 유지하고, 명시적으로 선택하는 `chatgpt_plan` 경로를 추가한다.
   공급자 생성은 허용한 식별자만 처리하도록 제한한다. 현재 `_provider()`의 비-Ollama 분기를 그대로 확장하지 않는다.
2. ChatGPT 계정 연결·해제와 활성 계정 표시를 추가한다. 초기 구현은 단일 로컬 운영자 대상으로 하되,
   연결 기록은 검증된 계정 식별자와 발급 client ID별로 분리해 다른 계정의 자격 증명을 덮어쓰지 않는다.
3. 인증된 로컬 요청으로 연결을 시작하고, 일회성 loopback callback을 사용한다. 작품·회차 저장 스키마를
   OAuth 자격 증명 저장소로 사용하지 않는다.
4. FastAPI의 기존 HTTP/SSE 처리를 활용하되, 구독용 요청·인증·오류 변환은 별도 경계로 둔다.
   모델은 서버가 조회·검증한 계정별 선택지에서 정한다. 기존 `OPENAI_MODEL`과 같은 모델이라고 가정하지 않는다.
5. 본문과 메모리 추출을 모두 구독 경로로 검증한다. 요청에는 전송된 원고·문체 참고·세계관·인물·요약이 포함될 수
   있음을 안내하고, 기존 요청별 외부 전송 동의를 유지한다.
6. 공급자·계정 연결 식별자·모델 선택을 작업에 고정한다. 재시도나 저장된 본문의 메모리 재처리 시
   활성 계정이 바뀌었다고 다른 계정·과금 경로를 조용히 사용하지 않는다.
7. 한도 초과·접근 거부 시 중단하고 초안을 유지한다. **유료 API로 자동 전환하지 않는다.**
   Ollama를 선택한 요청에서도 명시되지 않은 구독 또는 API fallback을 추가하지 않는다.

Codex app-server를 이용하더라도 OAuth 취득·갱신은 앱의 책임이고, 제공된 env-key 구성에서는 갱신 토큰을
반영하기 위해 프로세스를 재시작해야 한다. 현재 앱은 에이전트의 파일 편집·도구 실행을 필요로 하지 않으므로,
직접 HTTP 경로부터 검증하는 것이 변경 범위를 줄인다는 판단이다.
[공식 app-server 연동](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)

## 5. 인증·저장·화면 설계 조건

- 설치별로 안정적인 `ext_agent_host_id`를 만든다. 최초 등록에서 반환된 client ID를 계정 연결 기록에 저장하고
  재인증에 재사용한다. `state`·`nonce`·PKCE를 요청마다 생성·검증하고 ID token의 서명·issuer·audience·만료를 검증한다.
- callback은 공식 규격의 `127.0.0.1` loopback을 사용한다. 현재 UI의 `localhost`와 혼동하지 않는다.
  앱 Origin/세션 검사를 전체 해제하지 않고, callback만 보류 중인 인증 요청에 묶어 처리한다.
- 신원 로그인 성공과 구독 사용 허가는 다르다. 반환 scope에 `chatgpt.tokens.use.direct`가 없으면 추론을 시작하지 않는다.
  [등록·인증 규격](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- Windows에서는 OS 보호 저장소 또는 DPAPI로 보호한 앱 전용 저장소를 후보로 삼는다. 구체 라이브러리는 미선정이다.
  access/refresh/ID token은 브라우저 저장소·프런트 응답·Git·로그에 넣지 않는다. 갱신은 계정 연결별 직렬화하고,
  교체되는 refresh token과 access token을 함께 원자적으로 저장한다.
- 앱 로그아웃과 ChatGPT 연결 해제를 구별한다. 해제 시 원격 세션 폐기를 시도하고 로컬 토큰을 제거한다.
  네트워크 실패로 원격 폐기를 확인하지 못했으면 그 사실을 표시한다.
  [계정·세션 관리](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- 설정에는 공식 **Continue with ChatGPT** 연결 버튼, 작품 화면에는 “ChatGPT 구독 사용 중”과 활성 연결을 표시한다.
  최초 승인 후 안내와 [사용량 관리](https://chatgpt.com/settings/usage) 링크를 제공한다. 공식 한도 정보를 얻지 않은 상태에서
  “남은 토큰”이나 초기화 시각을 계산해 표시하지 않는다.
  [공식 UI/UX 지침](https://developers.openai.com/siwc/ui-ux-guidelines)

## 6. 실패 처리와 저장 원칙

아래 대응은 공식 오류 의미에 현재 앱의 초안·원자 저장 원칙을 적용한 제안이다.

| 상황 | 처리 제안 |
|---|---|
| 동의 거부·필수 scope 누락 | 연결 상태를 구별해 표시하고 구독 추론 금지 |
| `subscription_sharing_user_not_eligible` / 403 | 계정·워크스페이스 제한 안내, 동일 요청 자동 재시도 금지 |
| `subscription_sharing_usage_limit_exceeded` / 429 | 구독 요청 중단, 사용량 관리 연결. 전체 플랜 소진이나 초기화 시각을 추정하지 않음 |
| `subscription_sharing_usage_unavailable` / 503 | 자격 증명 유지, 제한된 backoff. 유료 경로 전환 금지 |
| `subscription_sharing_unsupported_capability` / 400 | 요청·모델 지원 범위 확인. 같은 잘못된 본문 재전송 금지 |
| 인증 만료·해제 확정 | 갱신 또는 재연결 안내. 앱 자체 로그아웃과 혼동하지 않음 |
| SSE 중간 실패·중단·완료 이벤트 누락 | `response.completed`로 성공 판정하지 않고 초안 유지 |
| 본문 완료 후 메모리 실패 | 회차 자동 확정 금지, 저장된 본문으로 메모리 재처리 가능 상태 유지 |

HTTP 상태만으로 모든 실패를 분류하지 않는다. SSE `response.failed` 안의 코드도 읽어야 한다.
진단에는 허용된 오류 코드와 공급자 request ID를 남기되, 응답 본문·원고·자격 증명 전체를 로그에 복사하지 않는다.
[공식 오류·복구 규격](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)

## 7. 구현 전후 검증 조건

아래 항목은 **앞으로 수행할 검증 계획**이다. 이번 검토에서 실행하거나 통과한 테스트가 아니다.

| 단계 | 완료 조건 |
|---|---|
| 계정·제공 범위 확인 | 실제 플랜·워크스페이스·개인 로컬 사용 범위와 동의 화면에서 자격 확인 |
| 최소 인증·추론 | 새 등록, 기존 연결 재인증, scope 거부, 갱신·해제 검증. 합성 짧은 입력의 `response.completed` 수신 |
| 요청 어댑터 단위 검증 | 본문·메모리 모두 system 변환, 금지 파라미터 제외. 기존 API 키·Ollama 요청은 기존 계약 유지 |
| 오류·초안 회귀 | 403·429·503 및 SSE 중간 한도 오류에서 초안 보존·미확정·과금 경로 미전환 확인 |
| 저장·재처리 | JSON Schema 추출·Pydantic 검증·원자 저장·재조회 일치. 계정 전환 후에도 다른 연결로 묵시 재처리 금지 |
| 실측 품질 | 기존 로컬 비교의 합성 장면으로 비교한 뒤 미사용 장면 추가. 분량·문체 예시 혼입·반복·수정 필요도 평가 |
| 사용량·운영 | 실제 사용 내역에서 경로 확인. 취소·상한·토큰 갱신·재시작 동작과 계정 한도 영향 기록 |

실제 사용자 원고나 API 키 없이 먼저 fixture·합성 자료로 검증한다. 계정 로그인과 실제 구독 요청은
별도 실행 단계로 둔다. 기존 [로컬 품질 검증 결과](local-quality-validation-2026-10-30.md)의 테스트 수나
모델 결과를 이 연동의 검증 실적으로 재사용하지 않는다.

## 8. 권고와 이번 작업의 경계

**권고: 계정 자격 확인 → 구독 직접 연동의 최소 추론 → 메모리 추출 검증 → 앱 통합 → 품질 비교 순서.**
API 크레딧 혜택이 실제 활성화 가능하고 변경 최소화가 우선이면 기존 API 경로도 대안이다.
선택한 계정에서 구독 직접 호출이 허용되지 않거나 메모리 추출이 호환되지 않으면 앱 통합부터 진행하지 않는다.

이번에는 공식 문서 조회와 현재 코드 대조, 검토 문서 작성만 수행했다. 코드·설정·계정 연결을 변경하지 않았고,
OAuth 로그인·토큰 발급/조회·모델 목록 API·실제 추론·크레딧 신청을 하지 않았다. 따라서 실제 과금·접근 가능성·
출력 품질·취소 효과는 미검증이다. 공식 문서는 검토일 기준이며 구현 시 다시 확인해야 한다.
