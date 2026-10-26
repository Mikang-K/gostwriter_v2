# Ghostwriter v2 구현 체크리스트

작성일: 2026-10-26. 이 문서는 구현 진행과 검증 증거를 관리한다.

- 기준: [기술 방향서](../../../ghostwriter_v2_technical_direction.md)
- 실행 작업: [구현 계획](2026-10-26-ghostwriter-v2.md)
- 모델 비교: [벤치마크 구현 계획](2026-10-26-ghostwriter-v2-benchmark.md)

## 현재 상태와 기록 원칙

현재 산출물은 계획 문서다. 애플리케이션 구현, 자동 테스트, 모델 호출, 하드웨어 측정, Docker 실행을 완료했다는 뜻이 아니다.

`[x]`는 해당 항목의 증거가 확보된 경우에만 사용한다. 계획 검토의 완료와 구현 작업의 완료는 구분한다. 작업을 완료하면 증거 칸에 실행일, commit, 환경, 명령, 결과 파일을 기록한다. 실행하지 못한 항목은 사유와 남은 조건을 적고 미완료로 유지한다.

별도 Supabase 프로젝트와 Next.js Node API는 구현 계획의 기본안이다. 방향서가 이미 확정한 기술 선택으로 표시하지 않는다. Local-first는 추론 우선 정책이며 hosted DB에 작품이 저장되는 경로를 숨기지 않는다. 정확한 Gemma 모델, 양자화, Backend, 성능 기준의 채택은 실측 이후 결정한다.

## 계획 검토 기록

- [x] 기술 방향서 §1~16을 읽고 아래 요구사항 추적표로 연결했다.
- [x] v1의 프런트엔드 API 호출, 직접 DB 접근, 생성 페이지 누락, 수동 테스트 범위를 정적으로 확인했다.
- [x] 주 계획의 stream·취소·Undo·재시도 계약을 읽고 모순 가능성을 검토 항목으로 등록했다.
- [x] T01~T16과 B01~B05의 구현 완료 표시를 모두 미완료로 유지했다.
- [x] 아래 계약 검토 항목의 수정 내용을 주 계획·벤치마크 계획과 최종 대조했다.
- [x] 상대 링크 9개, code fence, JSON 예시, UTF-8, 작업 21개·요구사항 32개·방향서 §1~16 연결을 정적으로 점검했다.

이 기록은 문서 검토 결과다. v1의 실제 실행 상태, 원격 DB/RLS, 현재 GPU 상태, 유료 Cloud 호출은 검증하지 않았다.

## 작업 진행과 증거

표의 상태는 구현 작업의 상태다. 세부 단계의 RED → 구현 → GREEN → 리뷰는 연결된 계획에서 추적한다. 번호보다 주 계획의 의존 순서를 우선하며 T10 adapter는 T08 통합 이전, B03은 T14 지표 helper 이후에 실행한다.

| 상태 | 작업 | 완료 시 남길 증거 | 현재 증거 |
|---|---|---|---|
| [ ] | T01 실행 골격·검증 명령 | runtime·lockfile, typecheck/lint/unit/build 결과 | 미실행 |
| [ ] | T02 초기 DB·RLS·baseline | 빈 DB migration·seed, TAP 판정, 사용자 격리 결과 | 미실행 |
| [ ] | T03 인증·작품 API | 401/403/404, 입력 상한, 모델 없는 CRUD 결과 | 미실행 |
| [ ] | T04 Provider 계약·Fake | finish/empty/blocked/abort 계약 테스트 | 미실행 |
| [ ] | T05 Context Builder | 필수 정보 보존·예산 초과·페이싱 테스트, ADR | 미실행 |
| [ ] | T06 Story Memory 추출 | strict schema·인물 ID·불변 정보·추출 실패 테스트 | 미실행 |
| [ ] | T07 예약·원자 commit·멱등성 | 동시 요청·rollback·재전송·epoch 회귀 결과 | 미실행 |
| [ ] | T08 SSE·취소·상태 복구 | 실제 HTTP flush·socket 종료·취소 경합 결과 | 미실행 |
| [ ] | T09 생성 UI·draft | UTF-8 parser, IME, 중복 클릭, reload E2E 결과 | 미실행 |
| [ ] | T10 Ollama | NDJSON/usage 테스트와 명시적 Local smoke 결과 | 미실행 |
| [ ] | T11 Timeout·Retry | fake timer, 첫 delta 이후 재생성 금지, deadline 결과 | 미실행 |
| [ ] | T12 Gemini·Fallback | 동의 없는 외부 호출 0, 오류·전환 fixture 결과 | 미실행 |
| [ ] | T13 작가 설정·완결·Undo | 첫 회차·에필로그·작가 수정 보존 DB/E2E 결과 | 미실행 |
| [ ] | T14 지표·로그·운영 화면 | TTFT/TPS 정의 검증, secret sentinel, 사용자 격리 | 미실행 |
| [ ] | T15 경계 통합·보안 회귀 | 전체 검증 결과와 실행 환경, 남은 수동 검증 한계 | 미실행 |
| [ ] | T16 Docker·문서·공개 산출물 | image/compose/smoke, 실행 문서, 공개 자료 점검 | 미실행 |
| [ ] | B01 후보·고정 입력 | 후보 설정 검증, 한국어 입력 세트·hash | 미실행 |
| [ ] | B02 llama.cpp 비교 어댑터 | 공통 Provider 계약·분할 SSE fixture 결과 | 미실행 |
| [ ] | B03 계측·하드웨어 수집 | 가짜 시간 검증, RAM/VRAM 표본·누락 표시 | 미실행 |
| [ ] | B04 명시적 비교 CLI | run별 manifest·trial JSON·출력, Cloud 명시 실행 기록 | 미실행 |
| [ ] | B05 통계·품질·모델 선택 | blind 품질표, 비교 보고서, 선택 ADR·채택 근거 | 미실행 |

## 요구사항 추적표

모든 행은 구현 또는 산출물의 검증 대상을 뜻한다. 작업 연결만으로 요구사항이 통과한 것은 아니다.

| ID | 방향서 | 요구사항 | 작업 | 판정 증거 |
|---|---|---|---|---|
| R01 | §1 | 첫 응답·복구·교체·문맥·운영을 개선한다 | T04~T15 | 통합 시나리오와 지표 비교 |
| R02 | §1.1 | 단일 API 전체 응답에서 공통 Provider 스트림으로 전환한다 | T04, T07~T12 | 동일 계약의 Fake/Local/Cloud 결과 |
| R03 | §2.1 | 생성·이어쓰기·중단·복구의 최소 사용 흐름을 완성한다 | T03, T07~T09, T13 | 로그인부터 확정 회차 재조회까지 E2E |
| R04 | §2.2 | 최대 모델·최대 context를 선행 조건으로 삼지 않는다 | T05, B01, B05 | 예산 설정과 실측 선택 근거 |
| R05 | §3 | RTX 4060 Ti 8GB에서 실사용 가능한 후보를 평가한다 | B03~B05 | VRAM·OOM·속도·품질 원자료 |
| R06 | §3 | RAM 16GB와 동시 실행 자원 제약을 기록한다 | T16, B03~B05 | RAM peak·실행 프로세스·환경 manifest |
| R07 | §4 | 화면·API·Orchestrator·Context·Gateway의 책임을 나눈다 | T01, T03~T08 | 파일 책임과 호출 경계 검토 |
| R08 | §4.1 | generate 계약·stream·오류·모델 metadata를 통일한다 | T04, T10, T12, B02 | adapter 계약 테스트 |
| R09 | §5 | Local-first이며 모델은 교체할 수 있다 | T04, T10, B05 | 기본 설정과 후보 교체 실행 |
| R10 | §5 | Cloud 선택·비교·장애 대체 경로를 유지한다 | T12, B04 | 명시 동의와 전환 시나리오 |
| R11 | §5.1 | 본문과 요약·상태 추출의 역할을 분리한다 | T06, T07, B05 | 분리된 task 기록과 모델 분리 채택 근거 |
| R12 | §6 | 전체 완료 전에 첫 본문을 화면에 표시한다 | T08~T10 | HTTP flush·브라우저 첫 delta 증거 |
| R13 | §6.1 | TTFT·latency·TPS·token·오류·retry를 측정한다 | T14, B03~B05 | 정의별 계산 테스트와 원자료 |
| R14 | §6.2 | keep-alive·context·양자화·중복 호출을 비교한다 | T05, T07, T10, B01~B05 | cold/warm 구분과 동일 조건 비교 |
| R15 | §7 | 세계관·인물·plot·복선·요약·최근 원문을 관리한다 | T02, T05, T06 | DB snapshot·Memory schema·선택 context |
| R16 | §7.1 | 핵심 규칙과 현재 인물 상태를 보존한다 | T05, T06, T13 | 예산 초과 거부·불변 정보·Undo 테스트 |
| R17 | §7.1 | 최근 원문·옛 요약·관련 정보의 선택을 추적한다 | T05, T14 | included/dropped ID·token 추정 기록 |
| R18 | §8 | deadline과 제한된 재시도로 무한 대기를 막는다 | T11 | timeout·Retry-After·논리 호출별 상한 테스트 |
| R19 | §8 | 사용자 취소를 서버와 upstream까지 전파한다 | T07~T09, T11 | 연결 종료·취소/commit 경합·socket 검증 |
| R20 | §8 | 설정 가능한 Fallback과 request ID 추적을 제공한다 | T08, T12, T14 | API·attempt·DB 기록 연결과 동의 검사 |
| R21 | §9 | Local 후보 2~3개와 Cloud 1개를 동일 입력으로 비교한다 | B01, B04, B05 | case hash·실행 조건·누락 없는 비교표 |
| R22 | §9.1 | 8개 소설 품질 기준으로 평가한다 | B01, B05 | blind 평가표·평가자·수정 필요도 |
| R23 | §10 | Ollama와 llama.cpp를 우선 비교하고 vLLM은 후속으로 둔다 | T10, B02~B05 | 같은 모델 비교와 Backend 선택 ADR |
| R24 | §11 | 로컬 포트를 보호하고 key·secret을 저장소와 분리한다 | T01, T10, T15, T16 | bind·image·로그·접근 경계 검증 |
| R25 | §11 | 본문 로그를 제한하고 Cloud 전송 범위를 알린다 | T12, T14~T16 | UI 안내·sentinel·외부 호출 0 테스트 |
| R26 | §12 | 분석부터 Packaging까지 단계별 의존성을 지킨다 | T01~T16, B01~B05 | 작업별 증거와 선행 gate 기록 |
| R27 | §13 | v1을 보존하고 재설계 이유를 설명한다 | T01, T16 | 분리된 저장소·v1/v2 비교 문서 |
| R28 | §13.1 | README·구조도·벤치마크·ADR·Docker·샘플을 공개한다 | T16, B05 | 민감정보 점검을 거친 공개 산출물 |
| R29 | §14 | 모델·양자화·Backend·RAM 변경은 실측으로 확정한다 | B03~B05 | 자원·품질·속도와 선택/보류 사유 |
| R30 | §14 | SSE와 Memory 저장 구조의 가정을 검증한다 | T02, T08, T09, T16 | DB 계약·생성/취소·배포 stream 결과 |
| R31 | §15 | v1 분류·최소 성공 기준·Gateway·Streaming을 먼저 정리한다 | T01~T10 | 정적 분석 기록과 Fake stream 완주 |
| R32 | §16 | 요구·제약·실측·지표 순서로 기술을 선택한다 | T05, T14, T16, B05 | 가정·실측·최종 결정을 구분한 ADR |

## 검증 게이트

각 게이트는 실제 실행 증거가 있을 때만 통과로 표시한다. 단위 테스트 통과를 DB·GPU·Docker 통과로 확장하지 않는다.

| 상태 | 게이트 | 실행 범위 | 통과 조건 |
|---|---|---|---|
| [ ] | 오프라인 로직 | typecheck·lint·unit·build | 실제 LLM 없이 계약·parser·예산·정책 검증. build 설정값은 별도 기록 |
| [ ] | DB·API | test:db·test:integration, 전용 DB·Fake upstream | fresh migration, RLS, 원자성, TAP 실패 판정, 실제 HTTP 중단 검증 |
| [ ] | 브라우저 | Chromium E2E, 전용 사용자·DB | 생성·취소·복구·IME·모바일·키보드 흐름 통과 |
| [ ] | 실제 Provider | 명시적 Ollama smoke와 Gemini 비교 | Local stream/usage/cancel 확인, Cloud 동의·자격증명·실행 범위 기록 |
| [ ] | 하드웨어·품질 | B01~B05의 동일 입력 비교 | RAM/VRAM·cold/warm·실패·속도·blind 품질을 함께 확보 |
| [ ] | Docker | compose 검증·build·up·smoke | host Ollama 접속, flush, 취소, restart, secret 비포함 확인 |

검증 명령은 주 계획 §5와 벤치마크 계획 B04를 따른다. 작업 문서에 없는 검증 명령을 이 체크리스트에서 새로 정의하지 않는다. 결과는 `docs/validation.md`와 각 run의 산출물에 연결한다.

## 계약 검토 항목

다음 항목은 계획 검토에서 발견한 확인 대상이다. 수정 여부는 해당 문서의 최종 내용과 대조한 뒤 체크한다.

- [x] 예약과 실행의 책임 분리 및 header 전 예약 결과의 실행 입력 전달을 계획에 반영했다.
- [x] epoch fencing으로 이전 실행의 늦은 draft·heartbeat·commit을 차단하는 계약을 반영했다.
- [x] mutable Memory의 Undo에서 현재 작가 worldBible·불변 인물 설정을 유지하도록 분리했다.
- [x] 논리 호출별 retry·Memory 누적 상한·전체 deadline을 함께 명시했다.
- [x] accepted 유실 시 저장한 원래 payload·멱등 키로 ID를 회수하는 복구 경로를 명시했다.
- [x] TAP의 실패·계획 불일치·bailout을 test:db 실패로 처리하는 규칙을 반영했다.
- [x] metadata 테스트가 실제 `characterStates` row의 금지된 name 변경을 검사하도록 수정했다.
- [x] SSE 테스트가 accepted의 ID와 delta의 text를 실제 이벤트 타입에 맞게 사용하도록 수정했다.

위 체크는 계획의 계약 정합성 검토다. 해당 동작의 구현·실행 검증은 작업 진행 표의 미완료 상태를 따른다.

## 종료 기준

- [ ] T01~T16의 검증과 리뷰가 끝나고 해당 증거가 기록되어 있다.
- [ ] B01~B05의 원자료·품질표·비교 보고서·모델 선택 ADR이 연결되어 있다.
- [ ] 첫 본문 표시, 취소 전파, 멱등성, 원자 저장, 재시작 복구, 작가 설정 보존이 경계 테스트로 입증된다.
- [ ] 동의 없는 Cloud 호출이 없고 사용자 격리·비밀정보·로그·포트 보호를 검증했다.
- [ ] 새 DB와 Docker/Node 실행 경로가 문서대로 재현되며 v1을 변경하지 않았다.
- [ ] 성능 제안값과 실측값을 구분하고, 측정하지 못한 수치나 품질을 통과로 표시하지 않았다.
- [ ] 스크린리더 수동 확인, 실제 Provider, Cloud 비교 등 미실행 범위가 공개 문서에 남아 있다.

필수 게이트를 실행하지 못하면 해당 작업과 릴리스 검증은 미완료다. 한계를 설명하는 기록은 필요하지만 통과 증거를 대신하지 않는다.
