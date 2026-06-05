"""
TC_013 / TC_015 / TC_031 regression guards.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc013_tc015_tc031_guards.run
"""

from __future__ import annotations

from pathlib import Path

import frappe


PASS = 0
FAIL = 0
RESULTS: list[tuple[str, str, str]] = []


def _ok(tid: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    RESULTS.append(("PASS", tid, detail))
    print(f"  PASS {tid}: {detail}" if detail else f"  PASS {tid}")


def _fail(tid: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    RESULTS.append(("FAIL", tid, detail))
    print(f"  FAIL {tid}: {detail}" if detail else f"  FAIL {tid}")


def _read(rel_path: str) -> str:
    base = Path(frappe.get_app_path("ch_pos"))
    return (base / rel_path).read_text(encoding="utf-8")


def tc013_state_dropdown_filter() -> None:
    tid = "TC_013"
    src = _read("public/js/ch_customer_dialog.js")

    checks = {
        "state_country_filter": 'get_query: () => ({ filters: { disabled: 0, country: "India" } })',
        "shipping_state_country_filter": 'depends_on: "eval:!doc.same_as_billing", get_query: () => ({ filters: { disabled: 0, country: "India" } })',
    }

    missing = [name for name, needle in checks.items() if needle not in src]
    if missing:
        _fail(tid, f"Missing India-scoped CH State query markers: {', '.join(missing)}")
        return

    _ok(tid, "State and Shipping State dropdowns are scoped to active Indian states")


def tc015_validation_modal_z_index() -> None:
    tid = "TC_015"
    src = _read("public/css/pos_components.css")

    checks = {
        "overlay_z": ".ch-pay-overlay {",
        "overlay_depth": "z-index: 9990;",
        "backdrop_z": ".modal-backdrop {",
        "backdrop_depth": "z-index: 9995 !important;",
        "modal_depth": ".modal {",
        "modal_z": "z-index: 9996 !important;",
        "modal_open_suppression": "body.modal-open .ch-pay-overlay {",
    }

    missing = [name for name, needle in checks.items() if needle not in src]
    if missing:
        _fail(tid, f"Missing modal stacking markers: {', '.join(missing)}")
        return

    _ok(tid, "Payment overlay keeps modals above it and suppresses interception while modals are open")


def tc031_sale_type_no_duplicate_visible_field() -> None:
    tid = "TC_031"
    cart_src = _read("public/js/pos_app/shared/cart_panel.js")
    payment_src = _read("public/js/pos_app/shared/payment_dialog.js")

    checks = {
        "cart_visible_sale_type": 'id="ch-pos-cart-saletype"',
        "cart_sale_type_comment": "Sale Type pills (#15 — relocated from payment dialog",
        "payment_hidden_engine": 'id="ch-pay-sale-type-engine" style="display:none"',
        "payment_hidden_note": "Sale type is selected in cart_panel.js.",
    }

    haystack = cart_src + "\n" + payment_src
    missing = [name for name, needle in checks.items() if needle not in haystack]
    if missing:
        _fail(tid, f"Missing sale-type separation markers: {', '.join(missing)}")
        return

    _ok(tid, "Cart panel owns the visible Sale Type control and the payment overlay keeps only a hidden engine")


def run() -> dict:
    global PASS, FAIL, RESULTS
    PASS = 0
    FAIL = 0
    RESULTS = []

    print("\n=== TC_013 / TC_015 / TC_031 Guards ===\n")
    tc013_state_dropdown_filter()
    tc015_validation_modal_z_index()
    tc031_sale_type_no_duplicate_visible_field()

    print(f"\n  Summary: {PASS} pass / {FAIL} fail")
    if FAIL:
        raise AssertionError(f"{FAIL} guard(s) failed")
    return {"pass": PASS, "fail": FAIL}
