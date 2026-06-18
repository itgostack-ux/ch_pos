"""POS VAS external IMEI E2E coverage.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_vas_external_imei_pos_e2e.run
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, nowdate, now_datetime


class TestFailure(AssertionError):
    pass


def _assert(condition, message):
    if not condition:
        raise TestFailure(message)


def _skip_eod_lock():
    class _Guard:
        def __enter__(self):
            frappe.flags.ignore_eod_lock = True

        def __exit__(self, exc_type, exc, tb):
            frappe.flags.ignore_eod_lock = False

    return _Guard()


def _pick_pos_profile():
    profile = frappe.db.get_value(
        "POS Profile",
        {"disabled": 0},
        ["name", "company", "warehouse", "customer"],
        as_dict=True,
    )
    _assert(profile, "No active POS Profile available")
    return profile


def _pick_customer(profile):
    return profile.customer or frappe.db.get_value("Customer", {"disabled": 0}, "name")


def _pick_mode_of_payment(profile_name):
    mop = frappe.db.get_value(
        "POS Payment Method",
        {"parent": profile_name, "default": 1},
        "mode_of_payment",
    )
    if mop:
        return mop
    mop = frappe.db.get_value("POS Payment Method", {"parent": profile_name}, "mode_of_payment")
    return mop or frappe.db.get_value("Mode of Payment", {}, "name") or "Cash"


def _ensure_session(profile):
    from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import (
        get_active_session,
        get_store_business_date,
    )

    active = get_active_session(profile.name)
    if active:
        return active["name"]

    store = frappe.db.get_value("POS Profile Extension", {"pos_profile": profile.name}, "store")
    if not store and profile.warehouse:
        store = frappe.db.get_value("CH Store", {"warehouse": profile.warehouse}, "name")
    business_date = get_store_business_date(store) if store else nowdate()

    with _skip_eod_lock():
        session = frappe.get_doc({
            "doctype": "CH POS Session",
            "company": profile.company,
            "pos_profile": profile.name,
            "store": store or "",
            "user": frappe.session.user,
            "business_date": business_date or nowdate(),
            "shift_start": now_datetime(),
            "opening_cash": 1000,
            "status": "Open",
        })
        session.insert(ignore_permissions=True)
        frappe.db.set_value("CH POS Session", session.name, "docstatus", 1)
        frappe.db.commit()
    return session.name


def _pick_item_tax_template(company):
    template = frappe.db.get_value(
        "Item Tax Template",
        {"company": company, "disabled": 0, "gst_rate": 18},
        "name",
    )
    if template:
        return template
    template = frappe.db.get_value(
        "Item Tax Template",
        {"company": company, "disabled": 0},
        "name",
        order_by="gst_rate desc",
    )
    _assert(template, f"No Item Tax Template configured for company {company}")
    return template


def _ensure_item_tax(item_code, company):
    template = _pick_item_tax_template(company)
    existing = frappe.db.get_value(
        "Item Tax",
        {"parent": item_code, "parenttype": "Item", "item_tax_template": template},
        "name",
    )
    if existing:
        return

    item = frappe.get_doc("Item", item_code)
    item.append("taxes", {"item_tax_template": template})
    item.flags.ignore_lifecycle_transition = True
    item.flags.ignore_plm_transition = True
    item.save(ignore_permissions=True)


def _ensure_non_stock_item(item_code, company=None, with_tax=False):
    if frappe.db.exists("Item", item_code):
        is_stock_item = frappe.db.get_value("Item", item_code, "is_stock_item")
        _assert(not is_stock_item, f"Test item {item_code} exists but is a stock item")
        frappe.db.set_value(
            "Item",
            item_code,
            {
                "disabled": 0,
                "is_sales_item": 1,
                "ch_category": "_Test Universal Cat",
                "ch_sub_category": "_Test Universal Cat-Cables",
                "ch_lifecycle_status": "Active",
                "ch_approval_status": "Approved",
                "ch_plm_status": "Approved",
            },
            update_modified=False,
        )
        if with_tax and company:
            _ensure_item_tax(item_code, company)
        return item_code

    item_group = (
        frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        or frappe.db.get_value("Item Group", {}, "name")
    )
    uom = "Nos" if frappe.db.exists("UOM", "Nos") else frappe.db.get_value("UOM", {}, "name")
    doc = frappe.get_doc({
        "doctype": "Item",
        "item_code": item_code,
        "item_name": item_code,
        "item_group": item_group,
        "stock_uom": uom,
        "is_stock_item": 0,
        "is_sales_item": 1,
        "is_purchase_item": 0,
        "include_item_in_manufacturing": 0,
        "ch_category": "_Test Universal Cat",
        "ch_sub_category": "_Test Universal Cat-Cables",
        "ch_approval_status": "Approved",
        "ch_plm_status": "Approved",
        "gst_hsn_code": "998599",
    })
    doc.insert(ignore_permissions=True)
    frappe.db.set_value(
        "Item",
        doc.name,
        {
            "ch_lifecycle_status": "Active",
            "ch_approval_status": "Approved",
            "ch_plm_status": "Approved",
        },
        update_modified=False,
    )
    if with_tax and company:
        _ensure_item_tax(doc.name, company)
    return doc.name


def _pick_service_items(company):
    service_item = _ensure_non_stock_item("QA-VAS-POS-SERVICE", company=company, with_tax=True)
    external_item = _ensure_non_stock_item("QA-VAS-EXTERNAL-DEVICE", company=company)
    return service_item, external_item


def _make_plan(company, service_item, external_item, *, allow_external, purchase_window=0):
    plan = frappe.new_doc("CH Warranty Plan")
    plan.company = company
    plan.plan_name = "POS External IMEI VAS " + frappe.generate_hash(length=8)
    plan.plan_type = "Protection Plan"
    plan.coverage_scope = "Screen Only"
    plan.service_item = service_item
    plan.status = "Active"
    plan.duration_months = 12
    plan.max_claims = 1
    plan.claims_per_year = 0
    plan.deductible_amount = 0
    plan.price = 99
    plan.pricing_mode = "Fixed"
    plan.purchase_window_hours = purchase_window
    plan.fulfillment_type = "Digital Activation"
    plan.allow_external_device = 1 if allow_external else 0
    if allow_external:
        plan.external_device_item = external_item
    plan.insert(ignore_permissions=True)
    return plan


def _invoice_external_vas(profile, customer, mop, plan, serial_no):
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
        )


def _expect_error(fn, expected_text):
    try:
        fn()
    except Exception as exc:
        message = str(exc)
        _assert(expected_text.lower() in message.lower(), f"Expected '{expected_text}' in '{message}'")
        return message
    raise TestFailure(f"Expected error containing '{expected_text}'")


def run() -> dict:
    profile = _pick_pos_profile()
    customer = _pick_customer(profile)
    _assert(customer, "No Customer available for POS VAS external IMEI test")
    mop = _pick_mode_of_payment(profile.name)
    _ensure_session(profile)
    service_item, external_item = _pick_service_items(profile.company)

    results = []

    sp = "vas_external_imei_allowed"
    frappe.db.savepoint(sp)
    try:
        plan = _make_plan(profile.company, service_item, external_item, allow_external=True)
        serial_no = "EXT-VAS-" + frappe.generate_hash(length=10).upper()
        result = _invoice_external_vas(profile, customer, mop, plan, serial_no)
        active_plan_name = (result.get("active_plans") or result.get("sold_plans") or [None])[0]
        _assert(active_plan_name, "POS invoice did not return an Active VAS Plan")

        active = frappe.get_doc("Active VAS Plans", active_plan_name)
        _assert(active.docstatus == 1, "Active VAS Plan was not submitted")
        _assert(active.item_code == external_item, "External IMEI did not use the configured external item")
        _assert(active.serial_no == serial_no, "External IMEI was not stored on Active VAS Plan")
        _assert(active.is_external_device == 1, "Active VAS Plan was not marked external")
        results.append(("PASS", "allowed_external_imei_creates_active_plan", active_plan_name))
    finally:
        frappe.db.rollback(save_point=sp)

    sp = "vas_external_imei_disallowed"
    frappe.db.savepoint(sp)
    try:
        plan = _make_plan(profile.company, service_item, external_item, allow_external=False)
        serial_no = "EXT-BLOCK-" + frappe.generate_hash(length=10).upper()
        message = _expect_error(
            lambda: _invoice_external_vas(profile, customer, mop, plan, serial_no),
            "cannot be sold for customer-provided IMEI",
        )
        results.append(("PASS", "disallowed_external_imei_hard_stops", message[:120]))
    finally:
        frappe.db.rollback(save_point=sp)

    sp = "vas_external_imei_purchase_window"
    frappe.db.savepoint(sp)
    try:
        plan = _make_plan(
            profile.company,
            service_item,
            external_item,
            allow_external=True,
            purchase_window=24,
        )
        serial_no = "EXT-WINDOW-" + frappe.generate_hash(length=10).upper()
        message = _expect_error(
            lambda: _invoice_external_vas(profile, customer, mop, plan, serial_no),
            "purchase window",
        )
        results.append(("PASS", "external_imei_purchase_window_requires_proof", message[:120]))
    finally:
        frappe.db.rollback(save_point=sp)

    sp = "vas_external_imei_catalog"
    frappe.db.savepoint(sp)
    try:
        allowed_plan = _make_plan(profile.company, service_item, external_item, allow_external=True)
        blocked_plan = _make_plan(profile.company, service_item, external_item, allow_external=False)
        from ch_pos.api.pos_api import get_vas_plans_with_rules

        plans = get_vas_plans_with_rules(cart_items=[])
        by_name = {plan.get("name"): plan for plan in plans}
        _assert(allowed_plan.name in by_name, "External-enabled plan missing from POS VAS catalog")
        _assert(blocked_plan.name in by_name, "Non-external plan missing from POS VAS catalog")
        _assert(not by_name[allowed_plan.name].get("blocked"), "External-enabled plan was blocked without cart device")
        _assert(by_name[allowed_plan.name].get("allows_external_device"), "Catalog did not mark external IMEI support")
        _assert(by_name[blocked_plan.name].get("blocked"), "Non-external protection plan was not blocked without cart device")
        results.append(("PASS", "vas_catalog_external_flag_and_blocking", allowed_plan.name))
    finally:
        frappe.db.rollback(save_point=sp)

    for status, scenario, detail in results:
        print(f"{status}: {scenario} - {detail}")

    return {"status": "passed", "results": results}
