"""
Regression guard for POS VAS/buyback frontend reflection.

Run:
  cd /home/palla/erpnext-bench
  bench --site erpnext.local execute ch_pos.tests.test_pos_vas_buyback_asset_reflection.run
"""

from pathlib import Path

import frappe


def _app_path(*parts):
    return Path(frappe.get_app_path("ch_pos")).joinpath(*parts)


def run():
    bundle = _app_path("public", "js", "ch_pos.bundle.js").read_text()
    buyback = _app_path(
        "public", "js", "pos_app", "modules", "buyback", "buyback_workspace.js"
    ).read_text()
    sw = _app_path("www", "pos-sw.js").read_text()

    assert "cart_service.js?v=20260618a" in bundle, (
        "POS bundle must cache-bust CartService so VAS changes reach browsers"
    )
    assert "buyback_workspace.js?v=20260618a" in bundle, (
        "POS bundle must cache-bust BuybackWorkspace so approval/payout UI changes reach browsers"
    )
    assert 'const SHELL_CACHE = "ch-pos-shell-v4";' in sw, (
        "POS service worker cache version must be bumped when shell/module imports change"
    )

    for token in (
        "customer_approval_method",
        "approval_date",
        "approval_remarks",
        "customer_payout_mode",
        "customer_payout_updated_at",
        "kyc_verified",
        "_html_order_summary",
    ):
        assert token in buyback, f"Buyback POS UI does not reference {token}"

    print("PASS: POS VAS/buyback asset reflection guard")

