# 예약 식단 전송 안정성 보완 구현 계획

> **실행 담당:** Orchestrator가 Implementer → 필수 QA → 작업 수용·집중 커밋 → 최종 독립 Reviewer 순서를 조정합니다. 체크박스는 하나의 통합 작업 안의 진행 단계이며 별도 커밋 단위가 아닙니다.

**목표:** 인스턴스 준비 상태와 Nanet 수집 재시도를 보완하고, 불확실한 Slack 게시의 자동 반복 및 안전하지 않은 재전송 안내를 차단합니다.

**구조:** 준비와 전송의 경계를 공용 실행 함수에서 기록합니다. 수집 원인과 게시 진행 상황을 구조화해 Firebase·CLI의 실패 안내에 전달하며, 메시지를 생성하는 Slack 요청은 단일 시도로 제한합니다.

**기술:** 기존 Python, requests, Firebase Functions, pytest, responses, Ruff. 새 외부 의존성이나 저장소를 추가하지 않습니다.

**승인 설계:** /Users/ma-24-007/Desktop/workspace/nanet-menu-bot/docs/superpowers/specs/2026-10-28-scheduled-menu-resilience-design.md

**설계 승인 근거:** 2026-10-28 사용자 승인, 본문 SHA-256 e1267fae7eae1557b84604dad7345838794db85af3ab69f95ee1c4759dea88c3. 이후 추가된 승인 상태·메타데이터는 본문 변경이 아닙니다.

**계획 승인 근거:** 2026-10-28 사용자 승인, 승인 당시 SHA-256 2f332dddf47a988a418d29a9e3c70f9f0661b9b3038e76b191f57da5464c15a9. 이후의 진행 상태 기록은 승인된 기술 내용을 변경하지 않습니다.

**계획 저장 대상:** /Users/ma-24-007/Desktop/workspace/nanet-menu-bot/docs/superpowers/plans/2026-10-28-scheduled-menu-resilience.md

## 전역 제약

- 작업 공간: /Users/ma-24-007/Desktop/workspace/nanet-menu-bot
- 브랜치: fix/scheduled-menu-resilience
- 구현 기준 HEAD: 7f144f0bcdd0857d9ecdd78a1dba1feac8e607c4
- 준비 상태: HEAD는 `7f144f0bcdd0857d9ecdd78a1dba1feac8e607c4`이며, 승인된 설계 문서와 구현 계획 문서가 모두 untracked입니다.
- min_instances=1을 추가합니다. 배포 후 유휴 비용은 사용자가 수용했습니다.
- max_instances=1, concurrency=1, retry_count=0, 평일 10:00 Asia/Seoul, 함수 제한 300초를 유지합니다.
- 전체 run 또는 build_messages 재시도는 추가하지 않습니다.
- 메시지 생성용 chat.postMessage, incoming webhook 및 pins.add는 단일 호출입니다. HTTP 429도 자동 재시도하지 않습니다.
- 준비 단계의 기존 GET 재시도만 보완합니다. 최대 5회, timeout (10, 30)초, 대기 5, 10, 20, 30초, 첫 GET부터 수집 예산 180초를 사용합니다.
- 수집 예산은 협력적 제한이며 엄격한 wall-clock 종료 보장이 아닙니다.
- 확인된 메뉴 게시 전 실패에만 재전송 CTA를 제공합니다. 부분 게시·고정 실패·결과 불명에는 제공하지 않습니다.
- retry_store.py, Firestore 스키마, 재전송 worker 상태 전이, GitHub workflow, 시크릿, 배포 인프라는 변경하지 않습니다.
- Cloud 배포·Cloud Console 변경·Slack/Nanet 등 외부 서비스 실호출은 수행하지 않습니다.
- 테스트는 HTTP·시간·서비스를 모의 처리합니다. 실제 재시도 대기나 운영 자격 증명은 사용하지 않습니다.
- 정확히 한 번 전송을 보장하지 않습니다. 향후 플랫폼 429는 해당 예약 실행 유실로 이어질 수 있습니다.

## 검토 초점

다음 다섯 조건을 통합 작업의 테스트에 포함합니다.

1. 게시 응답 유실·5xx·부분 성공 오류 뒤 같은 메시지 생성 POST가 반복되지 않아야 합니다.
2. 첫 payload 성공 후 후속 게시 또는 고정 실패 시 진행 정보가 보존되고 전체 재전송 CTA가 없어야 합니다.
3. 예외 종류나 문구가 같더라도 명시적인 단계 증거가 없으면 안전한 준비 실패로 추정하지 않아야 합니다.
4. 여러 후보 게시물의 GET과 대기가 같은 수집 예산을 공유하고, 집계 후에도 원인 정보가 보존돼야 합니다.
5. 공용 Slack 호출기 변경이 기존 재전송 worker의 상태 갱신 및 dispatch 제어를 손상하지 않아야 합니다.

---

## T1: 예약 실행·수집·Slack 전송 안전성 통합 보완

**선행 조건:** 승인 설계와 본 계획의 승인, 대상 브랜치·HEAD·보존할 문서 상태 확인.

**목표:** C1–C5를 하나의 일관된 변경으로 구현합니다. 설정·오류 계약·전송·실패 안내가 겹치므로 분리 수용하지 않습니다.

### 파일과 소유권

아래 구현 파일의 편집은 Implementer가 담당합니다. 모든 상대 경로는 다음 절대 경로를 기준으로 합니다.

/Users/ma-24-007/Desktop/workspace/nanet-menu-bot/

| 파일 | 책임 |
|---|---|
| src/nanet_menu/config.py | 수집 대기·예산 기본값 |
| src/nanet_menu/errors.py | HTTP 원인·실행 단계·게시 진행 정보 |
| src/nanet_menu/collector.py | 누적 예산과 기존 GET 재시도 보완 |
| src/nanet_menu/app.py | 준비/전송 경계, 원인 보존, 실패 시 중단 |
| src/nanet_menu/slack.py | 단일 게시·고정 호출과 결과 분류 |
| src/nanet_menu/firebase_function.py | 최소 인스턴스 및 안전한 실패 안내 |
| src/nanet_menu/__main__.py | CLI 실패 안내에 같은 정책 적용 |
| src/nanet_menu/formatter.py | 상태별 문구와 CTA 차단 |
| tests/test_collector.py | 수집 정책 |
| tests/test_app.py | 단계·부분 게시·원인 집계 |
| tests/test_slack.py | 단일 시도와 결과 분류 |
| tests/test_firebase_function.py | 함수 설정·알림·기존 worker 회귀 |
| tests/test_main.py | CLI 알림 |
| tests/test_formatter.py | CTA와 확인 안내 |
| README.md | 설정 영향·비용·미배포 상태·수동 복구 |

새 오류 계약의 직접 단위 검증이 필요하면 tests/test_errors.py 하나를 추가할 수 있습니다. 나머지 파일 추가·수정은 필요성과 범위를 Orchestrator에게 먼저 보고합니다.

QA와 Reviewer는 읽기 및 승인된 검증을 담당합니다. Git 변경과 커밋은 Orchestrator만 수행합니다.

### 공유 계약

다음은 이 작업 내부의 구현 계약입니다. 외부 사용 중인 기존 함수의 인자와 예외 생성 방식은 가능한 한 호환되게 유지합니다.

**HTTP 원인 정보 — errors.py**

- HttpFailure: 오류 종류, 원래 예외 종류, HTTP 상태, 실제 시도 횟수, 경과 시간, 예산 소진 여부를 담는 불변 데이터.
- CollectionError(message: str, *, failure: HttpFailure | None = None)
- MenuParseError(message: str, *, causes: tuple[NanetMenuError, ...] = ())

기존 문자열 생성자를 계속 사용할 수 있어야 합니다. build_messages는 후보 실패를 집계할 때 원래 예외를 causes에 보존합니다. 문자열 매칭으로 재시도 여부나 전송 안전성을 판단하지 않습니다.

**실행 실패 정보 — errors.py**

- DeliveryFailure: stage, pre_send_confirmed, posted_count, total_count, payload_index, message_timestamps, outcome_unknown을 담는 불변 데이터.
- stage: "prepare", "post", "pin", "unknown" 중 하나.
- payload 위치는 1부터 시작하며 준비 전에는 전체 수·위치가 없을 수 있습니다.
- DeliveryError(message: str, *, failure: DeliveryFailure)는 NanetMenuError를 상속합니다.
- confirmed_pre_send_failure(error: Exception) -> bool는 명시적인 DeliveryError의 준비 실패 증거가 있는 경우에만 True를 반환합니다.

DeliveryError는 raise ... from original로 최초 원인을 보존합니다. 일반 MenuParseError, SlackError 또는 임의 예외만으로는 CTA를 허용하지 않습니다.

**Slack 결과 — slack.py, errors.py**

- post_and_pin_to_slack(...) -> str: 게시와 고정 성공 시 게시 ts 반환.
- SlackError는 기존 문자열 생성과 호환하면서 method, outcome_unknown, message_ts 정보를 선택적으로 보유합니다.
- 게시 후 고정 실패는 확인된 message_ts를 포함합니다.
- post_message_to_slack 등 기존 진입점은 유지하되 모든 chat.postMessage 성공 응답에 유효한 ts가 있는지 검증합니다.
- max_attempts가 전달돼도 메시지 생성과 고정의 단일 호출 정책을 우회하지 못합니다.
- 기존 chat.update의 재시도 정책은 유지합니다.

**수집 예산 — collector.py**

- NanetCollector.ensure_budget() -> None: 첫 GET 이후 잔여 예산을 확인하고 소진 시 구조화된 CollectionError를 발생시킵니다.
- 생성 시점에는 예산을 시작하지 않습니다. 첫 GET 직전에 시작합니다.
- app.build_messages는 새 후보 처리 시작 전에 예산을 확인합니다.
- 모든 GET과 대기 직전에도 확인합니다. 후보 전환으로 예산을 재설정하지 않습니다.
- 파싱 등을 강제 중단하지 않습니다. 이후 GET을 시작할 때는 monotonic 경과 시간이 반영됩니다.

### 수용 기준

| ID | 기준 |
|---|---|
| C1 | min_instances=1과 유지할 예약·실행 설정을 로컬에서 확인하고 플랫폼 429 잔여 위험을 기록 |
| C2 | 불확실한 메시지 생성 응답에 추가 POST 없음. 부분 게시·고정 실패·결과 불명 CTA 차단, 확인된 준비 실패 CTA 유지 |
| C3 | 대기·5회 제한·누적 예산·원인 보존 구현, 소진 후 추가 요청 없음 |
| C4 | 수용한 비용 및 배포 후 적용 시점 문서화, 실제 배포·운영 변경·외부 발송 없음 |
| C5 | 지정 범위와 관련 단위 테스트·기존 재전송 동작 검증 |

### 구현 순서와 TDD

- [ ] **1. 오류 계약과 수집 경계의 실패 테스트를 작성합니다.**

  tests/test_collector.py 및 필요 시 tests/test_errors.py에서 다음을 검증합니다.

  - 기존 문자열 예외 생성 호환
  - 성공까지의 대기 5, 10, 20, 30초와 최대 5회
  - ConnectTimeout 최종 원인·횟수·경과 시간
  - HTTP 404 단일 시도와 429·5xx의 기존 GET 재시도
  - 첫 GET 전에는 예산 미시작
  - 여러 URL이 하나의 180초 예산 공유
  - GET 직전·대기 직전 소진 시 추가 동작 없음
  - 잔여 예산보다 긴 대기와 timeout 제한
  - 대기로 예산이 소진되면 다음 GET 없음

  시간과 HTTP를 모의 처리하며 실제 대기를 하지 않습니다.

- [ ] **2. 해당 테스트를 실행해 새 정책에 대한 실패를 확인합니다.**

      pytest tests/test_collector.py -q

  tests/test_errors.py를 추가했다면 같은 실행에 포함합니다. 예상 실패는 기존 대기값·예산 부재·구조화된 정보 부재입니다. 환경 오류를 의도한 실패로 취급하지 않습니다.

- [ ] **3. config.py, errors.py, collector.py의 최소 구현을 작성합니다.**

  공유 계약과 승인값을 적용합니다. 각 대기·GET 전에 잔여 시간을 확인하고 양수인 잔여 시간으로만 요청 timeout을 제한합니다. 예산 소진 오류는 시도 횟수와 원인을 보존합니다.

- [ ] **4. 수집 집중 테스트를 재실행해 통과를 확인합니다.**

  2단계와 동일한 명령을 사용합니다.

- [ ] **5. Slack 단일 호출 정책의 실패 테스트를 작성합니다.**

  tests/test_slack.py에서 메시지 생성 API와 webhook을 검증합니다.

  - timeout, ConnectionError, 5xx, 429 각각 POST 1회, 추가 대기 없음
  - max_attempts=3 이상을 전달해도 POST 1회
  - JSON 오류, 성공 응답의 ts 누락, internal_error, fatal_error는 결과 불명
  - 429의 Retry-After 누락·잘못된 값도 자동 재시도나 추가 파싱 오류를 유발하지 않음
  - 게시 실패 시 고정 호출 없음
  - 게시 성공 후 고정 실패 시 게시 1회·고정 1회, ts 보존
  - 정상 게시·고정 성공 시 ts 반환
  - 기존 chat.update 성공·재시도 동작 유지
  - URL·토큰 등 비밀값의 로그 노출 없음

- [ ] **6. Slack 집중 테스트의 의도한 실패를 확인합니다.**

      pytest tests/test_slack.py -q

- [ ] **7. slack.py의 메서드별 호출 정책과 오류 정보를 구현합니다.**

  메시지 생성과 고정은 단일 시도로 제한합니다. webhook에도 같은 정책을 적용합니다. 기존 재전송 worker가 사용하는 chat.update 분기를 보존합니다.

- [ ] **8. Slack 집중 테스트를 재실행해 통과를 확인합니다.**

  6단계와 동일한 명령을 사용합니다.

- [ ] **9. 실행 단계와 양쪽 알림 경로의 실패 테스트를 작성합니다.**

  기존 app·Firebase·CLI·formatter 테스트에 다음을 추가하거나 수정합니다.

  | 조건 | 기대 결과 |
  |---|---|
  | 수집·설정·파싱 실패가 준비 단계에서 발생 | 명시적인 준비 실패, 메뉴 게시 0회 |
  | 후보 CollectionError가 집계됨 | MenuParseError.causes에 구조화된 원인 보존 |
  | 첫 게시 결과 불명 | 확인된 게시 수 0, 결과 불명, 후속 전송 없음 |
  | 첫 payload 성공 후 두 번째 게시 실패 | 확인된 게시 수와 ts, 실패 위치 보존, 세 번째 호출 없음 |
  | 첫 payload 게시 후 고정 실패 | 해당 게시를 확인된 수에 포함, 고정 실패 단계, 후속 게시 없음 |
  | 실제 준비 단계의 예상 밖 예외 | 단계 경계로 준비 실패 확인, 최초 원인 보존 |
  | 단계 정보 없는 예외가 알림에 직접 전달됨 | 결과 불명 안내, CTA 없음 |
  | 준비 실패 알림 | Firebase 버튼·CLI 재시도 URL 유지 |
  | 부분 게시·고정 실패·불명 알림 | Firebase와 CLI 모두 버튼·재시도 URL 없음 |
  | formatter에 불명 상태와 retry 인자가 함께 전달됨 | 불명 상태가 우선해 CTA와 재실행 안내 문구 제거 |
  | 실패 알림 자체 전송 실패 | 재게시 없음, 최초 오류 보존 및 별도 로그 |
  | dry-run 또는 공휴일 | 기존 외부 발송 차단 유지 |

  formatter에는 새 선택 인자 retry_allowed: bool = False를 추가합니다. True는 공용 명시적 준비 실패 판별을 통과한 호출부만 전달합니다. False일 때 retry_date·retry_url이 있어도 CTA를 만들지 않습니다. 단순 Actions 실행 로그 링크는 유지할 수 있습니다.

- [ ] **10. 단계·알림 집중 테스트의 의도한 실패를 확인합니다.**

      pytest tests/test_app.py tests/test_firebase_function.py tests/test_main.py tests/test_formatter.py -q

- [ ] **11. app.py의 단계 경계와 진행 정보를 구현합니다.**

  준비 구간과 전송 구간을 명시적으로 구분합니다. 준비 중 예외는 명시적인 준비 실패로 감싸고, 전송 중 예외는 확인된 사실과 불확실성을 보존합니다. 성공한 ts를 누적하고 고정 실패의 알려진 ts도 게시 확인 수에 포함합니다.

  후보 처리에서는 예산과 원인을 보존하되 전체 준비를 재시도하지 않습니다. 전송 실패 후 남은 payload를 게시하지 않습니다.

- [ ] **12. Firebase·CLI·formatter의 알림 정책을 연결합니다.**

  confirmed_pre_send_failure를 공통으로 사용합니다. Firebase는 안전한 준비 실패에만 retry_date와 허용 상태를 전달하고, CLI는 같은 경우에만 retry_url과 허용 상태를 전달합니다.

  부분 게시·고정 실패·결과 불명에는 채널과 로그 확인을 안내합니다. 결과 불명 알림을 다시 자동 발송하지 않고 최초 오류를 보존합니다.

- [ ] **13. 예약 함수의 최소 인스턴스 선언과 설정 검증을 추가합니다.**

  min_instances=1을 추가하고 다른 설정을 유지합니다. 기존 Firebase endpoint 메타데이터 검증 방식을 따라 선언값을 확인하는 단위 테스트를 추가합니다. 실제 scheduler·Cloud API는 호출하지 않습니다.

- [ ] **14. 단계·알림 집중 테스트를 재실행해 통과를 확인합니다.**

  10단계와 같은 명령을 사용합니다. 기존 재전송 worker 테스트도 이 실행 범위에 포함됩니다.

- [ ] **15. README의 운영 설명을 변경합니다.**

  다음 사실을 반영합니다.

  - 최소 인스턴스의 비용 수용과 실제 적용은 향후 배포 후라는 점
  - Scheduler 자동 재시도 없음과 향후 429 실행 유실 가능성
  - 준비 실패에만 재전송 CTA 제공
  - 게시 불명·부분 게시·고정 실패의 채널 및 로그 확인 절차
  - 수집 대기·예산값과 엄격한 wall-clock 보장 부재
  - 정확히 한 번 전송 및 운영 장애 해소 미보장

  기존 재전송 worker 설명은 그대로 유지하면서 실패 버튼의 제공 조건만 정확하게 좁힙니다.

### 수용 전 필수 QA

- [ ] **16. Implementer가 변경 범위와 집중 테스트 증거를 Orchestrator에게 반환합니다.**

  파일 목록, 명령별 결과, 최초 오류 보존 방식, 단일 POST 및 CTA 차단 증거를 보고합니다. 외부 호출 여부와 남은 실패를 명시합니다. Implementer는 수용이나 Git 변경을 수행하지 않습니다.

- [ ] **17. Orchestrator가 현재 변경에 대해 독립 QA를 배정합니다.**

  QA는 C1–C5와 다섯 검토 초점을 확인하고 다음 명령을 실행합니다.

      ruff check .
      ruff format --check .
      pytest

  기본 pytest 설정의 integration, firestore_emulator 제외를 유지합니다. -m 재정의, 실서버 호출, 운영 자격 증명, 배포 명령을 사용하지 않습니다.

  QA 증거에는 실행 대상 HEAD·현재 변경 목록, 명령·종료 상태·테스트 요약, C1–C5별 판단, 실제 외부 호출이 없었다는 검증 방식과 한계를 포함합니다. 기본 테스트가 통과한 경우 근거 없이 반복하거나 범위를 확대하지 않습니다.

- [ ] **18. QA finding이 있으면 Implementer 수정 → 필요한 QA 재검증을 완료합니다.**

  실패 범위와 의존성을 분류해 영향을 받는 수용만 보류합니다. 변경된 동작에 필요한 집중 검사와 lint/format을 다시 확인합니다. 미해결 필수 검사가 있으면 수용하지 않습니다.

- [ ] **19. Orchestrator가 T1의 수용 근거를 확인하고 하나의 집중 구현 커밋을 수행합니다.**

  수용에는 C1–C5 충족, 필수 QA 통과, finding 해소, 지정 범위 준수 및 외부 실행 금지 준수 증거가 필요합니다. 승인 설계·계획 문서는 보존하며 관련 문서를 포함한 커밋 범위는 명시적으로 확인합니다.

  커밋 제목 제안: fix(menu): 예약 식단 전송 안정성 보완

  별도 사용자 커밋 승인을 추가하지 않습니다. push·배포는 하지 않습니다.

### 최종 전체 변경 검토

- [ ] **20. 작업 수용과 필수 QA 완료 후 독립 Reviewer가 전체 변경을 한 번 검토합니다.**

  기준 HEAD부터 최종 변경 전체를 승인 설계·계획과 비교합니다. 중점은 메시지 생성 재시도 우회, 잘못된 CTA 허용, 부분 게시 수 계산, 공유 예산 누락, worker 회귀, 문서의 과도한 보장입니다.

  Reviewer는 작업별 게이트가 아닙니다. 최종 전체 변경 검토이며 수용 후 커밋된 전체 상태를 대상으로 합니다.

- [ ] **21. 최종 Reviewer finding은 Implementer → Reviewer 재검토로 해소합니다.**

  최종 계획 검토 단계의 finding은 동작 finding을 포함해 QA에 재배정하지 않습니다. Implementer가 필요한 수정 및 검증 증거를 제공하고 Reviewer가 재확인합니다. 초기 최종 검토 전의 필수 QA와 QA 실패 재검증을 생략하는 근거로 사용하지 않습니다.

  Orchestrator는 모든 finding 및 필수 검사 상태를 확인한 뒤 전체 작업 완료를 판단합니다.

## 위험·보류·완료 증거

- 본 계획의 사용자 승인이 확인됐으며, 승인된 범위의 구현과 로컬 검증 실행이 허가됐습니다.
- 예산·단계 분류·범위 변경이 필요하면 종속 작업 전에 Orchestrator에게 보고합니다.
- 테스트 또는 도구 실패는 최초 결과와 현재 HEAD·변경 상태를 기록합니다. 환경을 임의 변경하거나 우회하지 않습니다.
- 운영 429 감소와 실제 Nanet 회복은 배포·운영 성공 기록 없이는 미검증입니다. 이번 완료 조건은 로컬 구현과 검증입니다.
- retry_count=0이어도 Scheduler 중복 실행 가능성은 남습니다. 영속적인 실행 회차별 중복 제어는 없습니다.
- 불확실한 전송을 재게시하지 않아 실제 미게시 메시지가 수동 확인 전까지 누락될 수 있습니다.
- 최종 보고에는 커밋, 변경 요약, QA·Reviewer 결과, 미배포 상태와 잔여 한계를 포함합니다.

## 자체 검토

- **설계 충족:** C1–C5, 승인값, 제외 범위 및 잔여 429 유실 가능성을 T1에 연결했습니다.
- **작업 단위:** 하나의 상위 작업이며 하위 체크박스를 별도 수용·커밋 단위로 만들지 않았습니다.
- **계약 일관성:** 준비 실패 판별, 수집 원인, Slack ts, 게시 진행 정보와 CTA 허용 상태를 같은 계약으로 연결했습니다.
- **검증:** 각 동작에 실패 테스트→실패 확인→최소 구현→통과 확인을 배치하고, 작업 수용 전 독립 QA를 필수로 두었습니다.
- **검토 경계:** 최종 Reviewer를 작업별 검사가 아닌 전체 변경 단계로 배치하고 finding 재검토 경로를 보존했습니다.
- **범위:** 배포·외부 실호출·새 저장소·worker 상태 변경을 추가하지 않았습니다.
- **작성 상태:** 계획이 지정 경로에 저장되고 사용자 승인을 받아 구현 준비가 완료됐습니다. 이 문서 상태 시점에는 코드·테스트 변경과 검증 실행을 아직 수행하지 않았습니다.

미해결 사항: 없습니다.

참조:

- 승인 설계: /Users/ma-24-007/Desktop/workspace/nanet-menu-bot/docs/superpowers/specs/2026-10-28-scheduled-menu-resilience-design.md
- 계획 작성 참조: /Users/ma-24-007/.codex/skills/arc/references/writing-plans.md
- 앞 단계 설계 참조: /Users/ma-24-007/.codex/skills/arc/references/brainstorming.md
