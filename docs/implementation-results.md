# Ghostwriter v2 구현·검증 기록

기준일: 2026-10-26. 사용자 선택에 따라 Next.js 화면 + FastAPI + PostgreSQL로 구현했다. [구조 결정](adr/0001-runtime-and-storage.md)이 기존 Supabase·Node API 계획을 대체한다.

**2026-10-27 갱신:** 외부 Provider를 Gemini에서 GPT (OpenAI Responses API)로 변경했다. 실제 LLM 호출은 사용자 요청에 따라 보류했다. 백업 복원과 브라우저 검증의 최신 결과는 [후속 검증 기록](validation-2026-10-27.md)을 따른다. 아래 표는 2026-10-26 실행 기록이다.

## 구현된 사용 흐름

- 운영자 암호 로그인·로그아웃, 서버 DB 세션과 owner 검증.
- 작품 생성·수정·보관, 고정 인물·세계관·문체 참고 설정, 회차 열람.
- Ollama/Gemini Provider와 비교용 llama.cpp adapter, 한국어 SSE 본문 표시, 취소·연결 종료 처리.
- 필수 설정을 보존하는 context 예산 검사, 별도 structured 상태 추출과 검증.
- 요청 멱등성, 프로젝트 revision·run epoch, 프로젝트별 생성 제한, 로컬 동시 추론 제한.
- 본문·기억·회차의 원자 확정, 실패 초안과 상태 추출 재시도, 마지막 회차 Undo·완결.
- 재접속 시 동일 요청 조회, 요청별 Cloud 동의와 첫 본문 이전에만 허용되는 fallback.
- 실제 token 기반 지표 API·화면, 모델 비교 CLI·blind 평가 양식, 실행·백업 안내와 Docker 구성.

## 검증 증거

| 검증 | 결과 | 범위 |
|---|---|---|
| `npm.cmd run typecheck` | 통과 | Next.js·TypeScript |
| `npm.cmd run lint` | 통과 | ESLint |
| `backend/.venv/Scripts/ruff.exe check backend scripts/model-smoke.py` | 통과 | Python 정적 검사 |
| `npm.cmd run test:unit` | 7개 통과 | health·분할 UTF-8/CRLF SSE·잘못된 frame/EOF·abort |
| `npm.cmd run test:integration` | 96개 통과, skip 없음 | 실제 PostgreSQL 포함 API·store·생성·context·memory·benchmark·header/Retry-After |
| `npm.cmd run build` | 통과 | production 페이지·route 생성. 아래 환경 한계 참조 |
| `npm.cmd run test:e2e` | Chromium 4개 통과, 최종 전체 실행 20.9초 | 아래 브라우저 흐름 |
| `backend/.venv/Scripts/python.exe scripts/model-smoke.py` | 통과 | 실제 Ollama Gemma 4B + PostgreSQL 원자 저장·재조회 |
| 로컬 비교 CLI | 36회 측정 완료 | [실측 해석과 절단 한계](benchmark-results-2026-10-26.md) |
| `docker compose config --quiet` | 통과 | placeholder 환경값을 사용한 정적 구성 검증 |

브라우저는 로그인, 문체 참고 3번부터 입력, 요청별 동의 초기화, 스트림·회차 저장·새로고침, 다음 회차·Undo·완결·보관·로그아웃을 확인했다. 별도 시나리오에서 upstream까지 취소 전파, 취소 후 다음 요청, length/잘못된 상태 JSON의 확정 거부, 실패 초안의 reload 복구, 스트림 중 reload 시 본문 재호출 없음도 확인했다. 1280px·390px 화면을 캡처·열람했으며 모바일 가로 넘침이 없었다. [Desktop](../artifacts/screenshots/workshop-desktop.png), [Mobile](../artifacts/screenshots/workshop-mobile.png).

독립 코드 리뷰의 7개 발견 사항과 실제 HTTP 검증에서 추가 발견한 반복 취소 시 슬롯 누수를 수정했다. 부분 metadata의 기존 기억 보존, 실패 receipt 유지, finalization 경쟁 방지, 동의 초기화, sparse 문체 배열 정규화, 회차 응답 경쟁 방지, 실제 fallback Provider 지표를 반영했다. 회귀 테스트는 실제 PG·AnyIO 반복 취소·예약 barrier를 포함한다.

## 실행 환경과 해석 한계

- Node 24.18.1, npm 11.16.0, Python 3.13.14. SQL 서버 `SHOW server_version` 결과 PostgreSQL **18.4**.
- Docker 엔진 기동 실패로 개발 검증은 독립 PostgreSQL binary를 사용했다. `embedded-postgres` wrapper는 18.4.0-beta.17이며 SQLite·가짜 SQL 엔진이 아니다. 시험 DB 이름은 `ghostwriter_test`로 제한했다.
- OneDrive 작업 경로에서 의존성과 `.next` 생성 파일 누락을 재현했다. 정확한 lockfile을 임시 경로에 설치하고 로컬 junction으로 연결했다. 빌드에는 이 환경에서만 `NODE_PATH`를 설정했다. 저장소에 사용자별 절대 경로를 넣지 않았다.
- 위 우회 환경의 standalone 출력은 tracing root 밖의 의존성 27개가 제외된다는 경고가 있다. 일반 production 서버와 E2E는 통과했으나 **이 Windows standalone 산출물을 배포 가능한 것으로 판정하지 않는다**. Docker의 독립 설치·build·기동 검증은 별도 남아 있다.
- 신규 로컬 DB 기본 경로는 OS 사용자 데이터 폴더다. 본 검증에서 먼저 만든 시험 클러스터는 임시 경로를 유지한다. 사용자 데이터 경로를 자동 이동하거나 기존 DB를 삭제하지 않았다.

## 남은 릴리스 조건

1. 실제 Gemini 계정·명시적 동의로 Cloud 호출과 기준 모델 비교.
2. 사람이 blind 품질표를 채우고 fatal·완결성·절단을 검토한 뒤 기본 모델 확정. 현재 수동 점수는 null이다.
3. Docker 엔진이 정상인 환경의 이미지 build·compose 기동·내부 연결 smoke.
4. 별도 PostgreSQL 도구로 백업을 빈 DB에 복원하고 원고를 재조회하는 운영 훈련.
5. 장시간 사용, screen reader·다른 브라우저, 실제 동시 다중 프로세스 배포 검증. 현재 로컬 슬롯은 단일 API 프로세스를 전제로 한다.

위 조건을 미완료로 남겼으며 전체 릴리스 승인을 주장하지 않는다. v1 DB·credential은 사용하지 않았고 배포·원격 push도 수행하지 않았다.
