# FastAPI·PostgreSQL 구현 변경 지침

2026-10-26 사용자 확정: Next.js 화면 + FastAPI + PostgreSQL.

이 문서는 기존 계획의 Supabase·Node API·TypeScript Provider·pgTAP 실행 지침을 대체한다. 본문 스트림, 멱등성, epoch, 원자 저장, Undo, Cloud 동의, 품질·성능 검증 조건은 그대로 적용한다. 별도 구조를 동시에 구현하지 않는다.

| 기존 작업 | 실제 소유 파일·실행 방식 |
|---|---|
| T01 | 루트 Next.js/React/TS/Vitest/Playwright. Python은 backend/pyproject.toml과 uv.lock. |
| T02/T07/T13 저장 | backend/migrations/001_core.sql, backend/app/store.py. PostgreSQL transaction·FOR UPDATE·partial UNIQUE·epoch 검사. |
| T03 인증/API | backend/app/main.py, config.py, models.py. 운영자 password 로그인, DB opaque sessions, owner 검증. |
| T04/T10/T12 Provider | backend/app/providers.py. Python async iterator/httpx, 동일 delta/usage/finish 계약. Ollama/Gemini/비교용 llama.cpp. |
| T05/T06 | backend/app/context.py, memory.py. 필수 설정 보존·예산 초과 거절·strict metadata 검증. |
| T07/T08/T11 | backend/app/generation.py. 예약 1회, SSE, 취소/연결 종료, deadline·제한 재시도, 동의 있는 fallback. |
| T09 | src/app 및 src/features. Next route proxy가 /api를 FastAPI로 연결. 브라우저 계약은 camelCase JSON/SSE 유지. |
| T14 | backend/app/metrics.py 및 API/화면. 벤치마크와 같은 계산 helper 사용. |
| T15 | backend/tests, tests/unit, tests/e2e. 실제 PostgreSQL 통합, fake upstream, 실제 HTTP/브라우저 검증을 구분. |
| T16 | app/API/PostgreSQL compose, 실행 문서·백업·장애 runbook. |
| B01~B05 | benchmarks 입력·품질표, backend/app/benchmark.py. Python CLI가 같은 Provider를 재사용. |

## 경계

- API 포트 8000은 loopback, 화면 포트 3000도 loopback이다. API mutation은 허용된 Origin을 검사한다. CORS 전체 허용으로 해결하지 않는다.
- APP_PASSWORD는 운영자가 설정하고 채팅·로그·Git에 공개하지 않는다. 로그인 후 session token은 HttpOnly/SameSite 쿠키이며 DB에는 hash만 저장한다. 공개 회원가입과 결제는 제외한다.
- database_url은 전용 v2 PostgreSQL만 사용한다. v1 DB나 credential을 복사하지 않는다. 자동 연결 실패 시 SQLite나 메모리 저장으로 바꾸지 않는다.
- 개발/검증용 PostgreSQL을 Docker로 실행할 수 없으면 독립 PostgreSQL 바이너리 사용을 검토하되, 사용한 버전·경로·명령을 기록한다. 모든 DB 테스트는 전용 임시 database에서 실행한다.
- Next health route는 프런트 프로세스 상태만 뜻한다. 백엔드 health/DB 준비 상태와 구분한다.
- 기본 Cloud 비활성, 요청별 동의, 비밀정보·원고 로그 미출력 원칙을 유지한다.

## 검증 명령

```powershell
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run test:unit
npm.cmd run build
uv sync --project backend
uv run --project backend pytest -c backend/pyproject.toml backend/tests
npm.cmd run test:e2e
```

실제 DB·GPU·Cloud·Docker 결과는 각각의 실행 증거가 있어야 통과다. 환경이 없어 실행하지 못한 검증은 미완료로 남긴다.
