# Slack 식단 재전송 중복 방지 구현 계획

> **승인 상태:** 사용자는 본 계획, TDD/QA 로컬 검증, SDK 전체 deadline 한계를 반영한 시간 기준 변경안을 2026-10-20에 승인했다. 공개 GAPIC의 조건부 atomic commit은 승인된 원자 선점·RPC 예산을 구현하는 T1 저장 방식이며 데이터 모델·처리 흐름·외부 실행 범위를 변경하지 않는다. 로컬 구현·수용과 실제 플랫폼 cold/warm ACK 3초 출시 gate는 구분한다.

> **에이전트 실행 지침:** 저장된 승인 계획과 ARC 역할·승인·검증 계약에 따라 Coder가 구현하고 QA가 지정 검증을 수행하며, Orchestrator만 작업 수용과 Git 변경을 수행한다.

**목표:** Firestore 원자 선점과 outbox로 원본 Slack 실패 알림별 중복 dispatch를 막고, 빠른 ACK와 공유 진행 상태를 제공한다.

**구조:** HTTPS handler가 원본 선점과 outbox 생성을 하나의 조건부 atomic commit으로 저장한 뒤 ACK한다. 별도 Firestore 생성 trigger worker가 처리권을 선점하고, 공유 진행 표시 성공 후 GitHub dispatch를 한 번 호출한다.

**기술:** Python 3.12, Firebase Functions, `google-cloud-firestore==2.28.0` 공개 generated GAPIC, Cloud Firestore, 기존 Slack wrapper와 `requests`, pytest, 격리된 Firestore Emulator.

**승인된 설계:** `/Users/ma-24-007/Desktop/workspace/nanet-menu-bot/docs/superpowers/specs/2026-10-20-slack-retry-dedup-design.md`

## 대상과 승인 상태

- 저장소: `/Users/ma-24-007/Desktop/workspace/nanet-menu-bot`
- 브랜치: `feat/slack-retry-dedup`
- 기준 HEAD: `d38623f4734ce6f633f06e1690dde0cf587e3470`
- 사용자 선택에 따라 Orchestrator가 브랜치를 준비하고 동일 HEAD와 clean 상태를 확인했다.
- 초기 계획 승인 시점에는 제품 코드 변경이 없었다. 이후 T1 구현·검증 파일은 아래 QA 근거와 함께 Orchestrator가 수용했다.
- 인계 기록: `/Users/ma-24-007/Desktop/workspace/nanet-menu-bot/.git/arc/handoff/slack-retry-dedup.md`
- 사용자는 2026-10-20에 Firestore outbox·별도 생성 trigger worker 구조와 formal spec을 승인했다.
- 사용자는 2026-10-20에 본 계획과 TDD/QA를 위한 pytest 및 격리된 loopback Firestore Emulator 테스트 추가·실행도 승인했다.
- 승인된 테스트 범위는 로컬·격리 환경이며 실제 Firebase 프로젝트, Slack/GitHub 호출, provisioning, IAM 변경과 배포는 제외한다.

Orchestrator는 계획 저장·승인 및 Coder 인계 시 실제 HEAD, 변경 목록, 문서 버전과 소유권을 확인한다. 기존 승인과 준비 근거는 변경 없는 범위에서 재사용한다.

## 전역 제약

- 원본 키는 `channel_id + message.ts + action_id`다. 날짜와 사용자 ID는 포함하지 않는다.
- 문서 ID는 위 세 문자열의 고정 순서 JSON 배열을 UTF-8로 직렬화한 SHA-256 hex 값이다.
- `(default)` database의 `slack_retry_requests/{alert_key}`, `slack_retry_outbox/{attempt_id}`를 사용한다.
- 최초 date-only 버튼은 `initial` generation으로 처리한다.
- 복원 버튼은 `{"v":1,"target_date":"YYYY-MM-DD","generation":"..."}` 형식이다.
- 상태는 `queued`, `preparing`, `dispatching`, `accepted`, `retryable`, `unknown`, `blocked`다.
- 읽기·검증과 조건부 commit 사이에서 Slack·GitHub 호출을 하지 않고, 저장 시도 전에 식별자를 생성한다.
- ACK 전에 Slack·GitHub를 호출하지 않는다.
- 유효한 interaction의 ACK는 빈 본문 HTTP `200`이며 개별 메시지 표시를 뜻하지 않는다.
- handler 반환의 로컬 측정 기준은 2초 이내다. Firestore 의존 작업에는 handler 진입 기준 1.5초의 애플리케이션 예산을 두고, 제어 가능한 RPC에 남은 시간을 배분한다.
- 개별 RPC timeout과 read→commit 시도 제한은 초기화·인증을 포함한 전체 실행 시간의 강제 상한이 아니다. 실제 플랫폼 cold/warm ACK 3초 미만은 이번 범위에서 미검증인 출시 조건으로 유지한다.
- GitHub `200`, `204`만 접수 확인으로 처리한다.
- 미검증 응답은 모두 `unknown`이다. 비어 있지 않은 명확한 거절 allowlist에는 공식 근거가 필요하다.
- 자동 POST 재시도·redirect 재전송·TTL·lease 탈취·불명 시도 재dispatch를 추가하지 않는다.
- `response_url`, token, 서명과 원본 HTTP payload를 저장하거나 로그로 출력하지 않는다.
- provisioning, API 활성화, IAM 변경, 배포와 실제 Slack/GitHub 호출은 수행하지 않는다.
- 테스트 계획의 서술은 테스트 작성·실행 권한을 의미하지 않는다.

### 메시지 크기 경계

임의의 `64 KiB` 제품 제한을 도입하지 않는다.

T1에서 기존 실패 알림 schema, 저장할 정규화 필드, 고정 메타데이터와 Firestore의 공식 문서·요청 크기 제한을 근거로 저장 가능한 메시지 경계를 산출한다. 한글·escape·필드 이름·문서 경로 등 실제 인코딩 비용을 측정해 경계값과 산식을 기록한다.

이 경계는 서비스를 이용하기 위한 저장 안전 조건이며 임의의 본문 축약 기준이 아니다. 정상적인 기존 실패 알림이 제외된다면 독단적으로 잘라 저장하지 않고 Orchestrator에 반환한다. 확정된 경계의 검증과 상수는 T1이 생산하고 T2가 재사용한다.

## 기존 진입점과 파일 책임

저장소 루트의 `main.py`는 현재 다음 두 함수를 import·export한다.

```python
post_daily_menu
retry_daily_menu
```

신규 `process_retry_outbox`를 같은 진입점에 추가한다. 기존 두 함수는 유지한다.

`firebase.json`의 functions source는 `.`이고 runtime은 `python312`다. 이를 그대로 사용하며 근거 없는 설정 변경을 계획하지 않는다.

| 파일 | 책임 |
|---|---|
| 신규 `src/nanet_menu/retry_store.py` | 원자 선점, outbox, 소유권, generation, 상태 전이, RPC budget과 저장 크기 경계 |
| 수정 `src/nanet_menu/firebase_function.py` | 검증·ACK, trigger worker, 메시지 변환, dispatch 결과 처리 |
| 수정 `main.py` | 신규 trigger import·export |
| 수정 `pyproject.toml` | SDK 의존성과 Emulator 테스트 marker·기본 제외 |
| 신규 `tests/test_retry_store.py` | 저장 계약·상태 전이·크기 경계 |
| 신규 `tests/test_retry_store_emulator.py` | 격리된 실제 CAS commit 경쟁 |
| 수정 `tests/test_firebase_function.py` | handler·worker·복구·ACK |
| 수정 `README.md` | 실행 구조, 서비스 전제, 검증·장애·수동 복구 |

`src/nanet_menu/slack.py`와 `.github/workflows/daily-menu.yml`의 기존 계약을 재사용한다.

## 검토 초점

| 조건 | 기대 동작 | 담당 |
|---|---|---|
| commit 적용 후 응답 유실 | 직접 dispatch·무조건 재commit 없음 | T1·T2 |
| worker 이벤트 동시 중복 전달 | worker 선점과 POST 각각 한 번 | T1·T2 |
| 복원 버튼 공개와 새 클릭이 경쟁 | 새 시도를 이전 worker가 덮어쓰지 않음 | T1·T2 |
| SDK 초기화·재시도 지연 | 개별 RPC 예산과 전체 시간 제어의 한계를 구분하고, 로컬 측정 및 미검증 출시 조건을 기록 | T1·T2 |
| GitHub 접수 후 저장·표시 실패 | 재dispatch 없이 상태·표시 장애 구분 | T2 |

## 공통 인터페이스

`retry_store.py`에 다음 모델을 정의한다.

```python
RetryIdentity(
    channel_id: str,
    message_ts: str,
    action_id: str,
)

RetryRequest(
    identity: RetryIdentity,
    target_date: str,
    generation: str,
    message: dict[str, Any],
)

AttemptOwner(
    alert_key: str,
    attempt_id: str,
    worker_id: str,
)

ClaimResult(
    outcome: Literal["claimed", "duplicate", "stale", "blocked"],
)
```

`RetryStoreError`는 초기화·RPC·commit 결과 불명 등 안전한 진행이 불가능한 저장 오류다.

저장 인터페이스는 다음으로 고정한다.

```python
alert_key(identity: RetryIdentity) -> str

validate_retry_message(message: dict[str, Any]) -> None

claim_and_enqueue(
    request: RetryRequest,
    *,
    attempt_id: str,
    deadline: float,
) -> ClaimResult

claim_worker(
    alert_key: str,
    *,
    attempt_id: str,
    worker_id: str,
) -> AttemptOwner | None

transition(
    owner: AttemptOwner,
    *,
    expected_state: str,
    new_state: str,
    reason: str | None = None,
    dispatch_status_code: int | None = None,
) -> bool

make_retryable(
    owner: AttemptOwner,
    *,
    next_generation: str,
    dispatch_status_code: int,
) -> bool

block_retryable(
    owner: AttemptOwner,
    *,
    generation: str,
    reason: str,
) -> bool
```

`validate_retry_message`는 지원 schema와 T1에서 산출한 저장 경계를 검사한다. 잘못된 입력은 `ValueError`로 구분한다. `claim_and_enqueue`도 저장 전에 이 검증을 적용한다.

`deadline`은 제어 가능한 후속 작업의 착수와 RPC timeout 배분에 사용하는 monotonic 절대 시각이다. SDK 초기화·인증·실행 중 callback을 강제 중단하는 전체 deadline을 뜻하지 않는다.

각 제어 지점에서 잔여 시간을 계산한다. 잔여 시간이 없으면 새로운 RPC나 재시도를 시작하지 않는다. 이미 시작한 commit이 예산을 넘겼거나 결과가 불명확하면 저장 성공을 추정하지 않으며, handler가 직접 dispatch하거나 무조건 재commit하지 않는다. 이미 성공한 durable handoff가 취소됐다고 가정하지 않는다.

상태 변경의 `False`는 상태·소유권 불일치이며 저장 오류와 구분한다.

`make_retryable`은 `dispatching` 소유권 검증, 거절 결과 저장, generation 회전과 `retryable` 전환을 원자적으로 수행한다. `block_retryable`은 이전 시도의 소유권과 복원 generation이 여전히 일치할 때만 변경한다.

---

## T1 — 원자 상태 저장과 outbox 계약

**목표:** 외부 API 호출 없이 원본·outbox 원자 저장, worker 선점, generation과 소유권 안전성을 완성한다.

**소유 파일**

- `src/nanet_menu/retry_store.py`
- `tests/test_retry_store.py`
- `tests/test_retry_store_emulator.py`
- `pyproject.toml`
- `README.md`의 SDK·데이터 계약·격리 검증 부분

**선행 조건:** 계획 및 테스트·검증 권한 승인 완료. Emulator 검증 전에 Orchestrator가 loopback 주소, 테스트 project ID와 임시 데이터·프로세스 정리 책임을 기록한다.

**생산 계약:** 위 공통 모델·저장 함수와 메시지 크기 검증. T2가 소비한다.

### 진행 단계

- [x] **T1.1 — 공개 GAPIC의 조건부 atomic commit과 RPC 예산을 적용한다.**
  `google-cloud-firestore==2.28.0`의 공개 generated `FirestoreClient`를 사용한다. 읽기 잠금을 유지하는 read-write transaction 대신, 비transactional `batch_get_documents`로 상태를 읽고 CAS precondition을 지정한 `commit`으로 변경한다. `batch_get_documents`에는 transaction·new_transaction·과거 read_time을 지정하지 않으며, `commit`에도 transaction ID를 지정하지 않는다.

  최초 원본이 없으면 원본 write에 `current_document.exists=false`를 적용한다. 원본이 존재하면 schema·날짜·상태·generation을 검증하고, 해당 문서의 정확한 `update_time`을 원본 write의 precondition으로 사용한다. `read_time`, 애플리케이션의 `updated_at` 또는 반올림한 시각으로 대체하지 않는다.

  원본의 `queued` 전환과 outbox 생성은 반드시 하나의 atomic commit에 포함한다. outbox write에는 별도로 `current_document.exists=false`를 적용한다. 조건 없는 write나 여러 commit으로 나누는 fallback을 두지 않는다. 비원자적인 `batch_write`로 대체하지 않는다.

  worker 선점과 후속 상태 변경도 같은 방식으로 원본 문서를 읽어 소유권·상태를 검증하고, 읽은 `update_time`을 조건으로 commit한다. 변경 판단에 영향을 주는 다른 가변 문서가 추가되면 그 문서의 변경도 commit에서 보호할 방법을 정하기 전까지 보류한다.

  각 호출의 read→검증→조건부 commit 시도는 1회다. 두 RPC에 `retry=None`과 monotonic 잔여 예산 이하의 timeout을 지정한다. Firestore 의존 작업 예산은 handler 진입 기준 1.5초이며, 소진 후 새로운 RPC를 시작하지 않는다. `begin_transaction`과 `rollback`은 사용하지 않는다.

  확인된 precondition 충돌은 미선점 또는 조건부 변경 실패로 반환한다. 충돌 후 자동 재조회·재선점·commit 재전송을 하지 않는다. timeout·transport 오류·원인이 확인되지 않은 `ABORTED` 등은 저장 결과 불명 또는 저장 오류로 처리하며 조건 충돌로 단정하지 않는다.

  식별자는 저장 시도 전에 생성한다. 읽기와 commit 사이에는 Slack·GitHub 호출 등 외부 부작용을 두지 않는다. client 초기화·인증·애플리케이션 처리 전체의 강제 deadline은 보증하지 않는다. RPC 예산 적용, 추가 호출 제한과 실제 경과 시간을 각각 확인한다. private SDK API나 응답 후 background 실행으로 우회하지 않는다.

- [x] **T1.2 — 저장 크기 경계를 산출한다.**
  기존 실패 알림의 schema와 정규화 후 저장 필드를 확인한다. Firestore 공식 크기 계산 기준 및 제한과 실제 인코딩 측정값으로 경계를 산출한다. 문서 경로·필드 이름·메타데이터와 한글·escape 비용을 포함한다. 산식·측정 방법·확정값을 테스트와 README에 기록한다. 기존 정상 알림이 제외되면 보류한다.

- [x] **T1.3 — 격리 테스트 경계를 먼저 정의한다.**
  `pyproject.toml`에 `firestore_emulator` marker를 등록한다. 기존 기본 제외를 유지하면서 기본 addopts를 다음처럼 확장한다.

  ```text
  -ra -m 'not integration and not firestore_emulator'
  ```

  Emulator 파일 전체에 해당 marker를 부여한다. 테스트 수집 시 GAPIC client·transport·credentials를 초기화하지 않는다. `FIRESTORE_EMULATOR_HOST`가 없으면 client 생성 전에 skip한다. 값이 있으면 host와 port를 검증하고 명시적으로 허용한 loopback 주소만 받는다. 원격 주소, 불완전한 endpoint와 지원하지 않는 형식은 실패시킨다. 검증 실패를 운영 endpoint 또는 ADC로 fallback하지 않는다.

  검증된 endpoint를 공개 GAPIC transport에 직접 연결하고, 익명 credentials 및 Emulator용 비보안 gRPC channel을 명시한다. GAPIC이 `FIRESTORE_EMULATOR_HOST`나 익명 credentials를 자동 적용한다고 가정하지 않는다. 테스트 전용 project ID를 사용하며 실제 프로젝트 ID·서비스 계정·Secret을 요구하지 않는다.

  host 미설정이면 client·ADC 초기화 전에 skip하고, 잘못된 host이면 연결 전에 실패해야 한다. 허용된 host에서는 설정한 loopback transport만 사용하고 ADC 탐색·token 발급 경로를 호출하지 않아야 한다. client 최초 사용 경로도 운영 endpoint로 fallback해서는 안 된다. client/transport와 임시 문서는 테스트 종료 후 정리한다.

- [x] **T1.4 — 저장 계약의 실패 테스트를 작성한다.**
  테스트 허용 후 다음 이름과 assertion을 하나씩 추가하고 RED를 확인한다.

  ```python
  def test_initial_claim_atomically_creates_one_outbox():
      assert result.outcome == "claimed"
      assert state["state"] == "queued"
      assert state["attempt_id"] == outbox["attempt_id"]

  def test_old_generation_cannot_claim_after_rotation():
      assert old_result.outcome == "stale"
      assert new_result.outcome == "claimed"

  def test_stale_owner_cannot_update_new_attempt():
      assert changed is False
      assert current_attempt_id == new_attempt_id
  ```

  키의 timestamp 문자열 보존, 최초 `initial` 제한, 날짜·schema 불일치, 미지 상태와 산출한 크기 경계의 직전·경계·초과 입력도 각각 확인한다.

- [x] **T1.5 — RED 확인 후 조건부 저장을 최소 구현한다.**
  최초 `initial` 또는 현재 `retryable` generation만 선점한다. 비transactional 읽기로 확인한 원본의 미존재 또는 정확한 `update_time`을 조건으로 사용하고, 원본 변경과 outbox 생성을 한 atomic commit에 저장한다. outbox에도 미존재 조건을 적용한다.

  정규화·크기·상태 검증 실패 또는 precondition 충돌에서는 일부 write만 적용되지 않아야 한다. 조건 없는 덮어쓰기나 자동 재시도를 추가하지 않는다. 저장 성공이 확인된 호출만 선점 성공을 반환한다.

- [x] **T1.6 — 소유권과 복구 전이를 CAS로 구현한다.**
  `queued → preparing`, 조건부 상태 전이, generation 회전과 복원 실패 차단을 각각 TDD로 구현한다. 읽은 상태에서 소유권·generation·이전 상태가 맞아도, commit 시점에 `update_time`이 달라졌다면 변경하지 않는다.

  최초 원본 선점과 모든 후속 변경에 같은 버전 보호 원칙을 적용한다. 이전 시도의 늦은 변경이 현재 시도를 덮어쓰지 않는지 확인한다. 조건 충돌 뒤 호출자가 Slack 게시나 GitHub dispatch를 계속하지 못하도록 반환 계약을 검증한다.

- [x] **T1.7 — barrier 경쟁과 모든 요청의 결과를 확인한다.**
  격리 Emulator에서 다음 세 시나리오를 각각 요청 20개로 실행한다.

  1. 최초 선점: 모든 요청이 동일 원본의 미존재를 읽은 뒤 barrier를 통과해 조건부 commit을 시도한다.
  2. 재시도 선점: 모든 요청이 동일한 retryable generation과 동일 update_time을 읽은 뒤 barrier를 통과해 조건부 commit을 시도한다.
  3. worker 선점: 모든 실행이 동일 queued 상태와 동일 update_time을 읽은 뒤 barrier를 통과해 조건부 commit을 시도한다.

  barrier는 읽기 완료 후 commit 전에 둔다. 각 시나리오에서 읽은 미존재·generation·update_time의 일치를 기록한다. barrier와 테스트 대기는 유한하게 제한하며, 1.5초 애플리케이션 예산을 늘리거나 barrier 통과 후 새로 시작하지 않는다. 필요한 공통 읽기 조건을 충족하지 못한 실행을 barrier 경쟁의 합격 증거로 사용하지 않는다.

  최초·재시도 선점은 성공이 각각 정확히 1개이며, 해당 시도의 새 outbox도 정확히 1개여야 한다. worker 선점은 owner 획득이 정확히 1개여야 한다. 성공 0개와 복수 성공은 모두 실패다.

  요청 20개 전부의 결과를 개별 기록하고 다음 항목으로 집계한다.

  - 선점 성공 또는 worker owner 획득
  - 확인된 조건 충돌에 따른 duplicate 또는 명시적 no-owner
  - fail-closed `RetryStoreError`

  세 항목의 합은 20이어야 한다. 미완료 요청, 누락된 결과, 예상하지 않은 예외와 다른 결과값이 있으면 검증 실패로 처리한다. `DeadlineExceeded`·`RST_STREAM` 등 RPC 오류는 `RetryStoreError`로 구분하며 duplicate나 no-owner로 계산하지 않는다.

  `RetryStoreError`가 발생한 요청은 성공 경로를 실행하지 않아야 한다. 결과 집계와 별도로 최종 원본·outbox를 조회해 부분 저장·추가 outbox가 없음을 확인하고, 실패한 호출이 후속 외부 작업을 시작하지 않았는지 확인한다.

  retryable 경쟁 후에는 이전 generation의 callback이 선점하거나 새 outbox를 만들지 못함을 확인한다. 같은 날짜의 다른 timestamp는 별도 원본으로 독립 성공해야 한다.

  환경·동시성 수·barrier 조건·결과별 건수·RPC 오류 종류·경과 시간을 기록한다. RPC 오류는 Emulator 환경과 contention에서 관측된 한계로 보고하며 숨기거나 duplicate로 재분류하지 않는다. 이 검증은 모든 경쟁 요청의 성공적 응답이나 운영 Firestore 지연을 보증하지 않는다. 제품의 1.5초 예산과 별도 ACK 기준을 완화하지 않는다.

- [x] **T1.8 — stale read·원자성·결과 불명 장애를 주입한다.**
  읽기 후 다른 요청이 문서를 변경한 경우, outbox만 이미 존재하는 경우, 원본 조건만 실패하는 경우를 각각 재현한다. 어느 write의 조건이 실패해도 원본 전환과 outbox 생성이 부분 적용되지 않아야 한다.

  초기화·읽기 실패, commit 전 예산 소진, commit 적용 후 응답 유실도 확인한다. 확인된 조건 충돌과 결과 불명 오류를 구분하며, 후자에서 commit 재전송이나 새 outbox 생성으로 보상하지 않는다. `update_time` 전달 과정에서 정밀도가 유지되는지도 확인한다.

- [x] **T1.9 — 의존성과 문서를 정리한다.**
  공개 GAPIC API를 직접 사용하므로 `google-cloud-firestore==2.28.0`을 Firebase 실행·dev 환경의 직접 의존성으로 선언한다. `google.auth`나 `grpc`를 직접 import하면 해당 배포 패키지도 호환 근거에 맞는 범위로 직접 의존성에 선언하고, transitive dependency에 기대지 않는다. `firebase-functions` 의존성을 유지하고 Firebase Admin을 직접 import하지 않으면 Firestore 목적만으로 Firebase Admin 직접 의존성을 추가하지 않는다.

  운영 경로는 승인된 대상 Firebase 프로젝트의 `(default)` database와 ADC를 사용한다. 로컬 Emulator 경로는 검증된 loopback endpoint, 익명 credentials와 비보안 gRPC transport를 명시하며 운영 client 생성이나 실제 ADC/Firestore 접근을 하지 않는다. Emulator host만 운영 endpoint에 넣으면 TLS·인증 설정이 자동 전환된다고 가정하지 않는다. 일반 CLI import와 테스트 수집 단계에서는 client를 초기화하지 않는다.

  README에 저장 크기 경계의 근거, RPC deadline 한계, Emulator marker·host 검증·명시 transport·skip·ADC 차단·데이터 정리와 실제 서비스 미접속 조건을 기록한다.

### 집중 확인과 수용 기준

허용 후 실행할 명령은 다음과 같다.

```bash
python -m pytest tests/test_retry_store.py -q
python -m pytest tests/test_retry_store_emulator.py -m firestore_emulator -q -o addopts=''
```

두 번째 명령도 host 미설정이면 안전하게 skip해야 한다. skip은 경쟁 검증의 합격 증거가 아니다.

- 기본 pytest는 Emulator 없이 실행 가능해야 한다.
- host 미설정·잘못된 host에서 원격 Firestore·ADC 접근이 없어야 한다.
- 공개 GAPIC의 비transactional 읽기와 조건부 atomic commit을 사용한다. `begin_transaction`·`rollback`·transaction ID를 사용하지 않는다.
- 원본 변경과 outbox 생성은 한 commit이며, 각각 필요한 `current_document` precondition을 포함한다.
- 원본의 최초 생성은 `exists=false`, 기존 문서 변경은 읽은 정확한 `update_time`으로 보호한다.
- read와 commit에 `retry=None` 및 잔여 예산에 따른 timeout을 전달한다.
- 확인된 조건 충돌은 미선점 또는 `False`로 반환하고, timeout·transport·원인이 확인되지 않은 오류는 저장 오류로 처리한다. 충돌 후 자동 재조회·재commit을 하지 않는다.
- commit 결과 불명을 미접수로 단정하거나 새 outbox 생성으로 보상하지 않아야 한다.
- Emulator에서는 명시적인 loopback transport와 익명 credentials만 사용하고 ADC·운영 endpoint 접근이 없어야 한다.
- initial·retryable·worker 각각의 20개 barrier 경쟁에서 공통 읽기 조건을 확인하고, 성공 또는 owner 획득이 정확히 1개여야 한다.
- initial·retryable 경쟁마다 해당 시도의 새 outbox는 정확히 1개이며 부분 저장이나 추가 outbox가 없어야 한다.
- 모든 요청의 결과를 성공·확인된 조건 충돌·fail-closed `RetryStoreError`로 빠짐없이 집계한다. 합계는 각 시나리오마다 20이며, 미완료·누락·예상하지 않은 결과가 없어야 한다.
- timeout·`RST_STREAM` 등 RPC 오류를 duplicate 또는 no-owner로 취급하지 않는다. 오류 호출은 후속 효과를 시작하지 않는다.
- stale read와 어느 한 write의 조건 실패에서 부분 적용이 없고, 이전 owner·generation이 최신 상태를 덮어쓰지 못해야 한다.
- 이전 generation·owner가 새 시도를 변경하지 못해야 한다.
- generation 회전은 원자적이어야 한다.
- 저장 크기 경계는 공식 제한·schema·측정값으로 설명돼야 한다.
- SDK의 제어 가능한 RPC timeout·retry·read→commit 시도 횟수가 명시적으로 적용돼야 한다. 예산 소진 후 추가 호출 금지와 결과 불명 처리를 확인하고, 초기화·인증을 포함한 전체 시간 상한은 보증하지 못함을 기록한다.
- 격리 Emulator의 initial·retryable·worker barrier 경쟁에서 선점·outbox·worker 소유권이 각각 하나로 제한돼야 한다. RPC 오류·지연 한계를 증거에 명시하며, 이를 숨기거나 운영 contention·ACK 성능을 검증했다고 주장하지 않는다.
- Coder는 RED/GREEN, 장애 결과와 Emulator 경쟁 결과를 제출한다.
- QA는 저장 계약과 경쟁·장애를 독립 확인하며 실패 수정 후 필요한 재확인을 수행한다.

**보류 조건:** 필요한 테스트 권한·격리 환경 부재, RPC timeout·retry·read→commit 시도 제한의 미적용, 조건 없는 write, 원본·outbox의 분리 commit, `update_time` 정밀도 손실, stale read에 의한 변경 성공, 정상 경쟁 시나리오의 성공 0개 또는 복수 성공, 부분 적용, 불명 오류의 조건 충돌 오분류, 근거 없는 크기 제한, 정상 알림 제외, ADC·운영 endpoint 접근 또는 원격 접속 시도. `exists=false`·`update_time` protobuf precondition과 누락 문서 응답을 집중 확인한다. SDK에 전체 deadline 보증 API가 없고 운영 contention이 미검증이라는 한계를 기록하며, 로컬 경쟁 실패를 통과 처리하지 않는다.

**수용·커밋:** Orchestrator가 기준·필요 QA·지적 해결을 확인해 수용하고 T1을 하나의 focused commit으로 저장한다. SHA·포함 내용·HEAD·남은 변경을 확인한다.

**T1 QA 결과:** 기본 pytest `96 passed, 5 deselected`, 저장 계층 `31 passed`, Ruff 통과, Emulator `4 passed`다. initial·retryable·worker barrier 세 종류에서 각 20개가 같은 상태·버전을 읽었고 선점자 또는 worker 소유자가 각각 하나였다. Emulator에서는 다수의 `DeadlineExceeded`와 원인이 확인되지 않은 `FailedPrecondition`이 저장 오류로 fail-closed 처리됐다. outbox-only 충돌과 읽기 후 stale 원본 버전도 부분 저장 없이 확인했다. 이는 Emulator 검증이며 운영 contention과 실제 플랫폼 ACK 시간은 입증하지 않는다.

---

## T2 — ACK·trigger worker·공유 표시와 복구 통합

**목표:** 기존 handler를 durable handoff와 ACK로 바꾸고, worker에서 단일 dispatch와 공유 표시·복구를 수행한다.

**소유 파일**

- `src/nanet_menu/firebase_function.py`
- `main.py`
- `tests/test_firebase_function.py`
- `README.md`의 실행 구조·ACK·서비스 전제·장애·수동 복구 부분

**선행 조건:** T1 수용과 focused commit, 명시적인 검증 권한.

**소비 계약:** T1 공개 저장 인터페이스와 메시지 크기 검증.

**생산 인터페이스**

```python
retry_daily_menu(request)
process_retry_outbox(event)

_process_retry_attempt(
    outbox: dict[str, Any],
    *,
    worker_id: str,
) -> None

_classify_dispatch(
    status_code: int | None,
) -> Literal["accepted", "rejected", "unknown"]
```

메시지 변환은 원본 본문·관계없는 버튼을 유지하면서 재시도 버튼과 상태 문구만 변경하는 내부 helper로 분리한다.

### GitHub 분류 정책

초기 명확한 거절 allowlist는 빈 집합이다. 이 상태도 안전한 구현이며 T2 착수를 막지 않는다.

- `200`, `204`: `accepted`
- 나머지 응답·transport 오류: `unknown`
- 비어 있지 않은 거절 allowlist를 채택하려면 코드별 공식 미수락 근거·분류 테스트를 함께 기록한다.
- 근거 없는 `4xx`를 복원 대상으로 추가하지 않는다.

확정 거절의 복구 경로는 저장 계약과 분류 결과를 주입한 단위 테스트로 확인한다. 이를 근거 없는 실제 HTTP 코드를 확정 거절로 인정했다는 증거로 사용하지 않는다. 초기 정책에서는 일부 또는 모든 오류가 자동 복원 없이 수동 확인으로 남는다는 점을 README에 명시한다.

### 진행 단계

- [ ] **T2.1 — ACK 계약의 실패 테스트를 작성한다.**

  ```python
  def test_signed_request_enqueues_and_returns_empty_ack():
      assert response.status_code == 200
      assert response.get_data() == b""
      assert enqueue_count == 1
      assert slack_calls == []
      assert github_calls == []

  def test_stale_click_does_not_create_outbox():
      assert response.status_code == 200
      assert new_outbox_count == 0

  def test_commit_result_unknown_never_dispatches_in_handler():
      assert response.status_code == 200
      assert github_calls == []
  ```

  기존 직접 dispatch 기대는 RED를 확인한 후 새 계약으로 대체한다. 서명·timestamp·channel·action·날짜 검증은 유지한다.

- [ ] **T2.2 — RED 확인 후 handler를 구현한다.**
  date-only와 새 generation을 지원한다. T1의 메시지 검증을 사용하고 식별자를 생성해 저장 함수를 호출한다. 유효한 요청은 성공·중복·오류에서도 예산 안에 빈 HTTP `200`을 반환한다. ACK를 개별 Slack 메시지로 취급하지 않는다.

- [ ] **T2.3 — worker 중복과 순서의 실패 테스트를 작성한다.**

  ```python
  def test_duplicate_worker_delivery_dispatches_once():
      assert worker_claim_count == 1
      assert github_post_count == 1

  def test_progress_update_precedes_dispatch():
      assert calls.index("slack_progress") < calls.index("github_dispatch")

  def test_progress_failure_prevents_dispatch():
      assert github_post_count == 0

  def test_dispatch_state_commit_failure_prevents_dispatch():
      assert github_post_count == 0
  ```

- [ ] **T2.4 — RED 확인 후 trigger와 worker를 구현한다.**
  `slack_retry_outbox/{attempt_id}`의 생성 event를 검증하고 worker를 원자 선점한다. 중복은 외부 작업 없이 종료한다. 진행 표시와 `dispatching` 저장 성공 후에만 POST한다. POST 자동 retry·redirect를 사용하지 않는다.

- [ ] **T2.5 — 분류·최종 상태를 TDD로 구현한다.**
  빈 allowlist를 기본값으로 `200/204` 외의 결과를 보수적으로 처리한다. `408`, 대표 `3xx/5xx`, 미검증 `4xx/2xx`, timeout·transport 오류를 포함한다. 접수 후 Firestore·Slack 실패에서 잠금을 유지하고 재dispatch하지 않는다.

- [ ] **T2.6 — 복구 순서와 경쟁을 TDD로 구현한다.**

  ```python
  def test_rejection_commits_generation_before_button_publish():
      assert calls.index("retryable_commit") < calls.index("slack_retry_button")

  def test_old_generation_is_invalid_after_restore():
      assert old_result.outcome == "stale"

  def test_failed_restore_does_not_block_new_attempt():
      assert new_attempt_state == "queued"
      assert old_worker_block_changed is False
  ```

  확정 거절 결과를 주입해 복구를 확인한다. 새 generation의 `retryable` commit 뒤 버튼을 게시한다. 게시 실패 시 같은 generation이 미선점인 경우만 조건부 차단한다. 두 시스템 장애로 `retryable`이 남는 승인된 한계도 재현한다.

- [ ] **T2.7 — crash 경계를 확인한다.**
  worker 선점 후, Slack 진행 표시 후, `dispatching` 저장 후, GitHub 접수 후, generation 회전 후와 버튼 공개 후 중단을 주입한다. 재전달이 POST를 재실행하지 않는지 확인한다. 소유권 탈취·자동 만료를 추가하지 않는다.

- [ ] **T2.8 — 로컬 handler 시간을 측정하고 보증 범위를 구분한다.**
  handler 진입부터 HTTP 응답 반환까지 monotonic 시간으로 측정한다. 성공·중복·오래된 요청·저장 오류·commit 결과 불명 경로를 포함하고, 제어 가능한 RPC 지연·예산 소진과 worker 지연을 주입한다. worker 지연이 ACK 경로에 전파되지 않는지 확인한다.

  1.5초 의존 작업 예산에 따른 RPC timeout 배분과 추가 호출 제한을 확인한다. client 초기화·인증 등 직접 강제 중단할 수 없는 구간은 측정에 포함됐는지 명시하고, fake 지연이나 개별 timeout 합계를 실제 SDK 전체 시간의 보증으로 사용하지 않는다.

  각 측정의 환경·초기화 상태·주입 조건·반복 횟수·최대 시간을 기록하고 로컬 handler 2초 기준의 충족 여부를 보고한다. 해당 시나리오에서 2초를 넘으면 T2의 시간 기준을 충족했다고 판단하지 않고 Orchestrator에 반환한다. 관측되지 않은 모든 장애 경로의 상한까지 입증했다고 표현하지 않는다.

  실제 플랫폼 cold/warm ACK 3초 미만은 이번 배포 제외 범위에서 검증하지 않는다. 별도 허용된 출시 전 확인과 launch hold로 남긴다.

- [ ] **T2.9 — 진입점과 함수별 Secret 연결을 완성한다.**
  기존 루트 `main.py`의 import와 `__all__`에 `process_retry_outbox`를 추가한다. 기존 두 export를 유지한다. `firebase.json`의 source `.`·runtime `python312`는 변경하지 않는다. worker가 사용하는 Secrets를 연결하고 handler의 Secret 사용도 실제 역할에 맞춘다.

- [ ] **T2.10 — README에 운영 전제와 제한을 기록한다.**
  Firestore API·database·runtime IAM·ADC, trigger/Eventarc 전제를 설명한다. 설정·배포를 실행하지 않는다. ACK와 공유 메시지의 구분, 빈 거절 allowlist의 보수적 동작, 불명 시도·stale 버튼·수동 복구·자동 TTL 금지를 기록한다.

### 집중 확인과 수용 기준

허용 후 실행할 명령은 다음과 같다.

```bash
python -m pytest tests/test_firebase_function.py -q
python -m pytest tests/test_retry_store.py tests/test_retry_store_emulator.py tests/test_firebase_function.py -m 'not integration' -q -o addopts=''
```

- 동일 알림 동시 callback과 event 중복에서 POST는 시도당 1회다.
- 진행 표시와 `dispatching` 저장이 POST보다 먼저 성공한다.
- ACK 전에 Slack·GitHub가 호출되지 않는다.
- 유효한 interaction에 빈 본문 HTTP `200`을 반환하며 ACK 전에 Slack·GitHub를 호출하지 않는다.
- 지정한 로컬 시나리오의 handler 측정값이 2초 기준을 충족하고, 1.5초 의존 작업 예산의 RPC 배분·추가 호출 제한을 확인한다. 초기화·인증·callback 전체에 대한 강제 시간 보증과 실제 플랫폼 ACK 검증을 완료했다고 주장하지 않는다.
- Emulator와 로컬 측정의 범위·반복 횟수·최대값·한계를 기록한다. 운영 Firestore의 contention과 플랫폼 cold start는 미검증으로 구분한다.
- worker 선점 또는 `dispatching` CAS가 실패하면 Slack 후속 게시·GitHub dispatch를 진행하지 않는다.
- 복원 버튼 공개 전에 새 generation이 유효하며, 이전 callback·worker는 새 시도를 변경하지 못한다.
- 거절 복구 generation 회전 CAS가 실패하면 새 버튼을 게시하지 않는다.
- 복원 실패 차단 CAS가 실패하면 이미 선점된 새 시도의 상태를 보존한다.
- 빈 거절 allowlist에서는 미검증 응답이 모두 `unknown`이다.
- 복구 경로의 단위 검증과 실제 HTTP 분류 근거를 구분한다.
- `main.py`의 세 함수 export와 기존 예약·CLI 동작이 유지된다.
- Coder는 RED/GREEN, 호출 순서·횟수, 장애 표와 시간 측정을 제출한다.
- QA는 격리 환경에서 경쟁·복구·장애·ACK를 독립 확인하며 실패 수정 후 필요한 재확인을 수행한다.

**보류 조건:** 지정한 로컬 측정에서 handler 2초 기준 미달, RPC 예산 적용 실패, 중복 POST, 상태·generation 경쟁 실패, 미근거 거절 분류 추가 또는 실제 외부 호출이 필요한 검증으로의 확대. 측정 실패를 숨기거나 기준을 임의로 완화하지 않는다. 실제 플랫폼 ACK 미검증은 아래 출시 보류 조건으로 별도 관리한다.

**수용·커밋:** Orchestrator가 필요한 QA와 지적 해결을 확인해 T2를 수용하고 하나의 focused commit으로 저장한다. SHA·포함 내용·HEAD·남은 변경을 확인한다.

## 최종 전체 검증

T1·T2 수용과 필요한 QA 완료 후 Reviewer가 최종 전체 변경을 한 번 독립 검토한다. 원자성, owner/generation, ACK, event 중복, 응답 분류, 복구 순서, 진입점·의존성·Secrets와 문서 일치를 확인한다.

최종 Reviewer 지적은 Coder 수정→Reviewer 재확인으로 해결한다. 이 단계의 지적 수정에는 QA를 재할당하지 않는다. 초기 최종 review 전의 필수 QA와 QA 실패 재확인은 유지한다.

Reviewer 의견은 Orchestrator 수용을 대신하지 않는다.

## 이번 범위 밖의 출시 전 확인

실제 플랫폼 cold/warm ACK 3초 미만과 운영 Firestore의 contention·지연 동작은 로컬 측정이나 Emulator 결과로 증명되지 않는다. 현재 상태는 미검증이며 출시를 보류한다.

별도로 허용된 출시 전 확인에서 플랫폼 초기화·인증·전송을 포함한 ACK 시간을 측정해야 한다. cold/warm 상태, 반복 횟수, 요청 유형과 측정 결과를 기록한다. 3초 기준을 충족하지 못하거나 필요한 증거가 없으면 launch hold를 유지한다. 최소 인스턴스나 새로운 실행 기반이 필요하면 해당 비용·설정·범위 변경을 별도 승인받는다.

이번 계획은 실제 프로젝트 준비, API 활성화, IAM 변경, 배포와 실제 Slack/GitHub 호출을 포함하지 않는다. 위 확인을 위해 승인 범위를 자동으로 확대하지 않는다.

필요한 로컬 기준과 QA를 충족한 T1·T2는 독립적으로 수용·커밋할 수 있다. 이는 운영 ACK 요건 충족이나 출시 승인을 의미하지 않는다.

## 승인과 보류 조건

1. **테스트·검증 권한:** 2026-10-20에 승인됐다. 로컬 pytest와 격리 loopback Emulator만 포함하며 실제 Firebase·Slack·GitHub는 호출하지 않는다.
2. **계획 승인:** 2026-10-20에 승인됐다. Orchestrator의 T1 준비 확인 후 구현한다.
3. **SDK 시간 제어:** 제어 가능한 RPC timeout·retry·read→commit 시도 횟수와 잔여 예산 검사를 확인한다. 초기화·인증을 포함한 전체 deadline 보증은 SDK가 제공하지 않는 한계로 기록한다. 해당 API 부재만으로 구현을 보류하지 않으며, 로컬 측정 기준 미달은 영향받는 작업의 보류 조건이다.
4. **메시지 크기:** 공식 제한·schema·측정에 근거한 경계를 T1에서 확정한다. 임의의 제품 제한이나 조용한 본문 축약을 추가하지 않는다.
5. **거절 allowlist:** 빈 집합으로 진행할 수 있다. 비어 있지 않은 정책은 공식 근거와 검증이 선행한다.
6. **실행 준비:** Orchestrator가 승인 계획에 연결한 T1→T2 의존·소유권·검증·ready/held를 인계 기록에 저장한다.
7. **공유 파일:** `README.md` 변경과 T1 계약은 직렬로 관리한다.
8. **범위 변경:** SDK 한계나 새로운 운영 기반 때문에 승인 범위를 변경해야 하면 Orchestrator로 반환한다.
9. **출시 보류:** 실제 플랫폼 cold/warm ACK 3초 미만은 이번 범위에서 미검증이다. 별도 허용된 확인으로 기준을 충족하기 전까지 launch hold를 유지한다. 로컬 구현 수용·커밋과 출시 승인을 구분한다.
