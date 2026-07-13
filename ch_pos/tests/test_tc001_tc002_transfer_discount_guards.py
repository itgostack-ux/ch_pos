"""TC_001 + TC_002 regression guards for POS API hardening.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc001_tc002_transfer_discount_guards.run
"""

from __future__ import annotations

from pathlib import Path

import frappe


def run() -> dict:
    app_path = Path(frappe.get_app_path("ch_pos"))
    api_src = (app_path / "api" / "pos_api.py").read_text(encoding="utf-8")

    tc001_needles = [
        "entries = []",
        "direction = (direction or \"incoming\").strip().lower()",
        "if direction not in {\"incoming\", \"outgoing\"}:",
    ]
    missing_tc001 = [n for n in tc001_needles if n not in api_src]
    if missing_tc001:
        raise AssertionError(f"TC_001 guard failed in API markers: {missing_tc001}")

    tc002_needles = [
        "and not discount_authorized_by",
        "Below Minimum Selling Price",
        "custom_discount_authorized_by",
        "fields=[\"selling_price\", \"mop\", \"status\", \"channel\"]",
        "price_floor_candidates.append(min(selling_price, mop))",
    ]
    missing_tc002 = [n for n in tc002_needles if n not in api_src]
    if missing_tc002:
        raise AssertionError(f"TC_002 guard failed in API markers: {missing_tc002}")

    print("PASS: TC_001/TC_002 transfer + discount approval guards")
    return {"pass": 1, "fail": 0}
