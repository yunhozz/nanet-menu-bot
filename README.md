# nanet-menu

국회도서관 식단표 PDF에서 오늘의 박물관식당 중식 메뉴를 수집하고,
NAVER API HUB 이미지 검색 결과와 함께 Slack에 게시·고정하는 Python 3.12 프로젝트입니다.
Firebase가 평일 오전 10시(`Asia/Seoul`)에 실행하며, 한국 공휴일에는 건너뜁니다.

## 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m nanet_menu --dry-run
python -m nanet_menu --date YYYY-MM-DD --dry-run
```

기본 날짜는 서울 시간의 오늘입니다. `--dry-run`은 인증 정보 없이 메시지 텍스트를 확인합니다.
실제 게시에는 아래 환경변수를 설정하고 `python -m nanet_menu`를 실행합니다.

| 환경변수 | 용도 |
| --- | --- |
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token |
| `SLACK_CHANNEL_ID` | 게시할 채널 ID |
| `NAVER_API_HUB_CLIENT_ID` | NAVER API HUB Client ID |
| `NAVER_API_HUB_CLIENT_SECRET` | NAVER API HUB Client Secret |
| `SLACK_ALERT_WEBHOOK_URL` | CLI·GitHub Actions 오류 알림용 Incoming Webhook URL (선택) |

NAVER Cloud Platform에서 NAVER API HUB 애플리케이션을 등록해 인증 정보를 발급받습니다.
이미지 검색에 실패하면 해당 메뉴는 이미지 없이 게시합니다.

## Slack 설정

1. Slack 앱에 Bot Token Scope `chat:write`, `pins:write`를 추가하고 워크스페이스에 설치합니다.
2. 봇을 게시할 채널에 초대하고 토큰과 채널 ID를 확인합니다.
3. **Socket Mode**를 끄고 **Interactivity & Shortcuts**를 켭니다.
4. 아래 재시도 함수 배포 후 **Request URL**에 `retry_daily_menu`의 HTTPS URL을 저장합니다.

## Firebase 배포

Firebase CLI 로그인과 Blaze 요금제가 필요합니다. 대상 프로젝트는 `nanet-menu`,
함수 리전은 `asia-northeast3`입니다. Firestore Native `(default)` 데이터베이스와
Firestore·Eventarc 관련 API, 이벤트 전달 권한, 함수 실행 계정의 Firestore 읽기·쓰기
권한(예: `roles/datastore.user`) 및 Secret 접근 권한을 준비합니다.

Firebase Secret Manager에 아래 값을 등록합니다. 같은 Secret은 여러 함수가 공유합니다.

| 함수 | 필요한 Secret |
| --- | --- |
| `post_daily_menu` | `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET` |
| `retry_daily_menu` | `SLACK_SIGNING_SECRET`, `SLACK_CHANNEL_ID` |
| `process_retry_outbox` | `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `GITHUB_ACTIONS_TOKEN` |

표의 각 Secret 이름으로 아래 명령을 반복합니다.

```bash
firebase functions:secrets:set SLACK_BOT_TOKEN --project nanet-menu
```

`SLACK_SIGNING_SECRET`은 Slack 앱의 **Basic Information → App Credentials → Signing Secret**에서 확인합니다.
`GITHUB_ACTIONS_TOKEN`은 이 저장소에 **Actions: Read and write** 권한을 가진
fine-grained personal access token이며, 재시도 worker가 GitHub Actions를 실행할 때 사용합니다.

먼저 아래 GitHub Actions 설정을 마치고 `daily-menu.yml`을 기본 브랜치에 반영합니다.
재시도 함수와 worker를 배포한 뒤 Slack의 Request URL을 저장하고 예약 함수를 배포합니다.

```bash
firebase deploy --project nanet-menu --only functions:retry_daily_menu,functions:process_retry_outbox
# Slack Interactivity Request URL 설정 후 실행
firebase deploy --project nanet-menu --only functions:post_daily_menu
```

## GitHub Actions

[Daily menu](.github/workflows/daily-menu.yml)는 `workflow_dispatch` 수동 실행만 제공합니다.
**Actions → Daily menu → Run workflow**에서 날짜(`target_date`)를 선택할 수 있으며,
기본값은 dry-run입니다. 실제 게시하려면 dry-run을 끕니다.

실제 게시에는 저장소의 Actions Secrets에 `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`,
`NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET`을 등록합니다.
오류 알림에는 선택적으로 `SLACK_ALERT_WEBHOOK_URL`을 추가합니다.
`GITHUB_ACTIONS_TOKEN`은 Firebase Secret Manager에 등록합니다.

## 실패 시 재시도

Firebase 예약 전송이 실패하면 Slack 채널에 재시도 버튼을 게시합니다. 채널 구성원 누구나
실패한 날짜의 재전송을 요청할 수 있습니다. Firestore 원자 선점으로 같은 실패 알림의
중복 요청을 막고, worker가 버튼을 공유 진행 상태로 바꾼 뒤 GitHub Actions를 실행합니다.
화면 갱신 전 버튼이 잠시 보여도 추가 실행 요청은 차단됩니다.

GitHub 접수 여부가 불명확한 타임아웃·오류는 자동 재시도하지 않습니다.
관리자가 Actions 실행 내역과 함수 로그를 확인해야 합니다.
실제 Slack cold/warm 환경에서의 3초 이내 ACK 응답은 아직 검증하지 않았습니다.

## 개발 확인

```bash
ruff check .
ruff format --check .
pytest
```

기본 `pytest`는 외부 사이트 통합 검사(`integration`)와 Firestore Emulator 검사
(`firestore_emulator`)를 제외합니다.
