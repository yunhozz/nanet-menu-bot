import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from time import monotonic

import pytest
from google.cloud.firestore_v1.types import BatchGetDocumentsResponse

from nanet_menu import retry_store as store
from nanet_menu.formatter import format_failure_alert_payload


def test_alert_key_preserves_timestamp_strings():
    from nanet_menu import retry_store as store

    identity = store.RetryIdentity("C1", "123.000100", "retry_daily_menu")
    expected = hashlib.sha256(
        json.dumps(
            ["C1", "123.000100", "retry_daily_menu"],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    assert store.alert_key(identity) == expected
    assert store.alert_key(identity) != store.alert_key(
        store.RetryIdentity("C1", "123.0001", "retry_daily_menu")
    )


class MemoryGapic:
    """단일 실행 계약용 GAPIC 대역. 원자성·경쟁은 Emulator에서 별도 검증한다."""

    def __init__(self):
        self.docs = {}
        self.calls = []
        self.failure = None
        self.after_commit = False
        self.version = 0
        self.reject_commit = None
        self.before_read = None
        self.before_commit = None

    def _call(self, name, request, kwargs):
        self.calls.append((name, request, kwargs))
        if self.failure == name:
            raise RuntimeError("injected failure")

    def batch_get_documents(self, request, **kwargs):
        self._call("get", request, kwargs)
        if self.before_read is not None:
            callback, self.before_read = self.before_read, None
            callback()
        assert "transaction" not in request
        return iter(
            [
                BatchGetDocumentsResponse(found=self.docs[name])
                if name in self.docs
                else BatchGetDocumentsResponse(missing=name)
                for name in request["documents"]
            ]
        )

    def commit(self, request, **kwargs):
        self._call("commit", request, kwargs)
        if self.before_commit is not None:
            callback, self.before_commit = self.before_commit, None
            callback()
        assert "transaction" not in request
        from google.api_core.exceptions import AlreadyExists, FailedPrecondition
        from google.cloud.firestore_v1.types import Precondition

        if self.reject_commit:
            raise self.reject_commit("injected CAS rejection")
        for write in request["writes"]:
            previous = self.docs.get(write.update.name)
            kind = Precondition.pb(write.current_document).WhichOneof("condition_type")
            if kind == "exists":
                assert write.current_document.exists is False
                if previous is not None:
                    raise AlreadyExists("document already exists")
            elif kind == "update_time":
                if previous is None or previous.update_time != write.current_document.update_time:
                    raise FailedPrecondition("version changed")
            else:
                pytest.fail("모든 write에 관찰 버전 또는 exists=false가 필요합니다.")
        self.version += 1
        for write in request["writes"]:
            document = deepcopy(write.update)
            document.update_time = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(
                seconds=self.version
            )
            self.docs[document.name] = document
        if self.after_commit:
            raise TimeoutError("response lost")


@pytest.fixture
def setup_store():
    client = MemoryGapic()
    return store.RetryStore(client=client, project_id="demo-retry"), client


def request(generation="initial", ts="123.000100", target="2026-10-20"):
    return store.RetryRequest(
        store.RetryIdentity("C1", ts, "retry_daily_menu"),
        target,
        generation,
        dict(
            format_failure_alert_payload(date(2026, 10, 20), "오류", retry_date=date(2026, 10, 20))
        ),
    )


def claim(db, value=None, attempt="a1"):
    return db.claim_and_enqueue(value or request(), attempt_id=attempt, deadline=monotonic() + 1.5)


def decoded(client, collection, key):
    doc = next(doc for name, doc in client.docs.items() if name.endswith(f"/{collection}/{key}"))
    return store.decode_document(doc)


def test_initial_claim_atomically_creates_one_outbox(setup_store):
    db, client = setup_store
    assert claim(db).outcome == "claimed"
    key = store.alert_key(request().identity)
    state = decoded(client, "slack_retry_requests", key)
    outbox = decoded(client, "slack_retry_outbox", "a1")
    assert state["state"] == "queued"
    assert state["attempt_id"] == outbox["attempt_id"] == "a1"
    assert outbox["message"] == request().message
    assert len(client.docs) == 2
    assert claim(db, attempt="a2").outcome == "duplicate"
    assert len(client.docs) == 2
    commits = [call for call in client.calls if call[0] == "commit"]
    assert len(commits) == 1 and len(commits[0][1]["writes"]) == 2
    for _, _, kwargs in client.calls:
        assert kwargs["retry"] is None
        assert 0 < kwargs["timeout"] <= 1.5


def test_worker_claim_is_once_and_only_owner_can_transition(setup_store):
    db, client = setup_store
    claim(db)
    key = store.alert_key(request().identity)
    owner = db.claim_worker(key, attempt_id="a1", worker_id="w1")
    assert owner == store.AttemptOwner(key, "a1", "w1")
    assert db.claim_worker(key, attempt_id="a1", worker_id="w2") is None
    assert not db.transition(
        store.AttemptOwner(key, "a1", "wrong"), expected_state="preparing", new_state="dispatching"
    )
    assert db.transition(owner, expected_state="preparing", new_state="dispatching")
    assert db.transition(
        owner, expected_state="dispatching", new_state="accepted", dispatch_status_code=204
    )
    assert not db.transition(owner, expected_state="accepted", new_state="queued")
    assert decoded(client, "slack_retry_requests", key)["state"] == "accepted"


def test_old_generation_cannot_claim_after_rotation(setup_store):
    db, client = setup_store
    assert claim(db, request("made-up")).outcome == "stale"
    claim(db)
    key = store.alert_key(request().identity)
    owner = db.claim_worker(key, attempt_id="a1", worker_id="w1")
    db.transition(owner, expected_state="preparing", new_state="dispatching")
    assert db.make_retryable(owner, next_generation="g2", dispatch_status_code=422)
    assert claim(db, attempt="a2").outcome == "stale"
    assert claim(db, request("g2"), attempt="a2").outcome == "claimed"
    assert not db.block_retryable(owner, generation="g2", reason="display_failed")
    assert not db.transition(owner, expected_state="queued", new_state="blocked")
    assert decoded(client, "slack_retry_requests", key)["attempt_id"] == "a2"


def test_block_retryable_requires_current_generation(setup_store):
    db, client = setup_store
    claim(db)
    key = store.alert_key(request().identity)
    owner = db.claim_worker(key, attempt_id="a1", worker_id="w1")
    db.transition(owner, expected_state="preparing", new_state="dispatching")
    db.make_retryable(owner, next_generation="g2", dispatch_status_code=422)
    assert not db.block_retryable(owner, generation="wrong", reason="display_failed")
    assert db.block_retryable(owner, generation="g2", reason="display_failed")
    assert claim(db, request("g2"), attempt="a2").outcome == "blocked"


def test_date_schema_and_unknown_state_fail_closed(setup_store):
    db, client = setup_store
    claim(db)
    assert claim(db, request(target="2026-10-21"), attempt="a2").outcome == "blocked"
    name = next(n for n in client.docs if "/slack_retry_requests/" in n)
    client.docs[name].fields["schema_version"].integer_value = 99
    assert claim(db, attempt="a2").outcome == "blocked"
    client.docs[name].fields["schema_version"].integer_value = 1
    client.docs[name].fields["state"].string_value = "future_state"
    assert claim(db, attempt="a2").outcome == "blocked"


@pytest.mark.parametrize("failure", ["get", "commit"])
def test_rpc_failures_never_retry_or_partially_write(setup_store, failure):
    db, client = setup_store
    client.failure = failure
    with pytest.raises(store.RetryStoreError):
        claim(db)
    assert not client.docs
    assert sum(name == failure for name, _, _ in client.calls) == 1
    if failure == "commit":
        assert [name for name, _, _ in client.calls] == ["get", "commit"]


def test_commit_response_loss_preserves_handoff_without_retry(setup_store):
    db, client = setup_store
    client.after_commit = True
    with pytest.raises(store.RetryStoreError):
        claim(db)
    assert len(client.docs) == 2
    assert [name for name, _, _ in client.calls] == ["get", "commit"]


def test_expired_budget_starts_no_rpc(setup_store):
    db, client = setup_store
    with pytest.raises(store.RetryStoreError):
        db.claim_and_enqueue(request(), attempt_id="a1", deadline=monotonic() - 1)
    assert not client.calls


def test_budget_expiry_after_read_starts_no_commit_or_cleanup(setup_store, monkeypatch):
    db, client = setup_store
    ticks = iter([0.0, 2.0, 2.1])
    monkeypatch.setattr(store.time, "monotonic", lambda: next(ticks, 3.0))
    with pytest.raises(store.RetryStoreError):
        db.claim_and_enqueue(request(), attempt_id="a1", deadline=1.5)
    assert [name for name, _, _ in client.calls] == ["get"]
    assert not client.docs


def test_message_schema_rejects_secret_fields_and_unsupported_blocks():
    for message in (
        {"text": "x", "blocks": [], "response_url": "secret"},
        {"text": "x", "blocks": [{"type": "image", "image_url": "x"}]},
        {"text": "x", "blocks": [], "token": "secret"},
    ):
        with pytest.raises(ValueError):
            store.validate_retry_message(message)
    store.validate_retry_message(request().message)


def test_message_storage_boundary_counts_utf8_not_json_escaping():
    limit = store.MAX_MESSAGE_STORAGE_BYTES
    empty = {"text": "", "blocks": []}
    overhead = store.storage_size(empty)
    for delta in (-1, 0):
        value = {"text": "x" * (limit - overhead + delta), "blocks": []}
        store.validate_retry_message(value)
    with pytest.raises(ValueError):
        store.validate_retry_message({"text": "x" * (limit - overhead + 1), "blocks": []})
    assert store.storage_size({"text": "한"}) - store.storage_size({"text": ""}) == 3
    assert store.storage_size({"text": '"'}) - store.storage_size({"text": ""}) == 1


def test_aborted_commit_is_not_retried(setup_store):
    from google.api_core.exceptions import Aborted

    db, client = setup_store

    def abort(request, **kwargs):
        client.calls.append(("commit", request, kwargs))
        raise Aborted("contention")

    client.commit = abort
    with pytest.raises(store.RetryStoreError):
        claim(db)
    assert [name for name, _, _ in client.calls] == ["get", "commit"]
    assert not client.docs


@pytest.mark.parametrize(
    "endpoint",
    [
        None,
        "",
        "example.com:8080",
        "0.0.0.0:8080",
        "localhost",
        "http://127.0.0.1:8080",
        "127.0.0.1:0",
        "127.0.0.1:65536",
    ],
)
def test_emulator_factory_rejects_endpoint_before_initialization(endpoint, monkeypatch):
    import google.auth
    import grpc
    from google.cloud.firestore_v1.services.firestore import FirestoreClient

    def forbidden(*args, **kwargs):
        pytest.fail("인증/client/transport를 초기화하면 안 됩니다.")

    monkeypatch.setattr(google.auth, "default", forbidden)
    monkeypatch.setattr(grpc, "insecure_channel", forbidden)
    monkeypatch.setattr(FirestoreClient, "__init__", forbidden)
    with pytest.raises(ValueError):
        store.create_emulator_client(endpoint, project_id="demo-retry")


def test_emulator_factory_uses_explicit_loopback_transport(monkeypatch):
    import google.auth
    import grpc
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.firestore_v1.services.firestore import FirestoreClient
    from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport

    calls = {}
    channel = object()

    def credentials_forbidden(*args, **kwargs):
        pytest.fail("ADC를 조회하면 안 됩니다.")

    monkeypatch.setattr(google.auth, "default", credentials_forbidden)
    monkeypatch.setattr(
        grpc, "insecure_channel", lambda target: calls.setdefault("host", target) and channel
    )

    def transport_init(self, **kwargs):
        calls["transport"] = kwargs

    def client_init(self, **kwargs):
        calls["client"] = kwargs

    monkeypatch.setattr(FirestoreGrpcTransport, "__init__", transport_init)
    monkeypatch.setattr(FirestoreClient, "__init__", client_init)
    store.create_emulator_client("127.0.0.1:8080", project_id="demo-retry")
    assert calls["host"] == "127.0.0.1:8080"
    assert calls["transport"]["channel"] is channel
    assert isinstance(calls["transport"]["credentials"], AnonymousCredentials)
    assert isinstance(calls["client"]["transport"], FirestoreGrpcTransport)


def test_duplicate_read_performs_no_cleanup_or_write(setup_store):
    db, client = setup_store
    claim(db)
    assert claim(db, attempt="a2").outcome == "duplicate"
    assert [name for name, _, _ in client.calls] == ["get", "commit", "get"]


def test_acceptance_status_cannot_be_made_retryable(setup_store):
    db, client = setup_store
    claim(db)
    key = store.alert_key(request().identity)
    owner = db.claim_worker(key, attempt_id="a1", worker_id="w1")
    db.transition(owner, expected_state="preparing", new_state="dispatching")
    for status in (200, 204):
        with pytest.raises(ValueError):
            db.make_retryable(owner, next_generation="g2", dispatch_status_code=status)
    assert decoded(client, "slack_retry_requests", key)["state"] == "dispatching"


def test_exact_create_cas_rejection_has_no_retry_or_partial_outbox(setup_store):
    from google.api_core import exceptions

    db, client = setup_store
    client.reject_commit = exceptions.AlreadyExists
    assert claim(db).outcome == "duplicate"
    assert not client.docs
    assert [name for name, _, _ in client.calls] == ["get", "commit"]


def test_worker_and_owner_cas_rejection_preserve_state(setup_store):
    from google.api_core.exceptions import FailedPrecondition

    db, client = setup_store
    claim(db)
    key = store.alert_key(request().identity)
    client.reject_commit = FailedPrecondition
    with pytest.raises(store.RetryStoreError):
        db.claim_worker(key, attempt_id="a1", worker_id="w1")
    assert decoded(client, "slack_retry_requests", key)["state"] == "queued"
    client.reject_commit = None
    owner = db.claim_worker(key, attempt_id="a1", worker_id="w1")
    client.reject_commit = FailedPrecondition
    with pytest.raises(store.RetryStoreError):
        db.transition(owner, expected_state="preparing", new_state="dispatching")
    assert decoded(client, "slack_retry_requests", key)["state"] == "preparing"


@pytest.mark.parametrize(
    "message",
    [
        "",
        "generic precondition failure",
        "the stored version (123) does not match the required base version (1)",
    ],
)
def test_unconfirmed_failed_precondition_is_storage_error(setup_store, message):
    from google.api_core.exceptions import FailedPrecondition

    db, client = setup_store
    client.reject_commit = lambda ignored: FailedPrecondition(message)
    with pytest.raises(store.RetryStoreError) as caught:
        claim(db)
    assert isinstance(caught.value.__cause__, FailedPrecondition)
    assert not client.docs
    assert [name for name, _, _ in client.calls] == ["get", "commit"]


def test_already_exists_without_create_precondition_is_storage_error(setup_store):
    from google.api_core.exceptions import AlreadyExists

    db, client = setup_store
    claim(db)
    key = store.alert_key(request().identity)
    client.reject_commit = AlreadyExists
    with pytest.raises(store.RetryStoreError):
        db.claim_worker(key, attempt_id="a1", worker_id="w1")
    assert decoded(client, "slack_retry_requests", key)["state"] == "queued"


def test_existing_outbox_with_missing_original_is_untouched(setup_store):
    from google.cloud.firestore_v1.types import Document, Value

    db, client = setup_store
    key = store.alert_key(request().identity)
    original_name = db._name("slack_retry_requests", key)
    outbox_name = db._name("slack_retry_outbox", "a1")
    existing_outbox = Document(
        name=outbox_name,
        fields={
            "attempt_id": Value(string_value="a1"),
            "message": Value(string_value="existing durable handoff"),
        },
        update_time=datetime(2026, 1, 1, tzinfo=UTC),
    )

    def seed_before_read():
        assert original_name not in client.docs
        client.docs[outbox_name] = deepcopy(existing_outbox)

    # Database state is installed before the original's read is served.
    client.before_read = seed_before_read
    assert claim(db).outcome == "duplicate"
    assert original_name not in client.docs
    assert set(client.docs) == {outbox_name}
    assert Document.pb(client.docs[outbox_name]) == Document.pb(existing_outbox)
    assert [name for name, _, _ in client.calls] == ["get", "commit"]


def test_original_version_change_before_commit_preserves_concurrent_state(setup_store):
    from google.api_core.exceptions import FailedPrecondition
    from google.cloud.firestore_v1.types import Document, Value

    db, client = setup_store
    claim(db)
    key = store.alert_key(request().identity)
    owner = db.claim_worker(key, attempt_id="a1", worker_id="w1")
    assert db.transition(owner, expected_state="preparing", new_state="dispatching")
    assert db.make_retryable(owner, next_generation="g2", dispatch_status_code=422)
    original_name = db._name("slack_retry_requests", key)
    new_outbox_name = db._name("slack_retry_outbox", "a2")
    observed_version = client.docs[original_name].update_time
    baseline_outbox_name = db._name("slack_retry_outbox", "a1")
    baseline_outbox = deepcopy(client.docs[baseline_outbox_name])
    concurrent = deepcopy(client.docs[original_name])
    concurrent.fields["generation"] = Value(string_value="g3")
    concurrent.fields["attempt_id"] = Value(string_value="concurrent-attempt")
    concurrent.fields["state"] = Value(string_value="queued")
    concurrent.fields["worker_id"] = Value(null_value=0)
    concurrent.update_time = observed_version + timedelta(seconds=1)

    def write_after_read():
        client.docs[original_name] = deepcopy(concurrent)

    client.before_commit = write_after_read
    client.calls.clear()
    with pytest.raises(store.RetryStoreError) as caught:
        claim(db, request("g2"), attempt="a2")
    assert isinstance(caught.value.__cause__, FailedPrecondition)
    assert new_outbox_name not in client.docs
    assert Document.pb(client.docs[original_name]) == Document.pb(concurrent)
    assert Document.pb(client.docs[baseline_outbox_name]) == Document.pb(baseline_outbox)
    assert [name for name, _, _ in client.calls] == ["get", "commit"]
    writes = client.calls[-1][1]["writes"]
    original_write = next(write for write in writes if write.update.name == original_name)
    assert original_write.current_document.update_time == observed_version
