"""
TC_023 executable regression test.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc023_queue_billing_hold_release.run
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe
from frappe.utils import add_days, now_datetime

from ch_pos.api.token_api import recover_stale_pos_billing, release_pos_billing, start_pos_billing


PASS = 0
FAIL = 0
RESULTS: list[tuple[str, str, str]] = []


@dataclass
class Ctx:
    pos_profile: str
    company: str
    warehouse: str


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



def _get_ctx() -> Ctx | None:
    row = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0},
        fields=["name", "company", "warehouse"],
        order_by="name asc",
        limit=1,
    )
    if not row:
        return None
    first = row[0]
    return Ctx(pos_profile=first.name, company=first.company, warehouse=first.warehouse)



def _create_token(ctx: Ctx, customer_name: str) -> str:
    from ch_pos.api.token_api import _generate_token_display

    company_abbr = frappe.db.get_value("Company", ctx.company, "abbr") or "CH"
    token_display = _generate_token_display(ctx.pos_profile, company_abbr)
    doc = frappe.get_doc(
        {
            "doctype": "POS Kiosk Token",
            "pos_profile": ctx.pos_profile,
            "company": ctx.company,
            "store": ctx.warehouse,
            "status": "Waiting",
            "token_display": token_display,
            "customer_name": customer_name,
            "customer_phone": "9876543210",
            "visit_source": "Counter",
            "visit_purpose": "Sales",
            "expires_at": add_days(now_datetime(), 1),
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert()
    doc.submit()
    return doc.name



def _cleanup(tokens: list[str]) -> None:
    for token in tokens:
        if not token or not frappe.db.exists("POS Kiosk Token", token):
            continue
        try:
            frappe.db.set_value("POS Kiosk Token", token, "status", "Cancelled", update_modified=False)
        except Exception:
            pass
    frappe.db.commit()



def tc023_queue_billing_hold_release() -> None:
    tid = "TC_023"
    ctx = _get_ctx()
    if not ctx:
        _fail(tid, "No enabled POS Profile found")
        return

    tokens: list[str] = []
    try:
        token_a = _create_token(ctx, "TC023 Customer A")
        token_b = _create_token(ctx, "TC023 Customer B")
        tokens.extend([token_a, token_b])

        result_a = start_pos_billing(token_a, ctx.pos_profile)
        if result_a.get("action") != "started":
            _fail(tid, f"First token did not start billing: {result_a}")
            return

        status_a = frappe.db.get_value("POS Kiosk Token", token_a, "status")
        if status_a != "In Progress":
            _fail(tid, f"First token status should be In Progress, got {status_a}")
            return

        result_b = start_pos_billing(token_b, ctx.pos_profile)
        if result_b.get("action") != "held":
            _fail(tid, f"Second token should be held when another billing is active: {result_b}")
            return

        status_b = frappe.db.get_value("POS Kiosk Token", token_b, "status")
        if status_b != "Hold":
            _fail(tid, f"Second token status should be Hold, got {status_b}")
            return

        release_pos_billing(token_name=token_a, pos_profile=ctx.pos_profile, revert_current=1)
        status_a_after = frappe.db.get_value("POS Kiosk Token", token_a, "status")
        status_b_after = frappe.db.get_value("POS Kiosk Token", token_b, "status")
        if status_a_after != "Waiting":
            _fail(tid, f"Released active token should return to Waiting, got {status_a_after}")
            return
        if status_b_after != "Waiting":
            _fail(tid, f"Held token should be released back to Waiting, got {status_b_after}")
            return

        result_restart = start_pos_billing(token_a, ctx.pos_profile)
        if result_restart.get("action") != "started":
            _fail(tid, f"Restart billing after release failed: {result_restart}")
            return
        hold_again = start_pos_billing(token_b, ctx.pos_profile)
        if hold_again.get("action") != "held":
            _fail(tid, f"Second hold cycle failed: {hold_again}")
            return

        frappe.db.set_value("POS Kiosk Token", token_a, "status", "Converted")
        release_pos_billing(token_name=token_a, pos_profile=ctx.pos_profile, revert_current=0)
        status_b_final = frappe.db.get_value("POS Kiosk Token", token_b, "status")
        if status_b_final != "Waiting":
            _fail(tid, f"Held token should release after billing completion path, got {status_b_final}")
            return

        _ok(tid, "Active billing holds the next token and releases it when billing ends")
    finally:
        _cleanup(tokens)


def tc023_stale_recovery() -> None:
    tid = "TC_023_RECOVERY"
    ctx = _get_ctx()
    if not ctx:
        _fail(tid, "No enabled POS Profile found")
        return

    tokens: list[str] = []
    try:
        token_a = _create_token(ctx, "TC023 Stale A")
        token_b = _create_token(ctx, "TC023 Stale B")
        tokens.extend([token_a, token_b])

        start_pos_billing(token_a, ctx.pos_profile)
        start_pos_billing(token_b, ctx.pos_profile)

        stale_at = add_days(now_datetime(), -1)
        frappe.db.sql(
            "UPDATE `tabPOS Kiosk Token` SET modified = %s WHERE name = %s",
            (stale_at, token_a),
        )
        frappe.db.commit()

        result = recover_stale_pos_billing(timeout_minutes=30)
        status_a = frappe.db.get_value("POS Kiosk Token", token_a, "status")
        status_b = frappe.db.get_value("POS Kiosk Token", token_b, "status")

        if status_a != "Waiting":
            _fail(tid, f"Stale active token should recover to Waiting, got {status_a}")
            return
        if status_b != "Waiting":
            _fail(tid, f"Held token should release during stale recovery, got {status_b}")
            return
        if token_a not in (result.get("recovered_tokens") or []):
            _fail(tid, f"Recovery result did not include stale token: {result}")
            return

        _ok(tid, "Stale In Progress billing is auto-recovered and held queue is released")
    finally:
        _cleanup(tokens)



def run() -> dict:
    global PASS, FAIL, RESULTS
    PASS = 0
    FAIL = 0
    RESULTS = []

    print("\n=== TC_023 Queue Billing Hold/Release ===\n")
    tc023_queue_billing_hold_release()
    tc023_stale_recovery()

    print(f"\n  Summary: {PASS} pass / {FAIL} fail")
    if FAIL:
        raise AssertionError(f"{FAIL} test(s) failed")
    return {"pass": PASS, "fail": FAIL}
