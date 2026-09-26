from __future__ import annotations
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liquidity_bot.market_quality_store import QualityStore, read_archive
from liquidity_bot.market_quality_measurement import priority_grade, OrderWitness, emit, binding_snapshot, emit_discovery
from liquidity_bot.execution_ledger import ExecutionLedger, OrderState
from liquidity_bot.live_commissioning import CommissioningRunner
from liquidity_bot.market_selector import RewardMarketSelector
from scripts.market_quality_report import build_report, latest_rewards, rest_bounds, render, exact_sum, discovery_payloads
from test_market_economics import fixture, earning, fill, DAY, NOW, AT, END, SLUG
from test_market_selector import FakeGateway, market, incentive, book, NOW as SCAN_NOW


class Sink:
    def __init__(self):
        self.records = []
        self.dropped = 0
    def emit(self, kind, payload):
        self.records.append(dict(kind=kind, payload=deepcopy(payload)))


def graded(rank=2):
    data = fixture()
    doc = json.loads(data['observations'][0]['observation_json'])
    doc['selection_provenance'] = {'original': dict(rank=rank, snapshot_id='scan1', bound_at=AT, observed_at=AT)}
    data['observations'][0]['observation_json'] = json.dumps(doc)
    return data


def report(data=None, earnings=None, records=None):
    return build_report(data or graded(), [earning()] if earnings is None else earnings,
                        records or [], [], first=DAY, last=DAY, as_of=NOW)


class GradeReportTests(unittest.TestCase):
    def test_exact_totals_do_not_depend_on_grouping(self):
        values=[D('1E+28'),D('1'),D('-1E+28')]
        self.assertEqual(exact_sum(values),D(1))
        self.assertEqual(exact_sum([exact_sum(values[:2]),values[2]]),D(1))

    def test_grade_boundaries(self):
        for n, expected in [(1,'S'),(5,'S'),(6,'A'),(10,'A'),(11,'B'),(20,'B'),
                            (21,'C'),(30,'C'),(31,'D'),(None,'Unknown'),(True,'Unknown'),(0,'Unknown'),('1','Unknown')]:
            with self.subTest(n=n): self.assertEqual(priority_grade(n), expected)

    def test_paid_only_net_and_no_automatic_grade(self):
        r = report()
        self.assertEqual(r['groups'][0]['usable_paid_net'], D(2))
        self.assertEqual(r['groups'][0]['performance'], 'unproven')
        self.assertFalse(r['groups'][0]['comparable'])
        self.assertTrue(r['reconciliation']['passed'])
        self.assertNotIn('reward_per_side_hour', r['rows'][0])

    def test_pending_not_final_profit_even_when_zero(self):
        for amount in ('0','200'):
            r = report(earnings=[earning('PENDING', amount)])
            self.assertIsNone(r['rows'][0]['finalized_net'])
            self.assertEqual(r['groups'][0]['usable_market_days'], 0)

    def test_missing_not_zero(self):
        r = report(earnings=[])
        self.assertIsNone(r['rows'][0]['paid'])
        self.assertIsNone(r['rows'][0]['finalized_net'])

    def test_legacy_current_rank_not_original(self):
        r = report(data=fixture())
        self.assertEqual(r['rows'][0]['priority_grade'], 'Unknown')

    def test_future_binding_not_original(self):
        data = graded()
        d = json.loads(data['observations'][0]['observation_json'])
        d['selection_provenance']['original']['bound_at'] = END
        data['observations'][0]['observation_json'] = json.dumps(d)
        self.assertEqual(report(data=data)['rows'][0]['priority_grade'], 'Unknown')

    def test_mixed_grade_one_reward_not_twice(self):
        data = graded()
        other = graded(12)
        for table in ('intents','orders','observations'):
            row = deepcopy(other[table][0]);row['client_order_id'] = 'o2'
            data[table].append(row)
        r = report(data=data)
        self.assertEqual(r['rows'][0]['priority_grade'], 'Mixed')
        self.assertEqual(r['reconciliation']['source']['paid'], D(2))
        self.assertEqual(r['reconciliation']['source']['side_hours'], D(1))
        self.assertIsNone(r['rows'][0]['finalized_net'])

    def test_duplicate_reward_refused(self):
        r = report(earnings=[earning(),earning()])
        self.assertIsNone(r['rows'][0]['paid'])

    def test_fee_counted_once(self):
        data = graded()
        data['fills'] = [fill(fee='.10'),fill('f2',intent='ORDER_INTENT_SELL_LONG',price='.6',at=END,fee='.05')]
        r = report(data=data)
        self.assertEqual(r['rows'][0]['closed_fill_pnl'], D('.85'))
        self.assertEqual(r['rows'][0]['paid_net_observed'], D('2.85'))

    def test_open_inventory_never_finalizes(self):
        data = graded();data['fills']=[fill()]
        self.assertIsNone(report(data=data)['rows'][0]['finalized_net'])

    def test_reversal_excludes_closed_profit(self):
        data = graded();data['fills']=[fill(),fill('f2',intent='ORDER_INTENT_SELL_LONG',price='.6',at=END)]
        data['fills'][1]['reversed']=1
        self.assertIsNone(report(data=data)['rows'][0]['finalized_net'])

    def test_empty_dataset(self):
        data = {k:[] for k in ('intents','orders','observations','fills')}
        r = build_report(data, [], [], [], first=DAY,last=DAY,as_of=NOW)
        self.assertEqual(r['groups'],[])
        self.assertTrue(r['reconciliation']['passed'])

    def test_latest_snapshot_replaces_not_adds_and_preserves_missing(self):
        a = dict(kind='rewards',payload=dict(at=AT,rows=[earning('PENDING','5')]),sha256='a',run_id='one')
        b = dict(kind='rewards',payload=dict(at=END,rows=[earning('PAID','4')]),sha256='b',run_id='two')
        rows, src = latest_rewards([a,b],NOW)
        self.assertEqual(rows[0]['reward'],'4')
        b['payload']['rows']=[]
        self.assertEqual(latest_rewards([a,b],NOW)[0],[])

    def test_same_time_conflict_refused(self):
        a = dict(kind='rewards',payload=dict(at=AT,rows=[]),sha256='a',run_id='one')
        b = {**a,'sha256':'b'}
        with self.assertRaises(ValueError):latest_rewards([a,b],NOW)

    def test_future_received_reward_cannot_enter_asof_report(self):
        r=dict(kind='rewards',payload=dict(at=AT,rows=[earning()]),
               received_at='2099-01-01T00:00:00Z',sha256='a',run_id='a')
        self.assertEqual(latest_rewards([r],NOW),( [], None))

    def test_local_time_bounds_and_absence(self):
        data = graded();data['orders'][0]['exchange_order_id']='e1'
        start = datetime.fromisoformat(AT)+timedelta(seconds=2)
        terminal = datetime.fromisoformat(END)+timedelta(seconds=3)
        order = dict(client_order_id='o1',exchange_order_id='e1',market_slug=SLUG,status='OPEN',event_at=AT)
        records = [dict(kind='order_witness',payload=dict(at=start.isoformat(),order=order)),
                   dict(kind='order_witness',payload=dict(at=terminal.isoformat(),order={**order,'status':'CANCELED','event_at':END}))]
        bounds,_ = rest_bounds(data,records,DAY,DAY)
        values=bounds[(DAY.isoformat(),SLUG)]
        self.assertEqual(values['lower'],D(3598)/3600)
        self.assertEqual(values['upper'],D(3603)/3600)
        records.append(dict(kind='order_absent',payload=dict(client_order_id='o1',at=AT)))
        bounds,_=rest_bounds(data,records,DAY,DAY)
        self.assertEqual(bounds[(DAY.isoformat(),SLUG)]['lower'],0)

    def test_partial_fill_has_no_invented_exact_time(self):
        data=graded();data['orders'][0]['filled_qty']='5'
        bounds,_=rest_bounds(data,[],DAY,DAY)
        self.assertEqual(bounds[(DAY.isoformat(),SLUG)]['lower'],0)

    def test_unavailable_display_not_zero_profit(self):
        text=render(report(earnings=[]))
        self.assertIn('unknown | unknown | unknown',text)

    def test_cohort_preserves_program_signature(self):
        r=report()
        self.assertEqual(r['matched_cohorts'][0]['signature'][1],'test-program')
        self.assertFalse(r['matched_cohorts'][0]['automatic_promotion'])

    def test_incomplete_coverage_not_account_certification(self):
        r=report(earnings=[])
        self.assertFalse(r['reconciliation']['complete_reward_coverage'])
        self.assertEqual(r['reconciliation']['missing_reward_market_days'],1)

    def test_future_capture_excluded(self):
        records=[dict(kind='discovery',payload=dict(at='2099-01-01T00:00:00Z',candidates=[],pools=[{'fake':1}]))]
        self.assertEqual(report(records=records)['latest_observed_pools'],[])

    def test_duplicate_archive_records_not_counted_twice(self):
        event=dict(kind='discovery',payload=dict(at=AT,candidates=[],pools=[]),seq=1,run_id='a')
        r=report(records=[event,event])
        self.assertEqual(r['selection_flow']['scans'],1)

    def test_conflicting_archive_records_refused(self):
        event=dict(kind='discovery',payload=dict(at=AT,candidates=[],pools=[]),seq=1,run_id='a')
        with self.assertRaises(ValueError):report(records=[event,{**event,'kind':'other'}])

    def test_today_refused(self):
        with self.assertRaises(ValueError):
            build_report(graded(),[earning()],[],[],first=NOW.date(),last=NOW.date(),as_of=NOW)

    def test_flow_counts_only_requested_days(self):
        records=[dict(kind='discovery',payload=dict(at=at,candidates=[],pools=[]))
                 for at in (AT,'2020-01-01T00:00:00Z')]
        self.assertEqual(report(records=records)['selection_flow']['scans'],1)

    def test_no_archive_is_not_complete_capture(self):
        self.assertFalse(report()['archive_capture_complete'])


class StoreTests(unittest.TestCase):
    def test_large_catalog_parts_complete_or_explicitly_missing(self):
        payload=dict(at=AT,candidates=[{'market':str(n)} for n in range(12311)],
                     counts={'catalog_markets':12311},pools=[])
        sink=Sink()
        with patch('liquidity_bot.market_quality_measurement.discovery_snapshot',return_value=payload):
            emit_discovery(sink)
        self.assertEqual(len(sink.records),13)
        self.assertEqual(discovery_payloads(sink.records),([payload],0))
        self.assertEqual(discovery_payloads(sink.records[:-1]),([],1))
        self.assertEqual(discovery_payloads(sink.records+sink.records[:1]),([],1))

    def test_invalid_bounds_refused_before_thread_start(self):
        for kwargs in ({'queue_size':0},{'max_bytes':-1},{'reserve_bytes':-1}):
            with self.subTest(kwargs=kwargs), patch('liquidity_bot.market_quality_store.Thread.start') as start:
                with self.assertRaises(ValueError):QualityStore(Path('unused'),**kwargs)
                start.assert_not_called()

    def test_producer_does_no_filesystem_or_network_work(self):
        with patch('liquidity_bot.market_quality_store.Thread.start'):
            store=QualityStore(Path('unused'))
        with patch('sqlite3.connect',side_effect=AssertionError('disk')), \
             patch('pathlib.Path.mkdir',side_effect=AssertionError('disk')), \
             patch('socket.socket',side_effect=AssertionError('network')):
            self.assertTrue(store.emit('test',{'value':1}))

    def test_round_trip_health_and_readonly(self):
        with tempfile.TemporaryDirectory() as td:
            store=QualityStore(Path(td),reserve_bytes=0)
            self.assertTrue(store.emit('test',{'value':3}))
            store.close();store.thread.join(2)
            rows,health=read_archive(store.path)
            self.assertEqual(rows[0]['payload']['value'],3)
            self.assertTrue(health['closed'])
            self.assertTrue(health['sequence_complete'])
            self.assertFalse(store.emit('late',{}))

    def test_overflow_bounded_no_wait(self):
        with patch('liquidity_bot.market_quality_store.Thread.start'):
            store=QualityStore(Path('unused'),queue_size=1)
            self.assertTrue(store.emit('test',{}))
            self.assertFalse(store.emit('test',{}))
            self.assertEqual(store.dropped,1)

    def test_nonfinite_and_oversize_dropped(self):
        with patch('liquidity_bot.market_quality_store.Thread.start'):
            store=QualityStore(Path('unused'),max_record_bytes=20)
            self.assertFalse(store.emit('test',{'x':float('nan')}))
            self.assertFalse(store.emit('test',{'x':'a'*100}))

    def test_storage_quota_failure_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            store=QualityStore(Path(td),max_bytes=1,reserve_bytes=0)
            store.emit('test',{})
            store.close();store.thread.join(2)
            self.assertTrue(store.disabled)
            self.assertEqual(store.errors,1)

    def test_checksum_tamper_detected(self):
        with tempfile.TemporaryDirectory() as td:
            store=QualityStore(Path(td),reserve_bytes=0);store.emit('test',{})
            store.close();store.thread.join(2)
            with sqlite3.connect(store.path) as c:c.execute("UPDATE records SET body='{}x'")
            with self.assertRaises(ValueError):read_archive(store.path)

    def test_broken_sink_never_raises(self):
        sink=Mock();sink.emit.side_effect=OSError('disk failed')
        emit(sink,'test',lambda:{})
        emit(None,'test',lambda:1/0)

    def test_order_change_dedup_and_absence(self):
        sink=Sink();w=OrderWitness(sink)
        order=OrderState('o',SLUG,'BUY',D('.5'),D(10),D(0),'OPEN',NOW,'e')
        w([order],NOW);w([order],NOW+timedelta(seconds=5))
        self.assertEqual(len(sink.records),1)
        w([],NOW,absent=['o']);w([order],NOW)
        self.assertEqual(len(sink.records),3)

    def test_ledger_callback_after_commit_and_failure_no_change(self):
        with tempfile.TemporaryDirectory() as td:
            ledger=ExecutionLedger(Path(td)/'ledger.db')
            seen=[]
            def observer(*args,**kwargs):
                seen.append(ledger.connection.in_transaction)
                raise ValueError('diagnostic broken')
            ledger.quality_observer=observer
            order=OrderState('o',SLUG,'BUY',D('.5'),D(10),D(0),'OPEN',NOW,'e')
            self.assertTrue(ledger.apply_order_event('test','e1',order,received_at=NOW))
            self.assertEqual(seen,[False])
            self.assertEqual(ledger.working_orders()[0]['status'],'OPEN')
            ledger.close()


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_pool_counts_distinct_events_not_rows(self):
        m1=replace(market('a'),event_slug='event-one')
        m2=replace(market('b'),event_slug='event-one')
        common=incentive('a',pool=100)
        inc2=replace(common,slug='b')
        sink=Sink()
        gateway=FakeGateway({'a':m1,'b':m2},{'a':common,'b':inc2},{'a':book('a'),'b':book('b')})
        await RewardMarketSelector(gateway,quality_sink=sink).select(now=SCAN_NOW)
        pools=sink.records[0]['payload']['pools']
        self.assertEqual(len(pools),1)
        self.assertEqual(pools[0]['observed_events'],1)
        self.assertEqual(pools[0]['observed_markets'],2)
        self.assertFalse(pools[0]['population_complete'])

    async def test_metadata_refused_without_extra_book(self):
        bad=market('bad',market_type_v2='SPORTS_MARKET_TYPE_SPREAD')
        gateway=FakeGateway({'bad':bad},{'bad':incentive('bad',pool=100)},{'bad':book('bad')})
        sink=Sink()
        await RewardMarketSelector(gateway,quality_sink=sink).select(now=SCAN_NOW)
        self.assertEqual(gateway.book_calls,[])
        self.assertEqual(sink.records[0]['payload']['candidates'][0]['stage'],'metadata_refused')

    async def test_oversized_catalog_keeps_executable_rows_instead_of_dropping(self):
        # 2026-09-26: a 43,935-market catalog exceeded the 20,000 bound and every
        # scan's discovery record was refused. Above the bound keep executable rows.
        bad=market('bad',market_type_v2='SPORTS_MARKET_TYPE_SPREAD')
        markets={'a':market('a'),'bad':bad}
        gateway=FakeGateway(markets,{s:incentive(s,pool=100) for s in markets},
                            {s:book(s) for s in markets})
        sink=Sink()
        with patch('liquidity_bot.market_quality_measurement.DISCOVERY_CATALOG_BOUND',1):
            result=await RewardMarketSelector(gateway,quality_sink=sink).select(now=SCAN_NOW)
        (record,)=sink.records
        payload=record['payload']
        self.assertEqual(record['kind'],'discovery')
        self.assertEqual(payload['catalog_rows'],'executable_only')
        self.assertEqual([c['market'] for c in payload['candidates']],['a'])
        self.assertEqual(payload['counts']['catalog_markets'],2)
        self.assertEqual(payload['counts']['no_applicable_period_markets'],
                         result.no_applicable_period_markets)
        sink=Sink()
        await RewardMarketSelector(FakeGateway(markets,{s:incentive(s,pool=100) for s in markets},
            {s:book(s) for s in markets}),quality_sink=sink).select(now=SCAN_NOW)
        self.assertEqual(sink.records[0]['payload']['catalog_rows'],'full')
        self.assertEqual(len(sink.records[0]['payload']['candidates']),2)

    async def test_store_defers_oversized_catalog_to_writer(self):
        with tempfile.TemporaryDirectory() as td:
            store=QualityStore(Path(td),reserve_bytes=0)
            markets={'a':market('a'),'bad':market('bad',market_type_v2='SPORTS_MARKET_TYPE_SPREAD')}
            gateway=FakeGateway(markets,{s:incentive(s,pool=100) for s in markets},
                                {s:book(s) for s in markets})
            with patch('liquidity_bot.market_quality_measurement.DISCOVERY_CATALOG_BOUND',1):
                await RewardMarketSelector(gateway,quality_sink=store).select(now=SCAN_NOW)
                for _ in range(100):
                    if store.written:
                        break
                    await asyncio.sleep(.02)
            store.close();store.thread.join(2)
            rows,health=read_archive(store.path)
            self.assertEqual(health['dropped'],0)
            (disc,)=[r for r in rows if r['kind']=='discovery']
            self.assertEqual(disc['payload']['catalog_rows'],'executable_only')

    async def test_no_applicable_period_is_counted(self):
        later=incentive('later',pool=100,starts_at=SCAN_NOW+timedelta(hours=1))
        gateway=FakeGateway({'later':market('later'),'now':market('now')},
                            {'later':later,'now':incentive('now',pool=100)},
                            {'later':book('later'),'now':book('now')})
        result=await RewardMarketSelector(gateway).select(now=SCAN_NOW)
        self.assertEqual(result.no_applicable_period_markets,1)
        self.assertEqual(result.eligible_pregame_markets,1)

    async def test_selection_same_calls_same_decision_with_capture_and_broken_sink(self):
        outputs=[];calls=[]
        sink=Sink();broken=Mock();broken.emit.side_effect=RuntimeError('sink')
        for target in (None,sink,broken):
            markets={s:market(s) for s in ('a','b')}
            gateway=FakeGateway(markets,{s:incentive(s,pool=100) for s in markets},
                                {s:book(s) for s in markets})
            selector=RewardMarketSelector(gateway,quality_sink=target)
            outputs.append(await selector.select(now=SCAN_NOW))
            calls.append((gateway.book_calls,gateway.upcoming_calls,gateway.incentive_symbol_calls))
        self.assertEqual(outputs[0],outputs[1]);self.assertEqual(outputs[0],outputs[2])
        self.assertEqual(calls[0],calls[1]);self.assertEqual(calls[0],calls[2])
        self.assertEqual(sink.records[0]['kind'],'discovery')
        self.assertFalse(sink.records[0]['payload']['pool_population_complete'])

    async def test_reward_archive_includes_retired_without_polling_or_session_drift(self):
        runner=object.__new__(CommissioningRunner)
        runner._active_market_slugs=('active',)
        runner.clock=lambda:NOW
        runner.gateway=NS(fetch_earnings=AsyncMock(return_value=[
            NS(date='2026-09-17',market_slug=s,program_type='liquidityProgram',reward=D(2),status='PAID')
            for s in ('active','retired')]))
        runner.ledger=Mock();runner.quality_sink=Sink()
        await runner._refresh_earnings(NOW)
        runner.gateway.fetch_earnings.assert_awaited_once()
        rows=runner.quality_sink.records[0]['payload']['rows']
        self.assertEqual({r['market_slug'] for r in rows},{'active','retired'})
        persisted=runner.ledger.record_reward_earnings.call_args.args[0]
        self.assertEqual([r.market_slug for r in persisted],['active'])


if __name__=='__main__':unittest.main()
