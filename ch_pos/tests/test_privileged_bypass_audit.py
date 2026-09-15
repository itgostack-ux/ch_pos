"""Privileged-bypass audit trail.

Every gate an Administrator / System Manager is permitted to skip must leave a
"Privileged Bypass" row in CH Business Audit Log. Without it the bypass is
invisible: a privileged login can open a till it has no POS Executive record
for, settle another cashier's session, approve its own cash drop and re-stamp
sealed approval state, and nothing in the trail records that the identity
checks never ran.

These tests pin the instrumentation itself, because the wrapper is
best-effort by design (a logging failure must never break the gate it watches)
— which means a regression here fails silently in production.
"""

import inspect
import json
import os
import unittest
from unittest.mock import patch

import frappe

from ch_pos import audit
from ch_pos.api import offline_sync, scope_guard
from ch_pos.pos_core.doctype.ch_cash_drop import ch_cash_drop
from ch_pos.pos_core.doctype.ch_free_sale_approval import ch_free_sale_approval
from ch_pos.pos_core.doctype.ch_manager_pin import ch_manager_pin
from ch_pos.pos_core.doctype.ch_pos_session import ch_pos_session
from ch_pos.pos_core.doctype.ch_pos_settlement import ch_pos_settlement

#: (module, gate label). The labels are this audit trail's vocabulary — renaming
#: one silently breaks every report and query filtering on it, so they are
#: pinned here deliberately.
INSTRUMENTED_GATES = (
    (scope_guard, "store_scope"),
    (scope_guard, "any_warehouse_scope"),
    (scope_guard, "pos_executive_till_access"),
    (ch_pos_session, "session_opening_approval"),
    (ch_pos_session, "pos_executive_allocation"),
    (ch_manager_pin, "manager_pin_store_match"),
    (ch_pos_settlement, "settlement_session_scope"),
    (ch_pos_settlement, "settlement_manager_signoff_verification"),
    (ch_pos_settlement, "settlement_signature_restamp"),
    (ch_pos_settlement, "settlement_signoff_mismatch"),
    (ch_cash_drop, "cash_drop_session_scope"),
    (ch_cash_drop, "cash_drop_self_approval"),
    (ch_free_sale_approval, "free_sale_sealed_field_override"),
    (offline_sync, "customer_catalog_no_profile"),
)


class TestPrivilegedBypassAudit(unittest.TestCase):
    def test_every_instrumented_gate_still_logs(self):
        for module, gate in INSTRUMENTED_GATES:
            source = inspect.getsource(module)
            self.assertIn(
                "log_privileged_bypass",
                source,
                f"{module.__name__} no longer logs privileged bypasses at all",
            )
            self.assertIn(
                f'"{gate}"',
                source,
                f"{module.__name__} no longer logs the {gate} gate",
            )

    def test_wrapper_forwards_a_privileged_bypass_event(self):
        with patch.object(audit, "log_business_event") as logged:
            audit.log_privileged_bypass(
                "some_gate", user="admin@example.com", store="ST-1", company="Co"
            )

        logged.assert_called_once()
        kwargs = logged.call_args.kwargs
        self.assertEqual(kwargs["event_type"], "Privileged Bypass")
        self.assertEqual(kwargs["user"], "admin@example.com")
        self.assertEqual(kwargs["store"], "ST-1")
        self.assertEqual(kwargs["company"], "Co")
        self.assertEqual(kwargs["remarks"], "some_gate")

    def test_acting_session_survives_in_remarks(self):
        """The bypassing identity and the acting session can differ — a cashier's
        till calling verify_manager_pin resolves a privileged *manager*. Both
        must stay recoverable from one row."""
        with patch.object(audit, "log_business_event") as logged:
            audit.log_privileged_bypass(
                "manager_pin_store_match",
                user="mgr@example.com",
                remarks="acting session: cashier@example.com",
            )

        kwargs = logged.call_args.kwargs
        self.assertEqual(kwargs["user"], "mgr@example.com")
        self.assertIn("manager_pin_store_match", kwargs["remarks"])
        self.assertIn("cashier@example.com", kwargs["remarks"])

    def test_doctype_ships_the_event_type(self):
        import ch_pos

        path = os.path.join(
            os.path.dirname(ch_pos.__file__),
            "pos_core",
            "doctype",
            "ch_business_audit_log",
            "ch_business_audit_log.json",
        )
        with open(path) as handle:
            doctype = json.load(handle)

        event_type = next(
            field for field in doctype["fields"] if field["fieldname"] == "event_type"
        )
        self.assertIn("Privileged Bypass", event_type["options"].split("\n"))


class TestPrivilegedBypassAuditLive(unittest.TestCase):
    """Proves the Select option actually reached the database.

    A value missing from the migrated options fails Frappe's Select validation,
    and `log_business_event` swallows that — so only a real insert proves the
    trail works end to end.
    """

    def setUp(self):
        if not getattr(frappe, "db", None):
            raise unittest.SkipTest("no database connection")

    def tearDown(self):
        frappe.db.rollback()

    def test_bypass_row_is_written(self):
        filters = {"event_type": "Privileged Bypass"}
        before = frappe.db.count("CH Business Audit Log", filters)

        audit.log_privileged_bypass(
            "test_gate", user=frappe.session.user, remarks="privileged bypass audit test"
        )

        self.assertEqual(
            frappe.db.count("CH Business Audit Log", filters),
            before + 1,
            "No Privileged Bypass row was written — has the doctype been migrated?",
        )


class TestBypassThrottle(unittest.TestCase):
    """The read-path gates run on every request; the money gates do not.

    assert_store_scope fires on essentially every POS call, so logging one row
    per call buried the rare, deliberate bypasses under thousands of identical
    rows — 61 of the first 65 rows observed on this bench were the same
    store_scope entry. Throttling fixes that, but it must never reach a money
    or identity event, where every single occurrence is the record.
    """

    def setUp(self):
        self.written = []

        def _capture(**kwargs):
            self.written.append(kwargs)

        patcher = patch.object(audit, "log_business_event", _capture)
        patcher.start()
        self.addCleanup(patcher.stop)
        frappe.cache().delete_keys("ch_pos:bypass:")

    def test_throttled_gate_logs_once_per_user_store_day(self):
        for _ in range(5):
            audit.log_privileged_bypass(
                "store_scope", user="throttle@test", store="ST-1", throttle=True
            )
        self.assertEqual(len(self.written), 1)

    def test_throttle_is_per_store_not_global(self):
        audit.log_privileged_bypass("store_scope", user="t@test", store="ST-1", throttle=True)
        audit.log_privileged_bypass("store_scope", user="t@test", store="ST-2", throttle=True)
        self.assertEqual(len(self.written), 2)

    def test_money_events_are_never_collapsed(self):
        """Default is un-throttled: three cash-drop self-approvals are three rows."""
        for _ in range(3):
            audit.log_privileged_bypass(
                "cash_drop_self_approval", user="mgr@test", store="ST-1"
            )
        self.assertEqual(len(self.written), 3)

    def test_a_dead_cache_still_logs(self):
        """A redis fault must cost a duplicate row, never the audit trail."""
        with patch.object(frappe, "cache", side_effect=RuntimeError("redis down")):
            audit.log_privileged_bypass(
                "store_scope", user="t@test", store="ST-1", throttle=True
            )
        self.assertEqual(len(self.written), 1)
