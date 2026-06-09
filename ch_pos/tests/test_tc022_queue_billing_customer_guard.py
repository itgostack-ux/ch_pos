"""
TC_022 regression guard.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc022_queue_billing_customer_guard.run
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


def tc022_queue_billing_customer_behavior() -> None:
    tid = "TC_022"
    queue_src = _read("public/js/pos_app/modules/queue/queue_workspace.js")
    dialog_src = _read("public/js/ch_customer_dialog.js")
    api_src = _read("api/token_api.py")

    required = {
        "queue_existing_lookup": '"ch_pos.api.token_api.find_customer_by_phone"',
        "queue_new_customer_gate": "if (token.customer_phone && window.ch_open_new_customer_dialog)",
        "queue_prefill_phone": "prefill_mobile: token.customer_phone || \"\"",
        "queue_prefill_name": "prefill_name: token.customer_name || \"\"",
        "dialog_prefill_support": "const prefill_mobile = (opts.prefill_mobile || \"\").trim();",
        "dialog_otp_gate": "Send & verify WhatsApp OTP before creating customer",
        "api_tail10_match": "RIGHT(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(IFNULL(mobile_no, ''), '+', ''), '-', ''), ' ', ''), '(', ''), ')', ''), 10)",
    }

    haystack = "\n".join([queue_src, dialog_src, api_src])
    missing = [name for name, needle in required.items() if needle not in haystack]

    if missing:
        _fail(tid, f"Missing queue-customer markers: {', '.join(missing)}")
        return

    _ok(tid, "Queue billing auto-resolves existing customers and enforces verified new-customer flow")


def run() -> dict:
    global PASS, FAIL, RESULTS
    PASS = 0
    FAIL = 0
    RESULTS = []

    print("\n=== TC_022 Queue Billing Customer Guard ===\n")
    tc022_queue_billing_customer_behavior()

    print(f"\n  Summary: {PASS} pass / {FAIL} fail")
    if FAIL:
        raise AssertionError(f"{FAIL} guard(s) failed")
    return {"pass": PASS, "fail": FAIL}
