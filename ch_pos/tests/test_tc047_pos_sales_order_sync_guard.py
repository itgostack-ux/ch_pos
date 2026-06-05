"""TC_047 regression guard: POS pre-booking must sync to Sales Order backend fields.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc047_pos_sales_order_sync_guard.run
"""

from __future__ import annotations

import frappe
from frappe.utils import flt


def _pick_profile_customer_item():
    profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
    customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
    item = frappe.db.get_value(
        "Item",
        {"disabled": 0, "is_stock_item": 1},
        "name",
    )
    return profile, customer, item


def run() -> dict:
    from ch_pos.api.pos_api import create_pre_booking

    sp = "tc047_prebook_sync"
    frappe.db.savepoint(sp)
    try:
        profile, customer, item_code = _pick_profile_customer_item()
        if not (profile and customer and item_code):
            print("SKIP: TC_047 (missing profile/customer/item)")
            return {"pass": 0, "fail": 0, "skip": 1}

        qty = 2
        rate = 1234.0
        result = create_pre_booking(
            pos_profile=profile,
            customer=customer,
            items=[{"item_code": item_code, "qty": qty, "rate": rate, "uom": "Nos"}],
            notes="TC_047 sync guard",
            reserve_stock=1,
        )

        so_name = result.get("name")
        if not so_name:
            raise AssertionError("TC_047: create_pre_booking returned no Sales Order")

        so = frappe.get_doc("Sales Order", so_name)
        if so.customer != customer:
            raise AssertionError("TC_047: Sales Order customer mismatch")
        if not so.items:
            raise AssertionError("TC_047: Sales Order has no items")

        row = so.items[0]
        if row.item_code != item_code:
            raise AssertionError("TC_047: Sales Order item_code mismatch")
        if flt(row.qty) != flt(qty):
            raise AssertionError(f"TC_047: qty mismatch {row.qty} != {qty}")
        if flt(row.rate) != flt(rate):
            raise AssertionError(f"TC_047: rate mismatch {row.rate} != {rate}")

        if so.meta.has_field("reserve_stock") and int(so.reserve_stock or 0) != 1:
            raise AssertionError("TC_047: reserve_stock not synced")

        print(f"PASS: TC_047 Sales Order backend synced ({so_name})")
        return {"pass": 1, "fail": 0}
    finally:
        frappe.db.rollback(save_point=sp)
