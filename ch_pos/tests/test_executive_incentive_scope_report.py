"""Regression checks for Executive Incentive Statement scope enforcement.

Run:
    bench --site erpnext.local execute ch_pos.tests.test_executive_incentive_scope_report.run
"""

from __future__ import annotations

import frappe

from ch_pos.pos_core.report.executive_incentive_statement import executive_incentive_statement as report


def run():
    orig_get_roles = frappe.get_roles
    orig_current_exec = report._current_user_executive
    orig_scope_get = report._scope_get_user_scope
    orig_scope_intersect = report._scope_intersect_filters

    try:
        # Case 1: scoped hierarchy user (ASM/ZSM pattern) should be restricted by stores.
        frappe.get_roles = lambda user=None: ["CH Area Sales Manager"]
        report._scope_get_user_scope = lambda user=None: {
            "bypass": False,
            "stores": {"STORE-001", "STORE-002"},
        }
        report._scope_intersect_filters = lambda **kwargs: {
            "allowed_stores": ["STORE-001"],
        }

        cond, values = report._build_conditions({
            "from_date": "2026-06-01",
            "to_date": "2026-06-30",
            "store": "STORE-001",
        })

        if "il.store IN %(allowed_stores)s" not in cond:
            raise AssertionError("Scope-based store restriction was not applied")
        if values.get("allowed_stores") != ("STORE-001",):
            raise AssertionError("Scope-based allowed stores were not set correctly")

        print("[PASS] Scoped hierarchy user is restricted by CH User Scope stores")

        # Case 2: no scope API available/fallback -> restrict by linked POS Executive.
        frappe.get_roles = lambda user=None: ["POS User"]
        report._scope_get_user_scope = lambda user=None: (_ for _ in ()).throw(ImportError("scope unavailable"))
        report._current_user_executive = lambda: "POSEXEC-001"

        cond2, values2 = report._build_conditions({
            "from_date": "2026-06-01",
            "to_date": "2026-06-30",
        })

        if "il.pos_executive = %(own_exec)s" not in cond2:
            raise AssertionError("Fallback own-executive restriction was not applied")
        if values2.get("own_exec") != "POSEXEC-001":
            raise AssertionError("Fallback own-executive value mismatch")

        print("[PASS] Non-scoped user falls back to own POS Executive restriction")
        print("Executive Incentive scope regression: ALL PASS")

    finally:
        frappe.get_roles = orig_get_roles
        report._current_user_executive = orig_current_exec
        report._scope_get_user_scope = orig_scope_get
        report._scope_intersect_filters = orig_scope_intersect
