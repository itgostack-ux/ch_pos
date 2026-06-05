"""TC_041 regression guard: Claims pipeline must stay VAS-only.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc041_claims_vas_filter_guard.run
"""

from __future__ import annotations

from pathlib import Path

import frappe


def run() -> dict:
    path = Path(frappe.get_app_path("ch_pos")) / "public" / "js" / "pos_app" / "modules" / "claims" / "claims_workspace.js"
    src = path.read_text(encoding="utf-8")

    required = [
        'this._claim_filter = "vas";',
        'const VAS_TYPES = ["vas_plan", "anniversary_warranty", "repair_warranty"]',
        'filters.coverage_type = ["in", VAS_TYPES];',
        'data-filter="vas"',
    ]
    for needle in required:
        if needle not in src:
            raise AssertionError(f"TC_041 guard failed. Missing marker: {needle}")

    forbidden = [
        'data-filter="all"',
        'data-filter="manufacturer"',
        'filters.coverage_type = "manufacturer_warranty"',
    ]
    for needle in forbidden:
        if needle in src:
            raise AssertionError(f"TC_041 guard failed. Forbidden marker present: {needle}")

    print("PASS: TC_041 Claims VAS-only filter guard")
    return {"pass": 1, "fail": 0}
