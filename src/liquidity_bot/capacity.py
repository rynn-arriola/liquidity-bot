"""Pure capacity planning for the live commissioning workload.

This module has no venue client, socket, credential, database or mutation
interface.  It converts the runner's existing scheduling rules into an
explicit request, stream, CPU and exposure budget so scale decisions can be
tested without starting a second process against the account.

The model deliberately separates observed inputs from policy.  In particular,
``quiet_market_fraction`` and ``mutation_requests_per_second`` must come from
measurement or a named stress scenario; a slot count is never inferred safe
from the REST ceiling alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import ceil
from typing import Mapping


# Every category emitted by adapters._request_category is classified here.
# The capacity report deliberately fails when a future category is not in this
# inventory, so a new endpoint cannot silently disappear from a width review.
FIXED_API_CATEGORIES = frozenset({
    "balance",
    "cash_history",
    "earnings",
    "open_orders",
    "positions",
})
BATCHED_API_CATEGORIES = frozenset({
    "incentive_contract_batch",
    "market_contract_batch",
})
SLOT_SCALED_API_CATEGORIES = frozenset({
    "execution_history",
    "incentive_discovery",
    "market_book",
    "market_contract",
    "market_discovery",
    "market_settlement",
    "mutation_cancel",
    "mutation_create",
    "mutation_other",
    "order_retrieve",
    "read_other",
})
KNOWN_API_CATEGORIES = (
    FIXED_API_CATEGORIES
    | BATCHED_API_CATEGORIES
    | SLOT_SCALED_API_CATEGORIES
)


@dataclass(frozen=True)
class ApiBudgetWindow:
    """One secret-free runtime limiter window parsed from the runner log."""

    elapsed_seconds: float
    slots: int
    attempts: int
    failures: int
    rate_limited: int
    max_waiting: int
    attempts_by_category: Mapping[str, int]


@dataclass(frozen=True)
class ApiWindowProjection:
    """Empirical projection from one observed width to a proposed width."""

    source_slots: int
    target_slots: int
    elapsed_seconds: float
    projected_runner_attempts: float
    projected_runner_rps: float
    scheduled_report_average_rps: float
    scheduled_report_peak_rps: float
    projected_host_average_rps: float
    projected_host_coincident_report_rps: float
    projected_by_category: Mapping[str, float]


def parse_api_budget_line(line: str) -> ApiBudgetWindow | None:
    """Parse one ``[api-budget]`` line without accepting partial evidence."""

    if "[api-budget]" not in str(line):
        return None
    fields: dict[str, str] = {}
    for token in str(line).split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key] = value
    required = {
        "slots", "seconds", "attempts", "failures", "rate_limited",
        "max_waiting", "categories",
    }
    missing = sorted(required - fields.keys())
    if missing:
        raise ValueError(
            "API budget line is missing fields: " + ", ".join(missing))
    try:
        categories = (
            {}
            if fields["categories"] == "none"
            else {
                name: int(raw)
                for item in fields["categories"].split(",")
                for name, raw in (item.split(":", 1),)
                if name
            }
        )
        result = ApiBudgetWindow(
            elapsed_seconds=float(fields["seconds"]),
            slots=int(fields["slots"]),
            attempts=int(fields["attempts"]),
            failures=int(fields["failures"]),
            rate_limited=int(fields["rate_limited"]),
            max_waiting=int(fields["max_waiting"]),
            attempts_by_category=categories,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("API budget line contains an invalid value") from exc
    if result.elapsed_seconds <= 0 or result.slots <= 0:
        raise ValueError("API budget window seconds and slots must be positive")
    if min(
        result.attempts,
        result.failures,
        result.rate_limited,
        result.max_waiting,
        *result.attempts_by_category.values(),
    ) < 0:
        raise ValueError("API budget counts cannot be negative")
    if sum(result.attempts_by_category.values()) != result.attempts:
        raise ValueError("API budget categories do not equal total attempts")
    unknown = sorted(set(result.attempts_by_category) - KNOWN_API_CATEGORIES)
    if unknown:
        raise ValueError(
            "unclassified API budget categories: " + ", ".join(unknown))
    return result


def project_api_budget_window(
    window: ApiBudgetWindow,
    target_slots: int,
    *,
    contract_batch_size: int = 8,
    scheduled_report_requests: int = 12,
    scheduled_report_interval_seconds: float = 3600.0,
    scheduled_report_max_rps: float = 0.25,
    host_limiter_rps: float = 4.0,
) -> ApiWindowProjection:
    """Conservatively project one observed window to another slot width.

    Account reads that remain one page below 100 owners are fixed. Contract
    and incentive batches move by the exact ``ceil(slots / 8)`` step. Every
    other event-, discovery-, execution- or market-shaped category scales with
    width; this is intentionally conservative for the first 32-slot canary.

    The hourly reward report is a cooperating second process. Its calls share
    the same host limiter but are absent from the runner's local attempt
    counter, so both its average and a worst coincident-window burst are shown.
    """

    if target_slots <= 0 or contract_batch_size <= 0:
        raise ValueError("projection slots and batch size must be positive")
    if (
        scheduled_report_requests < 0
        or scheduled_report_interval_seconds <= 0
        or scheduled_report_max_rps <= 0
        or host_limiter_rps <= 0
    ):
        raise ValueError("scheduled report inputs are invalid")
    unknown = sorted(set(window.attempts_by_category) - KNOWN_API_CATEGORIES)
    if unknown:
        raise ValueError(
            "unclassified API budget categories: " + ", ".join(unknown))
    slot_factor = target_slots / window.slots
    batch_factor = (
        ceil(target_slots / contract_batch_size)
        / ceil(window.slots / contract_batch_size)
    )
    projected: dict[str, float] = {}
    for category, attempts in window.attempts_by_category.items():
        if category in FIXED_API_CATEGORIES:
            factor = 1.0
        elif category in BATCHED_API_CATEGORIES:
            factor = batch_factor
        else:
            factor = slot_factor
        projected[category] = attempts * factor
    runner_attempts = sum(projected.values())
    runner_rps = runner_attempts / window.elapsed_seconds
    report_average = (
        scheduled_report_requests / scheduled_report_interval_seconds)
    report_peak = min(
        scheduled_report_max_rps,
        scheduled_report_requests / window.elapsed_seconds,
    )
    return ApiWindowProjection(
        source_slots=window.slots,
        target_slots=target_slots,
        elapsed_seconds=window.elapsed_seconds,
        projected_runner_attempts=runner_attempts,
        projected_runner_rps=runner_rps,
        scheduled_report_average_rps=report_average,
        scheduled_report_peak_rps=report_peak,
        projected_host_average_rps=runner_rps + report_average,
        projected_host_coincident_report_rps=(
            min(host_limiter_rps, runner_rps + report_peak)
        ),
        projected_by_category=projected,
    )


@dataclass(frozen=True)
class ApiWorkload:
    """REST work generated by one commissioning configuration.

    All values describe request *attempts*.  A confirmed 429 retry is therefore
    represented by ``read_attempt_multiplier`` rather than hidden as latency.
    Mutations are already attempts and are never automatically retried.
    """

    rest_requests_per_second: float = 4.0
    quiet_market_fraction: float = 0.0
    quiet_book_refresh_seconds: float = 3.0
    contract_refresh_seconds: float = 60.0
    contract_requests_per_batch: int = 2
    contract_batch_size: int = 1
    balance_refresh_seconds: float = 5.0
    account_mark_requests: int = 3
    cash_refresh_seconds: float = 15.0
    cash_activity_pages: int = 1
    reconcile_seconds: float = 10.0
    open_order_pages: int = 1
    position_pages: int = 1
    execution_activity_pages: int = 1
    mutation_requests_per_second: float = 0.0
    absent_order_requests_per_second: float = 0.0
    lifecycle_requests_per_second: float = 0.0
    recovery_requests_per_second: float = 0.0
    earnings_refresh_seconds: float = 900.0
    selection_refresh_seconds: float = 900.0
    selection_catalog_pages: int = 0
    selection_incentive_pages: int = 0
    selection_book_requests: int = 0
    scheduled_report_requests: int = 12
    scheduled_report_refresh_seconds: float = 3600.0
    scheduled_report_deadline_seconds: float = 2700.0
    read_attempt_multiplier: float = 1.0
    requested_headroom_fraction: float = 0.25

    def __post_init__(self) -> None:
        positive = {
            "rest_requests_per_second": self.rest_requests_per_second,
            "quiet_book_refresh_seconds": self.quiet_book_refresh_seconds,
            "contract_refresh_seconds": self.contract_refresh_seconds,
            "balance_refresh_seconds": self.balance_refresh_seconds,
            "cash_refresh_seconds": self.cash_refresh_seconds,
            "reconcile_seconds": self.reconcile_seconds,
            "earnings_refresh_seconds": self.earnings_refresh_seconds,
            "selection_refresh_seconds": self.selection_refresh_seconds,
            "scheduled_report_refresh_seconds": (
                self.scheduled_report_refresh_seconds),
            "scheduled_report_deadline_seconds": (
                self.scheduled_report_deadline_seconds),
            "read_attempt_multiplier": self.read_attempt_multiplier,
        }
        if any(value <= 0 for value in positive.values()):
            raise ValueError("capacity workload intervals and rates must be positive")
        if not 0 <= self.quiet_market_fraction <= 1:
            raise ValueError("quiet_market_fraction must be within [0, 1]")
        if not 0 <= self.requested_headroom_fraction < 1:
            raise ValueError("requested_headroom_fraction must be within [0, 1)")
        counts = (
            self.contract_requests_per_batch,
            self.contract_batch_size,
            self.account_mark_requests,
            self.cash_activity_pages,
            self.open_order_pages,
            self.position_pages,
            self.execution_activity_pages,
            self.selection_catalog_pages,
            self.selection_incentive_pages,
            self.selection_book_requests,
            self.scheduled_report_requests,
        )
        if any(value < 0 for value in counts):
            raise ValueError("capacity request counts cannot be negative")
        if self.contract_batch_size <= 0:
            raise ValueError("contract_batch_size must be positive")
        if self.account_mark_requests <= 0:
            raise ValueError("account_mark_requests must be positive")
        if min(
            self.mutation_requests_per_second,
            self.absent_order_requests_per_second,
            self.lifecycle_requests_per_second,
            self.recovery_requests_per_second,
        ) < 0:
            raise ValueError("capacity event rates cannot be negative")

    def request_breakdown(self, slots: int) -> Mapping[str, float]:
        """Average attempted requests/second by business responsibility."""

        if slots <= 0:
            raise ValueError("slots must be positive")

        # The ordinary five-second marked-risk read is balance/positions/
        # balance.  When the 15-second cash proof is due, the runner adds the
        # activity pages and a second bracketing balance.
        cash_adjustment = (
            self.cash_activity_pages + 1
        ) / self.cash_refresh_seconds
        reconciliation = (
            self.open_order_pages
            + self.position_pages
            + self.execution_activity_pages
        ) / self.reconcile_seconds
        selection = (
            self.selection_catalog_pages
            + self.selection_incentive_pages
            + self.selection_book_requests
        ) / self.selection_refresh_seconds

        reads = {
            "account_mark": (
                self.account_mark_requests / self.balance_refresh_seconds
            ),
            "cash_adjustment": cash_adjustment,
            "account_reconciliation": reconciliation,
            "market_contracts": (
                ceil(slots / self.contract_batch_size)
                * self.contract_requests_per_batch
                / self.contract_refresh_seconds
            ),
            "quiet_books": (
                slots
                * self.quiet_market_fraction
                / self.quiet_book_refresh_seconds
            ),
            "selection": selection,
            "earnings": 1 / self.earnings_refresh_seconds,
            "order_retrieve": self.absent_order_requests_per_second,
            "market_lifecycle": self.lifecycle_requests_per_second,
            "account_recovery": self.recovery_requests_per_second,
            # The hourly reward-table process shares the same host limiter but
            # is outside the runner's process-local telemetry. Its bounded
            # worst path is earnings + two cash-history walks + a
            # balance/positions/balance bracket: twelve calls.
            "scheduled_reporting": (
                self.scheduled_report_requests
                / self.scheduled_report_refresh_seconds
            ),
        }
        scaled_reads = {
            name: rate * self.read_attempt_multiplier
            for name, rate in reads.items()
        }
        return {
            **scaled_reads,
            "mutations": self.mutation_requests_per_second,
        }

    def selection_burst_requests(self) -> int:
        return (
            self.selection_catalog_pages
            + self.selection_incentive_pages
            + self.selection_book_requests
        )


@dataclass(frozen=True)
class HardwareProfile:
    """Measured single-process headroom on the intended production machine."""

    baseline_slots: int
    baseline_runner_cpu_cores: float
    comfortable_runner_cpu_cores: float = 0.75
    fixed_runner_cpu_cores: float = 0.0

    def __post_init__(self) -> None:
        if self.baseline_slots <= 0:
            raise ValueError("baseline_slots must be positive")
        if not 0 <= self.fixed_runner_cpu_cores < self.baseline_runner_cpu_cores:
            raise ValueError("fixed CPU must be below measured baseline CPU")
        if self.comfortable_runner_cpu_cores <= 0:
            raise ValueError("comfortable CPU budget must be positive")

    def projected_runner_cpu_cores(self, slots: int) -> float:
        if slots <= 0:
            raise ValueError("slots must be positive")
        scalable = self.baseline_runner_cpu_cores - self.fixed_runner_cpu_cores
        return self.fixed_runner_cpu_cores + scalable * slots / self.baseline_slots


@dataclass(frozen=True)
class ExposureProfile:
    """Portfolio headroom expressed as quote-size units beyond all slots."""

    portfolio_cushion_quote_units: float = 10.0
    required_cushion_over_slots: float = 0.25

    def __post_init__(self) -> None:
        if self.portfolio_cushion_quote_units < 0:
            raise ValueError("portfolio cushion cannot be negative")
        if self.required_cushion_over_slots < 0:
            raise ValueError("required exposure cushion cannot be negative")

    def cushion_over_slots(self, slots: int) -> float:
        if slots <= 0:
            raise ValueError("slots must be positive")
        return self.portfolio_cushion_quote_units / slots


@dataclass(frozen=True)
class CapacityAssessment:
    slots: int
    request_breakdown: Mapping[str, float]
    attempted_requests_per_second: float
    request_utilization: float
    request_headroom: float
    selection_burst_requests: int
    selection_burst_floor_seconds: float
    private_connections: int
    public_subscriptions: int
    projected_runner_cpu_cores: float | None
    exposure_cushion_over_slots: float
    comfortable: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class ScheduleSimulation:
    """Deadline evidence for one deterministic shared-governor replay."""

    duration_seconds: float
    generated_requests: int
    completed_requests: int
    remaining_requests: int
    missed_deadlines: int
    misses_by_category: tuple[tuple[str, int], ...]
    maximum_latency_seconds: float
    maximum_latency_by_category: tuple[tuple[str, float], ...]
    maximum_queue_depth: int


@dataclass(frozen=True)
class _ScheduledRequest:
    release_at: float
    deadline_at: float
    priority: int
    sequence: int
    category: str


def _periodic_releases(
    *, interval: float, duration: float, phase: float = 0.0,
) -> tuple[float, ...]:
    out: list[float] = []
    at = max(0.0, phase)
    while at < duration:
        out.append(at)
        at += interval
    return tuple(out)


def _scheduled_requests(
    slots: int, workload: ApiWorkload, duration_seconds: float,
) -> tuple[_ScheduledRequest, ...]:
    """Expand the runner's periodic business responsibilities into jobs."""

    jobs: list[_ScheduledRequest] = []
    sequence = 0

    def add(
        category: str,
        releases: tuple[float, ...],
        *,
        count: int,
        deadline_seconds: float,
        priority: int,
    ) -> None:
        nonlocal sequence
        for release in releases:
            for _ in range(count):
                jobs.append(_ScheduledRequest(
                    release,
                    release + deadline_seconds,
                    priority,
                    sequence,
                    category,
                ))
                sequence += 1

    add(
        "account_mark",
        _periodic_releases(
            interval=workload.balance_refresh_seconds,
            duration=duration_seconds,
        ),
        count=workload.account_mark_requests,
        deadline_seconds=10.0,
        priority=1,
    )
    add(
        "cash_adjustment",
        _periodic_releases(
            interval=workload.cash_refresh_seconds,
            duration=duration_seconds,
        ),
        count=workload.cash_activity_pages + 1,
        deadline_seconds=15.0,
        priority=1,
    )
    add(
        "account_reconciliation",
        _periodic_releases(
            interval=workload.reconcile_seconds,
            duration=duration_seconds,
        ),
        count=(
            workload.open_order_pages
            + workload.position_pages
            + workload.execution_activity_pages
        ),
        deadline_seconds=20.0,
        priority=1,
    )

    # Contract and incentive reads use the same bounded slug batches as the
    # runner. Batches naturally spread after their first response. Use even
    # phases rather than an unrealistic all-at-once steady-state burst;
    # startup/selection bursts are represented separately below.
    contract_batches = ceil(slots / workload.contract_batch_size)
    for index in range(contract_batches):
        phase = workload.contract_refresh_seconds * index / contract_batches
        add(
            "market_contracts",
            _periodic_releases(
                interval=workload.contract_refresh_seconds,
                duration=duration_seconds,
                phase=phase,
            ),
            count=workload.contract_requests_per_batch,
            deadline_seconds=workload.contract_refresh_seconds,
            priority=3,
        )

    quiet_markets = ceil(slots * workload.quiet_market_fraction)
    for index in range(quiet_markets):
        phase = (
            workload.quiet_book_refresh_seconds * index / quiet_markets
            if quiet_markets else 0.0
        )
        add(
            "quiet_books",
            _periodic_releases(
                interval=workload.quiet_book_refresh_seconds,
                duration=duration_seconds,
                phase=phase,
            ),
            count=1,
            # The refresh becomes due at age three and the decision book
            # becomes stale at age five.
            deadline_seconds=2.0,
            priority=2,
        )

    def event_releases(rate: float) -> tuple[float, ...]:
        return (() if rate <= 0 else _periodic_releases(
            interval=1 / rate,
            duration=duration_seconds,
            phase=min(1 / rate, 1.0),
        ))

    add(
        "mutations",
        event_releases(workload.mutation_requests_per_second),
        count=1,
        deadline_seconds=60.0,
        priority=0,
    )
    add(
        "order_retrieve",
        event_releases(workload.absent_order_requests_per_second),
        count=1,
        deadline_seconds=60.0,
        priority=2,
    )
    add(
        "market_lifecycle",
        event_releases(workload.lifecycle_requests_per_second),
        count=1,
        deadline_seconds=60.0,
        priority=2,
    )
    add(
        "account_recovery",
        event_releases(workload.recovery_requests_per_second),
        count=1,
        deadline_seconds=20.0,
        priority=1,
    )
    add(
        "earnings",
        _periodic_releases(
            interval=workload.earnings_refresh_seconds,
            duration=duration_seconds,
            phase=min(30.0, duration_seconds / 2),
        ),
        count=1,
        deadline_seconds=workload.earnings_refresh_seconds,
        priority=4,
    )
    add(
        "scheduled_reporting",
        _periodic_releases(
            interval=workload.scheduled_report_refresh_seconds,
            duration=duration_seconds,
            phase=min(420.0, duration_seconds / 2),
        ),
        count=workload.scheduled_report_requests,
        deadline_seconds=workload.scheduled_report_deadline_seconds,
        priority=5,
    )
    selection_count = workload.selection_burst_requests()
    if selection_count:
        add(
            "selection",
            _periodic_releases(
                interval=workload.selection_refresh_seconds,
                duration=duration_seconds,
                phase=min(60.0, duration_seconds / 2),
            ),
            count=selection_count,
            deadline_seconds=workload.selection_refresh_seconds,
            priority=4,
        )
    return tuple(sorted(jobs, key=lambda item: (item.release_at, item.sequence)))


def simulate_request_schedule(
    slots: int,
    workload: ApiWorkload,
    *,
    duration_seconds: float = 300.0,
    priority_scheduling: bool = True,
    response_seconds: float = 0.0,
) -> ScheduleSimulation:
    """Replay shared-governor contention without clients, sockets or sleeps."""

    if slots <= 0 or duration_seconds <= 0 or response_seconds < 0:
        raise ValueError("schedule simulation inputs are invalid")
    jobs = _scheduled_requests(slots, workload, duration_seconds)
    pending: list[tuple[float, float, int, _ScheduledRequest]] = []
    cursor = 0
    service_at = 0.0
    interval = 1 / workload.rest_requests_per_second
    maximum_depth = 0
    completed = 0
    misses: dict[str, int] = {}
    maximum_latency = 0.0
    maximum_by_category: dict[str, float] = {}

    while cursor < len(jobs) or pending:
        if not pending and cursor < len(jobs) and service_at < jobs[cursor].release_at:
            service_at = jobs[cursor].release_at
        while cursor < len(jobs) and jobs[cursor].release_at <= service_at:
            item = jobs[cursor]
            key = (
                item.priority if priority_scheduling else 0,
                item.deadline_at if priority_scheduling else item.release_at,
                item.sequence,
                item,
            )
            heappush(pending, key)
            cursor += 1
        maximum_depth = max(maximum_depth, len(pending))
        if not pending:
            continue
        _, _, _, item = heappop(pending)
        completed_at = service_at + response_seconds
        latency = max(0.0, completed_at - item.release_at)
        maximum_latency = max(maximum_latency, latency)
        maximum_by_category[item.category] = max(
            maximum_by_category.get(item.category, 0.0), latency)
        if completed_at > item.deadline_at:
            misses[item.category] = misses.get(item.category, 0) + 1
        completed += 1
        # Most commissioning calls are issued serially. A response slower
        # than the limiter interval therefore becomes the next possible start;
        # a fast response still cannot outrun the governor.
        service_at = max(service_at + interval, completed_at)
        # Work left after the observation window is a backlog, not magically
        # completed evidence.
        if service_at >= duration_seconds:
            break

    remaining = len(pending) + (len(jobs) - cursor)
    return ScheduleSimulation(
        duration_seconds=duration_seconds,
        generated_requests=len(jobs),
        completed_requests=completed,
        remaining_requests=remaining,
        missed_deadlines=sum(misses.values()),
        misses_by_category=tuple(sorted(misses.items())),
        maximum_latency_seconds=maximum_latency,
        maximum_latency_by_category=tuple(sorted(maximum_by_category.items())),
        maximum_queue_depth=maximum_depth,
    )


def assess_capacity(
    slots: int,
    workload: ApiWorkload,
    *,
    hardware: HardwareProfile | None = None,
    exposure: ExposureProfile = ExposureProfile(),
    markets_per_private_connection: int = 8,
    public_subscription_limit: int = 100,
) -> CapacityAssessment:
    """Assess one width without contacting the venue or opening a socket."""

    if slots <= 0:
        raise ValueError("slots must be positive")
    if markets_per_private_connection <= 0 or public_subscription_limit <= 0:
        raise ValueError("stream capacities must be positive")

    breakdown = workload.request_breakdown(slots)
    attempted = sum(breakdown.values())
    utilization = attempted / workload.rest_requests_per_second
    request_headroom = 1 - utilization
    private_connections = ceil(slots / markets_per_private_connection)
    projected_cpu = (
        hardware.projected_runner_cpu_cores(slots)
        if hardware is not None else None
    )
    exposure_cushion = exposure.cushion_over_slots(slots)

    blockers: list[str] = []
    if request_headroom < workload.requested_headroom_fraction:
        blockers.append(
            "REST workload does not preserve the requested request-rate headroom"
        )
    if slots > public_subscription_limit:
        blockers.append("public market subscription limit is exceeded")
    if projected_cpu is not None and (
        projected_cpu > hardware.comfortable_runner_cpu_cores
    ):
        blockers.append("projected single-process CPU exceeds the comfort budget")
    if exposure_cushion < exposure.required_cushion_over_slots:
        blockers.append(
            "current portfolio exposure cap does not preserve its required cushion"
        )

    burst_requests = workload.selection_burst_requests()
    return CapacityAssessment(
        slots=slots,
        request_breakdown=breakdown,
        attempted_requests_per_second=attempted,
        request_utilization=utilization,
        request_headroom=request_headroom,
        selection_burst_requests=burst_requests,
        selection_burst_floor_seconds=(
            burst_requests / workload.rest_requests_per_second
        ),
        private_connections=private_connections,
        public_subscriptions=slots,
        projected_runner_cpu_cores=projected_cpu,
        exposure_cushion_over_slots=exposure_cushion,
        comfortable=not blockers,
        blockers=tuple(blockers),
    )


def highest_comfortable_slot(
    workload: ApiWorkload,
    *,
    minimum: int = 1,
    maximum: int = 100,
    hardware: HardwareProfile | None = None,
    exposure: ExposureProfile = ExposureProfile(),
) -> int | None:
    """Return the highest width passing every supplied comfort constraint."""

    if minimum <= 0 or maximum < minimum:
        raise ValueError("capacity sweep bounds are invalid")
    passing = [
        slots for slots in range(minimum, maximum + 1)
        if assess_capacity(
            slots, workload, hardware=hardware, exposure=exposure
        ).comfortable
    ]
    return max(passing) if passing else None
