"""TC_033 regression guard: Process Return must expose and pass Refund Payment mode.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc033_refund_payment_mode_guard.run
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


def _read(app: str, rel: str) -> str:
    return (Path(frappe.get_app_path(app)) / rel).read_text(encoding="utf-8")


def tc033_1_returns_ui_has_refund_payment_field() -> None:
    tid = "TC_033-1"
    src = _read("ch_pos", "public/js/pos_app/modules/returns/returns_workspace.js")
    required = {
        "fieldname": 'fieldname: "refund_mode_of_payment"',
        "label": 'label: __("Refund Payment")',
        "depends": "depends_on: \"eval:doc.refund_method=='Original Tender'\"",
        "api_arg": "refund_mode_of_payment: justification.refund_mode_of_payment || \"\"",
    }
    missing = [k for k, v in required.items() if v not in src]
    if missing:
        _fail(tid, f"Missing returns UI wiring: {', '.join(missing)}")
        return
    _ok(tid, "Process Return dialog includes Refund Payment selector and API forwarding")


def tc033_2_create_pos_return_accepts_refund_mop() -> None:
    tid = "TC_033-2"
    src = _read("ch_pos", "api/pos_api.py")
    required = {
        "signature": "refund_mode_of_payment=None",
        "selection": "selected_refund_mop = (refund_mode_of_payment or \"\").strip()",
        "validation": "Invalid Refund Payment mode",
        "override": "default_mode = selected_refund_mop",
        "audit_tag": "[PHASE4_REFUND_MOP={default_mode}]",
    }
    missing = [k for k, v in required.items() if v not in src]
    if missing:
        _fail(tid, f"Missing backend refund-mop wiring: {', '.join(missing)}")
        return
    _ok(tid, "create_pos_return accepts, validates, applies, and audits Refund Payment mode")


def run() -> dict:
    global PASS, FAIL, RESULTS
    PASS = 0
    FAIL = 0
    RESULTS = []

    print("\n=== TC_033 Refund Payment Guard ===\n")
    try:
        tc033_1_returns_ui_has_refund_payment_field()
        tc033_2_create_pos_return_accepts_refund_mop()
    except Exception as exc:  # pragma: no cover
        _fail("TC_033-runtime", str(exc))

    print(f"\n  Summary: {PASS} pass / {FAIL} fail")
    for verdict, tid, detail in RESULTS:
        print(f"    {verdict} {tid}: {detail}")

    if FAIL:
        raise AssertionError(f"{FAIL} assertion(s) failed")

    return {"pass": PASS, "fail": FAIL, "results": RESULTS}
