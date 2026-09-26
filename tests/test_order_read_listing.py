"""A venue 404 on GET-by-id is superseded only by the complete open listing.

Measured 2026-09-26: in two in-play markets the venue answered GET
/v1/order/{id} with 404 for minutes after placement while the same order was
in the complete open-order listing. Each 404 failed the whole-account proof and
every market read `execution_state_unknown`. Real ledger and runner, fake venue.
"""
import contextlib
import io
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase

import test_hybrid_account as hybrid
from test_venue_state import raw_order
from liquidity_bot.errors import AdapterError
from liquidity_bot.runner_rest_proof import RestProofMixin


class ListedOrderSupersedesNotFoundTests(IsolatedAsyncioTestCase):
    setUp = hybrid.HybridRunnerTests.setUp
    build = hybrid.HybridRunnerTests.build

    def listed(self, **overrides):
        fields = dict(
            id="exchange-one", marketSlug="market-one", side="BUY",
            intent="ORDER_INTENT_BUY_LONG", quantity="10", cumQuantity="0",
            state="ORDER_STATE_NEW", price={"value": "0.49", "currency": "USD"},
            createTime=hybrid.fixtures.NOW.isoformat(),
            lastTransactTime=self.now.isoformat())
        fields.update(overrides)
        return raw_order(**fields)

    async def prepared(self, *, listed=True, error=None):
        runner, client = await self.build()
        self.fixture.seed_working_order(
            runner, slug="market-one", side="bid", suffix="one")
        if listed:
            client.orders.rows["exchange-one"] = self.listed()
        self.now += timedelta(seconds=1)
        reads = []

        async def lagging(order_id):
            reads.append(order_id)
            raise error or AdapterError(
                f"GET /v1/order/{order_id} failed: NotFoundError: "
                "Order not found (status=404, code=5)", status_code=404)

        client.orders.retrieve = lagging
        runner.executor.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS = 1
        return runner, reads

    async def test_listed_order_supersedes_a_lagging_not_found(self):
        runner, reads = await self.prepared()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            await runner._rest_reconcile(self.now, tag="lag")
        self.assertEqual(reads, ["exchange-one"])
        self.assertIn("[order-read-listed]", out.getvalue())
        self.assertIn("superseded_not_found=1 ids=exchange-one", out.getvalue())
        self.assertEqual(
            [row["exchange_order_id"] for row in runner.ledger.working_orders()],
            ["exchange-one"])
        self.assertFalse(runner.ledger.absent_orders())
        self.assertTrue(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_unlisted_not_found_still_fails_the_proof(self):
        runner, _ = await self.prepared(listed=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaisesRegex(
                AdapterError, "could not re-read 1 working order"):
            await runner._rest_reconcile(self.now, tag="gone")
        self.assertNotIn("[order-read-listed]", out.getvalue())
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))

    async def test_other_read_failures_are_never_superseded(self):
        # A listed 404 in the same pass must not license a listed auth failure.
        runner, client = await self.build()
        for slug, suffix in (("market-one", "one"), ("market-two", "two")):
            self.fixture.seed_working_order(runner, slug=slug, side="bid", suffix=suffix)
            client.orders.rows[f"exchange-{suffix}"] = self.listed(
                id=f"exchange-{suffix}", marketSlug=slug)
        self.now += timedelta(seconds=1)

        async def failing(order_id):
            if order_id == "exchange-one":
                raise AdapterError("GET failed: NotFoundError (status=404)",
                                   status_code=404)
            raise AdapterError("GET failed: AuthenticationError (status=401)",
                               status_code=401)

        client.orders.retrieve = failing
        runner.executor.gateway.RETRIEVE_NOT_FOUND_ATTEMPTS = 1
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                AdapterError, r"could not re-read 1 working order\(s\): exchange-two"):
            await runner._rest_reconcile(self.now, tag="auth")
        self.assertFalse(runner.gate.session_ready(runner.ledger, now=self.now))


class ListingIdentityTests(TestCase):
    ROW = {"status": "OPEN", "client_order_id": "client-one",
           "exchange_order_id": "exchange-one", "market_slug": "market-one",
           "side": "BUY", "price": "0.49", "original_qty": "10"}

    def order(self, **overrides):
        fields = dict(status="OPEN", client_order_id="client-one",
                      exchange_order_id="exchange-one", market_slug="market-one",
                      side="buy", price=Decimal("0.49"), original_qty=Decimal("10"))
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def test_only_an_open_identical_owned_row_matches(self):
        match = RestProofMixin._listing_matches_owned_row
        self.assertTrue(match(self.order(), self.ROW))
        self.assertTrue(match(self.order(status="PARTIAL"), self.ROW))
        for field, value in (("status", "FILLED"), ("status", "EXPIRED"),
                             ("client_order_id", "client-two"),
                             ("exchange_order_id", "exchange-two"),
                             ("market_slug", "market-two"), ("side", "sell"),
                             ("price", Decimal("0.50")),
                             ("original_qty", Decimal("11"))):
            with self.subTest(field=field, value=value):
                self.assertFalse(match(self.order(**{field: value}), self.ROW))
        self.assertFalse(match(self.order(), dict(self.ROW, status="CANCELED")))
        foreign = dict(self.ROW, client_order_id="venue:exchange-one")
        self.assertFalse(match(self.order(client_order_id="venue:exchange-one"), foreign))

    def test_missing_or_duplicate_listing_keeps_the_failure(self):
        runner = SimpleNamespace(
            _order_read_not_found={"exchange-one"},
            ledger=SimpleNamespace(query=lambda *args: [self.ROW]),
            _listing_matches_owned_row=RestProofMixin._listing_matches_owned_row,
            _now=lambda: hybrid.fixtures.NOW)
        failures = [("exchange-one", "AdapterError: NotFoundError")]
        supersede = RestProofMixin._open_listing_supersedes_not_found
        self.assertEqual(supersede(runner, failures, ()), failures)
        self.assertEqual(
            supersede(runner, failures, (self.order(), self.order())), failures)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(supersede(runner, failures, (self.order(),)), [])
        runner._order_read_not_found = set()
        self.assertEqual(supersede(runner, failures, (self.order(),)), failures)
