"""The runner layer map reflects the real class hierarchy."""
import inspect
import unittest

from liquidity_bot.account_source_runner import AccountSourceRunner
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.rest_fallback import RestFallbackRunner
from scripts.runner_layers import layer_rows


class RunnerLayerMapTests(unittest.TestCase):
    def test_map_matches_the_classes_it_describes(self):
        rows = {name: (home, rest, account) for name, home, rest, account in layer_rows()}
        self.assertIn("_rest_reconcile", rows)
        home, rest, account = rows["_rest_reconcile"]
        self.assertTrue(home.startswith("runner_rest_proof.py:"))
        self.assertNotEqual(rest, "-")
        self.assertNotEqual(account, "-")
        for name, (home, rest, account) in rows.items():
            with self.subTest(name=name):
                self.assertEqual(rest != "-", name in vars(RestFallbackRunner))
                self.assertEqual(account != "-", name in vars(AccountSourceRunner))
                if home != "-":
                    self.assertTrue(hasattr(CommissioningRunner, name))
        self.assertTrue(issubclass(AccountSourceRunner, RestFallbackRunner))
        self.assertTrue(inspect.isclass(CommissioningRunner))


if __name__ == "__main__":
    unittest.main()
