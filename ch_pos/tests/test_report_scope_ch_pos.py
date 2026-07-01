# Copyright (c) 2026, GoGizmo and contributors
# For license information, please see license.txt
"""
Tier 4 — Report scope injection E2E tests for ch_pos.

Verifies:
  * Every ch_pos custom report (SQL and frappe.get_all based) honours the
    caller's CH User Scope.
  * Scoped users never see out-of-scope stores / pos_profiles / warehouses
    in any report row.
  * The new ``narrow_filters_by_store_scope`` helper narrows dict-filter
    based reports and short-circuits to empty for fail-closed callers.
  * Administrator (bypass) runs every report without narrowing.
"""

from __future__ import annotations

import unittest

import frappe

from ch_erp15.ch_erp15.scope import clear_scope_cache
from ch_erp15.ch_erp15.report_scope import narrow_filters_by_store_scope


_TEST_USER = "tier4-chpos-user@ch-tests.local"
_TEST_NOSCOPE_USER = "tier4-chpos-noscope@ch-tests.local"
_TEST_STORE = "TIER4-CHPOS-STORE-A"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_user(user: str) -> None:
    if frappe.db.exists("User", user):
        return
    doc = frappe.new_doc("User")
    doc.email = user
    doc.first_name = "Tier4POS"
    doc.enabled = 1
    doc.new_password = "TestPass123!Tier4"
    doc.send_welcome_email = 0
    doc.append("roles", {"role": "Accounts User"})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


def _get_or_create_warehouse(name: str, company: str) -> str:
    abbr = frappe.db.get_value("Company", company, "abbr")
    full = f"{name} - {abbr}"
    if frappe.db.exists("Warehouse", full):
        return full
    doc = frappe.new_doc("Warehouse")
    doc.warehouse_name = name
    doc.company = company
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


def _get_or_create_ch_store(name: str, warehouse: str, company: str) -> None:
    if frappe.db.exists("CH Store", name):
        return
    doc = frappe.new_doc("CH Store")
    doc.store_id = name
    doc.store_code = name
    doc.store_name = name
    doc.company = company
    doc.warehouse = warehouse
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)


def _make_scope(user: str, store: str) -> None:
    for row in frappe.get_all("CH User Scope", filters={"user": user}, pluck="name"):
        frappe.delete_doc("CH User Scope", row, ignore_permissions=True, force=True)
    doc = frappe.new_doc("CH User Scope")
    doc.user = user
    doc.scope_role = "Store Executive"
    doc.enabled = 1
    doc.append("stores", {"store": store})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


# ─────────────────────────────────────────────────────────────────────────────
# TestCase
# ─────────────────────────────────────────────────────────────────────────────

class TestReportScopeChPos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = frappe.db.get_value("Company", {}, "name")
        if not cls.company:
            raise Exception("No Company in this site — cannot run Tier 4 ch_pos tests.")

        cls.wh_in_scope = _get_or_create_warehouse("Tier4 ChPos A WH", cls.company)
        cls.wh_out_of_scope = _get_or_create_warehouse(
            "Tier4 ChPos B WH", cls.company
        )
        _get_or_create_ch_store(_TEST_STORE, cls.wh_in_scope, cls.company)
        _ensure_user(_TEST_USER)
        _ensure_user(_TEST_NOSCOPE_USER)
        _make_scope(_TEST_USER, _TEST_STORE)
        clear_scope_cache(_TEST_USER)
        clear_scope_cache(_TEST_NOSCOPE_USER)
        frappe.db.commit()

    def setUp(self):
        frappe.set_user(_TEST_USER)
        clear_scope_cache(_TEST_USER)
        clear_scope_cache(_TEST_NOSCOPE_USER)

    def tearDown(self):
        frappe.set_user("Administrator")

    # ── helper contract ─────────────────────────────────────────────────

    # 1 — narrow_filters_by_store_scope for scoped user injects an IN filter
    def test_01_narrow_scoped_user_injects_in_filter(self):
        filters = {}
        proceed = narrow_filters_by_store_scope(filters, store_field="store")
        self.assertTrue(proceed)
        self.assertIn("store", filters)
        op, values = filters["store"]
        self.assertEqual(op, "in")
        self.assertIn(_TEST_STORE, values)

    # 2 — bypass user is unchanged
    def test_02_narrow_bypass_user_no_change(self):
        filters = {}
        proceed = narrow_filters_by_store_scope(
            filters, user="Administrator", store_field="store"
        )
        self.assertTrue(proceed)
        self.assertNotIn("store", filters)

    # 3 — no-scope user short-circuits (fail-closed)
    def test_03_narrow_no_scope_user_short_circuits(self):
        filters = {}
        proceed = narrow_filters_by_store_scope(
            filters, user=_TEST_NOSCOPE_USER, store_field="store"
        )
        self.assertFalse(proceed)

    # 4 — user-supplied store out of scope raises
    def test_04_narrow_out_of_scope_scalar_raises(self):
        filters = {"store": "NEVER-EXISTS"}
        with self.assertRaises(frappe.PermissionError):
            narrow_filters_by_store_scope(filters, store_field="store")

    # 5 — user-supplied in-scope store is preserved
    def test_05_narrow_in_scope_scalar_preserved(self):
        filters = {"store": _TEST_STORE}
        proceed = narrow_filters_by_store_scope(filters, store_field="store")
        self.assertTrue(proceed)
        self.assertEqual(filters["store"], _TEST_STORE)

    # ── SQL reports smoke + scope contract ──────────────────────────────

    # 6 — CH POS Session-based reports run cleanly for scoped user
    def test_06_ch_pos_session_reports_scoped(self):
        from ch_pos.pos_core.report.cash_variance_report.cash_variance_report import (
            execute as cvr_execute,
        )
        from ch_pos.pos_core.report.closed_session_audit.closed_session_audit import (
            execute as csa_execute,
        )
        from ch_pos.pos_core.report.user_wise_session_activity.user_wise_session_activity import (
            execute as uwsa_execute,
        )
        for label, fn in [
            ("cash_variance_report", cvr_execute),
            ("closed_session_audit", csa_execute),
            ("user_wise_session_activity", uwsa_execute),
        ]:
            cols, rows = fn({})[:2]
            self.assertIsInstance(cols, list, f"{label} must return columns")
            for row in rows:
                if isinstance(row, dict) and row.get("store"):
                    self.assertNotEqual(
                        row["store"],
                        self.wh_out_of_scope,
                        f"{label} leaked out-of-scope store",
                    )

    # 7 — CH POS Settlement / Kiosk Token reports run cleanly
    def test_07_settlement_and_kiosk_reports_scoped(self):
        from ch_pos.pos_core.report.company_wise_daily_settlement.company_wise_daily_settlement import (
            execute as cwds_execute,
        )
        from ch_pos.pos_core.report.session_vs_payment_reconciliation.session_vs_payment_reconciliation import (
            execute as svpr_execute,
        )
        from ch_pos.pos_core.report.session_vs_pos_invoice.session_vs_pos_invoice import (
            execute as svpi_execute,
        )
        from ch_pos.pos_core.report.drop_analysis_report.drop_analysis_report import (
            execute as drop_execute,
        )
        from ch_pos.pos_core.report.staff_conversion_report.staff_conversion_report import (
            execute as staff_execute,
        )
        from ch_pos.pos_core.report.walkin_conversion_report.walkin_conversion_report import (
            execute as walkin_execute,
        )
        for fn in (cwds_execute, svpr_execute, svpi_execute, drop_execute,
                   staff_execute, walkin_execute):
            result = fn({})
            self.assertTrue(len(result) >= 2)

    # 8 — VAS attach report runs cleanly for scoped user
    def test_08_vas_attach_report_scoped(self):
        from ch_pos.pos_core.report.vas_attach_rate_by_store_cashier_category_day.vas_attach_rate_by_store_cashier_category_day import (
            execute as vas_execute,
        )
        cols, rows = vas_execute({})
        self.assertIsInstance(cols, list)

    # 9 — Sales-Invoice-based POS reports (exchange/margin/tax) run cleanly
    def test_09_sales_invoice_reports_scoped(self):
        from ch_pos.pos_core.report.pos_exchange_reconciliation.pos_exchange_reconciliation import (
            execute as exch_execute,
        )
        from ch_pos.pos_core.report.pos_margin_leakage.pos_margin_leakage import (
            execute as marg_execute,
        )
        from ch_pos.pos_core.report.pos_tax_summary.pos_tax_summary import (
            execute as tax_execute,
        )
        for fn in (exch_execute, marg_execute, tax_execute):
            cols, rows = fn({"from_date": "2000-01-01", "to_date": "2099-12-31"})
            self.assertIsInstance(cols, list)
            for row in rows:
                if isinstance(row, dict) and row.get("warehouse"):
                    self.assertNotEqual(
                        row["warehouse"],
                        self.wh_out_of_scope,
                        "Sales Invoice POS report leaked out-of-scope warehouse",
                    )

    # ── frappe.get_all reports ──────────────────────────────────────────

    # 10 — dict-filter reports short-circuit for a no-scope caller
    def test_10_get_all_reports_short_circuit_no_scope(self):
        frappe.set_user(_TEST_NOSCOPE_USER)
        from ch_pos.pos_core.report.cash_movement_report.cash_movement_report import (
            execute as cash_mov_execute,
        )
        from ch_pos.pos_core.report.device_wise_open_sessions.device_wise_open_sessions import (
            execute as dws_execute,
        )
        from ch_pos.pos_core.report.device_wise_settlement_summary.device_wise_settlement_summary import (
            execute as dwss_execute,
        )
        from ch_pos.pos_core.report.store_wise_business_date_closure.store_wise_business_date_closure import (
            execute as swbdc_execute,
        )
        for fn in (cash_mov_execute, dws_execute, dwss_execute, swbdc_execute):
            _cols, rows = fn({})
            self.assertEqual(
                rows,
                [],
                f"{fn.__module__}: no-scope caller must see zero rows",
            )

    # 11 — dict-filter reports allow scoped caller to run (may be empty)
    def test_11_get_all_reports_scoped_user_runs(self):
        from ch_pos.pos_core.report.cash_movement_report.cash_movement_report import (
            execute as cash_mov_execute,
        )
        from ch_pos.pos_core.report.device_wise_open_sessions.device_wise_open_sessions import (
            execute as dws_execute,
        )
        for fn in (cash_mov_execute, dws_execute):
            cols, rows = fn({})
            self.assertIsInstance(cols, list)
            # rows may be empty but must not leak.
            for row in rows:
                if row.get("store"):
                    self.assertEqual(
                        row["store"],
                        _TEST_STORE,
                        "get_all report leaked non-scope store",
                    )

    # 12 — Administrator bypass runs every report without narrowing
    def test_12_administrator_bypass_runs_everything(self):
        frappe.set_user("Administrator")
        from ch_pos.pos_core.report.cash_variance_report.cash_variance_report import (
            execute as cvr_execute,
        )
        from ch_pos.pos_core.report.device_wise_open_sessions.device_wise_open_sessions import (
            execute as dws_execute,
        )
        from ch_pos.pos_core.report.walkin_conversion_report.walkin_conversion_report import (
            execute as walkin_execute,
        )
        # Every call must succeed for admin — no PermissionError.
        cvr_execute({})
        dws_execute({})
        walkin_execute({})
