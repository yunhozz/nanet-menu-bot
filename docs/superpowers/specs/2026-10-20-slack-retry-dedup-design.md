# Slack 식단 재전송의 원본 알림별 중복 방지 설계

상태: 사용자 승인 완료 — 2026-10-20
설계 깊이: Architectural
승인 범위: Firestore 원본 알림별 원자 선점, outbox, 별도 Firestore 생성 trigger worker와 본 formal spec

시간 예산의 SDK 한계를 반영한 수용 기준 변경안은 2026-10-20 사용자 승인을 받았다. 로컬 구현·수용은 실제 플랫폼 cold/warm ACK 3초 출시 gate와 별개다. T1에서 확인된 Emulator read-write transaction 잠금 승격 경합을 피하기 위해 원자 선점·outbox의 구현 방식을 update_time/exists precondition을 사용한 조건부 atomic commit으로 구체화했다. 이는 데이터 모델·인터페이스·외부 실행 범위를 바꾸지 않는다.

## 대상과 승인 범위

- 저장소: `/Users/ma-24-007/Desktop/workspace/nanet-menu-bot`
- 브랜치: `feat/slack-retry-dedup`
- 기준 HEAD: `d38623f4734ce6f633f06e1690dde0cf587e3470`
- 준비 상태: Orchestrator가 사용자 선택에 따라 새 브랜치를 생성하고 동일 HEAD와 clean 상태를 확인했다.
- 공통 인계 기록: `/Users/ma-24-007/Desktop/workspace/nanet-menu-bot/.git/arc/handoff/slack-retry-dedup.md`

사용자는 2026-10-20에 Firestore를 통한 원본 실패 알림별 원자적 선점, 공유 진행 표시, 명확한 거절 시 재시도 복원, 접수 불명 시 잠금 유지와 함께 Firestore outbox·별도 생성 trigger worker 구조 및 본 formal spec을 승인했다.

HTTP handler는 durable handoff 후 ACK하고, 별도 worker가 공유 메시지 갱신과 GitHub dispatch를 수행한다. 이 실행 구조에 대한 추가 범위 승인은 필요하지 않다. 구현은 별도 계획 승인과 적용되는 실행·검증 조건 충족 후 진행한다.

실제 프로젝트 provisioning, 자원 생성, API 활성화, IAM 변경, 배포와 실제 Slack/GitHub 외부 호출은 승인된 이번 구현 범위에 포함되지 않는다. 필요한 서비스 전제와 절차는 문서화하되 실행하지 않는다.

## 목적과 보장 범위

같은 Slack 실패 알림을 여러 사람이 거의 동시에 눌러도 같은 시도에서 GitHub Actions dispatch가 중복 호출되지 않게 한다. 채널 구성원 모두에게 원본 알림의 진행 상태를 표시하며, 확실한 미접수 오류에서만 새로운 시도를 허용한다.

보장 범위는 다음과 같다.

- 동일 원본 알림·시도의 애플리케이션 측 GitHub POST를 한 번으로 제한한다.
- 같은 날짜라도 다른 원본 알림은 독립적으로 처리한다.
- 접수 여부 불명, crash와 Firestore 장애를 이유로 자동 재dispatch하지 않는다.
- Slack 메시지는 표시 수단이며, Firestore의 원자적 상태가 dispatch 허용 여부를 결정한다.
- GitHub 접수와 Firestore 기록은 하나의 transaction이 아니므로 workflow의 정확히 한 번 실행을 보장한다고 표현하지 않는다.

다음은 제외한다.

- 날짜 전체 또는 예약·수동 실행 전체의 중복 방지
- GitHub workflow 완료 추적
- 자동 잠금 만료와 자동 수동복구 대체
- 복구용 관리자 API·UI
- workflow 입력·실행 방식 변경
- 이번 단계의 provisioning·배포

## 현재 구조와 ACK 문제

현재 HTTPS handler는 Slack 요청을 검증하고 GitHub dispatch를 호출한 뒤 원본 버튼을 제거한다. Firestore 선점을 추가하고 모든 상태 갱신까지 같은 요청에서 끝내면 다음 외부 왕복이 ACK 앞에 놓인다.

1. Firestore 선점
2. Slack 진행 표시
3. dispatch 진입 상태 저장
4. GitHub dispatch
5. 결과 저장
6. Slack 최종 표시

Slack은 유효한 interaction payload 수신 후 3초 안에 HTTP `200 OK`로 ACK할 것을 요구한다. ACK는 workflow 접수·실행 성공과 구분된다. [Slack interaction 문서](https://docs.slack.dev/interactivity/handling-user-interaction/)

현재 함수의 10초 timeout은 이 요구를 충족하는 근거가 아니다. 개별 HTTP timeout을 짧게 합산하는 것만으로 SDK 재시도, 초기 연결과 초기화까지 포함한 전체 지연도 입증되지 않는다.

HTTP 응답 후 background thread에서 나머지를 수행하는 방식은 채택하지 않는다. Firebase는 함수 종료 후 background 작업이 계속 실행된다고 보장하지 않는다. [Firebase 함수 운영 지침](https://firebase.google.com/docs/functions/tips)

## 승인된 구조: durable handoff 후 ACK

### 처리 구조

Firestore의 조건부 atomic commit 하나로 다음 두 작업을 함께 적용한다.

- 원본 알림 문서를 현재 시도의 `queued` 상태로 선점
- 해당 시도의 outbox 문서 생성

원본을 비transactional 방식으로 읽어 상태를 검증한다. 최초 생성에는 미존재 조건, 기존 문서 변경에는 읽은 정확한 `update_time` 조건을 적용한다. outbox 생성에도 미존재 조건을 적용한다. 읽기 후 경쟁 변경이 발생하면 조건이 충족되지 않아 commit 전체가 실패하며, 선점 성공을 반환하지 않는다.

이 방식은 읽기 잠금을 유지하는 read-write transaction을 사용하지 않는다. 원본과 outbox의 원자적 적용 및 stale read 거부를 안전 계약으로 삼는다.

commit 성공이 확인되면 HTTPS handler는 즉시 ACK한다. Firestore outbox 생성 trigger의 별도 worker가 진행 표시와 GitHub dispatch를 수행한다.

ACK 앞에서는 Slack API와 GitHub API를 호출하지 않는다. worker는 원본 공유 메시지를 진행 표시로 바꾼 뒤에만 dispatch한다. 따라서 사용자는 빠른 ACK를 받고, 채널에는 실제 처리를 시작하는 worker가 진행 상태를 표시한다.

outbox 생성 이벤트가 중복 전달되더라도 worker의 원자 선점에 성공한 실행 하나만 외부 작업을 수행한다.

확인된 write precondition 충돌은 현재 호출이 선점·전이를 하지 못했다는 뜻으로만 처리한다. 모든 `FAILED_PRECONDITION` 또는 `ABORTED`를 충돌로 간주하지 않는다. timeout·transport 오류·원인이 불명확한 오류는 저장 오류 또는 결과 불명으로 유지하며 자동 재조회·재commit하지 않는다.

### 승인된 구조의 구성 범위

2026-10-20 사용자 승인에는 다음 구조가 포함된다.

- Firestore outbox collection을 사용하는 애플리케이션 처리
- Firestore 생성 trigger worker 함수
- 해당 trigger의 Eventarc 등 배포 기반과 서비스 권한 전제 문서화
- worker 중복 전달·장애 처리
- ACK와 worker 처리 분리 및 필요한 검증 항목

이 구조에 대한 별도 범위 확인은 완료됐다. 실제 프로젝트 준비, API 활성화, IAM 변경과 배포 실행은 이번 범위 밖이다.

계획 승인과 명시적인 테스트·검증 권한 등 실행 조건은 구조 승인과 구분하며, 해당 조건 충족 전에는 구현을 시작하지 않는다.

## ACK 계약과 시간 기준

### handler 처리

1. 기존 Slack 서명·시간·채널·액션 검증을 수행한다.
2. 원본 메시지 식별, 날짜, generation과 필수 설정을 검증한다.
3. Firestore 선점과 outbox 생성을 원자적으로 commit한다.
4. commit 성공 확인 직후 빈 본문의 HTTP `200 OK`를 반환한다.

HTTP `200`은 interaction 수신을 확인하는 ACK다. 사용자에게 메시지가 표시되거나 GitHub 요청이 접수됐음을 뜻하지 않는다. ACK 응답 본문이 Slack에 개별 안내로 표시된다고 가정하지 않는다.

중복·오래된 클릭은 새로운 outbox를 만들지 않고 HTTP `200`으로 ACK한다. ACK 자체로 클릭한 사용자에게 처리 중·접수 완료·오래된 버튼 등의 안내를 약속하지 않는다.

Firestore 오류나 commit 결과 불명에서도 유효한 interaction에는 시간 예산 안에서 HTTP `200`을 반환하고 오류를 기록한다. ACK와 별도로 실패 문구가 사용자에게 전달된다고 가정하지 않는다. commit 응답이 유실되어도 실제 commit과 worker 실행이 시작됐을 수 있으므로 handler는 직접 dispatch하거나 commit을 무조건 재시도하지 않는다.

### 사용자에게 보이는 상태

사용자가 확인할 처리 상태는 trigger worker가 갱신하는 원본 공유 메시지다. Firestore는 dispatch 허용 여부를 결정하는 서버 측 상태이며, Slack 메시지는 이를 사용자에게 표시하는 경로다.

outbox 저장이 실패했거나 worker의 메시지 갱신이 실패하면 원본 메시지가 그대로 남을 수 있다. 따라서 ACK 수신만으로 원본 메시지에 반드시 진행·실패 안내가 표시된다고 보장하지 않는다. 이 경우의 서버 측 진단은 오류 로그와 Firestore 기록을 이용한다.

`response_url`을 이용한 개별 ephemeral 안내는 ACK와 별개의 메시지 전송이다. 이번 범위에는 해당 전송과 그에 필요한 별도 비동기 outbox action을 추가하지 않는다. `response_url`도 저장하거나 기록하지 않는다.

### 시간 예산과 검증

- 애플리케이션 handler 진입부터 응답 반환까지의 로컬 측정 기준은 **2초 이내**다.
- Firestore 의존 작업에는 handler 진입 기준 **1.5초**의 애플리케이션 예산을 두고, 제어 가능한 RPC에 남은 시간을 배분한다.
- 비transactional `batch_get_documents`와 조건부 commit의 timeout·retry를 명시한다. 각 저장 호출의 read→검증→commit 시도는 1회이며, 잔여 예산이 없으면 새 RPC를 시작하지 않는다.
- 위 제어는 client 초기화·인증·실행 중 작업을 포함한 전체 deadline을 SDK가 강제 보증한다는 의미가 아니다. 이미 성공한 commit이나 시작된 worker가 취소됐다고 가정하지 않는다.
- 유효 요청의 성공·중복·오류·commit 결과 불명 경로에서 HTTP `200` 응답 반환 시간을 측정한다. 환경·초기화 상태·주입 조건·반복 횟수·최대 시간과 측정되지 않은 구간을 기록한다.
- 로컬 시나리오가 2초 기준을 충족하지 못하면 해당 기준을 통과했다고 판단하지 않고 Orchestrator에 반환한다. timeout 파라미터 합계나 fake 지연 테스트를 전체 SDK·플랫폼 시간 보증으로 사용하지 않는다.
- worker 지연이 ACK 지연으로 전파되지 않아야 한다.
- 플랫폼 초기화·인증·전송을 포함한 cold/warm ACK **3초 미만**은 별도 출시 전 검증 기준이다. 이번 배포 제외 범위에서는 미검증이며, 필요한 증거를 확보하기 전까지 launch hold를 유지한다.
- 플랫폼 기준을 충족하지 못하면 출시를 보류한다. 비용·운영 설정이나 실행 구조 변경이 필요하면 별도 승인받는다.
- HTTP ACK 자체가 사용자에게 개별 메시지를 표시한다고 기대하지 않는다.

로컬 구현 수용·커밋은 운영 ACK 요건 충족이나 출시 승인을 의미하지 않는다. Emulator의 경쟁·원자성 관측도 운영 Firestore의 contention·지연 동작을 동일하게 재현했다는 증거로 사용하지 않는다.

## 식별과 데이터 계약

### 원본 알림 키

키 입력은 다음 세 문자열이다.

- 검증된 `channel_id`
- 원본 `message.ts`
- 검증된 `action_id`

순서가 고정된 JSON 배열을 UTF-8로 직렬화한 뒤 SHA-256 hex 값을 문서 ID로 사용한다. 날짜·사용자 ID는 키에 넣지 않으며 Slack timestamp는 문자열로 유지한다.

저장 위치는 같은 Firebase 프로젝트의 `(default)` Firestore database다.

- 원본 상태: `slack_retry_requests/{alert_key}`
- 시도 outbox: `slack_retry_outbox/{attempt_id}`

### generation과 소유권

- `generation`: 버튼이 나타내는 재시도 세대
- `attempt_id`: handler가 선점한 시도 식별자
- `worker_id`: 해당 시도를 실제 처리하도록 선점한 worker 실행 식별자

식별자는 저장 시도 전에 생성한다. 모든 후속 상태 변경은 해당 시도의 `attempt_id`와 필요한 경우 `worker_id`, 예상 이전 상태를 확인하고, 읽은 정확한 `update_time`을 CAS precondition으로 사용한다.

현재 버튼 값 `YYYY-MM-DD`는 `initial` generation으로 해석한다. 복원 버튼은 다음 형식의 JSON 문자열을 사용한다.

```json
{"v":1,"target_date":"2026-10-20","generation":"<opaque-random-value>"}
```

문서가 없는 최초 선점은 `initial`에만 허용한다. `retryable`에서는 저장된 현재 generation과 일치하는 요청만 허용한다.

이전 generation의 늦은 callback은 다시 선점할 수 없다. 날짜와 원본 식별은 최초 선점 후 변경하지 않는다.

### 원본 상태 문서

다음을 저장한다.

- `schema_version`
- `channel_id`, `message_ts`, `action_id`, `target_date`
- `state`, `generation`, `attempt_id`, `worker_id`
- `created_at`, `updated_at`
- `dispatch_status_code`
- 정해진 오류 분류인 `reason`

### outbox 문서

worker 수행에 필요한 다음 정보만 저장한다.

- 원본 상태 문서의 키
- `attempt_id`, `generation`, 검증된 날짜
- 원본 메시지 갱신에 필요한 검증·정규화한 본문 및 blocks
- 생성 시각과 schema version

원본 HTTP payload, Slack 서명, token과 `response_url`은 저장하지 않는다. 메시지 데이터는 이번 식단 실패 알림에 필요한 필드로 제한한다. 지원 크기를 초과하거나 안전하게 정규화할 수 없으면 요청을 거부하고 outbox를 만들지 않는다.

outbox의 보존·삭제는 잠금 해제와 연결하지 않는다. 원본 잠금 문서는 별도 정책 없이 삭제하거나 자동 만료시키지 않는다.

## 상태 전이

| 상태 | 의미 | 새 interaction 허용 |
|---|---|---|
| 문서 없음 | 최초 요청 전 | `initial` 요청 하나만 선점 |
| `queued` | 선점과 durable handoff 완료 | 금지 |
| `preparing` | worker 하나가 처리권을 획득 | 금지 |
| `dispatching` | dispatch 진입 권한을 저장함 | 금지 |
| `accepted` | GitHub 성공 응답을 확인·저장함 | 금지 |
| `retryable` | 확실한 거절 뒤 새로운 generation을 허용함 | 현재 generation 하나만 선점 |
| `unknown` | GitHub 접수 여부 불명 | 금지 |
| `blocked` | 진행에 필요한 상태·표시 갱신 실패 | 금지 |

정상 전이는 다음과 같다.

```text
문서 없음 또는 retryable
  → queued + outbox 생성
  → preparing
  → dispatching
  → accepted
```

worker는 현재 `attempt_id`의 상태를 읽어 `queued → preparing` 전이를 검증하고, 읽은 정확한 `update_time`을 precondition으로 commit한다. 조건부 commit 성공이 확인된 worker 하나만 처리권을 획득한다. 이미 `preparing` 이후인 이벤트와 precondition 충돌은 외부 작업 없이 종료한다.

자동 lease 만료·소유권 탈취·불명 시도의 재dispatch를 두지 않는다. 선점 후 crash하면 해당 시도가 멈출 수 있으며 수동 복구로 처리한다.

읽기·검증과 조건부 commit 사이에는 Firestore 작업만 둔다. Slack과 GitHub 호출을 넣지 않으며, 충돌이나 불명확한 commit 결과를 자동 재실행하지 않는다. 원본과 outbox의 관련 write는 하나의 atomic commit에 포함한다.

## worker 처리 순서

### 진행 표시

worker 선점 성공 후 원본 Slack 메시지에서 재전송 버튼을 제거하고 다음을 표시한다.

> 식단 재전송을 요청하고 있습니다…

관련 없는 본문과 버튼은 보존하며 fallback `text`도 상태와 일치시킨다. 상태 문구는 중복 추가하지 않고 교체한다.

Slack 버튼에는 `disabled` 필드가 없으므로 버튼을 제거하고 텍스트로 교체한다. [Slack Button 문서](https://docs.slack.dev/reference/block-kit/block-elements/button-element/)

진행 표시의 성공 여부를 확인하지 못하면 dispatch하지 않는다. 가능하면 `blocked`를 저장하고 원본 상태를 수동 확인 대상으로 남긴다.

### dispatch 진입

Slack 진행 표시가 성공한 뒤 원본 문서의 현재 소유권과 `preparing` 상태를 확인하고, 읽은 `update_time`을 조건으로 `dispatching` 전환을 commit한다. CAS commit 성공이 확인된 경우에만 GitHub를 호출한다. 조건 충돌 또는 저장 결과 불명이면 호출하지 않는다.

`dispatching` 자체는 실제 호출·접수를 증명하지 않는다. 이 상태 저장 직후 crash할 수 있기 때문이다.

### GitHub 호출

현재 저장소·workflow·`ref: master`·`dry_run: false`·`target_date` 계약을 유지한다.

시도 하나의 GitHub POST는 한 번만 호출한다. 자동 POST 재시도와 자동 redirect 재전송을 사용하지 않는다.

## GitHub 결과의 보수적 분류

| 분류 | 조건 | 처리 |
|---|---|---|
| 접수 확인 | 현재 handler가 허용하는 `200` 또는 `204` | `accepted` 저장 후 접수 표시 |
| 명확한 거절 | GitHub가 요청을 수락하지 않았다는 신뢰할 수 있는 근거가 있는 응답 | 새 generation의 재시도 복구 가능 |
| 접수 불명 | 위 두 분류에 해당하지 않는 모든 결과 | 잠금 유지, 수동 확인 |

구체적인 보수 정책은 다음과 같다.

- `200`, `204` 이외의 응답을 성공으로 추정하지 않는다.
- `408`, `3xx`, `5xx`, transport 오류, timeout과 응답 해석 불가는 `unknown`으로 처리한다.
- `4xx`에서 `408`을 제외했다고 해서 모두 자동으로 명확한 거절로 분류하지 않는다.
- 특정 `4xx`를 명확한 거절로 허용하려면 해당 응답이 GitHub endpoint의 미수락을 뜻한다는 공식 계약 또는 충분한 근거를 오류 분류 정책과 검증에 기록해야 한다.
- 근거가 없는 코드는 `unknown`이 기본값이다. 응답 본문의 자연어 문구만으로 미수락을 추정하지 않는다.

현재 제공된 endpoint 문서 근거만으로 전체 `4xx` 미수락 정책을 확정하지 않는다. 따라서 구현 준비 단계에서 근거가 확보된 거절 코드의 명시적 allowlist를 정해야 한다. 충분한 근거가 없으면 자동 복원 대상은 보수적으로 줄어든다. [GitHub workflow API 문서](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

## 결과 표시

### 접수 확인

소유권을 확인하고 `accepted`를 저장한 뒤 공유 메시지를 갱신한다.

> 식단 재전송 요청을 접수했습니다. 결과가 게시될 때까지 기다려 주세요.

버튼은 복원하지 않는다. GitHub 접수와 식단 게시 완료를 구분한다.

결과 저장에 실패하면 기존 잠금을 유지한다. 원본 공유 메시지를 갱신할 수 있으면 GitHub 접수는 확인됐지만 상태 저장에 문제가 있음을 표시한다. 단순 성공으로만 표시하지 않는다.

상태 저장 성공 후 Slack 갱신이 실패하면 `accepted`를 유지하고 오류를 기록한다. 원본 메시지가 최신 상태를 보여주지 못할 수 있다. 이후 오래된 버튼을 눌러도 추가 dispatch는 발생하지 않지만, 해당 클릭의 HTTP ACK만으로 개별 안내가 표시되지는 않는다.

### 접수 불명

가능하면 `unknown`을 저장하고 다음을 표시한다.

> 재전송 요청의 접수 여부를 확인하지 못했습니다. 중복 실행을 막기 위해 다시 요청하지 않습니다. 관리자 확인이 필요합니다.

Firestore 또는 Slack 갱신에 실패해도 기존 잠금은 유지한다. 이벤트 재전달을 재dispatch 수단으로 사용하지 않는다.

## 명확한 거절의 복구 순서

새 버튼이 클릭 가능한 상태로 보이기 전에 새 generation이 서버에서 유효해야 한다.

1. 현재 `attempt_id`, `worker_id`, `dispatching`을 확인한다.
2. 현재 소유권과 `dispatching` 상태를 검증하고, 읽은 `update_time`을 조건으로 확실한 거절 결과 저장, 새 generation 회전과 `retryable` 전환을 commit한다.
3. commit 성공이 확인된 뒤에만 새 generation을 담은 버튼과 실패 문구를 `chat.update`로 게시한다.
4. 게시 성공 후 상태를 무조건 다시 쓰지 않는다.

복원 문구는 다음과 같다.

> 재전송 요청이 거절되었습니다. 원인을 확인한 후 다시 시도해 주세요.

이 순서에서는 이전 generation이 먼저 무효화된다. 새 버튼을 누른 요청은 이미 유효한 `retryable` 상태를 선점할 수 있다.

### 버튼 게시 실패

게시 실패 또는 결과 불명 시 동일 generation이 여전히 `retryable`인지 조건부 확인한다.

- 그대로라면 `blocked`로 전환해 추가 시도를 막고 수동 확인 대상으로 남긴다.
- 새 시도가 이미 `queued` 이후로 진행했다면 해당 시도의 상태를 변경하지 않는다.
- 이전 worker가 최신 상태를 덮어쓰거나 이전 버튼을 재게시하지 않는다.

Slack 갱신 timeout 전에 새 버튼이 실제로 게시되고 클릭될 수 있다. 그 클릭이 먼저 선점했다면 새 시도는 유지한다. 이전 dispatch는 명확한 거절이었고 새 generation도 한 번만 선점되므로 같은 시도의 중복 dispatch는 발생하지 않는다.

### 두 시스템 간 한계

Slack 게시 실패와 Firestore의 조건부 차단 쓰기가 함께 실패하면 서버에 `retryable`이 남을 수 있다. 늦게 적용된 새 버튼은 이후 한 번 선점될 수 있다.

이때도 이전 generation은 무효이며 새 generation의 원자 선점은 유지된다. 그러나 **Slack 표시 실패를 감지한 즉시 모든 새 클릭을 원자적으로 차단한다는 보장은 하지 않는다.** Slack과 Firestore에는 이를 묶는 transaction이 없다.

표시 실패 후 추가 시도를 절대 허용하지 않는 요구가 별도로 있다면 현재의 “클릭 가능한 버튼 복원” 계약과 함께 재설계해야 한다. 현재 승인 목표는 원본 알림별 시도 중복 방지와 확실한 거절의 재시도 복원으로 해석한다.

## 오래된 버튼과 부분 갱신

중복·오래된 callback은 다음과 같이 서버에서 처리한다.

| 확인한 상태 | 서버 동작 |
|---|---|
| 이전 generation | 새 선점·outbox 생성 금지 |
| `queued`, `preparing`, `dispatching` | 기존 시도 유지, 추가 요청 생성 금지 |
| `accepted` | 추가 요청 생성 금지 |
| `unknown`, `blocked` | 자동 재요청 금지, 수동 확인 대상 유지 |
| Firestore 조회 실패 | 상태를 추정하지 않고 추가 요청 생성 금지 |

유효한 interaction에는 HTTP `200`으로 ACK하지만, 이것이 클릭한 사용자에게 개별 안내를 표시한다는 뜻은 아니다. 사용자가 확인할 상태는 worker가 갱신한 원본 공유 메시지다.

중복·오래된 callback은 payload에 포함된 오래된 blocks로 원본 메시지를 갱신하지 않는다. 원본 메시지 갱신이 실패한 경우 오래된 버튼이 계속 보일 수 있으나, 서버 측 generation과 원자 선점으로 추가 dispatch를 제한한다.

개별 ephemeral 메시지 전송은 이번 범위에 포함하지 않는다.

## 장애와 crash 처리

| 구간 | 동작 |
|---|---|
| 선점·outbox 조건부 atomic commit의 확인된 precondition 충돌 | handler는 직접 dispatch하지 않고 새 outbox를 만들지 않음 |
| 조건부 commit의 다른 실패 또는 결과 불명 | HTTP `200` ACK와 오류 기록을 수행한다. 실제 commit·worker 실행 여부는 불명이며 개별 실패 메시지 전달을 약속하지 않음 |
| commit 후 ACK 유실 | 중복 interaction은 기존 선점으로 차단 |
| outbox 이벤트 중복 | worker 선점에 성공한 실행만 처리 |
| `queued` 뒤 worker 미실행 | 잠금 유지, 수동 조사 |
| worker 선점 뒤 crash | 자동 소유권 탈취 없이 잠금 유지 |
| Slack 진행 표시 실패 | dispatch 금지 |
| `dispatching` 저장 직후 crash | 실제 호출 여부를 단정하지 않고 잠금 유지 |
| GitHub 접수 뒤 결과 저장 실패 | 재dispatch 금지 |
| timeout·`5xx` | `unknown`, 잠금 유지 |
| 거절 복구 중 Slack 갱신 실패 | 동일 generation이 여전히 미선점이면 조건부 차단 |
| Firestore 장애 | 무잠금 fallback 금지 |

플랫폼 이벤트 재전달은 있을 수 있지만, 이미 worker가 선점한 시도의 외부 호출을 다시 실행하도록 만들지 않는다. transient 오류 자동 복구보다 중복 방지를 우선하므로 가용성 손실이 발생할 수 있다.

## 수동 복구

자동 TTL, 문서 삭제와 시간 경과에 따른 잠금 해제를 사용하지 않는다. 문서 삭제는 오래된 date-only 요청을 최초 시도로 재인정하게 할 수 있다.

별도 승인된 운영 절차에서 다음을 수행한다.

1. 원본 키, generation, 시도·worker 식별자, 상태와 로그를 확인한다.
2. GitHub 실행 내역과 Slack 게시 결과를 확인한다. 날짜만으로 같은 요청이라고 확정하지 않는다.
3. 실제 접수가 확인되면 `accepted`와 표시를 복구한다.
4. 미접수가 확실하면 새 generation을 `retryable`로 먼저 저장하고 버튼을 게시한다.
5. 불명확하면 잠금을 유지한다.

새 시도를 허용하기 전에는 기존 worker가 더 이상 늦은 dispatch나 상태 갱신을 수행하지 않는 조건을 확보해야 한다. 함수 timeout 경과만으로 GitHub 미접수를 증명하지 않는다.

수동 변경도 기존 상태·소유권을 확인하는 조건부 변경으로 수행한다. 복구용 도구 구현은 제외한다.

## 서비스·의존성 전제

### 승인된 Firestore 방향의 전제

- `firestore.googleapis.com` 활성화
- 같은 Firebase 프로젝트의 Native mode `(default)` database
- database 위치와 보존 정책 결정
- 실제 런타임 서비스 계정 식별
- 읽기·쓰기 권한
- 버전을 고정한 공개 generated GAPIC `FirestoreClient` 초기화와 운영 환경의 ADC 인증

기준 데이터 역할은 `roles/datastore.user`이며 실제 런타임 주체에 필요한 범위로 부여한다. server client 접근은 IAM으로 제어된다. [Firestore IAM 문서](https://docs.cloud.google.com/firestore/native/docs/security/iam)

운영 Firestore 접근은 공개 generated GAPIC `FirestoreClient`와 ADC를 사용한다. 별도 서비스 계정 JSON secret을 추가하지 않는다. Firebase Functions는 Firebase Admin SDK에 계속 의존하지만, 재전송 저장 계층이 Firebase Admin 초기화를 직접 수행해야 하는 것은 아니다. 데이터 접근 권한은 실제 런타임 서비스 계정의 IAM으로 제어한다. [Firebase Admin 설정 문서](https://firebase.google.com/docs/admin/setup)

### 승인된 trigger 구조의 운영 전제

별도 Firestore 생성 trigger worker는 승인된 구조다. Firestore 이벤트 전달과 worker 배포에 필요한 Eventarc·실행 주체·호출 권한 및 관련 API 전제를 문서화한다. 실제 프로젝트 상태와 함수 세대에 맞는 권한을 확인해야 하며, 계정이나 권한을 추정해서 고정하지 않는다.

저장소 루트 `main.py`에서 기존 `post_daily_menu`, `retry_daily_menu`를 유지하면서 신규 worker를 import·export한다. 현재 `firebase.json`의 functions source `.`와 runtime `python312`를 사용하며, 필요한 근거 없이 설정을 변경하지 않는다.

실제 프로젝트 준비, API 활성화, IAM 변경과 배포는 이번 구현 범위에서 실행하지 않는다. 실제 환경 준비 상태와 플랫폼 ACK 확인은 별도로 허용된 출시 전 확인 조건이다.

### Python 및 기록

재전송 저장 계층은 `google-cloud-firestore==2.28.0`의 공개 generated GAPIC `FirestoreClient`로 초기화한다. 운영 환경에서는 ADC를 사용한다. 고수준 Firebase Admin Firestore 초기화를 이 저장 계층의 필수 경로로 두지 않는다.

`pyproject.toml`의 Firebase 실행·dev 환경에는 실제 직접 import하는 패키지를 직접 의존성으로 선언한다. `google-cloud-firestore==2.28.0`을 직접 선언하고, `google.auth` 또는 `grpc`를 직접 import하면 각각 `google-auth`, `grpcio`도 호환 범위를 확인해 직접 선언한다. 직접 import하지 않는 `firebase-admin`을 이 변경만을 위해 직접 의존성으로 추가하지 않는다. Firebase Functions의 Firebase Admin 전이 의존성을 제거하거나 우회하지 않는다.

운영 경로와 Emulator 검증 경로를 명시적으로 분리한다. Emulator 검증에서는 검증된 loopback endpoint, 익명 credentials와 비보안 gRPC transport를 직접 구성하며 ADC와 운영 endpoint에 접근하지 않는다. GAPIC이 `FIRESTORE_EMULATOR_HOST`나 익명 credentials를 자동 적용한다고 가정하지 않는다. host 미설정 시 client 초기화 전에 skip하고, 잘못된 host에서는 연결 전에 실패한다.

일반 CLI 사용과 테스트 수집 단계에서는 Firestore client나 인증을 초기화하지 않는다. 초기화 실패를 무잠금 dispatch로 우회하지 않는다.

로그에는 원본 문서 키, 시도·worker 식별자, 상태 전이와 오류 분류를 기록한다. 토큰·서명·원본 HTTP payload는 기록하지 않는다.

## 변경 표면

| 표면 | 목적 |
|---|---|
| `src/nanet_menu/firebase_function.py` | 빠른 ACK, 요청 검증, 원자 선점·outbox, trigger worker, 상태·복구 처리 |
| `pyproject.toml` | Firebase Admin/Firestore 의존성 |
| `README.md` | 동작, 서비스 전제, 추가 trigger 기반, 오류·수동 복구 절차 |
| `tests/test_firebase_function.py` | 검증 허용 후 계약 확인 |

최초 실패 알림의 date-only 버튼과 현재 Slack wrapper·workflow 계약은 유지할 수 있다.

별도 trigger가 실제로 요구하는 배포 설정 파일과 권한 문서의 범위는 추가 구조 승인 후 확정한다. 현재 파일 목록만으로 인프라 변경까지 승인됐다고 해석하지 않는다.

## 수용 기준

| ID | 기준 |
|---|---|
| C1 | 같은 원본 알림·generation의 동시 callback과 outbox 이벤트 중복에서 GitHub POST가 한 번만 발생한다. |
| C2 | worker는 공유 메시지의 버튼 제거·진행 표시 성공을 확인한 뒤 dispatch한다. 표시 실패 시 호출하지 않는다. |
| C3 | 근거가 있는 명확한 거절에서는 새 generation을 `retryable`로 먼저 저장하고 버튼을 복원한다. 이전 callback은 선점하지 못한다. |
| C4 | 접수 불명·결과 저장 실패에서 잠금과 재dispatch 금지를 유지한다. 표시 장애와 GitHub 접수·게시 완료를 혼동하지 않는다. 거절 복구의 교차 시스템 한계는 별도 명시한다. |
| C5 | 키는 원본 `channel_id + message.ts + action_id`이며 같은 날짜의 다른 메시지는 독립적이다. |
| C6 | Firestore 실패에서 직접·무잠금 dispatch하지 않는다. API/database/runtime IAM과 ADC 전제를 문서화한다. |
| C7 | ACK 앞에는 Slack/GitHub 호출이 없다. 제어 가능한 비transactional read·조건부 commit의 RPC timeout·retry와 read→commit 1회 제한에 잔여 예산을 적용하고, 지정한 로컬 시나리오에서 handler 2초 이내 반환을 측정한다. 초기화·인증 전체의 강제 deadline 보증은 주장하지 않는다. 실제 플랫폼 cold/warm ACK 3초 미만은 별도 출시 전 검증 기준이며, 이번 범위에서 미검증인 동안 launch hold를 유지한다. |

## 검증과 승인 조건

필요한 집중 확인은 다음과 같다.

- 동시 interaction의 원자 선점과 단일 outbox 생성
- outbox 중복 전달의 worker 선점과 단일 POST
- 동일한 read/update_time을 관측한 동시 CAS에서 원본 선점·outbox·worker 소유권 단일 성공 및 외부 호출 순서
- worker 선점 또는 `dispatching` CAS 실패 시 Slack 후속 게시·GitHub dispatch 금지
- 복구 generation 회전 CAS 실패 시 새 버튼 게시 금지
- 복원 실패 차단 CAS 실패 시 이미 선점된 새 시도 상태 보존
- 최초 date-only 값과 새 generation 호환
- 복구 commit 전 새 버튼을 게시하지 않는 순서
- 버튼 갱신 실패·timeout과 새 callback 경쟁
- 이전 worker의 늦은 상태 갱신 차단
- 보수적인 GitHub 결과 분류와 미근거 코드의 `unknown` 처리
- 각 crash·Firestore·Slack 실패 지점에서 dispatch 허용 여부
- 같은 날짜의 별도 알림 독립 처리
- RPC 재시도 설정, read→commit 시도 제한, 잔여 예산 적용과 handler 시간 측정
- worker 지연과 무관한 ACK 반환 및 로컬 handler 2초 측정
- 실제 플랫폼 cold/warm 3초 미만 검증 상태와 launch hold 여부의 별도 확인
- HTTP `200` ACK와 사용자에게 보이는 메시지를 구분하며, 성공·중복·오류 ACK의 본문 표시나 개별 ephemeral 전송을 기대하지 않음
- 원본 메시지 갱신 실패 시에도 ACK 성공을 진행 상태 표시 성공으로 판단하지 않음
- 의존성 구성과 일반 CLI 경계

실제 원자성은 mock의 순차 호출만으로 입증하지 않는다. 검증이 허용되면 격리된 Firestore 환경에서 경쟁 조건을 확인한다.

사용자는 2026-10-20에 TDD/QA용 pytest 테스트 추가·실행 및 격리된 loopback Firestore Emulator 검증을 승인했다. 검증은 실제 Firebase 프로젝트·Slack·GitHub 호출을 포함하지 않는다.

Firestore outbox·별도 생성 trigger worker 구조와 본 설계의 사용자 승인은 2026-10-20에 완료됐다. 별도 구현 계획과 pytest·격리된 loopback Firestore Emulator의 테스트·검증 권한도 같은 날 승인됐다.

실제 Firebase 프로젝트 준비, provisioning, API 활성화, IAM 변경, 배포와 실제 Slack/GitHub 호출은 이번 구현 범위 밖이다. 실제 플랫폼 ACK 기준을 별도 승인된 확인으로 충족하기 전에는 출시를 보류한다. 구현 후 필요한 QA와 재확인, 최종 전체 변경에 대한 독립 Reviewer 검토를 수행하며, Reviewer를 작업별 필수 gate로 배치하지 않는다. 이번 문서는 구현 계획이 아니다.
