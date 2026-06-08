"""TC_015 regression guard: POS Billing must auto-sync Executive Name when
POS Executive changes.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc015_pos_executive_name_guard.run
"""

from __future__ import annotations

from pathlib import Path

import frappe


def run() -> dict:
    app_path = Path(frappe.get_app_path("ch_pos"))
    src = (app_path / "public" / "js" / "pos_app" / "shared" / "payment_dialog.js").read_text(encoding="utf-8")

    required = [
        'EventBus.on("executive:changed", () => this._sync_executive_badge());',
        'EventBus.on("profile:loaded", () => this._sync_executive_badge());',
        'this._sync_executive_badge();',
        '_sync_executive_badge() {',
        '.ch-pay-exec-tag',
        'this._update_discount_auth_ui();',
    ]
    missing = [marker for marker in required if marker not in src]
    if missing:
        raise AssertionError(f"TC_015 executive-name guard failed: {missing}")

    print("PASS: TC_015 POS executive name auto-sync guard")
    return {"pass": 1, "fail": 0}
