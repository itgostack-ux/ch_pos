"""TC_036 regression guard: Pickup/Bill must hydrate serial bundle for reserved items.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc036_pickup_serial_bundle_guard.run
"""

from __future__ import annotations

from pathlib import Path

import frappe


def run() -> dict:
    path = Path(frappe.get_app_path("ch_pos")) / "api" / "pos_api.py"
    src = path.read_text(encoding="utf-8")

    must_have = [
        'sre.reservation_based_on == "Serial and Batch"',
        "bundle = get_ssb_bundle_for_voucher(sre)",
        "it.serial_and_batch_bundle = (",
    ]
    for needle in must_have:
        if needle not in src:
            raise AssertionError(f"TC_036 guard failed: missing `{needle}`")

    forbidden = [
        "not use_serial_batch_fields",
    ]
    for needle in forbidden:
        if needle in src:
            raise AssertionError(f"TC_036 guard failed: stale condition present `{needle}`")

    print("PASS: TC_036 pickup serial-bundle hydration guard")
    return {"pass": 1, "fail": 0}
