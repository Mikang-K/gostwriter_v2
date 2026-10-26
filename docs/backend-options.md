# Ghostwriter v2 백엔드 선택 기록

검토·결정일: 2026-10-26 · 상태: **사용자 선택 확정**

선택한 구조는 **Next.js 화면 + Python FastAPI + PostgreSQL**이다. Python API와 PostgreSQL을 직접 다루려는 사용자의 판단에 따라 별도 프로세스·배포·테스트를 운영하는 비용을 받아들였다. [ADR 0001](adr/0001-runtime-and-storage.md), [실행 방법](../README.md)을 함께 읽는다.

## 비교했던 안

아래는 개인 집필·로컬 LLM 실험이라는 범위에서 검토했던 설계 차이다. 실측 성능 순위는 아니다. 모두 별도 Ollama 서버를 호출할 수 있다.

| 안 | 구성 | 장점 | 부담·한계 | 결정 |
|---|---|---|---|---|
| A | Next.js Node API + SQLite | API·화면을 TypeScript로 유지하며 별도 DB 서버 없이 파일로 저장 | 서버형 DB·동시 쓰기 운영을 직접 경험하기에는 범위가 다름 | 초기 단순성 권장안이었으나 미채택 |
| B | Next.js Node API + PostgreSQL | 서버형 관계형 DB·transaction을 직접 운영 | Node API와 DB 운영 필요 | 미채택 |
| C | Next.js 화면 + FastAPI + PostgreSQL | Python API·실험 코드와 서버형 DB 책임을 분리 | JS/Python 두 환경과 서비스 간 계약·배포 관리 | **사용자 채택** |
| D | Next.js Node API + Supabase | Auth·PostgreSQL 등 제공 기능 재사용 | hosted 사용 시 외부 저장·계정·네트워크 의존, 자체 호스팅 운영 부담 | 미채택 |

SQLite를 먼저 권장했던 문서는 선택 전 검토였다. 현재 구현 지침으로 사용하지 않는다. Python API를 선택했다는 사실만으로 Ollama의 추론 속도가 빨라진다고 주장하지 않는다.

## 실제 책임과 데이터 경로

```text
브라우저 http://localhost:3000
  → Next.js 화면 / 동일 출처 /api proxy
  → FastAPI 127.0.0.1:8000
      → PostgreSQL: 원고·이야기 기억·생성 요청·로그인 세션
      → Ollama: 기본 로컬 생성·구조화 기억 추출
      → Gemini: 서버 활성화 + 요청별 동의가 있는 요청만
```

- Next.js는 UI와 API proxy를 담당한다. 인증·CRUD·생성 orchestration·SSE·Memory·지표는 FastAPI가 담당한다.
- PostgreSQL의 짧은 transaction으로 본문·기억·회차를 함께 확정하며 LLM 응답을 기다리는 동안 DB lock을 유지하지 않는다.
- 운영자가 지정한 작업실 암호와 opaque HttpOnly/SameSite session을 사용한다. 공개 회원가입·Supabase Auth는 포함하지 않는다.
- 기본 저장은 로컬 PostgreSQL이다. 로컬 개발 DB는 `127.0.0.1:55432`, compose DB는 컨테이너 내부 5432이며 호스트에는 `127.0.0.1:5432`로 공개한다. 운영자가 외부 DB 주소를 지정하면 원고도 그 서버에 저장된다.
- API·화면·DB의 호스트 포트는 loopback으로 제한한다. 컨테이너 내부의 `0.0.0.0` 바인딩과 호스트 외부 공개는 구분한다.
- Cloud 비활성 및 요청별 동의, v1 데이터·자격증명 보존, 원고·비밀키 기본 로그 미출력 원칙을 유지한다.

## 검증 경계

Python API·generation 테스트는 실제 격리 PostgreSQL과 주입한 Provider로 검사했다. API의 ASGITransport 테스트는 응답을 버퍼링하므로 브라우저 스트림 flush 검증을 대신하지 않는다. 별도 네이티브 HTTP fixture에서 여러 청크 수신과 취소를 확인했다.

Docker 실행 파일은 제공하지만 현재 환경에서 Docker 엔진을 시작하지 못해 이미지 build·compose 기동을 검증하지 않았다. 실제 Cloud 호출과 수동 blind 품질 평가는 미완료다. 원계획의 체크박스를 일괄 완료로 바꾸지 않는다.
