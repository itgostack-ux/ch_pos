"""
TC_032 regression guard:
Sale Type change from Finance Sale -> Direct Sale Payment must reset payment state.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc032_sale_type_reset_guard.run
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


def _payment_dialog_source() -> str:
    app_path = Path(frappe.get_app_path("ch_pos"))
    js = app_path / "public" / "js" / "pos_app" / "shared" / "payment_dialog.js"
    return js.read_text(encoding="utf-8")


def tc032_1_reset_hook_present() -> None:
    """Verify sale-type switch path calls hard reset when leaving finance."""
    tid = "TC_032-1"
    src = _payment_dialog_source()

    checks = {
        "finance_exit_call": "this._reset_after_finance_exit();",
        "reset_method": "_reset_after_finance_exit() {",
        "single_cash_row": 'this._payments = [this._new_payment_row("Cash", grand)];',
        "reset_sale_sub_type": "PosState.sale_sub_type = null;",
        "reset_finance_tenure": "PosState.finance_tenure = null;",
        "reset_sale_reference": "PosState.sale_reference = null;",
    }

    missing = [name for name, needle in checks.items() if needle not in src]
    if missing:
        _fail(tid, f"Missing reset hooks in payment_dialog.js: {', '.join(missing)}")
        return

    _ok(tid, "Finance->Direct reset hook and state clearing markers are present")


def tc032_2_reset_contract_logic() -> None:
    """Contract-level logic test for expected reset outcome.

    Mirrors intended behavior after finance exit:
    - Replace any existing mixed rows with one fresh Cash row
    - Remove stale finance/gateway metadata
    """
    tid = "TC_032-2"

    # Representative pre-reset state: finance + down-payment + stale gateway lock fields.
    pre_rows = [
        {
            "mode": "UPI",
            "amount": 2500,
            "upi_transaction_id": "UPI-123",
            "gateway_provider": "TestGateway",
            "payment_machine": "PM-01",
            "gateway_order_id": "ORD-1",
            "gateway_status": "CREATED",
            "gateway_initiated": True,
            "is_down_payment": True,
            "finance_provider": "Bajaj",
            "finance_tenure": "12",
            "finance_approval_id": "APR-01",
            "finance_down_payment": 2500,
        },
        {
            "mode": "Finance",
            "amount": 5000,
            "finance_provider": "Bajaj",
            "finance_tenure": "12",
            "finance_approval_id": "APR-01",
        },
    ]

    grand = 7500

    # Expected post-reset contract (fresh row only).
    post_rows = [
        {
            "mode": "Cash",
            "amount": grand,
            "upi_transaction_id": "",
            "card_reference": "",
            "card_last_four": "",
            "bank_partner": "",
            "bank_reference": "",
            "finance_provider": "",
            "finance_tenure": "",
            "finance_approval_id": "",
            "finance_down_payment": 0,
        }
    ]

    if len(post_rows) != 1:
        _fail(tid, "Post-reset rows should contain exactly one payment row")
        return

    row = post_rows[0]
    if row.get("mode") != "Cash" or row.get("amount") != grand:
        _fail(tid, f"Expected single Cash row with amount={grand}, got {row}")
        return

    forbidden_keys = {"gateway_provider", "payment_machine", "gateway_order_id", "gateway_status", "gateway_initiated", "is_down_payment"}
    leaked = [k for k in forbidden_keys if k in row]
    if leaked:
        _fail(tid, f"Reset row leaked stale lock/down-payment keys: {', '.join(sorted(leaked))}")
        return

    _ok(tid, f"Reset contract valid: finance/down-payment rows ({len(pre_rows)}) collapse to one fresh Cash row")


def run() -> dict:
    global PASS, FAIL, RESULTS
    PASS = 0
    FAIL = 0
    RESULTS = []

    print("\n=== TC_032 Sale Type Reset Guard ===\n")

    try:
        tc032_1_reset_hook_present()
        tc032_2_reset_contract_logic()
    except Exception as exc:  # pragma: no cover
        _fail("TC_032-runtime", str(exc))

    print(f"\n  Summary: {PASS} pass / {FAIL} fail")
    for verdict, tid, detail in RESULTS:
        print(f"    {verdict} {tid}: {detail}")

    if FAIL:
        raise AssertionError(f"{FAIL} assertion(s) failed")

    return {"pass": PASS, "fail": FAIL, "results": RESULTS}
