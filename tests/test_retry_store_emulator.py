"""Orchestrator가 준비한 격리 loopback Emulator에만 접속한다."""

import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from time import monotonic, sleep
from uuid import uuid4

import pytest
from google.api_core.exceptions import Aborted, DeadlineExceeded

from nanet_menu.formatter import format_failure_alert_payload
from nanet_menu.retry_store import (
    RetryIdentity,
    RetryRequest,
    RetryStore,
    RetryStoreError,
    alert_key,
    create_emulator_client,
)

pytestmark = pytest.mark.firestore_emulator


@pytest.fixture
def isolated_store():
    endpoint = os.environ.get("FIRESTORE_EMULATOR_HOST")
    if endpoint is None:
        pytest.skip("격리 loopback Firestore Emulator가 없습니다. client/ADC 초기화 생략")
    # factory validates before constructing credentials, transport, or client.
    client = create_emulator_client(endpoint, project_id="demo-slack-retry")
    db = RetryStore(project_id="demo-slack-retry", client=client)
    paths = set()
    try:
        yield db, client, paths
    finally:
        try:
            snapshots = list(
                client.batch_get_documents(
                    request={"database": db.database, "documents": sorted(paths)},
                    retry=None,
                    timeout=5,
                )
            )
            existing = sorted(snapshot.found.name for snapshot in snapshots if snapshot.found.name)
            for path in existing:
                for attempt in range(3):
                    try:
                        client.delete_document(request={"name": path}, retry=None, timeout=5)
                        break
                    except (Aborted, DeadlineExceeded) as exc:
                        print(
                            {
                                "cleanup_error_at": datetime.now(UTC).isoformat(),
                                "endpoint": endpoint,
                                "path": path,
                                "attempt": attempt + 1,
                                "method": "delete_document(retry=None, timeout=5)",
                                "error": type(exc).__name__,
                            }
                        )
                        if attempt == 2:
                            raise
                        sleep(0.25 * (attempt + 1))
            remaining = list(
                client.batch_get_documents(
                    request={"database": db.database, "documents": sorted(paths)},
                    retry=None,
                    timeout=5,
                )
            )
            assert not any(snapshot.found.name for snapshot in remaining)
            print({"cleanup": "verified_missing", "deleted_documents": len(existing)})
        finally:
            client.transport.close()


def test_distinct_timestamps_are_independent(isolated_store):
    from nanet_menu.retry_store import decode_document

    db, client, paths = isolated_store
    run_id = uuid4().hex
    keys = []
    for index, timestamp in enumerate(("123.000100", "123.000101")):
        identity = RetryIdentity("C" + run_id, timestamp, "retry_daily_menu")
        value = RetryRequest(
            identity,
            "2026-10-20",
            "initial",
            dict(
                format_failure_alert_payload(
                    date(2026, 10, 20), "오류", retry_date=date(2026, 10, 20)
                )
            ),
        )
        key = alert_key(identity)
        keys.append(key)
        attempt = f"{run_id}-{index}"
        state_path = db._name("slack_retry_requests", key)
        outbox_path = db._name("slack_retry_outbox", attempt)
        paths.update((state_path, outbox_path))
        assert (
            db.claim_and_enqueue(value, attempt_id=attempt, deadline=monotonic() + 1.5).outcome
            == "claimed"
        )
        snapshots = list(
            client.batch_get_documents(
                request={"database": db.database, "documents": [state_path, outbox_path]},
                retry=None,
                timeout=5,
            )
        )
        documents = {doc.found.name: decode_document(doc.found) for doc in snapshots}
        assert len(documents) == 2
        assert documents[state_path]["state"] == "queued"
        assert documents[state_path]["message_ts"] == timestamp
        assert (
            documents[state_path]["attempt_id"] == documents[outbox_path]["attempt_id"] == attempt
        )
    assert keys[0] != keys[1]


@pytest.mark.parametrize("phase", ["initial", "worker", "retryable"])
def test_barrier_same_observation_has_one_winner(isolated_store, monkeypatch, phase):
    """20개 호출이 같은 missing/version을 읽은 후에만 commit 경쟁을 시작한다."""
    from threading import Barrier

    from google.cloud.firestore_v1.types import Document

    from nanet_menu.retry_store import decode_document

    db, client, paths = isolated_store
    run_id = uuid4().hex
    identity = RetryIdentity("C" + run_id, "123.000200", "retry_daily_menu")
    key = alert_key(identity)
    state_path = db._name("slack_retry_requests", key)
    paths.add(state_path)
    message = dict(
        format_failure_alert_payload(date(2026, 10, 20), "오류", retry_date=date(2026, 10, 20))
    )
    value = RetryRequest(identity, "2026-10-20", "initial", message)
    base_attempt = run_id + "-base"
    if phase != "initial":
        paths.add(db._name("slack_retry_outbox", base_attempt))
        assert (
            db.claim_and_enqueue(value, attempt_id=base_attempt, deadline=monotonic() + 1.5).outcome
            == "claimed"
        )
    if phase == "retryable":
        owner = db.claim_worker(key, attempt_id=base_attempt, worker_id="base-worker")
        assert owner is not None
        assert db.transition(owner, expected_state="preparing", new_state="dispatching")
        assert db.make_retryable(owner, next_generation="g2", dispatch_status_code=422)
        value = RetryRequest(identity, value.target_date, "g2", message)

    gate = Barrier(20)
    observations = []
    original_rpc = db._rpc

    def gated_rpc(method, rpc_request, deadline):
        result = original_rpc(method, rpc_request, deadline)
        if method.__name__ == "batch_get_documents":
            result = list(result)
            assert len(result) == 1
            snapshot = result[0]
            if snapshot.found.name:
                observations.append(
                    ("version", Document.pb(snapshot.found).update_time.SerializeToString())
                )
            else:
                observations.append(("missing", snapshot.missing))
            gate.wait(timeout=1.0)
        return result

    monkeypatch.setattr(db, "_rpc", gated_rpc)
    attempts = [f"{run_id}-{index}" for index in range(20)]
    if phase != "worker":
        paths.update(db._name("slack_retry_outbox", attempt) for attempt in attempts)

    def compete(index):
        try:
            if phase == "worker":
                owner = db.claim_worker(key, attempt_id=base_attempt, worker_id=f"w{index}")
                return ("won" if owner is not None else "no_owner", index, None)
            outcome = db.claim_and_enqueue(
                value, attempt_id=attempts[index], deadline=monotonic() + 1.5
            ).outcome
            return ("won" if outcome == "claimed" else outcome, index, None)
        except RetryStoreError as exc:
            cause = exc.__cause__ or exc
            detail = type(cause).__name__
            if "RST_STREAM" in str(cause):
                detail += ":RST_STREAM"
            return "store_error", index, detail

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(compete, range(20)))
    monkeypatch.setattr(db, "_rpc", original_rpc)
    counts = Counter(status for status, _, _ in results)
    errors = Counter(detail for status, _, detail in results if status == "store_error")
    print(
        {
            "barrier_phase": phase,
            "outcomes": dict(counts),
            "errors": dict(errors),
            "same_observation_count": len(observations),
        }
    )
    assert len(observations) == 20
    assert len(set(observations)) == 1, observations
    assert observations[0][0] == ("missing" if phase == "initial" else "version")
    assert len(results) == 20 and sum(counts.values()) == 20
    assert counts["won"] == 1, results
    assert set(counts) <= {"won", "store_error", "duplicate", "no_owner"}, results
    assert sum(errors.values()) == counts["store_error"]
    snapshot = next(
        client.batch_get_documents(
            request={"database": db.database, "documents": [state_path]}, retry=None, timeout=5
        )
    )
    state = decode_document(snapshot.found)
    winner = next(index for status, index, _ in results if status == "won")
    if phase == "worker":
        assert state["state"] == "preparing"
        assert state["worker_id"] == f"w{winner}"
        assert state["attempt_id"] == base_attempt
    else:
        assert state["state"] == "queued"
        assert state["attempt_id"] == attempts[winner]
        assert state["generation"] == value.generation
        assert state["worker_id"] is None
        new_outboxes = list(
            client.batch_get_documents(
                request={
                    "database": db.database,
                    "documents": [db._name("slack_retry_outbox", attempt) for attempt in attempts],
                },
                retry=None,
                timeout=5,
            )
        )
        assert sum(bool(doc.found.name) for doc in new_outboxes) == 1
        if phase == "retryable":
            baseline = next(
                client.batch_get_documents(
                    request={
                        "database": db.database,
                        "documents": [db._name("slack_retry_outbox", base_attempt)],
                    },
                    retry=None,
                    timeout=5,
                )
            )
            assert baseline.found.name
