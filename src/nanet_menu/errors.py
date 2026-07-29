class NanetMenuError(RuntimeError):
    """Base error for a failed menu-bot stage."""


class CollectionError(NanetMenuError):
    """Notice or attachment collection failed."""


class MenuParseError(NanetMenuError):
    """The PDF did not yield a trustworthy menu."""


class SlackError(NanetMenuError):
    """Slack rejected the message or was unavailable."""
