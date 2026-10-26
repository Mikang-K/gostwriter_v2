# Ghostwriter v2

로컬 LLM으로 소설을 쓰고 이어 가는 개인 작업실입니다. 작품·인물·세계관을 저장하고, 생성되는 글을 읽으며 중단할 수 있습니다. 회차 본문과 이야기 기억이 함께 저장되어야 확정 원고가 됩니다.

화면은 Next.js, API는 Python FastAPI, 저장소는 PostgreSQL입니다. Ollama를 기본으로 사용하며 Gemini는 서버 설정과 요청별 동의가 있을 때만 사용합니다. [구조 결정](docs/adr/0001-runtime-and-storage.md), [모델 비교 실행](docs/benchmarks.md)을 참고하세요.

## 준비

- Node.js 24, uv와 Python 3.13, Ollama가 필요합니다.
- Windows PowerShell 명령은 저장소 루트에서 실행합니다. `npm` 대신 `npm.cmd`를 사용합니다.
- Ollama에서 사용할 모델을 준비합니다. 이 작업에는 다운로드가 필요합니다.

```powershell
ollama pull gemma3:1b-it-q4_K_M
ollama pull gemma3:4b-it-q4_K_M
ollama list
```

현재 확인된 GPU는 **RTX 4060 Laptop GPU 8GiB**입니다. 두 Q4 모델은 이 작업 환경에 준비되어 있지만, 설치한 모델만으로 장편 품질이나 목표 속도를 보장하지는 않습니다. 기본 모델의 최종 선택에는 [벤치마크와 수동 품질 평가](docs/benchmarks.md)가 필요합니다.

## 설치하고 실행하기

먼저 의존성을 설치합니다.

```powershell
npm.cmd ci
uv sync --project backend --locked
```

터미널 세 개를 열어 각각 실행합니다.

**1. 데이터베이스**

```powershell
npm.cmd run db:local
```

전용 PostgreSQL이 `127.0.0.1:55432`에서 실행됩니다. 처음 실행할 때 로컬 로그인 암호와 DB 연결 정보를 생성해 `backend/.env.local`에 저장합니다. 기존 파일은 덮어쓰지 않습니다. 암호는 해당 파일의 `APP_PASSWORD` 값이며 채팅이나 Git에 올리지 마세요.

`backend/.env.local`에 아래 모델 설정을 추가하세요. 로그인·DB 설정은 생성된 값을 유지합니다.

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma3:4b-it-q4_K_M
CONTEXT_WINDOW=8192
MAX_OUTPUT_TOKENS=1024
ENABLE_CLOUD=false
```

**2. API**

```powershell
$env:PYTHONPATH = "backend"
backend/.venv/Scripts/python.exe -m app
```

API는 `127.0.0.1:8000`에서 실행되며 시작할 때 DB migration을 적용합니다. Windows에서는 이 실행 명령이 PostgreSQL 연결에 필요한 이벤트 루프를 설정합니다.

**3. 화면**

```powershell
npm.cmd run dev
```

브라우저에서 **http://localhost:3000**을 열고 `APP_PASSWORD`로 로그인합니다. 주소를 `127.0.0.1:3000`으로 바꾸면 허용된 Origin과 달라 저장 요청이 거절될 수 있으므로 `localhost`를 유지하세요. 각 터미널에서 `Ctrl+C`로 해당 프로세스를 종료합니다.

## 소설 쓰기

1. 작품 목록에서 새 작품을 만들고 제목·인물·세계관·작가 노트를 입력합니다. 인물은 최대 10명, 문체 참고 글은 최대 3개입니다.
2. 전개 방향을 적고 첫 회차 생성을 누릅니다. 글이 차례로 표시되며 생성 중단을 누르면 미확정 초안으로 남습니다.
3. 본문 생성 뒤 상태 정리가 끝나고 저장이 완료되면 확정 회차가 됩니다. 다음 회차 생성이나 에필로그 생성으로 이어 씁니다.
4. 실패한 본문이 완성된 상태라면 상태 저장 재시도를 사용합니다. 이 작업은 본문을 새로 생성하지 않습니다.
5. 마지막 회차 되돌리기는 해당 회차 이전의 이야기 기억을 복원합니다. 직접 수정한 세계관과 고정 인물 설정은 유지합니다.

화면을 새로 열면 저장된 원고와 요청 상태를 확인합니다. 모델 서버가 꺼져 있어도 이미 저장된 작품은 열람할 수 있습니다. 운영 지표 화면에서는 저장된 생성 시간과 오류 기록을 확인합니다.

## 저장과 백업

원고·인물·이야기 기억·생성 요청·로그인 세션은 연결한 PostgreSQL에 저장됩니다. 기본 로컬 실행에서 Supabase나 외부 DB 계정은 필요하지 않습니다. 외부 PostgreSQL 주소를 직접 설정하면 그 서버에 원고가 저장됩니다.

로컬 DB의 실제 데이터 경로는 `.cache/development-db.json`의 `databaseDir`에 기록됩니다. 새 설치의 기본값은 Windows `%LOCALAPPDATA%/ghostwriter-v2/postgres`이며, `Ctrl+C`는 DB 파일을 지우지 않습니다. 이 구현을 검증하면서 만든 기존 시험 클러스터는 임시 폴더에 남아 있으므로 시험 데이터로만 사용하세요. 기존 경로를 자동으로 이동하거나 덮어쓰지 않습니다. 개인 원고는 별도로 백업하세요. `.cache/development-db.json`과 `backend/.env.local`에는 비밀정보가 있습니다.

PostgreSQL 클라이언트 도구가 설치되어 있다면 아래처럼 백업할 수 있습니다. `pg_dump`와 `pg_restore`는 DB와 호환되는 버전을 사용합니다. 명령이 암호를 물으면 `backend/.env.local`의 DB 암호를 입력합니다. 암호를 명령 인자나 결과 파일명에 적지 않습니다.

```powershell
pg_dump --host=127.0.0.1 --port=55432 --username=ghostwriter --password --dbname=ghostwriter_v2 --format=custom --file=ghostwriter-backup.dump
createdb --host=127.0.0.1 --port=55432 --username=ghostwriter --password ghostwriter_restore
pg_restore --host=127.0.0.1 --port=55432 --username=ghostwriter --password --dbname=ghostwriter_restore --no-owner ghostwriter-backup.dump
```

복원 대상은 비어 있는 별도 DB로 준비하고, 앱의 `DATABASE_URL`을 복원 DB로 바꾼 뒤 작품·회차를 확인하세요. 개인 원고가 든 백업도 Git에 올리지 않습니다.

## 외부 모델 사용

기본값은 외부 모델 비활성입니다. 사용하려면 `backend/.env.local`에 `ENABLE_CLOUD=true`, `GEMINI_MODEL`, `GEMINI_API_KEY`를 설정하고 API를 다시 시작합니다. 화면에서도 **이번 요청에 외부 모델 사용 허용**을 선택해야 합니다.

허용된 요청은 작품 설정·인물·최근 원문·요약·전개 방향·생성 본문을 생성 또는 상태 정리를 위해 Gemini로 전송할 수 있으며 비용이 발생할 수 있습니다. 로컬 생성이 첫 글자를 내보낸 뒤 실패하면 다른 모델로 이어 붙이지 않습니다. 실제 Cloud 호출과 모델명을 가린 수동 품질 평가는 아직 검증하지 않았습니다.

## 문제가 생기면

| 증상 | 확인할 내용 |
|---|---|
| 작업실에 들어갈 수 없음 | `backend/.env.local`의 `APP_PASSWORD`를 사용했는지 확인합니다. 연속 로그인 실패가 누적되면 잠시 기다립니다. |
| API 연결 실패 | DB와 API 터미널이 실행 중인지, `http://127.0.0.1:8000/api/health`가 응답하는지 확인합니다. 화면의 `/api/health`는 Next.js 상태만 뜻합니다. |
| 저장 요청 거절 | `http://localhost:3000`으로 접속하고 `APP_ORIGIN`도 같은 주소인지 확인합니다. |
| 생성 실패·모델 없음 | Ollama가 실행 중인지, `ollama list`에 `OLLAMA_MODEL`의 정확한 이름이 있는지 확인합니다. |
| 입력 예산 초과 | 세계관·인물·작가 노트를 줄이거나 준비된 모델에 맞춰 `CONTEXT_WINDOW`를 조정합니다. 실제 GPU 메모리 사용량도 확인합니다. |
| 다른 탭의 수정과 충돌 | 새로고침해 최신 작품을 불러온 뒤 다시 작업합니다. |
| DB 포트 사용 중 | 이미 실행한 DB 터미널을 확인합니다. 같은 DB를 다시 띄우지 않습니다. |

## Docker 실행 구성

`compose.yaml`은 화면·API·PostgreSQL을 분리하고 호스트 공개 포트를 `127.0.0.1`로 제한합니다. 호스트 Ollama는 별도로 준비합니다. 이 환경에서는 Docker 엔진을 시작할 수 없어 **이미지 build와 compose 기동은 검증하지 못했습니다**.

먼저 아래 PowerShell로 `.env.compose`를 생성합니다. 파일이 이미 있으면 덮어쓰지 않습니다. 암호는 출력하지 않습니다.

```powershell
if (Test-Path .env.compose) { throw ".env.compose already exists" }
$dbPassword = [guid]::NewGuid().ToString("N")
$loginPassword = [guid]::NewGuid().ToString("N")
@("POSTGRES_PASSWORD=$dbPassword", "APP_PASSWORD=$loginPassword", "OLLAMA_MODEL=gemma3:4b-it-q4_K_M") |
  Set-Content -Encoding ascii .env.compose
docker compose --env-file .env.compose up --build
```

로그인 암호는 `.env.compose`의 `APP_PASSWORD`입니다. 화면은 `http://localhost:3000`, API는 `127.0.0.1:8000`, DB는 `127.0.0.1:5432`에 연결합니다. 기존 화면·API 프로세스와 포트가 겹치므로 먼저 종료합니다. 컨테이너 API는 `host.docker.internal:11434`로 Ollama에 접근하므로 해당 경로의 연결과 호스트 방화벽 설정을 별도로 확인해야 합니다.

```powershell
docker compose --env-file .env.compose down
```

DB는 named volume에 보존됩니다. `down -v`는 DB volume까지 삭제하므로 원고 보존이 필요할 때 사용하지 않습니다. 네이티브 실행 DB와 compose DB는 별개이며 자동 이관하지 않습니다.

## 개발 검증

```powershell
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run test:unit
npm.cmd run build
```

Python DB 테스트는 전용 `ghostwriter_test*` DB의 `TEST_DATABASE_URL`을 사용합니다. 로컬 DB를 실행한 상태에서 아래 명령은 로컬 설정의 연결 문자열을 화면에 표시하지 않고 테스트 프로세스에 전달합니다.

```powershell
npm.cmd run test:integration
backend/.venv/Scripts/python.exe -m ruff check backend
```

E2E 실행 전 일반 화면·API를 종료해 3000·8000·테스트용 11435 포트를 비워 둡니다. 위의 production build를 먼저 완료하고 DB는 실행 상태로 유지합니다. E2E 스크립트가 전용 테스트 DB·모의 Ollama·테스트 API·production 화면 서버를 준비하고 종료하며 Chromium 브라우저 설치가 필요합니다.

```powershell
npx.cmd playwright install chromium
npm.cmd run test:e2e
```

모의 Provider 통과, 실제 PostgreSQL 통과, 브라우저 통과, 실제 모델 성능, Docker 기동은 각각 별도의 검증입니다. [최신 검증 결과](docs/implementation-results.md)와 [로컬 모델 측정](docs/benchmark-results-2026-10-26.md)에서 실행 증거와 남은 조건을 확인합니다. 전체 원계획의 완료를 의미하지 않습니다.
