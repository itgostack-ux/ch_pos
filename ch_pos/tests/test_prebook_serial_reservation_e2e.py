"""
Pre-booking IMEI reservation — E2E Test Suite.

Covers the professional serial-reservation flow:
  • scan-confirm at billing (_confirm_prebook_serials): missing/extra/match
  • hard lock: a reserved IMEI cannot be sold via the regular sale path
  • validity: an expired pre-booking no longer locks the IMEI
  • expiry scheduler runs cleanly

Pure-logic checks always run; the integration checks self-skip when the site
has no free serialized stock to build a reservable Sales Order.

Run:
    bench --site erpnext.local execute ch_pos.tests.test_prebook_serial_reservation_e2e.run_all
"""

import traceback
import types
import frappe
from frappe.utils import nowdate, add_days

_results = []
FLOW = "PrebookReserve"


def _ok(step, detail=""):
    _results.append({"step": step, "status": "PASS"})
    print(f"  PASS  [{FLOW}] {step}" + (f"  ({detail})" if detail else ""))


def _fail(step, detail=""):
    _results.append({"step": step, "status": "FAIL", "detail": detail})
    print(f"  FAIL  [{FLOW}] {step}" + (f"  — {detail}" if detail else ""))


def _skip(step, detail=""):
    _results.append({"step": step, "status": "SKIP"})
    print(f"  SKIP  [{FLOW}] {step}" + (f"  ({detail})" if detail else ""))


def _so_with_serial(serial):
    """A fake SO carrying one IMEI on a row (SimpleNamespace header, _dict row)."""
    return types.SimpleNamespace(items=[frappe._dict(custom_serial_no=serial)])


def test_01_scan_confirm_logic():
    """_confirm_prebook_serials enforces exact scanned == reserved."""
    try:
        from ch_pos.api.pos_api import _confirm_prebook_serials

        # No reserved serials → no-op (accessory pre-booking)
        _confirm_prebook_serials(types.SimpleNamespace(items=[frappe._dict(custom_serial_no="")]), None)

        so = _so_with_serial("IMEI-AAA")

        # No scan → must raise
        try:
            _confirm_prebook_serials(so, None)
            return _fail("01 scan-confirm logic", "no-scan did not raise")
        except frappe.ValidationError:
            pass

        # Missing (scanned a different one) → raise
        try:
            _confirm_prebook_serials(so, ["IMEI-BBB"])
            return _fail("01 scan-confirm logic", "mismatch did not raise")
        except frappe.ValidationError:
            pass

        # Extra serial → raise
        try:
            _confirm_prebook_serials(so, ["IMEI-AAA", "IMEI-CCC"])
            return _fail("01 scan-confirm logic", "extra did not raise")
        except frappe.ValidationError:
            pass

        # Exact match (also accepts JSON string) → passes
        _confirm_prebook_serials(so, ["IMEI-AAA"])
        _confirm_prebook_serials(so, '["IMEI-AAA"]')
        _ok("01 scan-confirm logic", "missing/extra blocked, exact match ok")
    except Exception as e:
        _fail("01 scan-confirm logic", str(e))


def test_02_reserved_serials_helper():
    """_reserved_serials_on_so parses newline/comma serial lists."""
    try:
        from ch_pos.api.pos_api import _reserved_serials_on_so
        so = types.SimpleNamespace(items=[
            frappe._dict(custom_serial_no="A1\nA2"),
            frappe._dict(custom_serial_no="B1, B2"),
            frappe._dict(custom_serial_no=""),
        ])
        got = _reserved_serials_on_so(so)
        assert got == {"A1", "A2", "B1", "B2"}, got
        _ok("02 reserved-serials helper", f"{sorted(got)}")
    except Exception as e:
        _fail("02 reserved-serials helper", str(e))


def _make_reserved_so(serial, item, warehouse, delivery_date):
    company = frappe.db.get_value("Warehouse", warehouse, "company")
    customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
    if not (company and customer):
        return None
    so = frappe.new_doc("Sales Order")
    so.customer = customer
    so.company = company
    so.transaction_date = nowdate()
    so.delivery_date = delivery_date
    so.order_type = "Sales"
    so.currency = frappe.get_cached_value("Company", company, "default_currency")
    so.selling_price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
    so.ignore_pricing_rule = 1
    if so.meta.has_field("set_warehouse"):
        so.set_warehouse = warehouse
    if so.meta.has_field("reserve_stock"):
        so.reserve_stock = 1
    row = {"item_code": item, "qty": 1, "rate": 1000, "warehouse": warehouse,
           "delivery_date": delivery_date}
    if frappe.get_meta("Sales Order Item").has_field("custom_serial_no"):
        row["custom_serial_no"] = serial
    so.append("items", row)
    so.flags.ignore_permissions = True
    so.insert(ignore_permissions=True)
    try:
        so.submit()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "prebook test SO submit")
    so.reload()
    return so


def _cleanup_so(so):
    try:
        if so and frappe.db.exists("Sales Order", so.name):
            d = frappe.get_doc("Sales Order", so.name)
            if d.docstatus == 1:
                d.cancel()
            frappe.delete_doc("Sales Order", so.name, force=True, ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()


def _free_serial_lot():
    rows = frappe.db.sql(
        """SELECT sn.name serial_no, sn.item_code, sn.warehouse
           FROM `tabSerial No` sn
           JOIN `tabBin` b ON b.item_code = sn.item_code AND b.warehouse = sn.warehouse
           WHERE sn.status='Active' AND IFNULL(sn.warehouse,'')!=''
             AND b.actual_qty >= 1 AND IFNULL(b.reserved_qty,0) = 0
           LIMIT 1""",
        as_dict=True,
    )
    return rows[0] if rows else None


def test_03_reserved_imei_blocks_sale():
    """A reserved (in-validity) IMEI is rejected by validate_serial_for_sale."""
    so = None
    try:
        from ch_pos.api.pos_api import validate_serial_for_sale, _get_open_reserved_sales_order_for_serial
        lot = _free_serial_lot()
        if not lot:
            _skip("03 reserved IMEI blocks sale", "no free serialized stock")
            return
        so = _make_reserved_so(lot.serial_no, lot.item_code, lot.warehouse, add_days(nowdate(), 5))
        if not so or so.docstatus != 1 or not so.get("reserve_stock"):
            _skip("03 reserved IMEI blocks sale", "could not create a reserved SO on this site")
            return
        # Detected as reserved
        detected = _get_open_reserved_sales_order_for_serial(lot.serial_no, lot.warehouse)
        assert detected == so.name, f"reservation not detected (got {detected})"
        # Regular sale blocked
        res = validate_serial_for_sale(lot.serial_no, lot.item_code, lot.warehouse, allow_fifo_override=1)
        assert res.get("valid") is False and res.get("reserved"), f"sale not blocked: {res}"
        _ok("03 reserved IMEI blocks sale", f"{lot.serial_no} locked by {so.name}")
    except Exception as e:
        _fail("03 reserved IMEI blocks sale", str(e))
    finally:
        _cleanup_so(so)


def test_04_expired_reservation_releases():
    """An expired pre-booking (delivery_date past grace) no longer locks the IMEI."""
    so = None
    try:
        from ch_pos.api.pos_api import _get_open_reserved_sales_order_for_serial, PREBOOK_HOLD_GRACE_DAYS
        lot = _free_serial_lot()
        if not lot:
            _skip("04 expired reservation releases", "no free serialized stock")
            return
        # ERPNext blocks a past delivery date at creation — create valid, then
        # age the reservation by backdating the delivery date in the DB.
        so = _make_reserved_so(lot.serial_no, lot.item_code, lot.warehouse, add_days(nowdate(), 3))
        if not so or so.docstatus != 1 or not so.get("reserve_stock"):
            _skip("04 expired reservation releases", "could not create a reserved SO")
            return
        # Sanity: detected while in validity
        assert _get_open_reserved_sales_order_for_serial(lot.serial_no, lot.warehouse) == so.name
        past = add_days(nowdate(), -(PREBOOK_HOLD_GRACE_DAYS + 5))
        frappe.db.set_value("Sales Order", so.name, "delivery_date", past, update_modified=False)
        detected = _get_open_reserved_sales_order_for_serial(lot.serial_no, lot.warehouse)
        assert detected is None, f"expired reservation still locks IMEI (got {detected})"
        _ok("04 expired reservation releases", f"{lot.serial_no} freed (delivery {past})")
    except Exception as e:
        _fail("04 expired reservation releases", str(e))
    finally:
        _cleanup_so(so)


def test_05_release_scheduler_runs():
    """release_expired_prebook_reservations executes without error."""
    try:
        from ch_pos.api.pos_api import release_expired_prebook_reservations
        n = release_expired_prebook_reservations()
        assert isinstance(n, int)
        _ok("05 release scheduler runs", f"released {n}")
    except Exception as e:
        _fail("05 release scheduler runs", str(e))


def test_06_advance_alert_builds():
    """_notify_expired_prebook_advances resolves recipients + renders without error."""
    try:
        from ch_pos.api.pos_api import _notify_expired_prebook_advances
        fake = [frappe._dict(name="SO-EXP-TEST", customer="CUST", customer_name="Test Customer",
                             advance_paid=5000, grand_total=20000, delivery_date="2026-06-10")]
        frappe.flags.mute_emails = 1
        try:
            _notify_expired_prebook_advances(fake)  # build + (muted) send
        finally:
            frappe.flags.mute_emails = 0
        _ok("06 advance alert builds", "heads + accounts notifier ran (muted)")
    except Exception as e:
        _fail("06 advance alert builds", str(e))


def run_all():
    global _results
    _results = []
    print("\n" + "=" * 60)
    print("Pre-booking IMEI Reservation — E2E Tests")
    print("=" * 60 + "\n")
    frappe.set_user("Administrator")
    for t in (test_01_scan_confirm_logic, test_02_reserved_serials_helper,
              test_03_reserved_imei_blocks_sale, test_04_expired_reservation_releases,
              test_05_release_scheduler_runs, test_06_advance_alert_builds):
        try:
            t()
        except Exception as e:
            _fail(t.__name__, f"Unhandled: {e}")
            traceback.print_exc()
        try:
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
    p = sum(1 for r in _results if r["status"] == "PASS")
    f = sum(1 for r in _results if r["status"] == "FAIL")
    s = sum(1 for r in _results if r["status"] == "SKIP")
    print(f"\n{'='*60}\nTOTAL: {p} passed, {f} failed, {s} skipped / {len(_results)}")
    if f:
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  FAIL  {r['step']}: {r.get('detail','')}")
    print("=" * 60)
    if f:
        raise Exception(f"Prebook reservation E2E: {f} failed")
    return _results
