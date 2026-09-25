# nanet-menu

국회도서관 주간 식단표 PDF에서 기준일의 박물관식당 중식 메뉴를 읽어
Slack에 게시하고, 해당 메시지를 고정하는 Python 3.12 프로젝트입니다.
Firebase 예약 함수는 평일 오전 10시(서울 시간)에 실행됩니다.

## 동작 방식

1. `식단` 공지 목록에서 게시물의 실제 `searchNoSeq`를 확인합니다.
2. 제목의 날짜 범위가 기준일을 포함하는 공지를 우선 선택합니다. 제목만으로
   날짜를 확정하기 어렵거나 PDF 처리가 실패하면 최근 공지를 차례로 확인합니다.
3. 상세 페이지의 `newViewerCall(...)` 인자에서 PDF 시스템 파일명을 찾아
   내려받고, `pdfplumber`로 표를 읽습니다.
4. 기준일의 박물관식당 중식 메뉴를 신뢰성 있게 찾지 못하면 실패 처리하고
   Slack에는 게시하지 않습니다.

현재 사용하는 Hancom PDF에는 텍스트가 포함되어 있어 OCR을 사용하지 않습니다.

## 로컬 설치와 실행

Python 3.12 환경에서 개발 의존성과 함께 설치합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

`--dry-run`을 사용하면 Slack에 게시하지 않고 메시지 내용을 확인할 수
있습니다. 기본 기준일은 `Asia/Seoul`의 오늘이며, `--date`로 날짜를 지정할
수도 있습니다.

```bash
python -m nanet_menu --dry-run
python -m nanet_menu --date 2026-07-29 --dry-run
```

실제 Slack 게시에는 다음 환경변수가 필요합니다.

```bash
export SLACK_BOT_TOKEN="<Slack Bot User OAuth Token>"
export SLACK_CHANNEL_ID="<게시할 Slack 채널 ID>"
export NAVER_API_HUB_CLIENT_ID="<NAVER API HUB Client ID>"
export NAVER_API_HUB_CLIENT_SECRET="<NAVER API HUB Client Secret>"
python -m nanet_menu
```

`SLACK_ALERT_WEBHOOK_URL`은 CLI와 GitHub Actions의 오류 알림에 사용하는
선택 환경변수입니다. 설정하면 실행 실패 시 별도 운영 채널로 오류를
전송합니다. GitHub Actions에서 실행한 경우에는 해당 실행 로그 링크도
알림에 포함됩니다. Webhook URL은 코드, 설정 파일, 로그에 저장하지 마세요.

Firebase 예약 함수가 실패하면 `SLACK_BOT_TOKEN`과 `SLACK_CHANNEL_ID`로
식단 채널에 오류 알림과 수동 재시도 버튼을 게시합니다. 버튼을 눌러 연
GitHub Actions 화면에서 `dry_run` 옵션을 해제해야 실제 Slack 게시가
실행됩니다.

각 메뉴 항목에는 NAVER API HUB 이미지 검색 결과 중 제목에 메뉴명이 포함된
이미지의 썸네일을 표시합니다. 일치하는 결과가 없거나 검색에 실패하면 해당
항목은 이미지 없이 표시됩니다.
NAVER Cloud Platform 콘솔에서 NAVER API HUB 애플리케이션을 등록하고 Client
ID와 Client Secret을 발급받으세요. 실제 Slack 게시에는 이미지 검색 인증 정보가
필요하지만, `--dry-run`은 인증 정보 없이 텍스트를 확인할 수 있습니다. Slack
메시지 하나의 블록 수가 50개를 넘으면 식당과 식사 구분을 유지해 여러 메시지로
나눠 게시합니다.

## 테스트와 린트

기본 테스트는 fixture와 HTTP 모킹을 사용하므로 외부 사이트에 접속하지
않습니다.

```bash
ruff check .
ruff format --check .
pytest
```

실사이트 통합 검사는 다음 명령으로 별도 실행합니다.

```bash
pytest -m integration
```

## Firebase 예약 전송 설정

1. Slack 앱의 Bot Token Scope에 `chat:write`와 `pins:write`를 추가합니다.
2. 앱을 워크스페이스에 설치하고 Bot User OAuth Token을 발급받습니다.
   게시할 채널에 앱을 초대하고, Slack 채널 상세 화면에서 채널 ID를
   확인합니다.
3. Firebase 프로젝트를 Blaze 요금제로 전환하고 Firebase CLI에 로그인합니다.
4. 프로젝트 루트에서 사용할 Firebase 프로젝트를 선택합니다.

   ```bash
   firebase use --add
   ```

5. Firebase Secret Manager에 필요한 값을 등록합니다.

   ```bash
   firebase functions:secrets:set SLACK_BOT_TOKEN
   firebase functions:secrets:set SLACK_CHANNEL_ID
   firebase functions:secrets:set NAVER_API_HUB_CLIENT_ID
   firebase functions:secrets:set NAVER_API_HUB_CLIENT_SECRET
   ```

6. 예약 함수를 배포합니다.

   ```bash
   firebase deploy --only functions:post_daily_menu
   ```

`post_daily_menu`는 서울 리전(`asia-northeast3`)에서 월요일부터 금요일까지
오전 10시에 실행됩니다. 대한민국 공휴일과 대체공휴일에는 메뉴 수집과 Slack
게시를 건너뜁니다. 네 개의 Secret은 함수의 런타임 환경변수로 연결됩니다.

## GitHub Actions 수동 실행

`.github/workflows/daily-menu.yml`에는 예약 트리거가 없습니다. Firebase 예약
전송과 중복되지 않도록 수동 dry-run 또는 긴급 게시에만 사용합니다. 실제
게시에는 GitHub 저장소에 `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`,
`NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET`의 네 Actions Secret을
등록해야 합니다. 오류 알림도 사용하려면 `SLACK_ALERT_WEBHOOK_URL`도
등록하세요.

**Actions → Daily menu → Run workflow**에서 실행할 수 있습니다. 기본값은
dry-run이며, 실제 게시가 필요할 때만 dry-run 옵션을 끄세요.

## 장애 확인과 PDF 변경 대응

Cloud Logging이나 수동 Actions 실행 로그에서 `목록 수집`, `게시물 선택`,
`PDF 다운로드`, `PDF 파싱`, `오늘 식단 선택`, `Slack 전송` 중 어느 단계에서
실패했는지 확인합니다. Webhook URL과 전체 HTTP 헤더는 로그에 남기지 않습니다.

국회도서관 사이트 HTML이 바뀌면 `tests/fixtures/notice_*.html`에 비밀정보가
없는 최소 표본을 갱신하고 `collector.py`의 선택자를 수정합니다. PDF 표의
날짜·식당·식사 배치가 바뀌면 민감 정보가 없는 최소 표본이나 간소화한 표
데이터를 `test_pdf_parser.py`에 추가해 문제를 재현한 뒤 `pdf_parser.py`를
수정합니다. PDF가 이미지형으로 바뀐 사실을 확인하기 전에는 OCR을 추가하지
않습니다.
