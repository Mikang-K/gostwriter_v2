# 브랜치와 통합 절차

## 저장소와 브랜치

원격 이름은 `origin`이며 주소는 `https://github.com/Mikang-K/gostwriter_v2.git`이다. `main`과 `develop`은 같은 저장소 안의 브랜치다.

| 브랜치 | 역할 | 통합 대상 |
|---|---|---|
| `main` | 검증한 운영 버전. 기본 브랜치 | 운영 검증을 마친 `develop`만 PR로 통합 |
| `develop` | 개발 결과 통합과 릴리스 준비 | 기능·수정 브랜치를 PR로 통합 |
| `feat/작업명`, `fix/작업명`, `chore/작업명` | 개별 작업 | `develop` |

구성 초기에는 `7c5cbfa` 계획 커밋을 `main`·`develop`의 공통 기준으로 사용한다. 기존 `feat/ghostwriter-v2`는 이 커밋의 후손이므로 이력을 재작성하지 않고 `develop`에 merge commit으로 통합한다. 기존 기능 브랜치는 보존하며 이후 새 작업에 재사용하지 않는다.

**초기 main은 계획 문서만 있는 미출시 기준점이다. 운영 가능한 앱이 배포되었다는 뜻이 아니다.** 현재 구현은 `develop`에서 검증하고, [남은 릴리스 조건](implementation-results.md)을 충족한 뒤 `main`으로 승격한다.

## 새 작업 시작

작업 중인 변경을 먼저 커밋하거나 보관한 뒤 저장소 루트에서 실행한다.

```powershell
git switch develop
git pull --ff-only origin develop
git switch -c feat/작업명
```

구현·검증이 끝나면 작업 브랜치를 push하고 `develop`을 대상으로 PR을 만든다.

```powershell
git push -u origin HEAD
```

PR의 변경 범위와 테스트를 확인하고 CI가 통과한 뒤 merge한다. `main`·`develop`에 직접 push하거나 강제 push하는 방식으로 검증을 건너뛰지 않는다.

## 자동 검증과 보호 규칙

`.github/workflows/ci.yml`은 `develop` push와 `develop`·`main` 대상 PR에서 `validate`를 실행한다. Node24·Python3.13·실제 PostgreSQL18을 사용해 lint, unit, Python 통합 테스트, production build, typecheck, Chromium·Firefox E2E를 검사한다. 브라우저별 API와 owner를 분리해 순차 실행한다. 모델 호출은 테스트 전용 upstream을 사용하며 실제 GPU·유료 Cloud 성능을 검증하지 않는다.

`main`과 `develop`은 PR 및 최신 기준의 `validate` 통과를 요구하고, force push·브랜치 삭제를 차단한다. 개인 저장소에서 혼자 작업할 수 있도록 필수 승인 인원은 0명이며 PR의 변경 검토와 release checklist는 직접 수행한다. 관리자에게도 같은 보호 규칙을 적용한다.

`main` 대상 PR의 source는 같은 저장소의 `develop`이어야 한다. 다른 기능 브랜치에서 바로 `main`으로 보내면 CI가 실패한다. PR 체크리스트는 운영 검증을 기록하는 절차이며 실제 배포를 자동 실행하지 않는다.

## 운영 버전으로 승격

1. `develop`에서 통합 테스트와 운영 환경 검증을 완료한다.
2. `develop → main` PR을 만들고 릴리스 체크리스트에 실제 실행 증거를 기록한다.
3. 최신 `main`과의 CI 통과를 확인한 뒤 merge commit으로 통합한다.
4. 통합된 커밋을 기준으로 릴리스 버전과 배포 기록을 남긴다. 배포는 별도 작업이다.
5. 다음 개발 주기 전 `main → develop` 동기화 PR로 릴리스 merge 이력을 반영해 양쪽 이력을 연결한다.

운영 검증이 끝나지 않은 구현을 브랜치 이름만 바꿔 `main`에 올리지 않는다. 합성 원고의 백업 복원은 검증했으며, Docker 기동·실제 IME·스크린리더·장시간 사용 등 남은 조건은 [최신 검증 기록](validation-2026-10-27.md)을 따른다. 실제 LLM 호출·모델 품질 비교는 사용자 요청으로 보류한다.
