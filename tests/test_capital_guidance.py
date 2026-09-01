import unittest
from decimal import Decimal

from liquidity_bot.capital_guidance import calculate_capital_guidance


class CapitalGuidanceTests(unittest.TestCase):
    def guidance(self, **overrides):
        values = {
            "quote_size": Decimal("10"),
            "slot_capacity": 24,
            "portfolio_exposure_cap_usd": Decimal("340"),
            "weekly_reserve_usd": Decimal("672"),
            "settled_cash_usd": Decimal("1300"),
            "buying_power_usd": Decimal("1200"),
            "state_proven": True,
        }
        values.update(overrides)
        return calculate_capital_guidance(**values)

    def test_size_10_and_24_slots_preserve_scaled_capital_reserves(self):
        result = self.guidance()

        self.assertEqual(result.slot_footprint_usd, Decimal("240"))
        self.assertEqual(result.cushion_usd, Decimal("100"))
        self.assertEqual(result.weekly_reserve_usd, Decimal("672"))
        self.assertEqual(result.comfortable_balance_usd, Decimal("1012"))
        self.assertEqual(result.free_to_withdraw_usd, Decimal("288.00"))

    def test_size_13_and_22_slots_follow_both_scaling_axes(self):
        result = self.guidance(
            quote_size=Decimal("13"),
            slot_capacity=22,
            portfolio_exposure_cap_usd=Decimal("416"),
            weekly_reserve_usd=Decimal("800.8"),
        )

        self.assertEqual(result.slot_footprint_usd, Decimal("286"))
        self.assertEqual(result.cushion_usd, Decimal("130"))
        self.assertEqual(result.weekly_reserve_usd, Decimal("800.8"))
        self.assertEqual(result.comfortable_balance_usd, Decimal("1216.8"))
        self.assertEqual(result.free_to_withdraw_usd, Decimal("83.20"))

    def test_size_10_and_32_slots_use_the_wider_daily_risk_reserve(self):
        result = self.guidance(
            slot_capacity=32,
            portfolio_exposure_cap_usd=Decimal("420"),
            weekly_reserve_usd=Decimal("896"),
            settled_cash_usd=Decimal("1500"),
            buying_power_usd=Decimal("1500"),
        )

        self.assertEqual(result.slot_footprint_usd, Decimal("320"))
        self.assertEqual(result.cushion_usd, Decimal("100"))
        self.assertEqual(result.comfortable_balance_usd, Decimal("1316"))
        self.assertEqual(result.free_to_withdraw_usd, Decimal("184.00"))

    def test_buying_power_caps_cash_that_is_actually_free(self):
        result = self.guidance(buying_power_usd=Decimal("75.12"))

        self.assertEqual(result.free_to_withdraw_usd, Decimal("75.12"))

    def test_balance_below_comfortable_never_suggests_a_withdrawal(self):
        result = self.guidance(
            settled_cash_usd=Decimal("1000"), buying_power_usd=Decimal("1000"))

        self.assertEqual(result.free_to_withdraw_usd, Decimal("0.00"))

    def test_withdrawal_is_rounded_down_never_up(self):
        result = self.guidance(
            settled_cash_usd=Decimal("1228.009"),
            buying_power_usd=Decimal("1228.009"))

        self.assertEqual(result.free_to_withdraw_usd, Decimal("216.00"))

    def test_unproven_or_invalid_account_state_fails_closed(self):
        unresolved = self.guidance(
            state_proven=False, unavailable_reason="unknown orders")
        invalid = self.guidance(settled_cash_usd=Decimal("NaN"))

        self.assertIsNone(unresolved.free_to_withdraw_usd)
        self.assertEqual(unresolved.unavailable_reason, "unknown orders")
        self.assertIsNone(invalid.free_to_withdraw_usd)
        self.assertEqual(invalid.unavailable_reason, "balance evidence is invalid")

    def test_missing_evidence_fails_closed_but_proven_zero_is_zero(self):
        missing = self.guidance(settled_cash_usd=None)
        zero = self.guidance(
            settled_cash_usd=Decimal("0"), buying_power_usd=Decimal("0"))

        self.assertIsNone(missing.free_to_withdraw_usd)
        self.assertEqual(
            missing.unavailable_reason, "balance evidence is unavailable")
        self.assertEqual(zero.free_to_withdraw_usd, Decimal("0.00"))

    def test_footprint_is_never_underfunded_if_configuration_drifts(self):
        result = self.guidance(
            quote_size=Decimal("13"), slot_capacity=31,
            portfolio_exposure_cap_usd=Decimal("390"))

        self.assertEqual(result.slot_footprint_usd, Decimal("403"))
        self.assertEqual(result.comfortable_balance_usd, Decimal("1075"))
        self.assertEqual(result.cushion_usd, Decimal("0"))

    def test_static_configuration_is_strictly_validated(self):
        for changes in (
                {"quote_size": 0}, {"slot_capacity": 0},
                {"slot_capacity": True},
                {"portfolio_exposure_cap_usd": 0},
                {"weekly_reserve_usd": -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.guidance(**changes)


if __name__ == "__main__":
    unittest.main()
