# nanet-menu

국회도서관 공지사항의 최신 주간식단표 PDF에서 오늘 식단을 읽어 평일 아침
Slack Incoming Webhook으로 전송하는 Python 3.12 프로젝트입니다.

## 동작 방식

봇은 `식단` 공지 목록에서 게시물의 실제 `searchNoSeq`를 읽고, 제목의 날짜
범위가 기준일을 포함하는 공지를 우선 처리합니다. 상세 페이지의
`newViewerCall(...)` 인자에서 시스템 파일명을 찾아 PDF를 내려받은 뒤
`pdfplumber`로 표를 추출합니다. 제목만으로 확정하지 못하거나 PDF가 바뀐
경우에는 최근 후보를 차례로 확인하되, 날짜나 메뉴를 신뢰성 있게 찾지
못하면 Slack에 아무것도 보내지 않고 실패합니다.

현재 PDF는 텍스트가 포함된 Hancom PDF이므로 OCR 의존성은 없습니다.

## 로컬 설치와 실행

Python 3.12 환경에서 다음과 같이 설치합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Slack에 보내지 않고 현재 날짜 또는 지정 날짜의 메시지를 확인할 수 있습니다.
날짜 기준은 `Asia/Seoul`입니다.

```bash
python -m nanet_menu --dry-run
python -m nanet_menu --date 2026-07-29 --dry-run
```

실제 전송에는 환경변수가 필요합니다.

```bash
export SLACK_WEBHOOK_URL="<Slack Incoming Webhook URL>"
export SLACK_ALERT_WEBHOOK_URL="<별도 운영 채널의 Incoming Webhook URL>"
export NAVER_API_HUB_CLIENT_ID="<NAVER API HUB Client ID>"
export NAVER_API_HUB_CLIENT_SECRET="<NAVER API HUB Client Secret>"
python -m nanet_menu
```

`SLACK_ALERT_WEBHOOK_URL`은 선택 사항입니다. 설정하면 메뉴 수집·파싱 또는
Slack 전송 실패 시 별도 운영 채널에 오류와 GitHub Actions 실행 링크를
전송합니다. Webhook URL은 코드, 설정 파일, 로그에 저장하지 마십시오.

각 음식에는 NAVER API HUB 이미지 검색의 첫 번째 결과가 썸네일로 표시됩니다.
NAVER Cloud Platform 콘솔에서 NAVER API HUB 애플리케이션을 등록한 뒤 Client
ID와 Client Secret을 발급받아야 합니다. 개별 음식의 검색 실패나 결과 없음은
해당 음식만 이미지 없이 표시하며, 인증 정보가 없으면 실제 Slack 전송은
실패합니다. 음식별 블록이 Slack의 메시지당 50블록 제한을 넘으면 식당·식사
구분을 유지한 채 여러 메시지로 나눠 전송합니다. `--dry-run`은 인증 정보
없이도 텍스트를 확인할 수 있습니다.

## 테스트와 린트

기본 테스트는 fixture와 HTTP 모킹을 사용하므로 외부 사이트에 접속하지
않습니다.

```bash
ruff check .
ruff format --check .
pytest
```

실사이트 통합 검사는 명시적으로 실행합니다.

```bash
pytest -m integration
```

## Slack과 Firebase 설정

1. Slack에서 앱을 만들고 **Incoming Webhooks**를 활성화합니다.
2. **Add New Webhook to Workspace**에서 고정 채널을 선택합니다.
3. Firebase 프로젝트를 Blaze 요금제로 전환하고 Firebase CLI로 로그인합니다.
4. 프로젝트 루트에서 사용할 프로젝트를 선택합니다.

   ```bash
   firebase use --add
   ```

5. Firebase Secret Manager에 아래 세 값을 등록합니다.

   ```bash
   firebase functions:secrets:set SLACK_WEBHOOK_URL
   firebase functions:secrets:set NAVER_API_HUB_CLIENT_ID
   firebase functions:secrets:set NAVER_API_HUB_CLIENT_SECRET
   ```

6. 예약 함수를 배포합니다.

   ```bash
   firebase deploy --only functions:post_daily_menu
   ```

`post_daily_menu` 함수는 월요일부터 금요일까지 `Asia/Seoul` 오전 10:00 정각에
서울 리전(`asia-northeast3`)에서 실행됩니다. 예약 함수에는 세 Secret이
런타임 환경변수로만 연결됩니다.

GitHub의 `daily-menu.yml`은 중복 예약 전송을 막기 위해 예약 트리거 없이 수동
dry-run/긴급 전송 수단으로만 남겨 둡니다. 사용하려면 GitHub 저장소에도 같은
이름의 Actions Secret을 등록하고 **Actions → Daily menu → Run workflow**에서
실행합니다. 기본값은 안전한 dry-run입니다.

## 장애 확인과 PDF 변경 대응

Cloud Logging 또는 수동 Actions 실행 로그에서 `목록 수집`, `게시물 선택`, `PDF 다운로드`, `PDF
파싱`, `오늘 식단 선택`, `Slack 전송` 중 실패한 단계를 확인합니다.
Webhook URL이나 전체 HTTP 헤더는 로그에 남지 않습니다.

사이트 HTML이 바뀌면 `tests/fixtures/notice_*.html`을 비밀정보 없이
최소 표본으로 갱신하고 `collector.py`의 선택자를 수정합니다. PDF 표의
날짜·식당·식사 배치가 바뀌면 민감 정보가 없는 최소 표본 또는 최소화한
표 데이터를 `test_pdf_parser.py`에 추가해 실패를 재현한 다음
`pdf_parser.py`를 수정합니다. 이미지형 PDF로 바뀐 사실이 확인되기
전에는 OCR을 추가하지 않습니다.
