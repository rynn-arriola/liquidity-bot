from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from liquidity_bot.maintenance_schedule import (
    MAINTENANCE_ACTIVE_URL,
    MAINTENANCE_HISTORY_URL,
    MAINTENANCE_UPCOMING_URL,
    MaintenanceFeedState,
    MaintenancePhase,
    MaintenanceWindow,
    load_maintenance_cache,
    maintenance_decision,
    probe_polymarket_us_maintenance_schedule,
    write_maintenance_cache,
)


UTC = timezone.utc
START = datetime(2026, 9, 5, 6, 0, tzinfo=UTC)
END = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)


def production_row(*, status="scheduled", completed_at=None):
    updates = []
    if completed_at is not None:
        updates.append({
            "status": "completed",
            "created_at": completed_at.isoformat(),
        })
    return {
        "id": "maintenance-one",
        "name": "Scheduled Maintenance",
        "status": status,
        "scheduled_for": START.isoformat(),
        "scheduled_until": END.isoformat(),
        "updated_at": (completed_at or START).isoformat(),
        "incident_updates": updates,
        "components": [{
            "id": "3xmdtkg3r8y5",
            "name": "[Production] Polymarket US Exchange",
        }],
    }


def window(*, status="scheduled", completed_at=None):
    return MaintenanceWindow(
        "maintenance-one", "Scheduled Maintenance", status,
        START, END, completed_at or START, completed_at)


class MaintenanceScheduleTests(unittest.TestCase):
    def test_boundaries_evacuation_outage_and_conservative_resume(self):
        scheduled = window()
        self.assertEqual(
            maintenance_decision(
                (scheduled,), START - timedelta(minutes=31)).phase,
            MaintenancePhase.NORMAL)
        self.assertEqual(
            maintenance_decision(
                (scheduled,), START - timedelta(minutes=30)).phase,
            MaintenancePhase.EVACUATE)
        self.assertEqual(
            maintenance_decision((scheduled,), START).phase,
            MaintenancePhase.MAINTENANCE)

        completed_early = window(
            status="completed", completed_at=END - timedelta(minutes=30))
        self.assertEqual(completed_early.resume_at, END + timedelta(hours=1))
        self.assertEqual(
            maintenance_decision((completed_early,), END).phase,
            MaintenancePhase.RESUME_HOLD)
        self.assertEqual(
            maintenance_decision(
                (completed_early,), END + timedelta(hours=1)).phase,
            MaintenancePhase.NORMAL)

        completed_late = window(
            status="completed", completed_at=END + timedelta(minutes=20))
        self.assertEqual(
            completed_late.resume_at, END + timedelta(hours=1, minutes=20))

    def test_history_probe_keeps_only_exact_production_component(self):
        preprod = production_row()
        preprod["id"] = "preprod"
        preprod["components"] = [{
            "id": "brsqlltp7mtk",
            "name": "[Pre-production] Polymarket US Exchange",
        }]

        with patch(
                "liquidity_bot.maintenance_schedule._read_json",
                return_value={
                    "scheduled_maintenances": [preprod, production_row()],
                }) as read:
            result = probe_polymarket_us_maintenance_schedule(
                include_history=True, now=START)

        self.assertEqual(result.state, MaintenanceFeedState.HEALTHY)
        self.assertEqual(
            [item.maintenance_id for item in result.windows],
            ["maintenance-one"])
        read.assert_called_once_with(MAINTENANCE_HISTORY_URL)

    def test_missing_known_window_requires_exact_completed_detail(self):
        completed_at = END + timedelta(minutes=7)

        def response(url):
            if url in {MAINTENANCE_UPCOMING_URL, MAINTENANCE_ACTIVE_URL}:
                return {"scheduled_maintenances": []}
            return {"scheduled_maintenance": production_row(
                status="completed", completed_at=completed_at)}

        with patch(
                "liquidity_bot.maintenance_schedule._read_json",
                side_effect=response) as read:
            result = probe_polymarket_us_maintenance_schedule(
                (window(),), now=END + timedelta(minutes=8))

        self.assertEqual(result.state, MaintenanceFeedState.HEALTHY)
        self.assertEqual(result.windows[0].status, "completed")
        self.assertEqual(result.windows[0].completed_at, completed_at)
        self.assertEqual(read.call_count, 3)

    def test_probe_failure_preserves_known_window_as_unknown(self):
        known = window()
        with patch(
                "liquidity_bot.maintenance_schedule._read_json",
                side_effect=TimeoutError("status unavailable")):
            result = probe_polymarket_us_maintenance_schedule(
                (known,), now=START)

        self.assertEqual(result.state, MaintenanceFeedState.UNKNOWN)
        self.assertEqual(result.windows, (known,))

    def test_malformed_production_timestamp_makes_whole_probe_unknown(self):
        malformed = production_row()
        malformed["scheduled_for"] = "not-a-timestamp"
        with patch(
                "liquidity_bot.maintenance_schedule._read_json",
                return_value={"scheduled_maintenances": [malformed]}):
            result = probe_polymarket_us_maintenance_schedule(
                include_history=True, now=START)

        self.assertEqual(result.state, MaintenanceFeedState.UNKNOWN)
        self.assertEqual(result.windows, ())

    def test_cache_round_trip_and_malformed_cache_fail_closed_to_empty(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "schedule.json"
            complete = window(
                status="completed", completed_at=END + timedelta(minutes=2))
            write_maintenance_cache((complete,), END, path)
            self.assertEqual(load_maintenance_cache(path), (complete,))

            path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            self.assertEqual(load_maintenance_cache(path), ())


if __name__ == "__main__":
    unittest.main()
