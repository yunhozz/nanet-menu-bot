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

Firebase 예약 함수가 실패하면 식단 채널에 오류 알림과 실패 날짜를 다시
실행하는 Slack 버튼을 게시합니다. 버튼 요청은 서명과 채널을 확인하는 Firebase
HTTPS 함수로 전달되므로 GitHub Actions 워크플로 실행/쓰기 권한이 없는 채널
구성원도 사용할 수 있습니다.

각 메뉴 항목은 NAVER API HUB 이미지 검색 결과에서 제목에 메뉴명이 포함된 첫
유효 이미지 URL을 표시합니다. 일치하는 유효 결과가 없으면 같은 검색 응답의 첫
유효 이미지를 사용합니다. 각 결과는 썸네일 URL을 우선 사용하고 유효하지 않으면
링크 URL을 확인합니다. 사용 가능한 이미지가 없거나 검색에 실패하면 해당 항목은
이미지 없이 표시됩니다.
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

5. Firebase Secret Manager에 필요한 값을 등록합니다. `SLACK_SIGNING_SECRET`은
   Slack 앱의 **Basic Information → App Credentials → Signing Secret**입니다.
   `GITHUB_ACTIONS_TOKEN`은 대상 저장소만 허용한 fine-grained personal access
   token으로, **Actions: Read and write** 권한이 필요합니다.

   ```bash
   firebase functions:secrets:set SLACK_BOT_TOKEN
   firebase functions:secrets:set SLACK_CHANNEL_ID
   firebase functions:secrets:set NAVER_API_HUB_CLIENT_ID
   firebase functions:secrets:set NAVER_API_HUB_CLIENT_SECRET
   firebase functions:secrets:set SLACK_SIGNING_SECRET
   firebase functions:secrets:set GITHUB_ACTIONS_TOKEN
   ```

6. `target_date` 입력을 포함한 `.github/workflows/daily-menu.yml` 변경을
   저장소 기본 브랜치에 먼저 병합해 게시합니다.

7. 아래 Firestore·Eventarc 전제와 출시 검증을 충족한 뒤, Slack 재시도 HTTPS
   엔드포인트와 outbox worker를 함께 배포합니다.

   ```bash
   firebase deploy --only functions:retry_daily_menu,functions:process_retry_outbox
   ```

8. 저장소에는 Slack Socket Mode 클라이언트가 없으며 재시도 함수는 HTTPS 요청을
   받습니다. Slack 앱에서 **Socket Mode**를 끄세요. Socket Mode가 켜져 있으면
   **Request URL** 입력란이 표시되지 않습니다. 이후 **Interactivity & Shortcuts**를
   켜고 **Request URL**에 배포 명령 출력에서 `retry_daily_menu` 함수의 HTTPS URL을
   복사해 입력한 뒤 설정을 저장합니다.

9. Request URL 설정을 저장한 다음에 예약 함수를 배포합니다. 그래야 새 실패
   알림이 동작하는 재시도 엔드포인트를 가리킵니다.

   ```bash
   firebase deploy --only functions:post_daily_menu
   ```

`post_daily_menu`는 서울 리전(`asia-northeast3`)에서 월요일부터 금요일까지
오전 10시에 실행됩니다. 대한민국 공휴일과 대체공휴일에는 메뉴 수집과 Slack
게시를 건너뜁니다. 네 개의 Secret은 함수의 런타임 환경변수로 연결됩니다.
HTTPS 재시도 함수에는 `SLACK_SIGNING_SECRET`, `SLACK_CHANNEL_ID`가 연결됩니다.
`process_retry_outbox` worker에는 `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`,
`GITHUB_ACTIONS_TOKEN`이 연결됩니다. worker도 서울 리전에서 실행됩니다.

## GitHub Actions 수동 실행

`.github/workflows/daily-menu.yml`에는 예약 트리거가 없습니다. Firebase 예약
전송과 중복되지 않도록 수동 dry-run 또는 긴급 게시에만 사용합니다. 실제
게시에는 GitHub 저장소에 `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`,
`NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET`의 네 Actions Secret을
등록해야 합니다. 오류 알림도 사용하려면 `SLACK_ALERT_WEBHOOK_URL`도
등록하세요.

**Actions → Daily menu → Run workflow**에서 실행할 수 있습니다. 기본값은
dry-run이며, 실제 게시가 필요할 때만 dry-run 옵션을 끄세요.
재시도 버튼은 클릭한 날짜를 `target_date`로 전달하고 실제 Slack 게시를
시작합니다. `GITHUB_ACTIONS_TOKEN`은 Firebase Secret Manager에만 등록하면
됩니다. 같은 원본 메시지를 여러 명이 동시에 눌러도 원본 상태와 outbox를
조건부 atomic commit으로 선점한 시도만 worker 처리 대상이 됩니다.

## 재전송 실행과 공유 상태

`retry_daily_menu`는 Slack 서명·5분 timestamp·채널·action·날짜·원본 메시지를
검증한 뒤 Firestore에 원본 상태와 outbox를 함께 저장합니다. 유효한 요청은
성공·중복·오래된 generation·저장 오류에서도 빈 본문의 HTTP 200 ACK를 반환합니다.
ACK는 사용자에게 개별 안내를 보내는 메시지가 아니며, 처리 접수나 화면 갱신을
보장하지 않습니다. ACK 경로에서 Slack·GitHub를 호출하거나 응답 후 background
작업을 이어가지 않습니다. `response_url`도 사용하거나 저장하지 않습니다.

`slack_retry_outbox/{attempt_id}` 생성 trigger가 원본과 outbox의 식별·날짜·세대를
대조하고 worker 소유권을 한 번 선점합니다. 원본 Slack 메시지의 해당 재시도 버튼을
없애고 **식단 재전송 중**이라는 비대화형 상태를 표시합니다. Slack Button에는
`disabled` 속성이 없으므로 버튼 자체를 진행 문구로 대체합니다. 원래 실패 본문과
관계없는 버튼은 보존합니다. 표시가 반영되기 전 잠시 버튼이 보여도 Firestore가
같은 원본의 추가 시도를 막습니다.

진행 표시와 `dispatching` 저장이 모두 성공한 뒤 GitHub POST를 한 번만 실행합니다.
진행 표시·소유권·상태 저장이 실패하면 POST하지 않습니다. POST는 자동 재시도와
redirect를 사용하지 않습니다. 접수가 확인되면 버튼 없는 접수 안내를 표시하고,
결과 식단은 기존 GitHub Actions 실행이 새 메시지로 게시합니다. 표시·저장 장애나
worker 중단 후에는 기존 소유권을 탈취하거나 이벤트 재전달로 다시 dispatch하지
않습니다.

GitHub `200`·`204`만 접수 확인입니다. 명확한 거절 allowlist는 현재 비어 있어,
미검증 `2xx/3xx/4xx/5xx`와 timeout·transport 오류는 모두 `unknown`으로 남습니다.
자동 버튼 복원 없이 관리자 확인이 필요하며, `403` 같은 응답도 코드만으로
미접수라고 단정하지 않습니다. 확정 거절 복구 경로는 합성 분류 입력으로만
검증했으며 실제 HTTP 거절 코드의 근거를 확보했다는 뜻이 아닙니다.

향후 공식 근거로 확정 거절을 분류할 때는 새 generation을 `retryable`로 먼저
저장한 뒤 새 버튼을 게시합니다. 게시 실패에서는 그 generation이 여전히 미선점일
때만 `blocked`로 바꿉니다. 게시 timeout 중 새 클릭이 먼저 선점했다면 새 시도를
보존합니다. Slack 게시와 차단 저장이 함께 실패하면 `retryable`이 남고 늦게 보인
버튼이 한 번 선점될 수 있습니다. 두 시스템을 하나의 transaction으로 묶는 보장은
없습니다.

## 재전송 서비스 전제와 출시 확인

프로젝트에는 Firestore Native `(default)` database와 Firestore API가 준비돼 있어야
합니다. 원본 상태·outbox를 읽고 쓰는 실제 Functions runtime 서비스 계정에 필요한
Firestore IAM(예: `roles/datastore.user`)과 연결된 Secret 접근 권한이 필요합니다.
운영 client는 runtime의 명시적인 `GCLOUD_PROJECT` 또는 `GOOGLE_CLOUD_PROJECT`와
ADC를 사용해 지연 초기화합니다. 모듈 import나 일반 CLI가 ADC를 조회하지 않으며,
로컬 Emulator client는 검증된 loopback transport를 별도로 주입합니다.

Firestore 생성 trigger를 전달할 Eventarc 및 관련 서비스가 준비돼야 합니다.
실제 trigger·runtime principal을 기준으로 이벤트 수신과 대상 함수 호출 권한을
확인하고, outbox가 해당 프로젝트·기본 database의 생성 이벤트로 전달되는지
확인합니다. API 활성화·database 생성·IAM 변경·배포는 별도 운영 작업입니다.

로컬 handler 측정 기준은 진입부터 응답 반환까지 2초이고, Firestore 의존 작업에는
진입 시점부터 1.5초 예산을 적용합니다. SDK 초기화·인증·platform cold start 전체를
강제 중단하는 보장은 없습니다. 실제 플랫폼 cold/warm ACK 3초 미만은 미검증
출시 gate이며, 실제 환경에서 별도로 확인하기 전까지 출시를 보류합니다.

## 잠금 상태와 수동 복구

`channel_id + message_ts + action_id`의 alert key로 원본 상태와 연결된 attempt의
outbox를 찾습니다. 사용자 ID는 키에 없고, 같은 날짜의 다른 원본 메시지는 독립적입니다.
Cloud Logging의 단계 오류와 상태의 `created_at`·`updated_at`·응답 코드를 참고해
GitHub Actions 실행의 날짜·시각과 대조합니다. 원본 payload·서명·token·response URL을
로그나 복구 메모에 복사하지 않습니다.

- `accepted`는 재요청하지 않고 해당 실행 결과와 Slack 표시만 확인합니다.
- `unknown`, `dispatching`, `preparing`, 장기간 `queued`, `blocked`는 자동 해제하지
  않습니다. GitHub 접수 여부와 worker 중단 지점을 확인하고 표시 장애를 구분합니다.
- 미접수가 확인돼 수동 재개하기로 결정한 경우에도 현재 버전·소유권을 확인한
  조건부 갱신으로 새 generation을 먼저 유효하게 만든 뒤 버튼을 게시해야 합니다.
  기존 잠금 삭제, outbox 재생성·재전달, 이전 generation 재사용으로 우회하지 않습니다.

자동 TTL·lease 만료·소유권 탈취는 없습니다. 수동 복구를 실행하는 API나 배포 작업도
이 코드가 자동 수행하지 않습니다.

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

## 재전송 저장 계약과 격리 검증

재전송 저장 계층은 `(default)` database의
`slack_retry_requests/{alert_key}`와 `slack_retry_outbox/{attempt_id}`를 사용합니다.
`alert_key`는 채널 ID·원본 timestamp 문자열·action ID를 고정 순서 JSON 배열로
직렬화한 SHA-256입니다. 사용자와 날짜는 키에 넣지 않습니다. 최초 `initial` 또는
현재 `retryable` generation만 원본 상태와 outbox를 한 atomic commit으로 생성하며,
worker ID와 attempt ID가 일치하는 실행만 후속 상태를 갱신합니다.

Firebase 실행·개발 의존성은 `google-cloud-firestore==2.28.0`으로 고정합니다.
공개 generated `FirestoreClient`의 nontransactional `batch_get_documents`로 문서와
`update_time`을 읽고 `commit`에 관찰 버전의 precondition을 전달합니다. 없는 원본과
outbox는 `exists=false`, 기존 원본 변경은 `update_time` 일치를 요구합니다.
원본 변경과 outbox 생성은 하나의 atomic commit에 포함됩니다. worker 선점·상태
변경도 같은 버전 CAS를 사용하므로 관찰 이후 바뀐 상태를 덮어쓰지 못합니다.
각 RPC는 `retry=None`과 monotonic 잔여 예산 이하의 timeout을 사용합니다.
`exists=false` create precondition을 포함한 commit의 `AlreadyExists`만 확인된
경쟁으로 처리합니다. update-only commit의 `AlreadyExists`와 모든 미확인
`FailedPrecondition`은 저장 오류로 유지합니다. 현재 공개 구조화 오류 정보에서
update_time 거절을 확인할 근거가 없어 영문 message 부분일치로 분류하지 않습니다.
다른 오류·`ABORTED`·commit 응답 유실은 저장 오류로 처리하며 자동 재시도나
보상 쓰기를 하지 않습니다. transaction을 시작하거나 rollback하지 않습니다.
[공개 GAPIC API](https://cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.services.firestore.FirestoreClient)
계약을 사용하며 private SDK API에 의존하지 않습니다.

Firestore 애플리케이션 예산은 1.5초입니다. 초기화·인증·callback 전체 시간을 SDK가
강제 중단한다는 보장은 없습니다. 로컬 handler 2초 측정과 실제 플랫폼 cold/warm
ACK 3초 미만의 출시 검증은 별도로 필요합니다. 운영 client는 Firebase runtime에서
명시한 프로젝트와 ADC로 초기화해 `RetryStore`에 주입합니다. 모듈 import·일반 CLI·
테스트 수집은 client나 ADC를 초기화하지 않습니다.

저장 메시지는 기존 실패 알림의 `text`, `blocks`, 선택 `mrkdwn`만 허용하고,
blocks 내부도 header/section/context/actions와 해당 텍스트·버튼 필드만 허용합니다.
원본 interaction payload, `response_url`, token, 서명은 저장하지 않습니다.
지원하지 않는 필드나 크기 초과 입력은 부분 저장하거나 잘라 저장하지 않습니다.

크기 계산은 Firestore의 [저장 크기 산식](https://firebase.google.com/docs/firestore/storage-size)과
[문서 한도 1 MiB](https://firebase.google.com/docs/firestore/quotas#collections_documents_and_fields)를
사용합니다. 문자열은 UTF-8 bytes + 1, 정수·timestamp는 8 bytes, bool/null은 1 byte,
array는 원소 합, map은 필드 이름·값 합 + 32 bytes입니다. 문서 이름은 collection과
ID 문자열 크기 합 + 16 bytes입니다. attempt/generation은 ASCII 128자 이내,
alert key는 64자, 날짜는 10자이므로 outbox 이름·메타데이터·`message` 필드 이름의
최대 비용은 **624 bytes**입니다. 따라서 메시지 map의 허용 상한은
**1,048,576 − 624 = 1,047,952 bytes**입니다. 최대 metadata envelope로 계산해
실제 식별자 길이에 관계없이 안전한 경계를 사용합니다. 두 문서 commit은
10 MiB 요청 한도보다 작으며 단일 문자열도 문서보다 작은 이 경계에 포함됩니다.
한글 오류 2,800자를 포함한 기존 실패 알림의 측정값은 **17,670 bytes**입니다.
JSON의 따옴표 escape는 저장 문자열의 추가 비용이 아니며 한글 한 글자는 UTF-8
3 bytes로 계산합니다. 경계 직전·경계·초과와 한글·escape는 단위 테스트로 확인합니다.

기본 pytest는 `integration`과 `firestore_emulator` marker를 제외합니다.
Emulator 검증은 Orchestrator가 격리 lifecycle과 정리를 관리하는 환경에서 실행합니다.
테스트는 `FIRESTORE_EMULATOR_HOST`가 없으면 client/ADC 생성 전에 skip합니다.
설정값은 `127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>`만 허용하며,
잘못된 값에서 원격 서비스로 fallback하지 않습니다. 공개 `FirestoreGrpcTransport`에
명시적인 insecure loopback channel과 `AnonymousCredentials`를 전달하고,
테스트 프로젝트는 `demo-slack-retry`로 고정합니다. 실제 Firebase·Slack·GitHub
호출과 배포는 이 검증에 포함하지 않습니다.

```bash
python -m pytest tests/test_retry_store.py -q
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 python -m pytest tests/test_retry_store_emulator.py -m firestore_emulator -q -o addopts=''
```

Emulator 테스트는 매 실행 고유 ID의 문서만 만들고 fixture 종료 시 실제 생성된
문서를 확인해 삭제하고, 시도한 모든 경로가 없는지 다시 확인합니다. 정리 전용
`delete_document`의 잠금·timeout 오류는 대상·시각·횟수를 기록하고 최대 3회만
재시도하며, 마지막 오류는 테스트 실패로 남깁니다. 이 정리 재시도는 제품 CAS에
적용하지 않습니다. transport는 정리 실패 때도 종료합니다. Orchestrator는 테스트 후
Emulator 프로세스와 임시 데이터를 정리합니다.
미설정으로 인한 skip은 경쟁 검증 합격이 아닙니다. 20개 동시 클릭의 원본·outbox
단일 선점과 worker 단일 소유권을 확인하지만 Emulator의 contention·지연 특성은
운영 Firestore와 다르므로 운영 성능이나 실제 ACK 시간을 입증하지 않습니다.
1.5초 예산 내 경쟁 요청 일부는 `DeadlineExceeded`로 끝날 수 있습니다. 이 경우는
중복 거절로 바꾸지 않고 저장 오류로 유지하며, commit 여부를 추정하거나 후속
외부 효과를 시작하지 않습니다. 경쟁 검사는 단일 선점·outbox·worker 소유권과
독립 timestamp를 확인하고 성공·명확한 거절·저장 오류 수를 함께 출력합니다.
