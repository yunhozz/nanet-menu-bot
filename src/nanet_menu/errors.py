from dataclasses import dataclass


@dataclass(frozen=True)
class HttpFailure:
    kind: str
    exception_type: str | None = None
    status: int | None = None
    attempts: int = 0
    elapsed: float = 0.0
    budget_exhausted: bool = False


class NanetMenuError(RuntimeError):
    """Base error for a failed menu-bot stage."""


class CollectionError(NanetMenuError):
    """Notice or attachment collection failed."""

    def __init__(self, message: str, *, failure: HttpFailure | None = None):
        super().__init__(message)
        self.failure = failure


class MenuParseError(NanetMenuError):
    """The PDF did not yield a trustworthy menu."""

    def __init__(self, message: str, *, causes: tuple[NanetMenuError, ...] = ()):
        super().__init__(message)
        self.causes = causes


class SlackError(NanetMenuError):
    """Slack rejected the message or was unavailable."""

    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        outcome_unknown: bool = False,
        message_ts: str | None = None,
    ):
        super().__init__(message)
        self.method = method
        self.outcome_unknown = outcome_unknown
        self.message_ts = message_ts


@dataclass(frozen=True)
class DeliveryFailure:
    stage: str
    pre_send_confirmed: bool = False
    posted_count: int = 0
    total_count: int | None = None
    payload_index: int | None = None
    message_timestamps: tuple[str, ...] = ()
    outcome_unknown: bool = False


class DeliveryError(NanetMenuError):
    def __init__(self, message: str, *, failure: DeliveryFailure):
        super().__init__(message)
        self.failure = failure


def confirmed_pre_send_failure(error: Exception) -> bool:
    return (
        isinstance(error, DeliveryError)
        and error.failure.stage == "prepare"
        and error.failure.pre_send_confirmed
        and error.failure.posted_count == 0
    )
