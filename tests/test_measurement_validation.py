from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace as NS

from liquidity_bot.selection_provenance import SelectionProvenance, rank_snapshot, market_rank
from scripts.measurement_validation import fill_validation, reward_validation
from scripts import measurement_validation as validation
from tests.test_market_economics import fixture, earning, fill, report, NOW
from tests.test_execution_ledger import quote_observation
from liquidity_bot.execution_ledger import ExecutionLedger


def selection(*slugs):
    items = [NS(market=NS(slug=s)) for s in slugs]
    return NS(opportunities=items, top_viable=lambda n: items[:n])


class ProvenanceTests(unittest.TestCase):
    def test_original_survives_reordering_and_absence(self):
        tracker = SelectionProvenance()
        a = rank_snapshot(selection('a', 'b'), NOW, NOW, 900)
        tracker.bind(('b',), a, NOW)
        later = NOW + timedelta(seconds=1)
        b = rank_snapshot(selection('a'), later, later, 900)
        tracker.bind(('b',), b, later)
        e = tracker.evidence('b', b)
        self.assertEqual(e['original']['rank'], 2)
        self.assertEqual(e['original']['snapshot_id'], a['snapshot_id'])
        self.assertEqual(e['current']['reason'], 'not_in_current_viable_slate')
        self.assertIsNone(e['current']['rank'])

    def test_release_and_rebind_has_new_origin(self):
        tracker = SelectionProvenance()
        a = rank_snapshot(selection('a', 'b'), NOW, NOW, 900)
        tracker.bind(('b',), a, NOW)
        tracker.bind((), a, NOW)
        b = rank_snapshot(selection('b'), NOW, NOW, 900)
        tracker.bind(('b',), b, NOW)
        self.assertEqual(tracker.evidence('b', b)['original']['rank'], 1)
        self.assertEqual(len(tracker.origins), 1)

    def test_missing_binding_not_backfilled_from_future_scan(self):
        tracker = SelectionProvenance()
        tracker.bind(('a',), rank_snapshot(None, None, NOW, 900), NOW)
        a = rank_snapshot(selection('a'), NOW, NOW, 900)
        tracker.bind(('a',), a, NOW)
        self.assertIsNone(tracker.evidence('a', a)['original']['rank'])

    def test_stale_future_empty_and_exact_boundary(self):
        for age, reason in ((901,'selection_stale'),(-1,'selection_clock_invalid'),(900,'ranked')):
            s = rank_snapshot(selection('a'), NOW-timedelta(seconds=age), NOW, 900)
            self.assertEqual(s['reason'], reason)
        self.assertEqual(market_rank(rank_snapshot(selection(), NOW, NOW, 900), 'a')['reason'],
                         'not_in_current_viable_slate')

    def test_duplicate_and_oversized_rejected(self):
        for s in (selection('a','a'), selection(*(str(n) for n in range(101)))):
            with self.assertRaises(ValueError): rank_snapshot(s, NOW, NOW, 900)

    def test_returned_origin_is_copy(self):
        t=SelectionProvenance();s=rank_snapshot(selection('a'), NOW,NOW,900)
        t.bind(('a',),s,NOW);t.evidence('a',s)['original']['rank']=999
        self.assertEqual(t.evidence('a',s)['original']['rank'],1)

    def test_provenance_and_model_persist_without_schema_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger=ExecutionLedger(Path(folder)/'ledger.db')
            self.addCleanup(ledger.close)
            t=SelectionProvenance();s=rank_snapshot(selection('a'),NOW,NOW,900)
            t.bind(('a',),s,NOW)
            obs=quote_observation(selection_rank=1,selection_provenance=t.evidence('a',s),
                                  fill_model_horizon_seconds=30,fill_model_calibration_id='test-prior')
            ledger.prepare_intent('test-intent','PLACE','market-one','test-client',{},
                                  created_at=NOW,quote_observation=obs,quote_observation_required=True)
            row=ledger.connection.execute('SELECT observation_json FROM execution_quote_observations').fetchone()
            saved=json.loads(row[0])
            self.assertEqual(saved['selection_provenance']['original']['rank'],1)
            self.assertEqual(saved['fill_model_horizon_seconds'],30)
            self.assertEqual(saved['fill_model_calibration_id'],'test-prior')
            ledger.close()


def fill_fixture():
    data=fixture()
    doc=json.loads(data['observations'][0]['observation_json'])
    doc['fill_probability']=.2
    data['observations'][0]['observation_json']=json.dumps(doc)
    return data


class ValidationTests(unittest.TestCase):
    def test_actual_30_second_fill_not_lifetime_label(self):
        for seconds,outcome in ((20,1),(30,1),(31,0),(60,0)):
            data=fill_fixture()
            at=datetime.fromisoformat(data['observations'][0]['observed_at'])
            data['fills']=[fill(at=(at+timedelta(seconds=seconds)).isoformat())]
            r=fill_validation(data,as_of=NOW)['splits']['holdout']
            self.assertEqual(r['observations'],1)
            self.assertEqual(r['fills_within_30s'],outcome)
            self.assertAlmostEqual(r['brier_score'],(.2-outcome)**2)

    def test_early_cancel_censored_not_negative(self):
        data=fill_fixture();at=datetime.fromisoformat(data['observations'][0]['observed_at'])
        data['orders'][0]['event_at']=(at+timedelta(seconds=15)).isoformat()
        r=fill_validation(data,as_of=NOW)
        self.assertEqual(r['splits']['holdout']['observations'],0)
        self.assertEqual(r['exclusions']['censored_before_horizon'],1)

    def test_fill_before_early_cancel_is_positive(self):
        data=fill_fixture();at=datetime.fromisoformat(data['observations'][0]['observed_at'])
        data['orders'][0]['event_at']=(at+timedelta(seconds=15)).isoformat()
        data['fills']=[fill(at=(at+timedelta(seconds=10)).isoformat())]
        self.assertEqual(fill_validation(data,as_of=NOW)['splits']['holdout']['fills_within_30s'],1)

    def test_reversed_and_missing_execution(self):
        data=fill_fixture();data['fills']=[fill()];data['fills'][0]['reversed']=1
        data['orders'][0]['status']='FILLED'
        r=fill_validation(data,as_of=NOW)
        self.assertEqual(r['exclusions']['missing_fill_execution'],1)

    def test_identity_and_unknown_not_negative(self):
        for field,value in (('market_slug','wrong'),('status','UNKNOWN_ABSENT'),('status','REJECTED')):
            data=fill_fixture();data['orders'][0][field]=value
            self.assertEqual(fill_validation(data,as_of=NOW)['splits']['holdout']['observations'],0)

    def test_invalid_probability_and_other_horizon(self):
        for key,value in (('fill_probability','NaN'),('fill_probability',2),('fill_model_horizon_seconds',60)):
            data=fill_fixture();d=json.loads(data['observations'][0]['observation_json']);d[key]=value
            data['observations'][0]['observation_json']=json.dumps(d)
            self.assertEqual(fill_validation(data,as_of=NOW)['splits']['holdout']['observations'],0)

    def test_temporal_split_excludes_unfinished_day(self):
        data=fill_fixture();other=deepcopy(data)
        for row in other['observations']:
            row['observed_at']='2026-09-16T12:00:00Z';row['client_order_id']='o2'
        other['orders'][0].update(client_order_id='o2',event_at='2026-09-16T13:00:00Z')
        other['intents'][0]['client_order_id']='o2'
        for k in ('orders','intents','observations'):data[k]+=other[k]
        r=fill_validation(data,as_of=NOW)
        self.assertEqual(r['splits']['reference']['days'],['2026-09-16'])
        self.assertEqual(r['splits']['holdout']['days'],['2026-09-17'])
        data['observations'][0]['observed_at']=NOW.isoformat()
        self.assertEqual(fill_validation(data,as_of=NOW)['splits']['holdout']['observations'],0)

    def test_duplicate_refuses_and_empty_unknown(self):
        data=fill_fixture();data['observations']*=2
        with self.assertRaises(ValueError):fill_validation(data,as_of=NOW)
        data['observations']=[]
        self.assertIsNone(fill_validation(data,as_of=NOW)['splits']['holdout']['brier_score'])

    def test_pending_paid_revision_no_double_count(self):
        old=report(earnings=[earning('PENDING')]);new=report()
        r=reward_validation(new,old)
        self.assertEqual(len(r['revisions']),1)
        self.assertEqual(r['groups']['daily_event']['paid'],2)
        self.assertEqual(r['groups']['daily_event']['pending'],0)

    def test_missing_reward_stays_unknown(self):
        r=reward_validation(report(earnings=[]),report())
        self.assertIsNone(r['revisions'][0]['after']['paid'])
        self.assertEqual(r['groups']['daily_event']['missing'],1)
        self.assertIn('reward_not_reported',r['unresolved'][0]['reasons'])

    def test_mixed_program_never_allocated(self):
        r=report();r['rows'][0]['signatures'].add(('second',))
        self.assertIn('unallocated',reward_validation(r)['groups'])

    def test_newer_previous_snapshot_refused(self):
        previous=report();current=report()
        previous['rows'][0]['reward_observed_at']='2026-09-18T13:00:00Z'
        with self.assertRaises(ValueError):reward_validation(current,previous)

    def test_terminal_before_fill_and_model_strata(self):
        data=fill_fixture();data['fills']=[fill(at='2026-09-17T14:00:00Z')]
        self.assertEqual(fill_validation(data,as_of=NOW)['exclusions']['terminal_before_fill'],1)
        data['fills']=[]
        r=fill_validation(data,as_of=NOW)
        self.assertEqual(r['strata'][0]['period'],'daily_event')
        self.assertEqual(r['strata'][0]['quantity'],'10')
        data['orders'][0]['exchange_order_id']='owned'
        data['fills']=[{**fill(), 'exchange_order_id':'different'}]
        self.assertEqual(fill_validation(data,as_of=NOW)['exclusions']['fill_identity_mismatch'],1)

    def test_cli_artifact_and_no_missing_zero_fabrication(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);earnings=root/'earnings.json'
            earnings.write_text(json.dumps([earning()]))
            data=fill_fixture();data['capture_finished_at']=NOW.isoformat()
            args=['validation','--earnings-json',str(earnings),'--output-root',str(root/'out')]
            with patch.object(validation,'read_ledger',return_value=data), patch.object(validation,'datetime',wraps=datetime) as clock, patch('sys.argv',args):
                clock.now.return_value=NOW
                self.assertEqual(validation.main(),0)
            report_path=next((root/'out').glob('*/report.json'))
            doc=json.loads(report_path.read_text())
            self.assertEqual(doc['enforcement'],'none')
            self.assertTrue(report_path.with_suffix('.md').exists())
            self.assertIn('earnings',doc['input_sha256'])


if __name__=='__main__':unittest.main()
