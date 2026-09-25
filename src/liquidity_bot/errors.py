"""Project-level boundary errors."""


class AdapterError(RuntimeError):
    """A remote response cannot be safely interpreted."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        status_code: int | None = None,
        error_code: str | None = None,
        trace_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        self.error_code = error_code
        self.trace_id = trace_id
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


def is_not_found(exc: BaseException) -> bool:
    """One classification for a venue "resource not found" read failure.

    The SDK raises ``NotFoundError`` for exactly HTTP 404, and the gateway keeps
    both that name in the message and the status on ``AdapterError``. Prefer
    the structured status so a wording change cannot silently alter retry or
    terminal-proof handling; the long-standing text match remains for errors
    built without a status. For real SDK responses both agree.
    """

    return (getattr(exc, "status_code", None) == 404
            or (isinstance(exc, AdapterError) and "NotFound" in str(exc)))


class AccountSnapshotConsistencyError(AdapterError):
    """A complete account snapshot crossed a real cash-state transition.

    This is not malformed venue data or transport backpressure.  A fill,
    settlement, deposit, or withdrawal can legitimately change settled cash
    between the reads used to bracket positions and cash activity.  Callers
    may retry the *whole* snapshot once; they must still fail closed if the
    fresh bracket is inconsistent.
    """


class MarketStreamUnavailable(AdapterError):
    """The shared public stream failed, not an individual market's book."""


class ApiAdmissionDeferred(AdapterError):
    """A local limiter declined work before any network request was sent.

    This is deliberately distinct from an ambiguous remote outcome.  The
    caller may safely leave the market unchanged and try again after the
    reported delay; there is no venue-side order to discover or quarantine.
    """


class EmptyBookError(AdapterError):
    """The venue returned a valid book wrapper with no executable side.

    This is response-backed market state, not a transport failure.  Keeping it
    distinct lets recovery quarantine one post-kickoff position for settlement
    monitoring without treating an unavailable or malformed book as equivalent
    evidence.
    """


class RuntimeInvariantViolation(RuntimeError):
    """A local ownership, identity, or exact-risk invariant was violated.

    Unlike an adapter or transport failure, retrying the same operation cannot
    make this condition safe.  The live runner must end maker quoting and enter
    its cancel/flatten shutdown path.
    """

    def __init__(self, code: str, detail: str | None = None):
        clean = str(code or "unspecified_runtime_invariant").strip()
        self.code = clean
        super().__init__(detail or clean)
