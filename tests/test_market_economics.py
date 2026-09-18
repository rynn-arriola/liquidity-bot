from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
import importlib.util
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.market_economics import (analyze, boundary, build_cohorts, encode,
    latest_cached_earnings, number, read_ledger, read_scan_log, render,
    shadow_rank, signature, stamp, fetch_earnings, main, frozen_cohorts)
from liquidity_bot.selection_measurement import selection_measurement

AT="2026-09-17T12:00:00+00:00"
END="2026-09-17T13:00:00+00:00"
NOW=datetime(2026,9,18,14,tzinfo=timezone.utc)
DAY=date(2026,9,17)
SLUG="aec-wtt-a-b-2026-09-17"


def fixture():
    obs={"market_slug":SLUG,"side":"bid","observed_at":AT,
         "program_id":"test-program","reward_period":"daily_event",
         "reward_pool":100,"discount_factor":0.3,"target_size":1000,
         "quantity":"10","projected_reward":"0.01","seconds_to_event":40000,
         "selection_rank":2}
    return {"intents":[{"client_order_id":"o1","operation":"PLACE","market_slug":SLUG,
                        "created_at":AT,"quote_observation_required":1}],
            "orders":[{"client_order_id":"o1","market_slug":SLUG,"status":"CANCELED",
                       "event_at":END,"side":"BUY","filled_qty":"0","original_qty":"10"}],
            "observations":[{"client_order_id":"o1","market_slug":SLUG,"side":"bid",
                             "observed_at":AT,"observation_json":json.dumps(obs)}],"fills":[]}


def earning(status="PAID",reward="2",**kwargs):
    return {"date":"2026-09-17","market_slug":SLUG,"program_type":"liquidityProgram",
            "status":status,"reward":reward,"observed_at":"2026-09-18T12:00:00+00:00",**kwargs}


def fill(ident="f1",client="o1",intent="ORDER_INTENT_BUY_LONG",price="0.5",at=AT,qty="10",fee="0"):
    return {"execution_id":ident,"client_order_id":client,"market_slug":SLUG,"event_at":at,
            "received_at":at,"intent":intent,"price":price,"qty":qty,"fee":fee,"reversed":0}


def report(data=None,earnings=None,first=DAY,last=DAY):
    return analyze(data or fixture(),[earning()] if earnings is None else earnings,
                   first=first,last=last,as_of=NOW)


def candidate_scan():
    return {"schema":1,"enforcement":"none","at":NOW.isoformat(),"projection_seconds":30,
            "candidates":[{"market":SLUG,"period":"daily_event","program":"test-program",
               "pool":100,"discount":0.3,"target":1000,"quantity":10,"current_rank":2,
               "game_start":"2026-09-18T20:00:00+00:00","program_end":None,
               "sides":[{"viable":True,"reward_eligible":True,"projected_reward":"0.01"}]}]}


class EconomicsTests(unittest.TestCase):
    def test_actual_paid_pending_separate_no_fake_zero(self):
        paid=report()["rows"][0]
        self.assertEqual(paid["paid"],D(2));self.assertTrue(paid["training_eligible"])
        pending=report(earnings=[earning("PENDING")])["rows"][0]
        self.assertFalse(pending["training_eligible"])
        self.assertEqual(pending["paid_contribution"],0)
        self.assertEqual(pending["provisional_contribution"],2)
        absent=report(earnings=[])["rows"][0]
        self.assertIsNone(absent["paid"]);self.assertIsNone(absent["paid_contribution"])
        self.assertFalse(absent["training_eligible"])

    def test_duplicate_status_invalid_not_double_reward(self):
        r=report(earnings=[earning(),earning()])["rows"][0]
        self.assertIsNone(r["paid"]);self.assertIn("invalid_reward_snapshot",r["flags"])

    def test_invalid_reward_values_fail_coverage(self):
        for value in (None,"NaN","Infinity","-1",True):
            with self.subTest(value=value):
                r=report(earnings=[earning(reward=value)])["rows"][0]
                self.assertFalse(r["training_eligible"]);self.assertIsNone(r["paid"])

    def test_other_reward_program_excluded(self):
        self.assertIsNone(report(earnings=[earning(program_type="volumeProgram")])["rows"][0]["paid"])

    def test_future_and_mixed_reward_observations_invalid(self):
        for rows in ([earning(observed_at="2026-09-19T00:00:00Z")],
                     [earning(),earning("PENDING",observed_at="2026-09-18T11:00:00Z")]):
            self.assertFalse(report(earnings=rows)["rows"][0]["training_eligible"])

    def test_rest_union_and_prediction_not_duplicate(self):
        data=fixture()
        for name in ("intents","orders","observations"):
            row=deepcopy(data[name][0]);row["client_order_id"]="o2";data[name].append(row)
        r=report(data)["rows"][0]
        self.assertEqual(r["side_hours"],1);self.assertEqual(r["quantity_hours"],10)
        self.assertAlmostEqual(float(r["placement_model_reward"]),1.2)

    def test_two_sides_two_hours_pool_not_summed(self):
        data=fixture()
        for name in ("intents","orders","observations"):
            row=deepcopy(data[name][0]);row["client_order_id"]="o2"
            if name=="orders":row["side"]="SELL"
            if name=="observations":
                doc=json.loads(row["observation_json"]);doc["side"]="offer"
                row["side"]="offer";row["observation_json"]=json.dumps(doc)
            data[name].append(row)
        r=report(data)["rows"][0]
        self.assertEqual(r["side_hours"],2);self.assertEqual(len(r["signatures"]),1)

    def test_reducers_rejected_orders_not_resting(self):
        for mode in ("reducer","rejected"):
            data=fixture()
            if mode=="reducer":data["intents"][0]["quote_observation_required"]=0
            else:data["orders"][0]["status"]="REJECTED"
            r=report(data)["rows"][0]
            self.assertEqual(r["side_hours"],0);self.assertFalse(r["training_eligible"])

    def test_unknown_and_modified_orders_not_training(self):
        for mode in ("unknown","modified"):
            data=fixture()
            if mode=="unknown":data["orders"][0]["status"]="UNKNOWN_ABSENT"
            else:data["intents"].append({**data["intents"][0],"operation":"MODIFY"})
            self.assertFalse(report(data)["rows"][0]["training_eligible"])

    def test_bad_missing_observation_not_training(self):
        for raw in ("{}","[]","not-json"):
            data=fixture();data["observations"][0]["observation_json"]=raw
            self.assertFalse(report(data)["rows"][0]["training_eligible"])

    def test_identity_mismatch_and_quantity_mismatch_not_training(self):
        for key,value in (("market_slug","another"),("quantity",20)):
            data=fixture();doc=json.loads(data["observations"][0]["observation_json"])
            doc[key]=value;data["observations"][0]["observation_json"]=json.dumps(doc)
            self.assertFalse(report(data)["rows"][0]["training_eligible"])

    def test_program_changes_and_old_new_periods_not_combined(self):
        data=fixture()
        for name in ("intents","orders","observations"):
            row=deepcopy(data[name][0]);row["client_order_id"]="o2"
            if name=="observations":
                doc=json.loads(row["observation_json"]);doc["reward_period"]="day_of"
                row["observation_json"]=json.dumps(doc)
            data[name].append(row)
        r=report(data)["rows"][0]
        self.assertFalse(r["training_eligible"]);self.assertEqual(len(r["periods"]),2)

    def test_open_inventory_excludes_marks_and_prevents_training(self):
        data=fixture();data["fills"]=[fill(fee="0.02")]
        r=report(data)["rows"][0]
        self.assertEqual(r["closed_fill_pnl"],D("-0.02"))
        self.assertIn("unmatched_inventory_at_cutoff",r["flags"])

    def test_closed_owned_fills_signed_fees_and_reverse(self):
        data=fixture();data["fills"]=[fill(fee="-0.01"),fill("f2",intent="ORDER_INTENT_SELL_LONG",price="0.4",at=END,fee="0.02")]
        r=report(data)["rows"][0]
        self.assertEqual(r["closed_fill_pnl"],D("-1.01"))
        self.assertEqual(r["fill_fees"],D("0.01"));self.assertEqual(r["closing_fills"],1)
        data["fills"][1]["reversed"]=1
        self.assertEqual(report(data)["rows"][0]["closed_fill_pnl"],D("0.01"))

    def test_manual_close_and_manual_basis_are_flagged(self):
        for which in (0,1):
            data=fixture();data["fills"]=[fill(),fill("f2",intent="ORDER_INTENT_SELL_LONG",price="0.4",at=END)]
            data["fills"][which]["client_order_id"]="venue:manual"
            r=report(data)["rows"][0]
            self.assertEqual(r["closed_fill_pnl"],-1)
            self.assertIn("manual_or_unattributed_episode",r["flags"])
            self.assertFalse(r["training_eligible"])

    def test_older_basis_replayed_cross_day_flag_not_zero(self):
        data=fixture();data["fills"]=[fill(at="2026-09-16T12:00:00Z"),fill("f2",intent="ORDER_INTENT_SELL_LONG",price="0.4",at=END)]
        r=report(data)["rows"][0]
        self.assertEqual(r["closed_fill_pnl"],-1);self.assertIn("cross_day_inventory",r["flags"])

    def test_fee_and_short_price_frame(self):
        data=fixture();data["fills"]=[fill(intent="ORDER_INTENT_BUY_SHORT",price="0.7"),fill("f2",intent="ORDER_INTENT_SELL_SHORT",price="0.6",at=END)]
        self.assertEqual(report(data)["rows"][0]["closed_fill_pnl"],1)

    def test_utc_et_split_and_dst(self):
        data=fixture();data["intents"][0]["created_at"]="2026-09-17T03:30:00Z"
        data["orders"][0]["event_at"]="2026-09-17T04:30:00Z"
        rows=report(data,first=date(2026,9,16))["rows"]
        self.assertEqual([r["side_hours"] for r in rows],[D("0.5"),D("0.5")])
        self.assertEqual((boundary(date(2026,3,9))-boundary(date(2026,3,8))).total_seconds(),23*3600)
        self.assertEqual((boundary(date(2026,11,2))-boundary(date(2026,11,1))).total_seconds(),25*3600)

    def test_reward_date_mismatch_not_shifted(self):
        data=fixture();data["intents"][0]["created_at"]="2026-09-17T01:00:00Z"
        data["orders"][0]["event_at"]="2026-09-17T02:00:00Z"
        r=report(data)["rows"][0]
        self.assertEqual(r["paid"],2);self.assertEqual(r["side_hours"],0)

    def test_today_naive_nonfinite_rejected(self):
        with self.assertRaises(ValueError):report(last=date(2026,9,18))
        with self.assertRaises(ValueError):stamp("2026-09-17T12:00:00")
        for value in (None,"NaN",float("inf"),True):
            with self.assertRaises(ValueError):number(value)

    def test_training_minimum_and_no_pending_leak(self):
        r=report();c=build_cohorts(r)
        self.assertFalse(c[0]["sample_ready"])
        ready=build_cohorts(r,min_markets=1,min_days=1,min_hours=D(1))
        self.assertTrue(ready[0]["sample_ready"])
        self.assertEqual(build_cohorts(report(earnings=[earning("PENDING")])),[])

    def test_missing_loser_cannot_disappear_from_cohort(self):
        r=report()
        missing=deepcopy(r["rows"][0]);missing["market"]="aec-wtt-c-d-2026-09-17"
        missing["training_eligible"]=False;missing["paid"]=None;missing["closed_fill_pnl"]=D(-20)
        r["rows"].append(missing)
        c=build_cohorts(r,min_markets=1,min_days=1,min_hours=D(1))[0]
        self.assertFalse(c["sample_ready"]);self.assertEqual(c["population_market_days"],2)
        self.assertFalse(c["complete_population"])

    def test_pending_zero_is_still_not_final(self):
        self.assertFalse(report(earnings=[earning("PENDING",reward="0")])["rows"][0]["training_eligible"])

    def test_manual_earlier_partial_close_taints_whole_episode(self):
        data=fixture();data["fills"]=[fill(),fill("f2",intent="ORDER_INTENT_SELL_LONG",qty="5",at=END),fill("f3",client="venue:manual",intent="ORDER_INTENT_SELL_LONG",qty="5",at="2026-09-17T14:00:00Z")]
        self.assertFalse(report(data)["rows"][0]["training_eligible"])

    def test_fill_exchange_identity_mismatch_not_owned(self):
        data=fixture();data["orders"][0]["exchange_order_id"]="expected"
        data["fills"]=[{**fill(),"exchange_order_id":"wrong"}]
        self.assertEqual(report(data)["rows"][0]["unowned_fills"],1)

    def test_empty_and_malformed_scan(self):
        self.assertEqual(shadow_rank([],{},now=NOW)["status"],"invalid_scan")
        scan=candidate_scan();scan["candidates"]=[]
        self.assertEqual(shadow_rank([],scan,now=NOW)["rows"],[])

    def test_shadow_unknown_retains_no_score_and_current_rank(self):
        r=shadow_rank([],candidate_scan(),now=NOW)
        self.assertEqual(r["rows"][0]["current_rank"],2)
        self.assertIsNone(r["rows"][0]["shadow_rank"])
        self.assertIsNone(r["rows"][0]["score_per_side_hour"])

    def test_shadow_score_and_parameter_drift(self):
        models=build_cohorts(report(),min_markets=1,min_days=1,min_hours=D(1))
        scan=candidate_scan();r=shadow_rank(models,scan,now=NOW)["rows"][0]
        self.assertEqual(r["shadow_rank"],1);self.assertGreater(r["score_per_side_hour"],0)
        scan["candidates"][0]["pool"]=101
        self.assertIsNone(shadow_rank(models,scan,now=NOW)["rows"][0]["shadow_rank"])

    def test_shadow_no_lookahead_stale_or_exit_boundary(self):
        models=build_cohorts(report(),min_markets=1,min_days=1,min_hours=D(1))
        models[0]["reward_observed_through"]=(NOW+timedelta(seconds=1)).isoformat()
        self.assertEqual(shadow_rank(models,candidate_scan(),now=NOW)["rows"][0]["status"],"outcomes_not_known_at_scan")
        scan=candidate_scan();scan["at"]=(NOW-timedelta(minutes=21)).isoformat()
        self.assertEqual(shadow_rank(models,scan,now=NOW)["status"],"stale_or_future_scan")
        models[0]["reward_observed_through"]="2026-09-18T12:00:00Z"
        scan=candidate_scan();scan["candidates"][0]["game_start"]=(NOW+timedelta(minutes=5)).isoformat()
        self.assertEqual(shadow_rank(models,scan,now=NOW)["rows"][0]["status"],"at_exit_boundary")

    def test_frozen_model_round_trip_and_late_ledger_knowledge(self):
        r=report();r["cohorts"]=build_cohorts(r,min_markets=1,min_days=1,min_hours=D(1))
        frozen=frozen_cohorts(json.loads(json.dumps(r,default=encode)))
        self.assertEqual(shadow_rank(frozen,candidate_scan(),now=NOW)["rows"][0]["shadow_rank"],1)
        frozen[0]["knowledge_as_of"]=(NOW+timedelta(seconds=1)).isoformat()
        self.assertEqual(shadow_rank(frozen,candidate_scan(),now=NOW)["rows"][0]["status"],"outcomes_not_known_at_scan")
        r["cohorts"][0]["knowledge_as_of"]=(NOW+timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):frozen_cohorts(r)

    def test_duplicate_candidates_and_stale_history(self):
        scan=candidate_scan();scan["candidates"].append(deepcopy(scan["candidates"][0]))
        self.assertEqual(shadow_rank([],scan,now=NOW)["status"],"invalid_scan")
        models=build_cohorts(report(),min_markets=1,min_days=1,min_hours=D(1))
        models[0]["last_day"]="2026-08-01"
        self.assertEqual(shadow_rank(models,candidate_scan(),now=NOW)["rows"][0]["status"],"stale_history")

    def test_cached_latest_status_replaces_pending(self):
        conn=sqlite3.connect(":memory:");conn.row_factory=sqlite3.Row
        conn.execute("CREATE TABLE execution_reward_earnings(date,market_slug,program_type,status,reward,observed_at)")
        for r in [earning("PENDING",observed_at="2026-09-18T10:00:00Z"),earning(),earning("PENDING",reward="99",observed_at="2026-09-19T00:00:00Z")]:
            conn.execute("INSERT INTO execution_reward_earnings VALUES(?,?,?,?,?,?)",tuple(r[k] for k in ("date","market_slug","program_type","status","reward","observed_at")))
        rows=latest_cached_earnings(conn,NOW);conn.close()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]["status"],"PAID")

    def test_truncated_scan_and_old_lines_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"scan.log"
            path.write_text('[payout-estimate] market=x\n[market-measurement] {\n',encoding="utf-8")
            self.assertIsNone(read_scan_log(path,through=NOW))
            path.write_text('[market-measurement] '+json.dumps(candidate_scan())+'\n[market-measurement] {',encoding="utf-8")
            self.assertEqual(read_scan_log(path,through=NOW)["candidates"][0]["market"],SLUG)

    def test_report_json_and_human_output(self):
        r=report();r["cohorts"]=build_cohorts(r);r["shadow"]=shadow_rank([],None,now=NOW)
        json.dumps(r,default=encode,allow_nan=False)
        self.assertIn("measurement only",render(r));self.assertIn("not zero",render(r))

    def test_missing_db_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"absent.db"
            with self.assertRaises(sqlite3.OperationalError):read_ledger(path)
            self.assertFalse(path.exists())

    def test_read_only_ledger_snapshot_does_not_change_database(self):
        from liquidity_bot.execution_ledger import SCHEMA
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"ledger.db"
            conn=sqlite3.connect(path);conn.executescript(SCHEMA);conn.close()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            data=read_ledger(path)
            self.assertEqual(data["orders"],[])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_daily_scenario_stops_at_midnight_and_accrual_unknown(self):
        models=build_cohorts(report(),min_markets=1,min_days=1,min_hours=D(1))
        scan=candidate_scan();scan["candidates"][0]["game_start"]="2026-09-19T12:00:00Z"
        row=shadow_rank(models,scan,now=NOW)["rows"][0]
        self.assertEqual(row["usable_until"],"2026-09-19T04:00:00+00:00")
        self.assertIsNone(row["accrued_reward"])

    def test_shadow_preserves_program_priority_and_negative_scores(self):
        models=build_cohorts(report(),min_markets=1,min_days=1,min_hours=D(1))
        scan=candidate_scan();models[0]["worst_day_cost_per_side_hour"]=D(999)
        row=shadow_rank(models,scan,now=NOW)["rows"][0]
        self.assertEqual(row["status"],"nonpositive_observational")
        self.assertLess(row["score_per_side_hour"],0)
        legacy=deepcopy(models[0]);legacy["signature"]=("day_of",*legacy["signature"][1:])
        cand=deepcopy(scan["candidates"][0]);cand["market"]="aec-wtt-c-d-2026-09-18";cand["period"]="day_of";cand["current_rank"]=1
        scan["candidates"].append(cand)
        rows=shadow_rank([*models,legacy],scan,now=NOW)["rows"]
        self.assertEqual([r["shadow_rank"] for r in rows],[1,1])


class SelectionMeasurementTests(unittest.TestCase):
    def inputs(self):
        projection=NS(eligible=True,snapshot_share=.2,projected_reward=.01,maximum_remaining_reward=2)
        side=NS(side="bid",price=.5,viable=True,blocker=None,projection=projection,
                fill_probability=.1,stressed_exit_pnl=-1,stressed_exit_complete=True)
        market=NS(slug=SLUG,event_slug="event-one",game_start=NOW+timedelta(hours=4))
        period_=NS(program_id="program",period="daily_event",reward_pool=100,target_size=1000,
                   discount_factor=.3,starts_at=None,ends_at=None)
        item=NS(viable_sides=("bid",),market=market,period=period_,book=NS(received_at=NOW),spread=.02,sides=(side,))
        config=NS(quote_size=D(10),reward_projection_horizon_seconds=30,reward_projection_haircut=D(".5"))
        return NS(opportunities=(item,)),config

    def test_complete_scan_contains_inputs_and_no_authority(self):
        selection,config=self.inputs()
        before=repr(selection)
        d=json.loads(selection_measurement(selection,config,NOW))
        self.assertEqual(d["enforcement"],"none");self.assertEqual(len(d["candidates"]),1)
        self.assertEqual(d["candidates"][0]["event"],"event-one")
        self.assertEqual(d["candidates"][0]["sides"][0]["share"],.2)
        self.assertEqual(repr(selection),before)

    def test_empty_full_overflow_nonfinite(self):
        selection,config=self.inputs()
        self.assertEqual(json.loads(selection_measurement(NS(opportunities=()),config,NOW))["candidates"],[])
        hundred=NS(opportunities=selection.opportunities*100)
        self.assertEqual(len(json.loads(selection_measurement(hundred,config,NOW))["candidates"]),100)
        with self.assertRaises(ValueError):selection_measurement(NS(opportunities=selection.opportunities*101),config,NOW)
        selection.opportunities[0].period.reward_pool=float("nan")
        with self.assertRaises(ValueError):selection_measurement(selection,config,NOW)

    def test_blocked_candidate_does_not_take_viable_rank(self):
        selection,config=self.inputs()
        blocked=deepcopy(selection.opportunities[0]);blocked.viable_sides=()
        blocked.sides[0].viable=False;blocked.sides[0].blocker="spread"
        selection.opportunities=(blocked,selection.opportunities[0])
        rows=json.loads(selection_measurement(selection,config,NOW))["candidates"]
        self.assertIsNone(rows[0]["current_rank"]);self.assertEqual(rows[1]["current_rank"],1)


class EconomicsIoTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_uses_explicit_shared_report_lane_and_closes(self):
        client=NS(close=AsyncMock())
        gateway=NS(fetch_earnings=AsyncMock(return_value=[NS(date="2026-09-17",market_slug=SLUG,program_type="liquidityProgram",status="PAID",reward=2)]))
        with patch("liquidity_bot.probe.client_from_environment",return_value=client), \
             patch("liquidity_bot.adapters.shared_api_limiter",return_value="shared") as limiter, \
             patch("liquidity_bot.adapters.AsyncReadOnlyGateway",return_value=gateway) as build:
            rows=await fetch_earnings(Path("active-owner.state"))
        self.assertEqual(limiter.call_args.args,(.25,))
        self.assertEqual(limiter.call_args.kwargs["state_path"],str(Path("active-owner.state").resolve()))
        self.assertEqual(build.call_args.kwargs["default_traffic_class"],"report")
        gateway.fetch_earnings.assert_awaited_once_with();client.close.assert_awaited_once()
        self.assertEqual(rows[0]["reward"],"2")

    async def test_fetch_failure_closes_and_never_returns_empty_success(self):
        client=NS(close=AsyncMock())
        with patch("liquidity_bot.probe.client_from_environment",return_value=client), \
             patch("liquidity_bot.adapters.shared_api_limiter",return_value="shared"), \
             patch("liquidity_bot.adapters.AsyncReadOnlyGateway",return_value=NS(fetch_earnings=AsyncMock(side_effect=RuntimeError("unavailable")))):
            with self.assertRaises(RuntimeError):await fetch_earnings(Path("active-owner.state"))
        client.close.assert_awaited_once()

    async def test_fetch_requires_limiter_before_any_ledger_or_network(self):
        with patch.object(sys,"argv",["market_economics","--fetch-earnings"]), \
             patch("scripts.market_economics.read_ledger") as read, \
             patch("sys.stderr"):
            with self.assertRaises(SystemExit) as exc:await main()
        self.assertEqual(exc.exception.code,2);read.assert_not_called()


if __name__=="__main__":unittest.main()
