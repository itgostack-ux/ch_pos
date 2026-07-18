"""POS VAS post-sale attach E2E.

Scenario: customer bought a device from OUR store, declined VAS at billing,
comes back later and wants the plan. Staff sell the VAS standalone and key
in the IMEI we sold — the system must recognise it as a store-sold serial
(bind the REAL device item, not the external-device placeholder), enforce
the plan's purchase window against the ORIGINAL sale time, and pick the
device price from the original invoice when linked.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_vas_postsale_attach_e2e.run
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

from ch_pos.tests.test_vas_external_imei_pos_e2e import (
    TestFailure,
    _assert,
    _ensure_session,
    _make_plan,
    _pick_customer,
    _pick_mode_of_payment,
    _pick_pos_profile,
    _skip_eod_lock,
)


def _pick_real_service_items(company):
    """Reuse a live plan's service item — avoids seeding test categories."""
    plan = frappe.db.get_value(
        "CH Warranty Plan",
        {"status": "Active", "service_item": ("!=", "")},
        ["service_item", "external_device_item"],
        as_dict=True,
    )
    _assert(plan and plan.service_item, "No active CH Warranty Plan with a service item")
    external = plan.external_device_item
    if not external or external == plan.service_item:
        external = frappe.db.get_value(
            "Item",
            {"is_stock_item": 0, "disabled": 0, "has_variants": 0, "name": ("!=", plan.service_item)},
            "name",
        )
    return plan.service_item, external


def _find_sold_serial_with_profile():
    """A serial we ourselves sold + an enabled POS profile in that company."""
    rows = frappe.db.sql(
        """
        SELECT sii.serial_no, sii.item_code, sii.rate, si.name AS invoice,
               si.customer, si.company
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON si.name = sii.parent
        JOIN `tabItem` i ON i.name = sii.item_code
        WHERE si.docstatus = 1 AND si.is_return = 0
          AND IFNULL(sii.serial_no, '') != '' AND i.is_stock_item = 1
        ORDER BY si.creation DESC
        LIMIT 100
        """,
        as_dict=True,
    )
    for r in rows:
        serial = (r.serial_no or "").strip().split("\n")[0].strip()
        if not serial or not frappe.db.exists("Serial No", serial):
            continue
        profile = frappe.db.get_value(
            "POS Profile",
            {"disabled": 0, "company": r.company},
            ["name", "company", "warehouse", "customer"],
            as_dict=True,
        )
        if profile:
            r.serial = serial
            return r, profile
    return None, None


def _sell_vas(profile, customer, mop, plan, serial_no, original_invoice=None):
    from ch_pos.api.pos_api import create_pos_invoice

    with _skip_eod_lock():
        return create_pos_invoice(
            pos_profile=profile.name,
            customer=customer,
            items=[{
                "item_code": plan.service_item,
                "qty": 1,
                "rate": flt(plan.price),
                "price_list_rate": flt(plan.price),
                "warranty_plan": plan.name,
                "for_serial_no": serial_no,
                "is_vas": 1,
            }],
            mode_of_payment=mop,
            amount_paid=flt(plan.price),
            client_request_id=frappe.generate_hash(length=20),
            original_invoice=original_invoice,
        )


def run() -> dict:
    frappe.db.commit = lambda *a, **k: None  # keep everything in-txn

    sold, profile = _find_sold_serial_with_profile()
    _assert(sold, "No store-sold serial + POS profile combination found")
    mop = _pick_mode_of_payment(profile.name)

    # In-txn POS Executive for the operator — the session guard needs one for
    # this company+store (rolled back with everything else).
    store = frappe.db.get_value("POS Profile Extension", {"pos_profile": profile.name}, "store")
    if not frappe.db.exists("POS Executive",
            {"user": frappe.session.user, "company": profile.company, "is_active": 1}):
        frappe.get_doc({
            "doctype": "POS Executive",
            "user": frappe.session.user,
            "executive_name": "E2E Operator",
            "company": profile.company,
            "store": store,
            "role": "Executive",
            "is_active": 1,
        }).insert(ignore_permissions=True, ignore_mandatory=True)

    _ensure_session(profile)
    service_item, external_item = _pick_real_service_items(profile.company)
    customer = sold.customer or _pick_customer(profile)
    print(f"X| using sold serial {sold.serial} (item {sold.item_code}, "
          f"invoice {sold.invoice}, rate {sold.rate})")

    results = []

    # ── A: no-window plan attaches to OUR sold IMEI as a real device ─────
    plan_a = _make_plan(profile.company, service_item, external_item, allow_external=1, purchase_window=0)
    inv = _sell_vas(profile, customer, mop, plan_a, sold.serial)
    inv_name = inv.get("invoice") or inv.get("name")
    sp = frappe.db.get_value(
        "Active VAS Plans",
        {"sales_invoice": inv_name, "warranty_plan": plan_a.name},
        ["name", "item_code", "serial_no", "is_external_device", "device_purchase_price"],
        as_dict=True,
    )
    _assert(sp, "A: Active VAS Plans record not created")
    _assert(sp.serial_no == sold.serial, f"A: serial mismatch {sp.serial_no}")
    _assert(sp.item_code == sold.item_code,
        f"A: bound to {sp.item_code}, expected the sold device {sold.item_code} (not the external placeholder)")
    _assert(not frappe.utils.cint(sp.is_external_device),
        "A: store-sold IMEI wrongly flagged as external device")
    print(f"X| A PASS — standalone VAS attached to sold IMEI as real device "
          f"({sp.item_code}), plan {sp.name}")
    results.append("A")

    # ── B: purchase-window plan blocks an old sale (window enforced
    #      against the ORIGINAL device sale time) ─────────────────────────
    plan_b = _make_plan(profile.company, service_item, external_item, allow_external=1, purchase_window=24)
    try:
        _sell_vas(profile, customer, mop, plan_b, sold.serial)
        raise TestFailure("B: 24h-window plan sold for a long-ago IMEI — window not enforced")
    except TestFailure:
        raise
    except Exception as exc:
        _assert("within" in str(exc).lower() and "hours" in str(exc).lower(), f"B: unexpected error {exc}")
        print(f"X| B PASS — window enforced: {str(exc)[:80]}")
        results.append("B")

    # ── C: linking the original invoice fills the device purchase price ──
    plan_c = _make_plan(profile.company, service_item, external_item, allow_external=1, purchase_window=0)
    inv2 = _sell_vas(profile, customer, mop, plan_c, sold.serial, original_invoice=sold.invoice)
    inv2_name = inv2.get("invoice") or inv2.get("name")
    sp2 = frappe.db.get_value(
        "Active VAS Plans",
        {"sales_invoice": inv2_name, "warranty_plan": plan_c.name},
        ["name", "device_purchase_price", "max_coverage_value"],
        as_dict=True,
    )
    _assert(sp2, "C: Active VAS Plans record not created")
    _assert(flt(sp2.device_purchase_price) == flt(sold.rate),
        f"C: device price {sp2.device_purchase_price} != original rate {sold.rate}")
    print(f"X| C PASS — original invoice link fills device price ₹{sp2.device_purchase_price} "
          f"(coverage {sp2.max_coverage_value})")
    results.append("C")

    frappe.db.rollback()
    print(f"X| {len(results)}/3 PASS — rolled back, no records kept")
    return {"passed": results}
