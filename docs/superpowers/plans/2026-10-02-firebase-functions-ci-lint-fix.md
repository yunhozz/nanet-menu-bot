# Firebase Functions CI Lint Fix Implementation Plan

> **For agentic workers:** Yunho Harness에 따라 Coder가 승인된 소스 수정을 담당하고 Lead가 검토·Ruff 확인·배포·완료 판단을 담당한다. 단계는 checkbox로 추적한다. 추가 실행 스킬·에이전트·테스트는 호출하지 않는다.

**Goal:** 이미지 검색 코드의 Ruff 오류 세 개를 동작 변경 없이 수정하고, Ruff 확인 후 `nanet-menu` 프로젝트의 Firebase Functions를 재배포한다.

**Architecture:** 기존 이미지 검색 선택 정책과 함수 인터페이스를 유지한다. 한 파일의 표현식 대입·줄 나눔을 수정한 뒤, lint와 format 확인을 통과한 동일 소스를 Functions에 배포한다.

**Tech Stack:** Python 3.12, Ruff, Firebase CLI, Firebase Functions (`python312`).

**Spec:** 대화에서 승인된 Bounded 설계(`ㄱㄱ`): `parsed_url.port`의 검증 접근을 대입으로 유지하고 두 긴 표현식을 여러 줄로 나눈다. 별도 spec 파일은 없다.

## Global Constraints

- 구현 수정 파일은 `src/nanet_menu/image_search.py`뿐이다.
- `_valid_image_url(value: object) -> str | None` 및 `NaverImageSearch.first_image_url(menu_item: str) -> str | None` 인터페이스를 유지한다.
- 포트 검증의 `ValueError` 처리, 제목 일치 우선, 동일 응답 대체 이미지, 썸네일→원본 링크 선택 순서를 유지한다.
- workflow·README·테스트·Firebase 설정과 이미지 검색 동작을 변경하지 않는다.
- 테스트를 추가하거나 실행하지 않는다. `ruff check .`, `ruff format --check .`는 허용된 비테스트 확인이다.
- 배포 범위는 이미 승인된 `firebase deploy --only functions --project nanet-menu --non-interactive`이다.
- Coder가 구현 파일을 수정하고 Lead가 확인·배포한다. Planner는 읽기 전용이다.
- 계획 승인 이후 Coder를 진행한다. 배포를 위해 새로운 허가를 요청하지 않는다.

## Evidence

- 기준 상태: Lead가 제공한 clean HEAD `d6678ec`.
- 첨부 진단 `/Users/ma-24-007/.codex/attachments/1807b3a9-9f2a-4711-b5df-55dc497c3f9a/붙여넣은 텍스트.txt:400–445`: B018 한 개(`image_search.py:29`), E501 두 개(`:80`, `:86`), `ruff check .` 종료 코드 1.
- `.github/workflows/test.yml:22–24`: `ruff check .` → `ruff format --check .` → `pytest`. 이번 작업에서는 pytest를 실행하지 않는다.
- Lead 제공 설정 근거: `firebase.json`의 Functions runtime `python312`, `.firebaserc`의 default `nanet-menu`.
- 예상 배포 함수: `post_daily_menu`, `retry_daily_menu`, 리전 `asia-northeast3`.

## Review Focus

다음 항목은 테스트 대신 Task 2의 소스/diff 검토로 확인한다.

1. 숫자가 아니거나 범위를 벗어난 포트: 속성 접근과 `ValueError` 처리 유지.
2. 포트가 없는 정상 URL: 기존 반환 경로 유지.
3. 문자열이 아닌 제목: 기존 빈 `plain_title` 처리 유지.
4. 잘못된 썸네일과 정상 원본 링크: `or` 평가 순서 유지.
5. 제목 일치·대체 이미지·이미지 없음: 기존 선택·반환 경로 유지.

실제 검색 품질이나 Slack 렌더링은 이 작업의 확인 결과로 주장하지 않는다.

### Task 1: 세 Ruff 오류 수정 — Coder

**Files:** Modify `src/nanet_menu/image_search.py:29`, `:80`, `:86`.

**Interfaces:** 기존 함수 시그니처를 소비하고 동일 인터페이스의 코드 diff를 생산한다.

- [x] **Step 1:** `parsed_url.port`를 `_ = parsed_url.port`로 바꾸고 포트 검증 주석과 `try`/`except ValueError` 위치를 유지한다.
- [x] **Step 2:** `plain_title` 조건식을 괄호 안의 여러 줄 표현식으로 나눈다. 조건과 두 결과 표현식을 유지한다.
- [x] **Step 3:** `image_url` 표현식을 괄호 안에서 나눈다. `_valid_image_url(thumbnail) or _valid_image_url(link)` 평가 순서를 유지한다.

**Deliverable:** 승인된 세 표현식 수정만 포함한 diff. 테스트 수정·실행 없음.

### Task 2: 동작 보존 검토 및 Ruff 확인 — Lead

**Files:** Read `src/nanet_menu/image_search.py`와 Task 1 diff.

**Interfaces:** Task 1의 수정 소스를 소비하고, 배포 가능한 검토·Ruff 통과 근거를 생산한다.

- [x] **Step 1:** `git diff -- src/nanet_menu/image_search.py`와 작업 상태를 읽고, 변경 범위 및 Review Focus 다섯 항목을 확인한다.
- [x] **Step 2:** 저장소 루트에서 `ruff check .`를 실행한다. 예상 결과는 종료 코드 0 및 lint 오류 없음이다.
- [x] **Step 3:** `ruff format --check .`를 실행한다. 예상 결과는 종료 코드 0 및 format 변경 요구 없음이다.

**Failure handling:** 관련 오류가 남으면 Lead가 정확한 오류를 Coder에게 전달하고 해당 수정 후 필요한 확인을 반복한다. 다른 파일 수정이 필요하거나 관련 없는 오류가 발견되면 승인 범위를 확장하지 않고 Lead가 처리 방향을 결정한다. 확인 실패 상태에서는 Task 3을 진행하지 않는다.

**Deliverable:** 동작 보존 검토 결과와 두 Ruff 명령의 성공 근거. 테스트 통과를 주장하지 않는다.

### Task 3: Firebase Functions 재배포 및 결과 확인 — Lead

**Files:** Read `.firebaserc`, `firebase.json` 및 기존 Functions 설정. 수정 없음.

**Interfaces:** Task 2를 통과한 동일 소스를 소비하고, 승인 대상 프로젝트의 배포 성공 근거를 생산한다.

- [x] **Step 1:** 설정을 읽어 프로젝트 `nanet-menu`, runtime `python312`, 예상 함수 `post_daily_menu`·`retry_daily_menu`와 리전 `asia-northeast3`를 확인한다. 제공된 범위와 불일치하면 배포 전에 원인을 확인한다.
- [x] **Step 2:** 저장소 루트에서 다음 명령을 실행한다.

```bash
firebase deploy --only functions --project nanet-menu --non-interactive
```

- [x] **Step 3:** 종료 코드 0, 프로젝트 대상, 두 함수의 배포 완료 및 CLI의 전체 배포 완료 출력을 확인하고 결과를 기록한다.

**Failure handling:** CLI가 실패하면 성공으로 보고하지 않는다. 실패 단계와 실제 배포된 함수 여부를 확인해 보고한다. 자동으로 프로젝트·서비스 범위를 바꾸거나 함수 삭제를 승인하지 않는다.

**Deliverable:** `nanet-menu` Functions 재배포 성공 근거. CLI 배포 성공은 함수의 실제 메뉴 전송이나 Slack 렌더링 검증을 의미하지 않는다.

## Dependencies and Completion Criteria

순서는 **Task 1 → Task 2 → Task 3**이다. 소스 수정 후 Ruff 확인, 확인 통과 후 배포가 필요하고 배포 결과는 별도 확인 경로이므로 계획을 작성한다.

완료 조건은 승인된 세 수정만 반영되고, 두 Ruff 확인이 성공하며, 지정 프로젝트의 Functions 배포 성공이 CLI 출력으로 확인되는 것이다.

## Assumptions and Open Decisions

- Lead가 제공한 `d6678ec` 기준 상태와 Firebase 설정을 실행 시점에 확인한다.
- Ruff와 Firebase CLI 및 기존 배포 인증 환경이 사용 가능하다고 가정한다. 사용할 수 없으면 필요한 설치·인증 조치와 권한을 먼저 확인한다. 구현 범위는 확장하지 않는다.
- 두 함수 외의 추가 배포·삭제가 요구되면 Lead가 승인 범위와 대조한다.
- commit·push는 이 계획에 포함하지 않는다.
- 미결 설계 사항은 없다. 계획 승인과 실행 환경 확인이 남아 있다.

## Self-Review Findings

- **Coverage:** 승인된 B018 수정과 E501 두 수정은 Task 1, Ruff 확인은 Task 2, 승인된 재배포는 Task 3에 포함했다.
- **Steps:** 각 단계의 파일·행동·기대 결과를 명시했고 실패 시 후속 배포를 차단했다.
- **Types:** 기존 두 함수의 시그니처와 반환 타입을 유지한다.
- **Review Focus:** 다섯 동작 보존 항목을 Task 2의 읽기 검토에 연결했다. 테스트 추가·실행은 명시된 제약에 따라 제외했다.
- **Proportion:** 세 표현식 수정과 그에 의존하는 확인·배포를 세 작업으로 구성했다. 관련 없는 변경이나 추가 실행자는 포함하지 않았다.
- **Lead integration:** 진단 로그의 전체 경로를 기록했고 도구 부재 시 실행 환경 확인을 명시했다. Harness 역할 배분이 일반 실행 스킬 안내에 우선한다.

## Execution Result

- Coder corrected port-validation assignment and Ruff-required line layout in `image_search.py`; image selection behavior preserved.
- `.venv/bin/ruff check .`: `All checks passed!`, exit 0.
- `.venv/bin/ruff format --check .`: `27 files already formatted`, exit 0.
- `firebase deploy --only functions --project nanet-menu --non-interactive`: exit 0, `Deploy complete!`; `post_daily_menu` and `retry_daily_menu` both successfully updated in `asia-northeast3` (Python 3.12, 2nd Gen).
- No tests, commits, pushes, tool installs, or dependency changes. GitHub CI was not rerun; local lint/format evidence and Firebase deployment evidence only.
