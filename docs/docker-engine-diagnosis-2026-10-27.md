# Docker 엔진 기동 실패 진단

확인일: 2026-10-27, 한국 시간. 사용자는 PC 재부팅을 보류하고 진단 결과와 후속 조치를 정리하도록 요청했다. 재부팅을 실행하거나 예약하지 않았다.

## 확인된 실패 원인과 미확정 사항

Docker Desktop이 사용하는 WSL2의 Plan 9 Redirector 장치 `\Device\P9Rdr` 접근이 거부된다. WSL 서비스가 배포판 실행 요청을 처리하다 `0xD0000022`를 반환하므로 Docker의 Linux 엔진이 시작되지 않고 named pipe도 생성되지 않는다. 같은 오류가 Ubuntu와 WSL system 배포판에서도 재현되므로 프로젝트 Compose 구성만으로 설명되는 장애가 아니다.

**접근 거부의 최종 주체는 아직 확정하지 못했다.** 보안 정책, 보안 에이전트와의 충돌, 드라이버 상태 문제를 구분하는 후속 확인이 필요하다. Privacy-i가 설치·실행 중이고 관련 DLL 개입 기록이 있지만, 그것만으로 해당 제품이 P9Rdr 접근을 차단했다고 확정하지 않는다.

## 직접 확보한 근거

| 확인 | 결과 |
|---|---|
| `docker version`, `docker info` | `dockerDesktopLinuxEngine` named pipe 부재. 서버에 연결하지 못함 |
| Docker backend 로그 | `bootstrapping main distribution`, `Wsl/Service/CreateInstance/0xd0000022` |
| `wsl -d Ubuntu --exec /bin/true` | 동일 오류, 종료 코드 -1 |
| `wsl -d docker-desktop --exec /bin/true` | 동일 오류, 종료 코드 -1 |
| `wsl --system --exec /bin/true` | 동일 오류 |
| WSL 서비스 ETW | `filesystem.cpp(989)`, `hr=0xD0000022`, `message=Path: \Device\P9Rdr` |
| 일반 셸에서 P9Rdr 장치 열기 | 접근 권한 0·읽기·읽기/쓰기 모두 Win32 error 5 |
| Hypervisor·서비스 | `HypervisorPresent=True`, WslService·vmcompute 실행 중 |
| `p9rdr.sys` 서명·파일 ACL | Microsoft 서명 검증 정상. SYSTEM·Administrators 읽기/실행 허용 |
| 관리자 권한에서 드라이버 시작 | `sc.exe start p9rdr` 실패 5, `Access is denied` |
| System 이벤트 7000 | 09:39:32, Plan 9 Redirector Driver 시작 실패, `Access is denied` |
| Security-Mitigations 이벤트 25 | 09:40:19, `sc.exe`의 `Privacy-i\PIProtectorAPI64.dll`에서 shadow stack mismatch. 해당 이벤트는 실행을 허용했다고 명시하므로 직접 차단 증거로 보지 않음 |

환경은 Windows 10.0.26200.8875, WSL 2.7.11.0, WSL kernel 6.18.33.2-2, Docker Desktop 로그 기준 4.84.0, Docker CLI 29.6.2다. `wsl --status`의 WSL1 미지원 안내는 WSL2 배포판에서 발생한 이번 오류의 직접 원인으로 확인되지 않았다.

Microsoft WSL 담당자가 [같은 오류 유형의 공개 이슈](https://github.com/microsoft/WSL/issues/12591)에서 P9Rdr 접근 거부와 보안 소프트웨어 확인 필요성을 설명한 바 있다. 이는 조사 방향의 참고이며 이 PC의 특정 보안 제품에 대한 확정 판정은 아니다. [공식 WSL 문제 해결 안내](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting)도 참고한다.

## 실행한 조치

1. Docker backend 로그와 WSL·가상화·서비스 상태를 확인했다.
2. 원래 Ubuntu·docker-desktop은 모두 Stopped였다. 시험 실행 후 남은 WSL VM을 `wsl --shutdown`으로 종료하고 재시험했지만 오류가 유지됐다.
3. 일반 셸에서는 WslService 재시작 권한이 거부되어 관리자 PowerShell로 재시작했다. Ubuntu·docker-desktop 재시험 모두 실패했다.
4. WSL Manager ETW provider만 짧게 수집해 P9Rdr 접근 거부를 확인했다. 로컬 파일로 저장했으며 외부에 업로드하지 않았다.
5. WSL 종료 후 WslService를 정지하고 p9rdr 드라이버 재시작을 시도했다. 드라이버 중지는 성공했지만 재시작이 거부됐다. finally 처리로 WslService를 다시 시작했다.
6. 관리자 `sc.exe start p9rdr`로 복원 시도를 한 차례 더 했지만 오류 5로 거부됐다.
7. 사용자 선택에 따라 PC 재부팅은 보류했다. 시험용 WSL VM은 종료했다.

## 현재 상태와 보존 범위

- **p9rdr는 Stopped 상태다.** 이번 재시작 시도에서 중지 후 시작이 거부되어 실행 상태를 복원하지 못했다.
- WslService·vmcompute는 Running이다. Docker 엔진은 여전히 사용할 수 없다.
- Ubuntu·docker-desktop 배포판 등록, VHDX, Docker 이미지·볼륨을 삭제하거나 초기화하지 않았다.
- `p9rdr.sys`, 파일 ACL, 서비스 보안 설정, 보안 프로그램·보안 정책은 변경하지 않았다.
- Docker Desktop 재설치, WSL unregister, factory reset, Windows 기능 변경은 하지 않았다.
- 애플리케이션 코드 변경 없이 이 진단 문서와 Git 제외 대상인 로컬 진단 파일만 추가했다. 앞선 프로젝트 변경은 유지했다.

## 후속 조치

보안 관리자 또는 PC 관리자에게 아래 내용을 전달한다. 보안 에이전트 전체 중지나 광범위한 예외 추가 대신 실제 거부 로그와 허용 범위를 확인한다.

> WSLService 2.7.11.0이 `\Device\P9Rdr`를 열 때 `0xD0000022`가 발생합니다. 관리자 `sc start p9rdr`도 오류 5로 실패합니다. 2026-10-27 09:38~09:40 KST의 P9Rdr 접근·드라이버 로드 거부 로그와 Privacy-i 정책 적용 여부를 확인해 주세요. WSL/Docker 사용이 허용된 장치라면 해당 경로·서명된 드라이버에 필요한 정책을 제품에서 지원하는 방식으로 조정해 주세요.

관리자 확인과 필요한 조치 후, 사용자가 열려 있는 작업을 저장하고 허용하는 시점에 PC를 재부팅한다. 재부팅만으로 해결된다고 보장하지 않는다. 재부팅 후 다음 순서로 확인한다.

```powershell
Get-Service p9rdr, WslService
wsl -d Ubuntu --exec /bin/true
docker desktop start
docker info
docker run --rm hello-world
```

Ubuntu 명령이 성공하고 Docker 서버 정보가 표시되어야 기동 복구로 판정한다. `hello-world`는 최초 실행 시 이미지 다운로드가 필요하며, 이번에는 실행하지 못했다. 그 다음 프로젝트의 이미지 build·Compose 기동 검증을 진행한다. 실제 LLM 호출은 계속 보류한다.

로컬 증거는 `.cache/docker-recovery/`에 있다. `repair-result.json`, `wsl-service.etl`, `wsl-events-full.xml`, `driver-restart-result.json`, `driver-start.txt`를 보존했다. Docker 원본 로그는 `%LOCALAPPDATA%\Docker\log\host\com.docker.backend.exe.log`다. 외부 전달 전에는 로그의 사용자명·경로 등 정보를 검토한다.
