#!/usr/bin/env python3
"""Rank every rewarded market starting within six hours by what it would earn us.

Measurement only: read-only venue reads on the opportunistic report lane, a
read-only ledger snapshot and the market-quality archives. No order, no
selection change. For each rewarded market (any market type) it reports:

* expected reward per hour for our quote size on both sides, from the live
  reward model with the live policy except the 2-cent spread rule;
* expected trading P&L per hour from our own history for that league
  (first-fill rate per 30 s times average position result);
* whether the league has ever been paid (never-paid leagues earn 0);
* whether the bot trades it now and, if not, which live rule blocks it.

    cd /home/polybot/liquidity-bot
    .venv/bin/python -B scripts/market_ranker.py --archive data/market-quality/*.sqlite3
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Credentials are found relative to the imported package, so prefer the
# working checkout's src (production) over this script's own checkout.
sys.path.insert(0, str(Path.cwd() / "src") if (Path.cwd() / "src" / "liquidity_bot").is_dir()
                else str(ROOT / "src"))

from liquidity_bot.fill_hazard import league_from_slug  # noqa: E402
from liquidity_bot.market_selector import (  # noqa: E402
    EVENT_DISCOVERY_CLOCK_SKEW_SECONDS, EVENT_DISCOVERY_PAGE_SIZE, OPEN_MARKET_STATUSES,
    evaluate_market_opportunity)
from liquidity_bot.rewards import select_applicable_period  # noqa: E402

UTC = timezone.utc
RANK_HORIZON_SECONDS = 6 * 3600  # operator: keep prioritizing the six-hour window
# 2026-09-26: 17,207 markets start within six hours (9,325 props, 4,591
# spreads, 2,448 totals, 621 drawable, 222 moneylines). Reward lookups for all
# of them take ~30 min on the report lane, so the frequent run covers the
# market types the bot could plausibly trade; spreads/totals run daily.
FREQUENT_TYPES = ("SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME")
DAILY_TYPES = FREQUENT_TYPES + ("SPORTS_MARKET_TYPE_SPREAD", "SPORTS_MARKET_TYPE_TOTAL")
MEASURE_SPREAD = 0.001           # score tight books the live 2-cent rule skips


# ---------------------------------------------------------------- pure logic

def pre_book_blockers(market, incentive, *, now, policy) -> list[str]:
    """Live rules that stop a rewarded market before any book is read."""
    blockers = []
    if not market.is_supported_moneyline:
        blockers.append("not_moneyline")
    if league_from_slug(market.slug) in policy.excluded_leagues:
        blockers.append("excluded_league")
    if market.minimum_trade_qty is None or market.minimum_trade_qty > policy.quote_size:
        blockers.append("minimum_quantity")
    if (market.catalog_best_bid is not None and market.catalog_best_offer is not None
            and market.catalog_best_offer - market.catalog_best_bid + 1e-9 < policy.minimum_entry_spread):
        blockers.append("spread_below_2c")
    return blockers


def applicable_period(incentive, market, *, now, policy):
    try:
        return select_applicable_period(
            incentive.periods, market.game_start, now,
            allow_daily_event=policy.allow_daily_event_rewards,
            daily_event_horizon_seconds=policy.daily_event_horizon_seconds,
            daily_event_observed_at=incentive.observed_at)
    except Exception:
        return None


def league_history(fill_cells: list[dict], episode_rows: list[dict]) -> dict:
    """Per-league first-fill rate per 30 s and average position result."""
    rates = {row["cell"].split(":", 1)[1]: row["observed_per_30s"]
             for row in fill_cells if row["cell"].startswith("league:")
             and row["observed_per_30s"] is not None and row["orders"] >= 50}
    results = {row["key"]: float(row["pnl_per_closed"]) for row in episode_rows
               if row["pnl_per_closed"] is not None and row["closed"] >= 5}
    glob = next((r["observed_per_30s"] for r in fill_cells if r["cell"] == "global"), None)
    all_rows = [r for r in episode_rows if r["pnl_per_closed"] is not None]
    closed = sum(r["closed"] for r in all_rows)
    overall = (sum(float(r["closed_pnl"]) for r in all_rows) / closed) if closed else 0.0
    return dict(rates=rates, results=results, global_rate=glob or 0.0, global_result=overall)


def expected_trading_per_hour(league: str | None, sides: int, history: dict) -> tuple[float, str]:
    rate = history["rates"].get(league)
    result = history["results"].get(league)
    basis = "league_history" if rate is not None and result is not None else "global_average"
    rate = history["global_rate"] if rate is None else rate
    result = history["global_result"] if result is None else result
    return rate * 120 * sides * result, basis


def paid_leagues(reward_snapshots: list[dict]) -> set[str] | None:
    """Leagues with any PAID liquidity reward in the newest archived snapshot."""
    snaps = [s for s in reward_snapshots if isinstance(s.get("rows"), list)]
    if not snaps:
        return None
    newest = max(snaps, key=lambda s: s.get("at", ""))
    return {league_from_slug(r["market_slug"]) or "other" for r in newest["rows"]
            if str(r.get("status", "")).upper() == "PAID" and Decimal(str(r.get("reward", 0))) > 0}


def unpaid_leagues(paid: set[str] | None, rested: set[str]) -> set[str] | None:
    """Leagues we rested orders in but were never paid for.

    A league we never quoted has no payout evidence either way; it is not
    "never paid", only unproven (operator question, 2026-09-26).
    """
    return None if paid is None else {league for league in rested if league not in paid}


WEEK_WEIGHTS = (0.50, 0.25, 0.25)  # operator: 3 weeks; most recent week 50%, then 25%, 25%
PAYOUT_SETTLE_DAYS = 3         # payouts post 1-3 days late
MIN_CALIBRATION_HOURS = 50.0   # resting order-hours before a league gets its own factor
MAX_REWARD_FACTOR = 3.0
WORKING_STATES = ("OPEN", "NEW", "PARTIAL", "PENDING", "SUBMITTED")


def _stamp(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def week_windows(end: datetime, weights=WEEK_WEIGHTS) -> list[tuple[datetime, datetime, float]]:
    """Most recent week first: [(start, end, weight), ...]."""
    return [(end - timedelta(days=7 * (k + 1)), end - timedelta(days=7 * k), w)
            for k, w in enumerate(weights)]


def weighted_reward_calibration(data: dict, payout_rows: list[dict],
                                windows: list[tuple[datetime, datetime, float]]) -> dict:
    """Actual paid / model-predicted reward for our own resting, by league.

    Every entry quote stored the live model's projected reward per 30 s at
    placement; that times the order's resting time in a window is what the
    model expected. Each market's PAID payout is split across windows in
    proportion to its predicted reward there, then both sides are weighted by
    the window weight. Leagues under MIN_CALIBRATION_HOURS use the overall ratio.
    """
    projected = {o["client_order_id"]: o["projected_reward"] for o in data["observations"]
                 if o.get("projected_reward") is not None}
    created = {i["client_order_id"]: _stamp(i["created_at"]) for i in data["intents"]
               if i["quote_observation_required"]}
    last_end = max(w[1] for w in windows)
    per = defaultdict(lambda: [0.0, 0.0])        # (market, window index) -> [predicted, hours]
    for order in data["orders"]:
        cid = order["client_order_id"]
        if cid not in created or cid not in projected:
            continue
        begin = created[cid]
        finish = (last_end if order["status"] in WORKING_STATES or not order["event_at"]
                  else _stamp(order["event_at"]))
        for k, (w_start, w_end, _) in enumerate(windows):
            lo, hi = max(begin, w_start), min(finish, w_end)
            if hi > lo:
                seconds = (hi - lo).total_seconds()
                cell = per[(order["market_slug"], k)]
                cell[0] += float(projected[cid]) * seconds / 30
                cell[1] += seconds / 3600
    paid_by_market = defaultdict(float)
    for row in payout_rows:
        if str(row.get("status", "")).upper() == "PAID":
            paid_by_market[row["market_slug"]] += float(row.get("reward") or 0)
    market_total = defaultdict(float)
    for (market, _), (pred, _) in per.items():
        market_total[market] += pred
    agg = defaultdict(lambda: dict(predicted=0.0, paid=0.0, hours=0.0))
    for (market, k), (pred, hrs) in per.items():
        weight = windows[k][2]
        share = pred / market_total[market] if market_total[market] > 0 else 0.0
        row = agg[league_from_slug(market) or "other"]
        row["predicted"] += weight * pred
        row["paid"] += weight * paid_by_market[market] * share
        row["hours"] += hrs
    leagues = {league: dict(predicted=round(v["predicted"], 4), paid=round(v["paid"], 4),
                            resting_hours=round(v["hours"], 1),
                            ratio=round(v["paid"] / v["predicted"], 4) if v["predicted"] > 0 else None)
               for league, v in agg.items()}
    total_pred = sum(v["predicted"] for v in agg.values())
    total_paid = sum(v["paid"] for v in agg.values())
    return dict(windows=[(a.isoformat(), b.isoformat(), w) for a, b, w in windows], leagues=leagues,
                overall_ratio=round(total_paid / total_pred, 4) if total_pred > 0 else None)


def reward_calibration(data: dict, payout_rows: list[dict], *, start: datetime,
                       end: datetime) -> dict:
    """Single unweighted window (kept for callers and tests)."""
    return weighted_reward_calibration(data, payout_rows, [(start, end, 1.0)])


def weighted_league_history(data: dict, windows: list[tuple[datetime, datetime, float]]) -> dict:
    """Fill rate per 30 s and average position result per league, week-weighted."""
    from scripts.calibration_report import fill_model_calibration, position_episodes
    fills, intervals, orders = defaultdict(float), defaultdict(float), defaultdict(int)
    for w_start, w_end, weight in windows:
        window_data = dict(data, observations=[
            o for o in data["observations"] if w_start <= _stamp(o["observed_at"]) < w_end])
        for row in fill_model_calibration(window_data, since=w_start, now=w_end)["cells"]:
            if row["cell"] == "global" or row["cell"].startswith("league:"):
                key = "global" if row["cell"] == "global" else row["cell"].split(":", 1)[1]
                fills[key] += weight * row["fills"]
                intervals[key] += weight * row["exposure_hours"] * 120
                orders[key] += row["orders"]
    building = {i["client_order_id"] for i in data["intents"] if i["quote_observation_required"]}
    observed = {o["client_order_id"] for o in data["observations"]}
    pnl, closed_w, closed_n = defaultdict(float), defaultdict(float), defaultdict(int)
    for episode in position_episodes(data):
        if (episode["closed_at"] is None or episode["entry_client_order_id"] not in building
                or episode["entry_client_order_id"] not in observed):
            continue
        for w_start, w_end, weight in windows:
            if w_start <= episode["opened_at"] < w_end:
                for key in ("global", league_from_slug(episode["market"]) or "other"):
                    pnl[key] += weight * float(episode["pnl"])
                    closed_w[key] += weight
                    closed_n[key] += 1
                break
    rates = {k: fills[k] / intervals[k] for k in intervals
             if k != "global" and intervals[k] > 0 and orders[k] >= 50}
    results = {k: pnl[k] / closed_w[k] for k in closed_w if k != "global" and closed_n[k] >= 5}
    return dict(rates=rates, results=results,
                global_rate=fills["global"] / intervals["global"] if intervals["global"] else 0.0,
                global_result=pnl["global"] / closed_w["global"] if closed_w["global"] else 0.0)


def reward_factor(league: str | None, calibration: dict | None) -> tuple[float, str]:
    if not calibration:
        return 1.0, "uncalibrated"
    row = calibration["leagues"].get(league or "other")
    if row and row["ratio"] is not None and row["resting_hours"] >= MIN_CALIBRATION_HOURS:
        return min(row["ratio"], MAX_REWARD_FACTOR), "league"
    overall = calibration.get("overall_ratio")
    if overall is not None:
        return min(overall, MAX_REWARD_FACTOR), "overall"
    return 1.0, "uncalibrated"


def score_row(market, period, relaxed, live, *, now, policy, history, unpaid, traded,
              rested=frozenset(), calibration=None) -> dict:
    """One ranked row; reward and trading value per hour for our quote size."""
    league = league_from_slug(market.slug)
    per_hour = 3600 / policy.projection_horizon_seconds
    reward_sides = [s for s in relaxed.sides if s.projection.eligible]
    model_reward = sum(s.projection.projected_reward for s in reward_sides) * per_hour
    factor, factor_basis = reward_factor(league, calibration)
    reward = model_reward * factor
    never_paid = unpaid is not None and (league or "other") in unpaid
    counted_reward = 0.0 if never_paid else reward
    trading, basis = expected_trading_per_hour(league, max(len(reward_sides), 1), history)
    live_viable = bool(live and live.viable_sides)
    side_blockers = sorted({s.blocker for s in (live.sides if live else ()) if s.blocker})
    return dict(
        market=market.slug, league=league or "other",
        market_type=getattr(market, "sports_market_type_v2", None),
        hours_to_start=round((market.game_start - now).total_seconds() / 3600, 2),
        period=period.period, pool=float(period.reward_pool or 0),
        spread=round(relaxed.book.best_offer.price - relaxed.book.best_bid.price, 4),
        reward_per_hour=round(reward, 4), reward_model_per_hour=round(model_reward, 4),
        reward_factor=round(factor, 4), reward_factor_basis=factor_basis,
        never_paid=never_paid,
        reward_evidence=("never_paid" if never_paid else
                         "paid_before" if (league or "other") in rested else "no_history"),
        trading_per_hour=round(trading, 4), trading_basis=basis,
        net_per_hour=round(counted_reward + trading, 4),
        traded_now=traded, live_viable=live_viable,
        blocked_by=[] if live_viable else side_blockers or ["not_viable_under_live_rules"])


def book_priority(items, *, traded: set[str], unpaid: set[str] | None):
    """Order book reads: traded, then paying-or-unproven moneylines, then the
    rest by pool; never-paid leagues last.

    Never-paid leagues are still measured (operator, 2026-09-26: "we can still
    measure those ... but they should not get priority"), only after every
    other market, so they cannot use up the book budget.
    """
    def never_paid(item):
        return unpaid is not None and (league_from_slug(item[0].slug) or "other") in unpaid

    ordered = sorted(items, key=lambda item: (
        never_paid(item), item[0].slug not in traded, not item[0].is_supported_moneyline,
        -float(item[2].reward_pool or 0), item[0].slug))
    return ordered, sum(1 for item in items if never_paid(item))


def rank(rows: list[dict]) -> list[dict]:
    rows = sorted(rows, key=lambda r: (-r["net_per_hour"], r["market"]))
    for index, row in enumerate(rows, 1):
        row["rank"] = index
    return rows


# ----------------------------------------------------------------- run

async def collect(*, now, policy, max_books: int, market_types: tuple[str, ...],
                  traded: set[str] = frozenset(), unpaid: set[str] | None = None) -> dict:
    from liquidity_bot.adapters import (
        AsyncReadOnlyGateway, OPPORTUNISTIC_REPORT_RATE_PER_SECOND, shared_api_limiter)
    from liquidity_bot.probe import client_from_environment
    gateway = AsyncReadOnlyGateway(
        client_from_environment(), limiter=shared_api_limiter(OPPORTUNISTIC_REPORT_RATE_PER_SECOND),
        default_traffic_class="report", max_pages=10)
    skew = timedelta(seconds=EVENT_DISCOVERY_CLOCK_SKEW_SECONDS)
    catalog = await gateway.list_upcoming_sports_markets(
        start=now - skew, end=now + timedelta(seconds=RANK_HORIZON_SECONDS) + skew,
        limit=EVENT_DISCOVERY_PAGE_SIZE, require_complete=True)
    universe = [m for m in catalog if m.active and not m.closed
                and m.status in OPEN_MARKET_STATUSES and m.game_start is not None
                and m.tick_size is not None
                and str(getattr(m, "sports_market_type_v2", "") or "").upper() in market_types
                and now < m.game_start <= now + timedelta(seconds=RANK_HORIZON_SECONDS)]
    incentives = await gateway.fetch_incentives(sorted(m.slug for m in universe))
    books, errors = {}, {}
    rewarded = [(m, incentives[m.slug]) for m in universe
                if m.slug in incentives and incentives[m.slug].periods]
    with_period = []
    for market, incentive in rewarded:
        period = applicable_period(incentive, market, now=now, policy=policy)
        if period is not None:
            with_period.append((market, incentive, period))
    ordered, never_paid = book_priority(with_period, traded=traded, unpaid=unpaid)
    for market, _, _ in ordered[:max_books]:
        try:
            books[market.slug] = await gateway.fetch_book(market.slug)
        except Exception as exc:
            errors[market.slug] = type(exc).__name__
    return dict(catalog_rows=len(catalog), event_pages=gateway.last_upcoming_event_pages,
                universe=universe, rewarded=rewarded, with_period=with_period,
                never_paid_markets=never_paid, books=books, book_errors=errors)


def build(collected: dict, *, now, policy, history, unpaid, traded_slugs, rested=frozenset(),
          calibration=None) -> dict:
    relaxed_policy = replace(policy, minimum_entry_spread=MEASURE_SPREAD, excluded_leagues=frozenset())
    rows, skipped = [], {"no_applicable_period": 0, "book_not_read": 0, "book_unusable": 0}
    skipped["no_applicable_period"] = len(collected["rewarded"]) - len(collected["with_period"])
    for market, incentive, period in collected["with_period"]:
        book = collected["books"].get(market.slug)
        if book is None:
            skipped["book_not_read"] += 1
            continue
        try:
            relaxed = evaluate_market_opportunity(market, incentive, book, period=period,
                                                  now=now, policy=relaxed_policy)
            live = evaluate_market_opportunity(market, incentive, book, period=period,
                                               now=now, policy=policy)
        except Exception:
            skipped["book_unusable"] += 1
            continue
        row = score_row(market, period, relaxed, live, now=now, policy=policy,
                        history=history, unpaid=unpaid, traded=market.slug in traded_slugs,
                        rested=rested, calibration=calibration)
        row["blocked_by"] = pre_book_blockers(market, incentive, now=now, policy=policy) + (
            [] if row["live_viable"] else row["blocked_by"])
        rows.append(row)
    ranked = rank(rows)
    return dict(
        generated_at=now.isoformat(), horizon_hours=RANK_HORIZON_SECONDS / 3600,
        enforcement="none", quote_size=policy.quote_size,
        catalog_rows=collected["catalog_rows"], event_pages=collected["event_pages"],
        open_markets_in_window=len(collected["universe"]),
        rewarded_markets=len(collected["rewarded"]),
        paying_now=len(collected["with_period"]), scored=len(ranked), skipped=skipped,
        paid_history_known=unpaid is not None, reward_calibration=calibration,
        never_paid_leagues=sorted(unpaid or ()),
        never_paid_markets=collected.get("never_paid_markets", 0), rows=ranked)


def render(report: dict, top: int = 40) -> str:
    out = [f"# Rewarded-market ranking — {report['generated_at'][:16]}Z",
           "",
           f"- Open markets starting within {report['horizon_hours']:g} h: {report['open_markets_in_window']}; "
           f"rewarded: {report['rewarded_markets']}; reward paying now: {report['paying_now']}; "
           f"scored: {report['scored']}; skipped: {report['skipped']}",
           f"- Quote size {report['quote_size']:g}; measurement only; money per hour is an estimate.",
           "",
           "| # | market | league | h to start | reward $/h | trading $/h | net $/h | traded now | blocked by |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in report["rows"][:top]:
        out.append(f"| {r['rank']} | {r['market'][4:]} | {r['league']} | {r['hours_to_start']} | "
                   f"{r['reward_per_hour']:.3f}{' (never paid)' if r['never_paid'] else ' (no history)' if r.get('reward_evidence') == 'no_history' else ''} | "
                   f"{r['trading_per_hour']:.3f} | {r['net_per_hour']:.3f} | "
                   f"{'yes' if r['traded_now'] else 'no'} | {', '.join(r['blocked_by']) or '-'} |")
    return "\n".join(out) + "\n"


def traded_now(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("markets") or [])
    except Exception:
        return set()


CRON_MARK = "# liquidity-bot market ranker"
DAILY_CRON_MARK = "# liquidity-bot market ranker daily"


def cron_lines(root: Path) -> tuple[str, str]:
    """Every 30 min (moneyline/drawable) plus a nightly run with spreads/totals.

    flock skips a run while the previous one (about 15 min) is still going.
    """
    base = (f"cd {root} && flock -n data/market-ranker.lock nice -n 10 .venv/bin/python -B "
            "scripts/market_ranker.py --archive data/market-quality/*.sqlite3")
    return (f"5,35 * * * * {base} >> logs/market-ranker.log 2>&1 {CRON_MARK}",
            f"50 3 * * * {base} --include-spreads-totals --max-books 400 "
            f">> logs/market-ranker.log 2>&1 {DAILY_CRON_MARK}")


def install_cron(root: Path) -> int:
    import subprocess
    from scripts.rotate_logs import with_cron_line
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    table = current.stdout if current.returncode == 0 else ""
    frequent, daily = cron_lines(root)
    table = with_cron_line(table, daily, mark=DAILY_CRON_MARK)
    kept = "\n".join(r for r in table.splitlines() if not r.endswith(CRON_MARK))
    table = (kept + "\n" if kept else "") + frequent + "\n"
    return subprocess.run(["crontab", "-"], text=True, input=table).returncode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=Path("data/commissioning.db"))
    p.add_argument("--archive", type=Path, action="extend", nargs="+", default=[])
    p.add_argument("--max-books", type=int, default=150)
    p.add_argument("--install-cron", action="store_true",
                   help="install the 30-minute and nightly runs for this checkout")
    p.add_argument("--include-spreads-totals", action="store_true",
                   help="daily run: also rank spread and total markets (slower)")
    p.add_argument("--output-root", type=Path, default=Path("data/market-rankings"))
    a = p.parse_args(argv)
    if a.install_cron:
        return install_cron(Path.cwd().resolve())
    from liquidity_bot.commissioning import CommissioningConfig
    from scripts.calibration_report import read_archive_records, read_ledger
    now = datetime.now(UTC)
    policy = CommissioningConfig.diagnostic_defaults().reward_selection_policy()
    data = read_ledger(a.db)
    history = weighted_league_history(data, week_windows(now))
    reward_snapshots = read_archive_records(a.archive, {"rewards"})
    paid = paid_leagues(reward_snapshots)
    newest_rewards = (max(reward_snapshots, key=lambda s: s.get("at", ""))["rows"]
                      if reward_snapshots else [])
    settled_end = now - timedelta(days=PAYOUT_SETTLE_DAYS)
    calibration = (weighted_reward_calibration(data, newest_rewards, week_windows(settled_end))
                   if newest_rewards else None)
    # Payouts post 1-3 days late: only rest at least 3 days old can prove "never paid";
    # the same three weeks as the reward calibration.
    settled = (now - timedelta(days=PAYOUT_SETTLE_DAYS)).isoformat()
    rested = {league_from_slug(o["market_slug"]) or "other" for o in data["observations"]
              if (now - timedelta(days=PAYOUT_SETTLE_DAYS + 7 * len(WEEK_WEIGHTS))).isoformat()
              <= o["observed_at"] <= settled}
    unpaid = unpaid_leagues(paid, rested)
    types = DAILY_TYPES if a.include_spreads_totals else FREQUENT_TYPES
    traded = traded_now(Path("data/tv-active-scope.json"))
    collected = asyncio.run(collect(now=now, policy=policy, max_books=a.max_books,
                                    market_types=types, traded=traded, unpaid=unpaid))
    report = build(collected, now=now, policy=policy, history=history, unpaid=unpaid,
                   traded_slugs=traded, rested=rested, calibration=calibration)
    report["market_types"] = list(types)
    out = a.output_root / now.strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)
    (out / "ranking.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf8")
    (out / "ranking.md").write_text(render(report), encoding="utf8")
    print(f"Ranking: {out}; scored={report['scored']} of rewarded={report['rewarded_markets']}; "
          "enforcement=none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
