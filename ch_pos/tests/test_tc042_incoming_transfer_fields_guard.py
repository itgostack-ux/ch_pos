"""TC_042 regression guard: Incoming Transfers must expose warehouses + item names.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc042_incoming_transfer_fields_guard.run
"""

from __future__ import annotations

from pathlib import Path

import frappe


def run() -> dict:
    app_path = Path(frappe.get_app_path("ch_pos"))
    api_src = (app_path / "api" / "pos_api.py").read_text(encoding="utf-8")
    ui_src = (app_path / "public" / "js" / "pos_app" / "modules" / "stock_transfer" / "stock_transfer_workspace.js").read_text(encoding="utf-8")

    api_needles = [
        "AS from_warehouse",
        "AS to_warehouse",
        "AS primary_item_name",
        "AS primary_item_code",
        "AS additional_item_count",
    ]
    missing_api = [n for n in api_needles if n not in api_src]
    if missing_api:
        raise AssertionError(f"TC_042 guard failed in API markers: {missing_api}")

    ui_needles = [
        "se.primary_item_name || se.primary_item_code",
        "additional_item_count",
        "${__(\"From\")}",
        "${__(\"To\")}",
    ]
    missing_ui = [n for n in ui_needles if n not in ui_src]
    if missing_ui:
        raise AssertionError(f"TC_042 guard failed in UI markers: {missing_ui}")

    print("PASS: TC_042 Incoming Transfers warehouses + item names guard")
    return {"pass": 1, "fail": 0}
