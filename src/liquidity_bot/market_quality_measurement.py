"""Pure evidence builders and a best-effort sink. Never read by selection."""
from __future__ import annotations
from collections import OrderedDict, defaultdict
from datetime import datetime
from dataclasses import asdict, is_dataclass
from decimal import Decimal
import uuid


def plain(value):
    if isinstance(value, (datetime, Decimal)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def priority_grade(rank):
    if type(rank) is not int or rank < 1:
        return "Unknown"
    return "S" if rank <= 5 else "A" if rank <= 10 else "B" if rank <= 20 else "C" if rank <= 30 else "D"


def emit(sink, kind, builder, *args, **kwargs):
    """Even a broken optional sink/builder cannot change caller behavior."""
    if sink is None:
        return
    try:
        sink.emit(kind, builder(*args, **kwargs))
    except Exception:
        try:
            sink.dropped += 1
        except Exception:
            pass


def reward_snapshot(rows, at):
    if len(rows) > 20000:
        raise ValueError("unbounded reward response")
    return {"schema": 1, "at": at.isoformat(), "scope": "account_response",
            "completeness": "adapter_validated_response_not_historical_coverage",
            "rows": [dict(date=r.date, market_slug=r.market_slug,
                          program_type=r.program_type, status=r.status,
                          reward=str(r.reward), observed_at=at.isoformat()) for r in rows]}


def emit_discovery(sink, *args, **kwargs):
    """Keep large bounded catalogs without an oversized single record.

    A report accepts multipart scans only when every part is present. Normal
    queue/disk limits still apply; never fetch again to repair diagnostic gaps.
    """
    if sink is None:
        return
    try:
        payload = discovery_snapshot(*args, **kwargs)
        rows = payload['candidates']
        if len(rows) <= 1000:
            sink.emit('discovery', payload)
            return
        ident = uuid.uuid4().hex
        total = (len(rows) + 999) // 1000
        header = {k: v for k, v in payload.items() if k != 'candidates'}
        for index in range(total):
            sink.emit('discovery_part', dict(at=payload['at'], scan_id=ident,
                index=index, parts=total, header=header if index == 0 else None,
                candidates=rows[index*1000:(index+1)*1000]))
    except Exception:
        try:
            sink.dropped += 1
        except Exception:
            pass


def binding_snapshot(runner):
    from .selection_provenance import rank_snapshot
    from .selection_measurement import selection_measurement
    import json
    at = runner._now()
    selection = runner._market_selection
    evidence = rank_snapshot(selection, runner._market_selection_observed_at, at,
                             runner.config.market_selection_refresh_seconds)
    return {"at": at.isoformat(), "active": list(runner.active_slugs),
            "origins": plain(runner._selection_provenance.origins),
            "selection": evidence,
            "candidate_evidence": json.loads(selection_measurement(selection, runner.config, at))
                if selection is not None else None,
            "max_markets": runner.config.max_markets, "size": str(runner.config.quote_size)}


def discovery_snapshot(catalog, executable, incentives, candidates, detailed, deferred, selection, at,
                       *, reasons=None, policy=None):
    if len(catalog) > 20000:
        raise ValueError("measurement catalog bound")
    prebook_rank = {m.slug: i for i, (m, _, _) in enumerate(candidates, 1)}
    candidates = {m.slug: p for m, _, p in candidates}
    detailed = {m.slug for m, _, _ in detailed}
    deferred = set(deferred)
    outcomes = {o.market.slug: o for o in selection.opportunities}
    rank = {o.market.slug: i for i, o in enumerate(selection.top_viable(max(1, len(outcomes))), 1)}
    pools = defaultdict(lambda: {"events": set(), "markets": set(), "missing_event": 0})
    result = []
    for market in catalog:
        slug = market.slug
        inc = incentives.get(slug)
        programs = []
        for period in inc.periods if inc else ():
            key = (period.program_id, period.period, str(period.starts_at), str(period.ends_at),
                   period.reward_pool, period.target_size, period.discount_factor)
            pools[key]["markets"].add(slug)
            if market.event_slug:
                pools[key]["events"].add(market.event_slug)
            else:
                pools[key]["missing_event"] += 1
            programs.append(plain(period))
        stage = ("metadata_refused" if slug not in executable else
                 "reward_missing" if inc is None else
                 "reward_or_catalog_spread_refused" if slug not in candidates else
                 "book_deferred" if slug in deferred else
                 "book_failed" if slug not in outcomes else
                 "viable" if outcomes[slug].viable_sides else "side_filters_refused")
        result.append(dict(market=slug, event=market.event_slug,
                           game_start=plain(market.game_start), stage=stage,
                           refusal_reason=(reasons or {}).get(slug),
                           metadata={k: plain(getattr(market, k, None)) for k in (
                               'active','closed','status','sports_market_type_v2','tick_size',
                               'minimum_trade_qty','catalog_best_bid','catalog_best_offer',
                               'event_checked_at','event_not_started','event_started_at')},
                           programs=programs, rank=rank.get(slug),
                           prebook_rank=prebook_rank.get(slug),
                           grade=priority_grade(rank.get(slug)), book_evaluated=slug in outcomes))
    return {"schema": 1, "at": at.isoformat(), "scope": "bounded_discovery",
            "policy": plain(policy) if policy is not None else None,
            "event_pages": selection.event_pages_used,
            "failures": list(selection.failures),
            "counts": {k: getattr(selection, k) for k in (
                'catalog_markets', 'executable_catalog_markets', 'rewarded_executable_markets',
                'unsupported_programs', 'eligible_pregame_markets', 'books_evaluated')},
            "pool_population_complete": False, "viable_count": len(rank),
            "candidates": result,
            "pools": [dict(identity=list(k), observed_events=len(v["events"]),
                           observed_markets=len(v["markets"]), missing_event_ids=v["missing_event"],
                           population_complete=False) for k, v in pools.items()]}


class OrderWitness:
    """Bounded change capture after existing ledger transactions commit.

    Records input venue witnesses, not alternate ledger authority. Repeated
    unchanged polls are not extra lifetime or fresh independent observations.
    """
    def __init__(self, sink):
        self.sink = sink
        self.seen = OrderedDict()

    def __call__(self, orders, observed_at, *, absent=()):
        for order in orders:
            doc = plain(order)
            key = doc["client_order_id"]
            signature = tuple(doc.get(k) for k in (
                "exchange_order_id", "market_slug", "side", "price", "original_qty", "filled_qty", "status"))
            if self.seen.get(key) == signature:
                continue
            self.seen[key] = signature
            self.seen.move_to_end(key)
            while len(self.seen) > 4096:
                self.seen.popitem(last=False)
            emit(self.sink, "order_witness", lambda: {
                "at": observed_at.isoformat(), "order": doc,
                "basis": "venue_input_after_committed_projection"})
        for key in absent:
            self.seen.pop(key, None)
            emit(self.sink, "order_absent", lambda: {
                "at": observed_at.isoformat(), "client_order_id": key})
