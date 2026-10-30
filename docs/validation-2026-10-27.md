# 2026-10-27 후속 구현·검증 기록

## 범위와 변경

사용자 요청으로 실제 LLM API 호출과 모델 비교를 보류했다. Ollama·GPT·Gemini를 실제로 호출하지 않았으며, HTTP fixture와 합성 원고만 사용했다. 모델 품질 점수와 기본 모델 선정은 갱신하지 않았다.

외부 모델 경로는 Gemini에서 GPT (OpenAI Responses API)로 변경했다. 화면·요청 검증·fallback·환경변수·Compose·벤치마크 후보·테스트를 함께 수정했다. 외부 사용은 계속 `ENABLE_CLOUD=false`가 기본이며, 활성화 후에도 요청별 동의가 필요하다. 모델명은 `OPENAI_MODEL`, 키는 `OPENAI_API_KEY`로 지정한다. 기존 `GEMINI_*` 설정과 새 Gemini 요청은 지원하지 않는다. 기존 확정 원고와 DB 스키마는 변경하지 않았다.

구현 기준은 [OpenAI 공식 스트리밍 문서](https://developers.openai.com/api/docs/guides/streaming-responses)와 [구조화 출력 문서](https://developers.openai.com/api/docs/guides/structured-outputs)다. `store=false`, 본문 delta, 실제 token usage, 출력 제한, refusal, 오류, terminal event 없는 연결 종료를 처리한다. Memory는 기존 부분 갱신 의미를 유지하기 위해 `json_schema`의 `strict=false`를 사용하고 기존 Pydantic 검증을 거친다. 모델별 지원 차이를 고려해 GPT에는 seed·temperature를 보내지 않으며 벤치마크 산출물에도 미적용으로 기록한다. 실제 계정·모델 호환성은 이번 검증 범위 밖이다.

## 이번 실행 결과

| 검사 | 결과 | 범위 |
|---|---|---|
| `npm.cmd run typecheck` | 통과 | Next.js·TypeScript |
| `npm.cmd run lint` | 통과 | ESLint |
| `backend/.venv/Scripts/ruff.exe check backend scripts/model-smoke.py scripts/backup-smoke.py` | 통과 | Python 정적 검사 |
| `npm.cmd run test:unit` | 7개 통과 | 프런트 단위 테스트 |
| `npm.cmd run test:integration` | 105개 통과, skip 없음 | 실제 PostgreSQL 포함. GPT HTTP MockTransport 사용 |
| `python -m pytest backend/tests/test_benchmark.py -q -p no:cacheprovider` | 42개 통과 | sampling 미적용 기록 assertion 추가 후 재검증 |
| `npm.cmd run build` | 통과, standalone 경고 | 아래 Windows 환경 한계 참조 |
| `npm.cmd run test:e2e` | Chromium 5개·Firefox 5개 통과 | 각각 19.7초·27.3초, fixture upstream 사용 |
| `scripts/backup-smoke.py` | 통과 | 실제 pg_dump·pg_restore, 합성 원고 복원·재조회 |
| `docker compose config --quiet` | 통과 | placeholder 값 사용, 엔진 불필요 |
| Docker 이미지 build·기동 | 미실행 | Docker Desktop 엔진 기동 실패 |

Python 통합 테스트에서 OneDrive 경로의 `backend/.pytest_cache` 쓰기 권한 경고 1개가 발생했다. 테스트 실패나 skip은 없었다. 캐시를 끈 벤치마크 재검증은 경고 없이 통과했다.

브라우저 검증은 로그인, 작품 생성, 생성 스트림, 저장·새로고침, 다음 회차, Undo·완결·보관·로그아웃, 취소 전파, 출력 제한·잘못된 기억의 확정 거부, 초안 복구, 스트림 중 새로고침을 포함한다. 추가로 Tab·Enter 로그인, 한글 문자열 입력, Space 동의 전환, GPT 선택 시 동의 없는 생성 차단을 검사했다. 문자열 주입은 실제 OS IME 조합 입력 검증을 대체하지 않는다.

브라우저들이 공유 API의 추론 슬롯·분당 요청 제한을 소진한 초기 실행은 실패했다. `scripts/e2e.mjs`가 브라우저별 API와 무작위 owner를 새로 만들고 순차 실행하도록 수정했다. 서버 종료도 기다린다. 운영 요청 제한은 변경하지 않았다. CI에는 Firefox 설치·검증을 추가했으며 원격 CI 자체는 이번에 실행하지 않았다.

스크린샷은 `artifacts/screenshots/workshop-{chromium,firefox}-{desktop,mobile}.png`에 저장한다. 모바일 viewport는 390×844이며 양쪽 브라우저에서 가로 넘침 검사를 통과했다.

## 백업·복원 재현

`pg_dump`, `pg_restore`가 설치된 PostgreSQL client 도구 폴더를 지정한다. Windows 예시는 다음과 같다.

```powershell
npm.cmd run db:local
# 별도 터미널
$env:PYTHONPATH = "backend"
backend/.venv/Scripts/python.exe scripts/backup-smoke.py --pg-bin "C:\Program Files\PostgreSQL\18\bin"
```

스크립트는 `.cache/development-db.json`의 로컬 시험 연결만 사용한다. 새 `ghostwriter_test_backup_<무작위값>_source`와 `_restored` DB를 만들고, 합성 작품·인물·Memory·확정 회차·생성 요청을 저장한다. custom dump를 빈 DB에 복원한 뒤 Store의 snapshot·episode·generation 응답이 같은지 assert한다. 검증 후 이번에 만든 DB 두 개만 삭제하고 dump·결과 JSON은 `artifacts/backup-smoke-<무작위값>/`에 남긴다. 사용자 원고 DB는 열지 않는다.

이번 실행은 PostgreSQL 서버 18.4와 client 18.6을 사용했다. client 도구는 [EDB 공식 binary 배포](https://www.enterprisedb.com/download-postgresql-binaries)에서 받아 로컬 임시 폴더에만 풀었다. 결과는 `artifacts/backup-smoke-a5e96e12e875/result.json`이며 설정·인물·기억·회차·요청 기록·migration 재진입이 모두 일치했다. 시험 DB 삭제도 확인했다. 사용자 실제 원고의 복원, 별도 서버 이전, 정기 백업 자동화는 검증하지 않았다.

## 환경 한계와 남은 조건

- 기존 `node_modules`·`.next` junction 때문에 최초 build는 `MODULE_NOT_FOUND`로 실패했다. `$env:NODE_PATH=(Resolve-Path node_modules).Path` 적용 후 build와 production E2E가 통과했다. 사용자별 경로를 저장소 설정에 넣지 않았다.
- standalone 출력에는 tracing root 밖의 파일 27개 누락 경고가 남았다. 이 Windows 산출물은 배포 가능으로 판정하지 않는다.
- `docker desktop start`를 시도했지만 엔진이 시작되지 않았다. `docker info`는 Linux engine named pipe 부재로 실패했다. 이미지 build·Compose 실행·컨테이너 내부 연결·재시작은 미검증이다. OS·가상화 설정은 변경하지 않았다.
- 후속 [Docker 엔진 진단](docker-engine-diagnosis-2026-10-27.md)에서 WSL의 `\Device\P9Rdr` 접근 거부를 확인했다. WSL 서비스 재시작은 효과가 없었고, 드라이버 재적재도 오류 5로 거부됐다. 현재 p9rdr는 Stopped이며 사용자가 PC 재부팅을 보류했다. 자세한 조치와 후속 확인은 해당 진단 문서를 따른다.
- 실제 IME, 스크린리더, 장시간 사용, 동시 다중 API 프로세스 배포는 미검증이다.
- 실제 GPT 호출·품질 평가·모델 재선정은 사용자 요청으로 보류한다. 기존 36회 출력 절단 결과를 성공 판정으로 바꾸지 않았다.
- 커밋·push·배포는 수행하지 않았다.
