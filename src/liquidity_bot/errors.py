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


class ApiAdmissionDeferred(AdapterError):
    """A local limiter declined work before any network request was sent.

    This is deliberately distinct from an ambiguous remote outcome.  The
    caller may safely leave the market unchanged and try again after the
    reported delay; there is no venue-side order to discover or quarantine.
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
