#!/usr/bin/env python3
"""Trade Scorer shadow: paper-trade markets the bot does not trade (read-only).

    scorer_shadow.py run       long-running; cron restarts it if it stops (flock)
    scorer_shadow.py report    write data/scorer-shadow/latest.md|json

Logic and its limits: src/liquidity_bot/scorer_shadow.py. API use: catalog
discovery on the opportunistic report lane (it only takes unused slots of the
shared 4/s gate), books and public trades over one market WebSocket each
(<= 100 markets, the documented subscription ceiling). No order endpoint is
ever called.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from liquidity_bot.scorer_shadow import (  # noqa: E402
    PaperTrade, RestingQuote, depth_at_and_better, summarize, ticks_from_best)
from liquidity_bot.trade_score import (  # noqa: E402
    TradeScoreModel, TradeScorer, load_trade_score_policy, league_of)

OUT = Path("data/scorer-shadow")
DB = OUT / "shadow.db"
CRON_MARK = "# liquidity-bot scorer shadow"
MAX_MARKETS = 100
HORIZON_HOURS = 6.0               # the bot's day-of entry horizon
MIN_LEAD_SECONDS = 420            # entry lifetime ends 5 min before the start
RESCOPE_SECONDS = 900
EVAL_SECONDS = 10.0
STATUS_SECONDS = 900

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades(
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, slug TEXT NOT NULL, league TEXT,
  side TEXT NOT NULL, price REAL, qty REAL, win_probability REAL, model TEXT,
  traits_json TEXT, placed_at TEXT NOT NULL, game_start TEXT, status TEXT NOT NULL,
  filled_qty REAL, filled_at TEXT, closed_at TEXT, close_kind TEXT,
  cash REAL, fees REAL, pnl REAL, bot_market INTEGER);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_trades(status);
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, note TEXT);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def log(msg: str) -> None:
    print(f"[scorer-shadow] at={now_utc().isoformat()} {msg}", flush=True)


def open_db(path: Path = DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


class Shadow:
    def __init__(self, args):
        from liquidity_bot.commissioning import CommissioningConfig
        from liquidity_bot.entry_quality import load_entry_quality_policy
        from liquidity_bot.fill_hazard import MondayFillHazardModel
        self.args = args
        self.config = CommissioningConfig.diagnostic_defaults()
        self.size = float(self.config.quote_size)
        self.min_spread = float(self.config.minimum_entry_spread)
        self.min_price = float(self.config.minimum_entry_price)
        self.max_early_exit_loss = float(self.config.max_early_exit_loss_usd)
        self.horizon = float(self.config.reward_projection_horizon_seconds)
        self.quality = load_entry_quality_policy()
        self.hazard = MondayFillHazardModel()
        policy = load_trade_score_policy()
        self.threshold = policy.min_win_probability
        # The shadow scores in every mode; it never trades.
        from dataclasses import replace
        self.scorer = TradeScorer(replace(policy, mode="scorer", check_interval_seconds=300))
        self.db = open_db()
        self.run_id = now_utc().strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
        self.meta: dict = {}
        self.books: dict = {}
        self.states: dict = {}
        self.open: dict[tuple[str, str], PaperTrade] = {}
        self.counts = {"placed": 0, "closed": 0, "expired": 0, "pnl": 0.0, "allowed": 0,
                       "trades_seen": 0, "skips": {}}

    # --- storage -------------------------------------------------------------------
    def _insert(self, t: PaperTrade, bot_market: bool) -> None:
        row = t.row()
        with self.db:
            cur = self.db.execute(
                "INSERT INTO paper_trades(run_id,slug,league,side,price,qty,win_probability,model,"
                "traits_json,placed_at,game_start,status,filled_qty,filled_at,closed_at,close_kind,"
                "cash,fees,pnl,bot_market) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, row["slug"], row["league"], row["side"], row["price"], row["qty"],
                 row["win_probability"], row["model"], row["traits_json"], row["placed_at"],
                 row["game_start"], row["status"], row["filled_qty"], row["filled_at"],
                 row["closed_at"], row["close_kind"], row["cash"], row["fees"], row["pnl"],
                 int(bot_market)))
        t.trade_id = cur.lastrowid

    def _update(self, t: PaperTrade) -> None:
        row = t.row()
        with self.db:
            self.db.execute(
                "UPDATE paper_trades SET status=?,filled_qty=?,filled_at=?,closed_at=?,close_kind=?,"
                "cash=?,fees=?,pnl=? WHERE id=?",
                (row["status"], row["filled_qty"], row["filled_at"], row["closed_at"],
                 row["close_kind"], row["cash"], row["fees"], row["pnl"], t.trade_id))

    # --- discovery -----------------------------------------------------------------
    async def discover(self, gateway) -> None:
        now = now_utc()
        markets = await gateway.list_upcoming_sports_markets(
            start=now - timedelta(minutes=5), end=now + timedelta(hours=HORIZON_HOURS),
            limit=500, require_complete=True)
        fresh = {}
        for m in markets:
            if (m.is_supported_moneyline and m.active and not m.closed and m.tick_size
                    and m.game_start is not None
                    and timedelta(seconds=MIN_LEAD_SECONDS) < m.game_start - now
                    <= timedelta(hours=HORIZON_HOURS)):
                fresh[m.slug] = m
        self.meta.update(fresh)
        for slug in [s for s, m in self.meta.items() if m.game_start < now and s not in
                     {k[0] for k in self.open}]:
            self.meta.pop(slug, None)
        self.candidates = fresh

    def choose_scope(self) -> list[str]:
        """Open paper trades first, then round-robin across leagues, soonest game first."""
        keep = [s for s in dict.fromkeys(k[0] for k in self.open)]
        by_league: dict[str, list] = {}
        for slug, m in sorted(self.candidates.items(), key=lambda kv: kv[1].game_start):
            if slug not in keep:
                by_league.setdefault(league_of(slug), []).append(slug)
        scope = keep[:MAX_MARKETS]
        while len(scope) < MAX_MARKETS and any(by_league.values()):
            for league in list(by_league):
                if by_league[league] and len(scope) < MAX_MARKETS:
                    scope.append(by_league[league].pop(0))
        return scope

    def bot_markets(self) -> set[str]:
        try:
            return set(json.loads(Path("data/warm-restart-scope.json").read_text())["markets"])
        except Exception:
            return set()

    # --- decisions -----------------------------------------------------------------
    def decide(self, slug: str, side: str, now: datetime, bot_markets: set[str]) -> None:
        from liquidity_bot.reconciler import entry_quote_price
        from liquidity_bot.shadow_math import cross_exit

        def skip(reason):
            self.counts["skips"][reason] = self.counts["skips"].get(reason, 0) + 1

        meta, book, state = self.meta.get(slug), self.books.get(slug), self.states.get(slug)
        if meta is None or book is None or state is None:
            return skip("no_book")
        lead = (meta.game_start - now).total_seconds()
        if lead <= MIN_LEAD_SECONDS:
            return skip("too_close_to_start")
        if not book.is_two_sided:
            return skip("book_one_sided")
        tick = float(meta.tick_size)
        price = entry_quote_price(book, side, self.min_spread, tick)
        if price is None:
            return skip("book_sides_overlap")
        if min(price, 1 - price) < self.min_price - 1e-9:
            return skip("price_too_extreme")
        exit_result = cross_exit(book, entry_side=side, entry_price=price, qty=self.size,
                                 depth_haircut=.5, exclude_entry_level=True)
        if not exit_result.complete:
            return skip("stressed_exit_incomplete")
        if self.quality.blocker(exit_result):
            return skip("entry_quality_below_minimum")
        spread = book.best_offer.price - book.best_bid.price
        touch_size = book.best_bid.size if side == "bid" else book.best_offer.size
        hazard = self.hazard.estimate(slug=slug, side=side, price=price, spread=spread,
                                      queue_ahead=touch_size, seconds_to_event=lead,
                                      horizon_seconds=self.horizon)
        better, at = depth_at_and_better(book, side, price)
        stake = exit_result.entry_cost
        traits = {
            "quote_price": price, "spread": spread, "side": side, "seconds_to_event": lead,
            "observed_queue_ahead": better + at, "depth_at_quote": at,
            "depth_better_than_quote": better, "bid_depth": book.bid_depth,
            "offer_depth": book.offer_depth,
            "retained_value_ratio": (stake + exit_result.pnl) / stake if stake > 0 else None,
            "fill_probability": hazard.probability,
            "touch_changes_30s": state.touch_changes(now, seconds=30),
            "touch_changes_120s": state.touch_changes(now, seconds=120),
            "seconds_since_touch_change": state.seconds_since_touch_change(now),
            "ticks_from_best": ticks_from_best(book, side, price, tick),
            "quantity": self.size, "stressed_exit_pnl": exit_result.pnl,
            "league": league_of(slug),
        }
        p = self.scorer.win_probability(traits, now)
        if p is None:
            return skip("model_unavailable")
        trade = PaperTrade(
            slug=slug, league=traits["league"], side=side, price=price, qty=self.size,
            win_probability=p, model=self.scorer.model.label, traits=traits, placed_at=now,
            game_start=meta.game_start, tick=tick,
            entry=RestingQuote(side, price, self.size, better + at))
        self._insert(trade, slug in bot_markets)
        self.open[(slug, side)] = trade
        self.counts["placed"] += 1
        self.counts["allowed"] += int(p >= self.threshold)

    def settle(self, trade: PaperTrade) -> None:
        self._update(trade)
        self.open.pop((trade.slug, trade.side), None)
        if trade.status == "closed":
            self.counts["closed"] += 1
            self.counts["pnl"] += trade.pnl
        else:
            self.counts["expired"] += 1

    # --- live loops ----------------------------------------------------------------
    async def watch(self, client, scope: list[str], until: datetime) -> None:
        from liquidity_bot.market_state import MarketState
        from liquidity_bot.streams import MarketReadOnlyStream, TradeReadOnlyStream
        books, trades = MarketReadOnlyStream(client), TradeReadOnlyStream(client)
        await books.start(scope)
        try:
            await trades.start(scope)
        except Exception as exc:
            log(f"trade_stream=unavailable error={type(exc).__name__}; book crossings only")
            trades = None
        for slug in scope:
            self.states.setdefault(slug, MarketState(slug=slug))
        log(f"scope markets={len(scope)} leagues={len({league_of(s) for s in scope})} "
            f"open={len(self.open)} trades_stream={'on' if trades else 'off'}")

        async def book_loop():
            while now_utc() < until:
                try:
                    book = await books.next_book(timeout=5)
                except asyncio.TimeoutError:
                    books.assert_live(now=now_utc())
                    continue
                now = now_utc()
                self.books[book.slug] = book
                state = self.states.setdefault(book.slug, MarketState(slug=book.slug))
                try:
                    state.observe_book_touch(book, observed_at=now)
                except Exception:
                    pass
                for side in ("bid", "offer"):
                    t = self.open.get((book.slug, side))
                    if t is not None:
                        t.on_book(book, now)
                        if t.status in ("closed", "expired"):
                            self.settle(t)

        async def trade_loop():
            if trades is None:
                return
            while now_utc() < until:
                try:
                    trade = await trades.next_trade(timeout=5)
                except asyncio.TimeoutError:
                    continue
                self.counts["trades_seen"] += 1
                now = now_utc()
                for side in ("bid", "offer"):
                    t = self.open.get((trade.slug, side))
                    if t is not None:
                        t.on_trade(trade, now)
                        if t.status in ("closed", "expired"):
                            self.settle(t)

        async def eval_loop():
            next_status = now_utc() + timedelta(seconds=STATUS_SECONDS)
            while now_utc() < until:
                now = now_utc()
                line = self.scorer.refresh(now)
                if line:
                    print(line, flush=True)
                bot = self.bot_markets()
                for (slug, side), t in list(self.open.items()):
                    t.step(self.books.get(slug), now, max_early_exit_loss=self.max_early_exit_loss)
                    if t.status in ("closed", "expired"):
                        self.settle(t)
                for slug in scope:
                    for side in ("bid", "offer"):
                        if (slug, side) not in self.open:
                            self.decide(slug, side, now, bot)
                if now >= next_status:
                    next_status = now + timedelta(seconds=STATUS_SECONDS)
                    self.status_line()
                await asyncio.sleep(EVAL_SECONDS)

        tasks = [asyncio.create_task(c) for c in (book_loop(), trade_loop(), eval_loop())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.exception() is not None:
                    raise task.exception()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for stream in (books, trades):
                if stream is not None:
                    try:
                        await stream.close()
                    except Exception:
                        pass

    def status_line(self) -> None:
        c = self.counts
        top = sorted(c["skips"].items(), key=lambda kv: -kv[1])[:4]
        log(f"placed={c['placed']} allowed={c['allowed']} closed={c['closed']} "
            f"expired={c['expired']} open={len(self.open)} pnl={c['pnl']:.2f} "
            f"trades_seen={c['trades_seen']} skips={dict(top)}")

    async def run(self) -> int:
        from liquidity_bot.adapters import (
            AsyncReadOnlyGateway, OPPORTUNISTIC_REPORT_RATE_PER_SECOND, shared_api_limiter)
        from liquidity_bot.probe import client_from_environment
        from liquidity_bot.runtime_safety import SingleInstanceLease
        if not Path("data/commissioning.db").exists():
            raise SystemExit("run from the live bot directory (it shares data/ and the API gate)")
        lease = SingleInstanceLease(OUT / "shadow.lease")
        lease.acquire()
        with self.db:
            n = self.db.execute("UPDATE paper_trades SET status='abandoned' "
                                "WHERE status IN ('resting','holding')").rowcount
            self.db.execute("INSERT INTO runs(run_id,started_at) VALUES(?,?)",
                            (self.run_id, now_utc().isoformat()))
        log(f"start run={self.run_id} size={self.size:g} threshold={self.threshold:g} "
            f"abandoned_from_previous_run={n}")
        client = client_from_environment()
        gateway = AsyncReadOnlyGateway(
            # The bot's own host-wide gate, even when this code runs from another checkout.
            client, limiter=shared_api_limiter(
                OPPORTUNISTIC_REPORT_RATE_PER_SECOND,
                state_path=Path.cwd() / "data" / "api-rate-limiter.state"),
            default_traffic_class="report", max_pages=10)
        stop_at = (now_utc() + timedelta(minutes=self.args.minutes)) if self.args.minutes else None
        self.candidates = {}
        try:
            while stop_at is None or now_utc() < stop_at:
                try:
                    await self.discover(gateway)
                    scope = self.choose_scope()
                    if not scope:
                        log("no candidate markets; waiting")
                        await asyncio.sleep(300)
                        continue
                    until = now_utc() + timedelta(seconds=RESCOPE_SECONDS)
                    if stop_at is not None:
                        until = min(until, stop_at)
                    await self.watch(client, scope, until)
                except Exception as exc:
                    log(f"error={type(exc).__name__}:{str(exc)[:160]}; retry in 60s")
                    await asyncio.sleep(60)
        finally:
            self.status_line()
            with self.db:
                self.db.execute("UPDATE runs SET ended_at=? WHERE run_id=?",
                                (now_utc().isoformat(), self.run_id))
        return 0


def report(args) -> int:
    policy = load_trade_score_policy()
    conn = open_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM paper_trades")]
    leagues: set[str] = set()
    try:
        model = TradeScoreModel.from_json(json.loads(Path(policy.model_path).read_text()))
        # The baseline (most common) league is not listed but is known to the model.
        leagues = set(model.leagues) | {"setkameua"}
    except Exception:
        pass
    s = summarize(rows, threshold=policy.min_win_probability, model_leagues=leagues)
    s["generated_at"] = now_utc().isoformat()
    s["since"] = min((r["placed_at"] for r in rows), default=None)
    (OUT / "latest.json").write_text(json.dumps(s, indent=1), encoding="utf-8")

    def fmt(g):
        rate = lambda v: "-" if v is None else f"{v:.0%}"
        per = "-" if g["pnl_per_trade"] is None else f"${g['pnl_per_trade']:.3f}"
        return (f"| {g['placed']} | {g['filled']} | {rate(g['fill_rate'])} | {rate(g['win_rate'])} "
                f"| ${g['pnl']:.2f} | {per} | {g['forced_exits']} |")

    cols = "| Paper orders | Filled | Fill rate | Profitable | Trading $ | $ per trade | Forced exits |"
    sep = "|---|---|---|---|---|---|---|---|"
    auc_txt = "-" if s["auc_closed"] is None else f"{s['auc_closed']:.3f}"
    lines = [
        "# Trade Scorer shadow (paper trading, read-only)", "",
        f"Since {s['since']}; generated {s['generated_at']}. Size {Shadow_size()} contracts, "
        f"threshold {s['threshold']:.0%}. Any league, rewarded or not.",
        "", "Paper fills are strict (queue ahead must trade first; no credit for cancels). The pretend "
        "entry is not repriced; maker fees are zero. Evidence, not proof.", "",
        "## Allowed (score >= threshold) vs skipped", "", "| Group " + cols, sep,
        "| Allowed " + fmt(s["allowed"]), "| Skipped " + fmt(s["skipped"]), "| All " + fmt(s["all"]),
        "", f"Separation on filled paper trades (AUC): {auc_txt}",
        "", "## Allowed trades: leagues the model knows vs new leagues", "", "| Group " + cols, sep,
        "| Known leagues " + fmt(s["allowed_known_leagues"]),
        "| New leagues " + fmt(s["allowed_new_leagues"]), "",
        "## Allowed trades by league", "", "| League " + cols, sep,
    ]
    lines += [f"| {lg} " + fmt(g) for lg, g in list(s["by_league"].items())[:25]]
    lines += ["", "## Predicted vs actual (filled paper trades)", "",
              "| Score | Trades | Predicted | Actual profitable | Trading $ |", "|---|---|---|---|---|"]
    lines += [f"| {c['bucket']} | {c['trades']} | {c['predicted']:.0%} | {c['actual']:.0%} | ${c['pnl']:.2f} |"
              for c in s["calibration"]]
    (OUT / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    a = s["allowed"]
    print(f"Scorer shadow: {s['all']['placed']} paper orders; allowed {a['placed']} "
          f"(filled {a['filled']}, profitable {a['win_rate'] if a['win_rate'] is None else round(a['win_rate'], 2)}, "
          f"${a['pnl']:.2f}); wrote {OUT / 'latest.md'}")
    return 0


def Shadow_size() -> str:
    try:
        return Path("data/commissioning_quote_size").read_text().strip()
    except OSError:
        return "?"


def cron_lines(root: Path, code: Path = ROOT) -> list[str]:
    """``root`` is the live bot directory (data, venv, API gate); ``code`` this checkout."""
    py = f"{root}/.venv/bin/python -B {code}/scripts/scorer_shadow.py"
    return [
        f"*/10 * * * * cd {root} && flock -n data/scorer-shadow/cron.lock nice -n 10 {py} run "
        f">> logs/scorer-shadow.log 2>&1 {CRON_MARK}",
        f"50 * * * * cd {root} && nice -n 10 {py} report >> logs/scorer-shadow.log 2>&1 {CRON_MARK} report",
    ]


def install_cron(root: Path) -> int:
    import subprocess
    (root / "data" / "scorer-shadow").mkdir(parents=True, exist_ok=True)
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    kept = [line for line in existing.splitlines() if CRON_MARK not in line]
    body = "\n".join(kept + cron_lines(root)) + "\n"
    return subprocess.run(["crontab", "-"], input=body, text=True).returncode


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("run", "report", "install-cron"))
    p.add_argument("--minutes", type=float, default=None, help="run: stop after this many minutes")
    a = p.parse_args(argv)
    if a.command == "report":
        return report(a)
    if a.command == "install-cron":
        return install_cron(Path.cwd().resolve())
    return asyncio.run(Shadow(a).run())


if __name__ == "__main__":
    raise SystemExit(main())
