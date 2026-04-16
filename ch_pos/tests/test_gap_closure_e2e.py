"""
Gap-closure smoke tests for remaining migration items.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_gap_closure_e2e.run_all
"""

import frappe

results = []


def ok(name, detail=""):
    results.append(("PASS", name, detail))
    print(f"PASS  {name}{f'  ({detail})' if detail else ''}")


def fail(name, detail=""):
    results.append(("FAIL", name, detail))
    print(f"FAIL  {name}{f'  ({detail})' if detail else ''}")


def _first_pos_profile():
    return frappe.db.get_value("POS Profile", {"disabled": 0}, "name")


def _first_customer():
    return frappe.db.get_value("Customer", {"disabled": 0}, "name")


def _first_item():
    item = frappe.db.get_value(
        "Item",
        {"disabled": 0, "is_stock_item": 1, "has_variants": 0},
        ["name", "item_name", "stock_uom"],
        as_dict=True,
    )
    return item


def test_prebooking_api():
    from ch_pos.api.pos_api import create_pre_booking

    pos_profile = _first_pos_profile()
    customer = _first_customer()
    item = _first_item()
    if not pos_profile or not customer or not item:
        fail("prebooking_api", "Missing master data")
        return

    result = create_pre_booking(
        pos_profile=pos_profile,
        customer=customer,
        items=[{"item_code": item.name, "qty": 1, "rate": 1, "uom": item.stock_uom or "Nos"}],
        advance_amount=100,
        notes="QA pre-booking smoke test",
    )
    so_name = result.get("name")
    if result.get("doctype") == "Sales Order" and so_name and frappe.db.exists("Sales Order", so_name):
        reserve_stock = frappe.db.get_value("Sales Order", so_name, "reserve_stock")
        ok("prebooking_api", f"{so_name} reserve_stock={reserve_stock}")
    else:
        fail("prebooking_api", str(result))


def test_received_not_billed_report():
    from ch_erp15.ch_erp15.report.received_not_billed.received_not_billed import execute

    cols, data = execute({})
    fieldnames = {c.get("fieldname") for c in cols if isinstance(c, dict)}
    if "pending_billing_amount" in fieldnames:
        ok("received_not_billed_report", f"columns={len(cols)} rows={len(data)}")
    else:
        fail("received_not_billed_report", f"fieldnames={sorted(fieldnames)}")


def test_courier_polling_api():
    from ch_erp15.ch_erp15.transfer_manifest_api import poll_courier_statuses

    result = poll_courier_statuses(dry_run=1)
    if isinstance(result, dict) and "checked" in result:
        ok("courier_polling_api", str(result))
    else:
        fail("courier_polling_api", str(result))


def run_all():
    print("\n=== GAP CLOSURE SMOKE TESTS ===")
    for fn in [
        test_prebooking_api,
        test_received_not_billed_report,
        test_courier_polling_api,
    ]:
        try:
            fn()
        except Exception as e:
            fail(fn.__name__, str(e))

    passed = len([r for r in results if r[0] == "PASS"])
    failed = len([r for r in results if r[0] == "FAIL"])
    print(f"\nSummary: PASS={passed} FAIL={failed}")
    return {"passed": passed, "failed": failed, "results": results}
