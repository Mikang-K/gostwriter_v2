# ADR 0001: Next.js 화면, FastAPI, PostgreSQL

상태: 사용자 선택 확정 · 2026-10-26

사용자는 Supabase 대안을 검토한 뒤 Next.js 화면 + FastAPI + PostgreSQL을 선택했다. [비교](../backend-options.md)

- Next.js 16/React 19는 화면과 동일 출처 API proxy를 담당한다.
- Python 3.13/FastAPI는 인증, CRUD, 생성 orchestration, SSE, Memory, 지표를 담당한다.
- PostgreSQL에 원고·immutable Memory·요청·session을 저장한다. psycopg의 짧은 transaction을 사용하고 LLM 대기 중 DB lock을 유지하지 않는다.
- 로컬 개인용 로그인은 운영자가 설정한 password와 opaque HTTP-only session으로 구성한다. Supabase 로그인·공개 회원가입은 구현하지 않는다.
- 기본 포트는 화면 3000, API 8000, DB 5432이며 host 공개는 loopback으로 한정한다. 실제 서비스 공개에는 별도 인증·HTTPS·운영 검토가 필요하다.
- TypeScript Provider/SQL RPC에 관한 구 계획은 [변경 구현 지침](../backend-implementation.md)의 Python 모듈로 대체한다. 제품·안전성 인수 조건은 유지한다.

이 선택은 Python API와 PostgreSQL 운영을 직접 경험하려는 사용자의 판단이다. 별도 프로세스·배포·테스트를 관리하는 비용을 받아들인다. Node 단독 API와 Supabase 의존성은 추가하지 않는다.
