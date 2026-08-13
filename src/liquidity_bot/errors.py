"""Project-level boundary errors."""


class AdapterError(RuntimeError):
    """A remote response cannot be safely interpreted."""


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
