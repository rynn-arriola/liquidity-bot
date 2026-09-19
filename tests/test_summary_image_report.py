import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from liquidity_bot.discord_summary import LiquiditySummary, build_liquidity_summary
from liquidity_bot.summary_report import prepare_summary, settled_totals
from liquidity_bot.notifications import DiscordNotifier
from liquidity_bot.runtime_secrets import RuntimeSecrets

NOW = datetime(2026,9,19,13,tzinfo=timezone.utc)


class SummaryReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name)/'report.db'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE execution_fills(execution_id TEXT,market_slug TEXT,intent TEXT,price TEXT,qty TEXT,fee TEXT,event_at TEXT,received_at TEXT,reversed INTEGER)')

    def fill(self, id, intent, price, qty, fee='0', at=None, received=None, reversed=0, market='a'):
        at = at or NOW-timedelta(minutes=2)
        with sqlite3.connect(self.db) as c:
            c.execute('INSERT INTO execution_fills VALUES (?,?,?,?,?,?,?,?,?)',
                      (id,market,'ORDER_INTENT_'+intent,price,qty,fee,at.isoformat(),(received or at).isoformat(),reversed))

    def payload(self, **kwargs):
        values=dict(mode='LIVE',readiness_status='SCALE_ELIGIBLE',observed_at=NOW,
                    session_started_at=NOW-timedelta(hours=1),report_database=str(self.db),
                    rewards_observed_at=NOW,attributable_paid_rewards_usd=Decimal('15'),
                    pending_rewards_usd=Decimal('12'),today_pnl_usd=Decimal('-999'),
                    today_realized_pnl_usd=Decimal('-900'),today_unrealized_pnl_usd=Decimal('-99'),
                    hard_stop_pnl_usd=Decimal('-6'),hard_stop_loss_usd=Decimal('25'),
                    hard_stop_window='06:00–12:00 ET · confirmed',pending_entry_cap=25,pending_entry_count=25,
                    hard_stop_state='Window budget available',slot_capacity=32,active_markets=22,
                    resting_markets=20,viable_markets=24,account_source='rest',inventory_limit_seconds=900,
                    quote_size=Decimal(10),capital_state_proven=True,settled_cash_usd=Decimal('1260'),
                    buying_power_usd=Decimal('900'),weekly_reserve_usd=Decimal('700'),
                    portfolio_exposure_cap_usd=Decimal('420'))
        values.update(kwargs)
        return build_liquidity_summary(LiquiditySummary(**values))

    def test_partial_close_open_inventory_and_fees_once(self):
        self.fill('1','BUY_LONG','.4','10','.02')
        self.fill('2','SELL_LONG','.5','4','.01')
        value=settled_totals(self.db,NOW)['today']
        self.assertEqual(value,dict(gains=Decimal('.4'),losses=Decimal(0),fees=Decimal('.03'),net=Decimal('.37')))

    def test_long_short_pair_and_rebate(self):
        self.fill('1','BUY_LONG','.4','10','-.01')
        self.fill('2','BUY_SHORT','.3','10')
        v=settled_totals(self.db,NOW)['lifetime']
        self.assertEqual(v['losses'],Decimal('-1'))
        self.assertEqual(v['net'],Decimal('-.99'))

    def test_open_fill_has_no_gross_profit_or_loss(self):
        self.fill('1','BUY_SHORT','.9','10')
        self.assertEqual(settled_totals(self.db,NOW)['today']['net'],0)

    def test_reversed_and_future_receipts_excluded(self):
        self.fill('1','BUY_LONG','.4','10',reversed=1)
        self.fill('2','SELL_LONG','.2','10',received=NOW+timedelta(seconds=1))
        self.assertEqual(settled_totals(self.db,NOW)['lifetime']['net'],0)

    def test_old_basis_closes_on_current_et_day(self):
        self.fill('1','BUY_LONG','.4','10',at=NOW-timedelta(days=1))
        self.fill('2','SELL_LONG','.5','10')
        self.assertEqual(settled_totals(self.db,NOW)['today']['gains'],1)

    def test_marked_pnl_never_enters_output(self):
        clean,model=prepare_summary(self.payload())
        self.assertNotIn('-999',json.dumps(clean))
        self.assertNotIn('Open mark',json.dumps(clean))
        self.assertIn('SETTLED P&L TODAY  $+0.00',clean['embeds'][0]['description'])
        self.assertIn(('Pending','$12.00'),model['lifetime'])
        self.assertIn(('Net profit · partial','$+15.00'),model['lifetime'])
        self.assertNotIn('_summary_report',clean)

    def test_pending_never_in_overall_net(self):
        _,a=prepare_summary(self.payload())
        _,b=prepare_summary(self.payload(pending_rewards_usd=Decimal('9999')))
        self.assertEqual(a['lifetime'][5],b['lifetime'][5])
        self.assertNotEqual(a['lifetime'][6],b['lifetime'][6])

    def test_missing_db_is_unknown_not_zero(self):
        clean,m=prepare_summary(self.payload(report_database=str(self.db)+'missing'))
        self.assertEqual(m['lifetime'][5][1],'--')
        self.assertEqual(m['today'][0][1],'--')
        self.assertFalse(Path(str(self.db)+'missing').exists())
        self.assertIn('unavailable',json.dumps(clean))

    def test_corrupt_fill_is_unknown(self):
        self.fill('bad','BUY_LONG','NaN','10')
        _,m=prepare_summary(self.payload())
        self.assertEqual(m['lifetime'][3][1],'--')

    def test_no_reward_snapshot_is_not_zero(self):
        _,m=prepare_summary(self.payload(rewards_observed_at=None))
        self.assertEqual(m['lifetime'][4][1],'--')
        self.assertEqual(m['lifetime'][5][1],'--')
        self.assertEqual(m['lifetime'][6][1],'--')

    def test_stale_and_future_rewards(self):
        _,m=prepare_summary(self.payload(rewards_observed_at=NOW-timedelta(hours=1)))
        self.assertIn('stale',m['reward_note'])
        _,m=prepare_summary(self.payload(rewards_observed_at=NOW+timedelta(seconds=1)))
        self.assertEqual(m['lifetime'][4][1],'--')

    def test_readonly_does_not_change_database(self):
        before=self.db.read_bytes()
        prepare_summary(self.payload())
        self.assertEqual(before,self.db.read_bytes())

    def test_image_and_text_share_values(self):
        from liquidity_bot.summary_image import render
        from PIL import Image
        clean,m=prepare_summary(self.payload())
        png=render(m)
        self.assertEqual(Image.open(io.BytesIO(png)).width,1200)
        self.assertLess(len(png),2_000_000)
        for _,value in m['lifetime']:
            self.assertIn(value,clean['embeds'][0]['fields'][3]['value'])

    def notifier(self):
        return DiscordNotifier(RuntimeSecrets(discord_webhook_url='https://discord.com/api/webhooks/1/test'))

    def test_upload_failure_does_not_send_text_or_retry(self):
        with patch('liquidity_bot.summary_image.render',return_value=b'png'), patch('liquidity_bot.summary_image.post',return_value=False) as post:
            n=self.notifier()
            result=n.send_payload(self.payload(),channel='summary')
            self.assertEqual(result.reason,'image_delivery_failed')
            post.assert_called_once()

    def test_render_failure_uses_settled_text(self):
        calls=[]
        n=self.notifier()
        with patch('liquidity_bot.summary_image.render',side_effect=ValueError), patch('liquidity_bot.notifications.urllib.request.urlopen') as http:
            http.return_value.__enter__.return_value.status=200
            result=n.send_payload(self.payload(),channel='summary')
            body=json.loads(http.call_args.args[0].data)
            self.assertTrue(result.sent)
            self.assertNotIn('_summary_report',body)
            self.assertIn('SETTLED',body['embeds'][0]['description'])
            self.assertNotIn('-999',json.dumps(body))

    def test_critical_stays_text_and_preserves_warning(self):
        calls=[]
        n=DiscordNotifier(RuntimeSecrets(discord_webhook_url='https://discord.com/api/webhooks/1/test'),transport=lambda url,body:calls.append(body) or True)
        with patch('liquidity_bot.summary_image.render') as render:
            n.send_payload(self.payload(integrity_problems=('Ownership conflict',)),channel='summary',critical=True)
            render.assert_not_called()
        self.assertIn('Ownership conflict',calls[0].decode())

    def test_preparation_failure_never_reverts_to_marked(self):
        calls=[]
        n=DiscordNotifier(RuntimeSecrets(discord_webhook_url='https://discord.com/api/webhooks/1/test'),transport=lambda url,body:calls.append(body) or True)
        with patch('liquidity_bot.summary_report.prepare_summary',side_effect=ValueError):
            n.send_payload(self.payload(integrity_problems=('Ownership conflict',)),channel='summary')
        self.assertNotIn('-999',calls[0].decode())
        self.assertIn('Ownership conflict',calls[0].decode())
        self.assertIn('unavailable',calls[0].decode())

    def test_error_route_strips_context(self):
        calls=[]
        n=DiscordNotifier(RuntimeSecrets(discord_errors_webhook_url='https://discord.com/api/webhooks/1/test'),transport=lambda url,body:calls.append(body) or True)
        n.send_payload(self.payload(),channel='errors')
        self.assertNotIn('_summary_report',calls[0].decode())

    def test_dst_elapsed_hours_are_actual_elapsed(self):
        at=datetime(2026,11,1,8,tzinfo=timezone.utc)  # 03:00 EST, four elapsed hours
        _,m=prepare_summary(self.payload(observed_at=at,session_started_at=at,
            rewards_observed_at=at,resting_side_hours_today=8))
        self.assertIn(('Avg sides','2.0'),m['today'])

    def test_fee_rebates_and_negative_net_label(self):
        self.fill('1','BUY_LONG','.9','10')
        self.fill('2','SELL_LONG','.1','10')
        _,m=prepare_summary(self.payload(attributable_paid_rewards_usd=Decimal('1')))
        self.assertIn(('Net loss · partial','$-7.00'),m['lifetime'])

    def test_no_window_proof_remains_unknown(self):
        _,m=prepare_summary(self.payload(hard_stop_pnl_usd=None))
        self.assertIn(('Loss / limit','-- / $25.00'),m['risk'])
        self.assertIn(('Budget left','--'),m['risk'])

    def test_oversize_status_falls_back_not_truncated(self):
        from liquidity_bot.summary_image import render
        _,m=prepare_summary(self.payload())
        m['status']='important warning '*100
        with self.assertRaises(ValueError): render(m)

    def test_critical_prepare_failure_retains_safety_warning(self):
        calls=[]
        n=DiscordNotifier(RuntimeSecrets(discord_webhook_url='https://discord.com/api/webhooks/1/test'),transport=lambda url,body:calls.append(body) or True)
        with patch('liquidity_bot.summary_report.prepare_summary',side_effect=RuntimeError):
            n.send_payload(self.payload(integrity_problems=('DO NOT TRADE',)),critical=True,channel='summary')
        self.assertIn('DO NOT TRADE',calls[0].decode())
        self.assertNotIn('999',calls[0].decode())


if __name__=='__main__': unittest.main()
