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
python -m nanet_menu
```

Webhook URL은 코드, 설정 파일, 로그에 저장하지 마십시오.

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

## Slack과 GitHub 설정

1. Slack에서 앱을 만들고 **Incoming Webhooks**를 활성화합니다.
2. **Add New Webhook to Workspace**에서 고정 채널을 선택합니다.
3. GitHub 저장소의 **Settings → Secrets and variables → Actions**에서
   `SLACK_WEBHOOK_URL` 이름으로 Webhook URL을 등록합니다.
4. 저장소의 **Actions** 탭에서 워크플로 실행을 허용합니다.

`daily-menu.yml`은 월요일부터 금요일까지 `Asia/Seoul` 오전 09:07에 실행됩니다. 정각의 GitHub
Actions 예약 실행 혼잡을 피하려고 7분을 사용합니다. 예약 워크플로는
default branch에 있는 최신 워크플로만 실행되므로 해당 파일을 default
branch에 반영해야 합니다. 수동 실행은 **Actions → Daily menu → Run
workflow**에서 하며 기본값은 안전한 dry-run입니다.

## 장애 확인과 PDF 변경 대응

Actions 실행 로그에서 `목록 수집`, `게시물 선택`, `PDF 다운로드`, `PDF
파싱`, `오늘 식단 선택`, `Slack 전송` 중 실패한 단계를 확인합니다.
Webhook URL이나 전체 HTTP 헤더는 로그에 남지 않습니다.

사이트 HTML이 바뀌면 `tests/fixtures/notice_*.html`을 비밀정보 없이
최소 표본으로 갱신하고 `collector.py`의 선택자를 수정합니다. PDF 표의
날짜·식당·식사 배치가 바뀌면 민감 정보가 없는 최소 표본 또는 최소화한
표 데이터를 `test_pdf_parser.py`에 추가해 실패를 재현한 다음
`pdf_parser.py`를 수정합니다. 이미지형 PDF로 바뀐 사실이 확인되기
전에는 OCR을 추가하지 않습니다.
