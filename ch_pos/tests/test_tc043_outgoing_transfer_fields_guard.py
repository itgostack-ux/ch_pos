"""TC_043 regression guard: Outgoing Transfers must expose warehouses + item names.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc043_outgoing_transfer_fields_guard.run
"""

from __future__ import annotations

from pathlib import Path

import frappe


def run() -> dict:
    app_path = Path(frappe.get_app_path("ch_pos"))
    api_src = (app_path / "api" / "pos_api.py").read_text(encoding="utf-8")
    ui_src = (app_path / "public" / "js" / "pos_app" / "modules" / "stock_transfer" / "stock_transfer_workspace.js").read_text(encoding="utf-8")

    # API must include both direction branches with warehouse + item metadata.
    required_api = [
        "direction=\"incoming\"",
        "se.from_warehouse = %s OR EXISTS",
        "se.to_warehouse = %s OR EXISTS",
        "AS from_warehouse",
        "AS to_warehouse",
        "AS primary_item_name",
        "AS primary_item_code",
    ]
    missing_api = [n for n in required_api if n not in api_src]
    if missing_api:
        raise AssertionError(f"TC_043 guard failed in API markers: {missing_api}")

    # Shared row renderer must show item label + from/to chips.
    required_ui = [
        "_transfer_row(se, tab)",
        "se.primary_item_name || se.primary_item_code",
        "${__(\"From\")}",
        "${__(\"To\")}",
    ]
    missing_ui = [n for n in required_ui if n not in ui_src]
    if missing_ui:
        raise AssertionError(f"TC_043 guard failed in UI markers: {missing_ui}")

    print("PASS: TC_043 Outgoing Transfers warehouses + item names guard")
    return {"pass": 1, "fail": 0}
