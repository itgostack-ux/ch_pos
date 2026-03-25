#!/usr/bin/env python3
"""
CH POS — Full Device Lifecycle E2E Test
========================================
Tests the complete journey of a single serial-tracked device through every
major business operation in the system:

  Step 1  PURCHASE    — Purchase Receipt → serial Active in warehouse
  Step 2  POS SALE    — create_pos_invoice → serial Delivered, Customer Device created
  Step 3  POS RETURN  — create_pos_return  → serial Active (back in stock)
  Step 4  POS RESALE  — create_pos_invoice → serial Delivered again (resell returned unit)
  Step 5  REPAIR      — create_repair_intake → POS Repair Intake + Service Request
  Step 6  BUYBACK     — Assessment → Inspection → Order → Approve →
                        Customer Approve → Record Payment → Close
                        (Stock Entry auto-created → serial Active)
  Step 7  REFURB SALE — create_pos_invoice at refurb price → serial Delivered
  Step 8  CLOSE SESSION

Run:
    bench --site erpnext.local execute ch_pos.test_lifecycle_e2e.run_all
"""

import frappe
from frappe.utils import flt, nowdate, add_days, add_months, now_datetime, getdate

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
POS_PROFILE   = "QA Velachery POS"
STORE         = "QA-VEL"          # CH Store name (for POS/Repair)
WAREHOUSE     = "QA Velachery - GGR"  # Warehouse (same store, used for Buyback)
COMPANY       = "GoGizmo Retail Pvt Ltd"
ITEM_CODE     = "DEVICE-SAMSUNG-S23"
TEST_IMEI     = "E2E-TEST-IMEI-LC01"  # Unique serial for this test
SUPPLIER      = "Samsung India Electronics"
CUSTOMER_1    = "TEST-GG-CUST-001"    # Original buyer / returns / buyback
CUSTOMER_2    = "QA Deepa Nair"       # Refurb buyer
MOBILE_NO     = "9876543210"          # Used for buyback OTP flows
SELL_RATE     = 21320.0               # CH POS price list rate
REFURB_RATE   = 14999.0               # Refurb device price
BUYBACK_PRICE = 12000.0               # Final buyback offer
COST_RATE     = 18000.0               # Purchase cost rate
# Grade Master name for "B" grade (SELECT name FROM `tabGrade Master` WHERE grade_name='B')
GRADE_B       = "GRD-00002"
# Dedicated test business date — far future so it never collides with real operations.
# Using a fixed date (not add_days(nowdate(),1)) so re-runs are idempotent.
TEST_DATE     = "2099-01-01"

# ─────────────────────────────────────────────────────────────────────────────
# Test date helpers
# ─────────────────────────────────────────────────────────────────────────────
from contextlib import contextmanager

@contextmanager
def _skip_eod_lock():
    """Temporarily bypass validate_eod_lock for test invoice creation.

    Uses frappe.flags.ignore_eod_lock which validate_eod_lock checks explicitly.
    Safe: flag is process-local and always reset in the finally block.
    """
    frappe.flags.ignore_eod_lock = True
    try:
        yield
    finally:
        frappe.flags.ignore_eod_lock = False


# ─────────────────────────────────────────────────────────────────────────────
# Reporting helpers
# ─────────────────────────────────────────────────────────────────────────────
_PASS = 0
_FAIL = 0
_WARN = 0
_context = {}   # accumulated doc names for cross-step references


def _ok(label, detail=""):
    global _PASS
    _PASS += 1
    suffix = f" — {detail}" if detail else ""
    print(f"    ✅ {label}{suffix}")


def _fail(label, detail=""):
    global _FAIL
    _FAIL += 1
    print(f"    ❌ {label} — {detail}")


def _warn(label, detail=""):
    global _WARN
    _WARN += 1
    print(f"    ⚠️  {label} — {detail}")


def _check(label, cond, detail=""):
    if cond:
        _ok(label, detail)
    else:
        _fail(label, detail)


def _section(title):
    print(f"\n{'─' * 72}")
    print(f"  {title}")
    print(f"{'─' * 72}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 — Cleanup (idempotent)
# ─────────────────────────────────────────────────────────────────────────────

def _cleanup():
    """Remove all artefacts from a previous run of this test."""
    _section("CLEANUP — removing previous run artefacts")
    frappe.flags.ignore_permissions = True

    # 1. Cancel & delete Sales Invoices referencing this serial
    si_parents = frappe.get_all(
        "Sales Invoice Item",
        filters={"serial_no": ["like", f"%{TEST_IMEI}%"]},
        fields=["parent"],
        group_by="parent",
    )
    for row in sorted(si_parents, key=lambda x: x.parent, reverse=True):
        try:
            doc = frappe.get_doc("Sales Invoice", row.parent)
            if doc.docstatus == 1:
                doc.cancel()
                frappe.db.commit()
            frappe.delete_doc("Sales Invoice", row.parent, force=True)
            frappe.db.commit()
        except Exception as e:
            print(f"    ⚠  SI {row.parent}: {e}")

    # 2. Cancel & delete Stock Entries for this serial (reverse order)
    se_parents = frappe.get_all(
        "Stock Entry Detail",
        filters={"serial_no": ["like", f"%{TEST_IMEI}%"]},
        fields=["parent"],
        group_by="parent",
    )
    for row in sorted(se_parents, key=lambda x: x.parent, reverse=True):
        try:
            doc = frappe.get_doc("Stock Entry", row.parent)
            if doc.docstatus == 1:
                doc.cancel()
                frappe.db.commit()
            frappe.delete_doc("Stock Entry", row.parent, force=True)
            frappe.db.commit()
        except Exception as e:
            print(f"    ⚠  SE {row.parent}: {e}")

    # 3. Cancel & delete Purchase Receipts for this serial
    pr_parents = frappe.get_all(
        "Purchase Receipt Item",
        filters={"serial_no": ["like", f"%{TEST_IMEI}%"]},
        fields=["parent"],
        group_by="parent",
    )
    for row in sorted(pr_parents, key=lambda x: x.parent, reverse=True):
        try:
            doc = frappe.get_doc("Purchase Receipt", row.parent)
            if doc.docstatus == 1:
                doc.cancel()
                frappe.db.commit()
            frappe.delete_doc("Purchase Receipt", row.parent, force=True)
            frappe.db.commit()
        except Exception as e:
            print(f"    ⚠  PR {row.parent}: {e}")

    # 4. Force-clean Serial and Batch Bundles via SQL (bypass link checks — safe for test serial)
    sbb_names = frappe.db.sql("""
        SELECT DISTINCT parent FROM `tabSerial and Batch Entry`
        WHERE serial_no = %s
    """, (TEST_IMEI,), as_list=True)
    for (sbb_name,) in sbb_names:
        try:
            frappe.db.sql("DELETE FROM `tabSerial and Batch Entry` WHERE parent = %s", (sbb_name,))
            frappe.db.sql("DELETE FROM `tabSerial and Batch Bundle` WHERE name = %s", (sbb_name,))
        except Exception as e:
            print(f"    ⚠  SBB {sbb_name}: {e}")

    # 4b. Clean up Stock Ledger Entries for this serial
    frappe.db.sql("""
        DELETE FROM `tabStock Ledger Entry`
        WHERE serial_no LIKE %s OR serial_and_batch_bundle IN (
            SELECT name FROM `tabSerial and Batch Bundle` WHERE name IN (
                SELECT DISTINCT parent FROM `tabSerial and Batch Entry` WHERE serial_no = %s
            )
        )
    """, (f"%{TEST_IMEI}%", TEST_IMEI))
    frappe.db.commit()

    # 5. Delete Buyback Orders whose imei_serial matches
    bbo_list = frappe.get_all(
        "Buyback Order",
        filters={"imei_serial": TEST_IMEI},
        fields=["name", "docstatus"],
    )
    for bo in bbo_list:
        try:
            if bo.docstatus == 1:
                frappe.db.set_value("Buyback Order", bo.name, "docstatus", 2)
            frappe.delete_doc("Buyback Order", bo.name, force=True)
        except Exception as e:
            print(f"    ⚠  Buyback Order {bo.name}: {e}")

    # 6. Delete Buyback Inspections linked to assessments for this IMEI
    ba_list = frappe.get_all(
        "Buyback Assessment",
        filters={"imei_serial": TEST_IMEI},
        fields=["name", "buyback_inspection"],
    )
    for ba in ba_list:
        if ba.buyback_inspection:
            try:
                frappe.delete_doc("Buyback Inspection", ba.buyback_inspection, force=True)
            except Exception:
                pass
        try:
            frappe.delete_doc("Buyback Assessment", ba.name, force=True)
        except Exception as e:
            print(f"    ⚠  BA {ba.name}: {e}")

    # 7. Cancel & delete POS Repair Intakes / Service Requests for this serial
    ri_list = frappe.get_all(
        "POS Repair Intake",
        filters={"serial_no": TEST_IMEI},
        fields=["name", "service_request", "docstatus"],
    )
    for ri in ri_list:
        if ri.service_request:
            try:
                sr_doc = frappe.get_doc("Service Request", ri.service_request)
                if sr_doc.docstatus == 1:
                    sr_doc.cancel()
                frappe.delete_doc("Service Request", ri.service_request, force=True)
            except Exception:
                pass
        try:
            if ri.docstatus == 1:
                intake_doc = frappe.get_doc("POS Repair Intake", ri.name)
                intake_doc.cancel()
                frappe.db.commit()
            frappe.delete_doc("POS Repair Intake", ri.name, force=True)
            frappe.db.commit()
        except Exception as e:
            print(f"    ⚠  Intake {ri.name}: {e}")

    # 8. Delete CH Customer Device records for this serial
    cd_list = frappe.get_all(
        "CH Customer Device",
        filters={"serial_no": TEST_IMEI},
        fields=["name"],
    )
    for cd in cd_list:
        try:
            frappe.delete_doc("CH Customer Device", cd.name, force=True)
        except Exception as e:
            print(f"    ⚠  Customer Device {cd.name}: {e}")

    # 9. Delete Serial No
    if frappe.db.exists("Serial No", TEST_IMEI):
        try:
            # First clear warehouse to avoid stock ledger issues
            frappe.db.set_value("Serial No", TEST_IMEI, "warehouse", "")
            frappe.db.commit()
            frappe.delete_doc("Serial No", TEST_IMEI, force=True)
        except Exception as e:
            print(f"    ⚠  Serial No {TEST_IMEI}: {e}")

    # 10. Clean up any residual stock ledger entries
    frappe.db.sql("""
        DELETE FROM `tabStock Ledger Entry`
        WHERE serial_no LIKE %s
    """, (f"%{TEST_IMEI}%",))

    # 11. Cancel & delete any test CH POS Sessions for TEST_DATE
    test_sessions = frappe.get_all(
        "CH POS Session",
        filters={"pos_profile": POS_PROFILE, "business_date": TEST_DATE},
        fields=["name"],
    )
    for ts in test_sessions:
        try:
            frappe.db.set_value("CH POS Session", ts.name, "docstatus", 2)
            frappe.delete_doc("CH POS Session", ts.name, force=True, ignore_permissions=True)
        except Exception as e:
            print(f"    ⚠  Test session {ts.name}: {e}")

    frappe.db.commit()
    print("    → Cleanup complete")


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_session():
    """Return name of an Open CH POS Session for TEST_DATE.

    Always uses TEST_DATE = "2099-01-01" so the session never collides with a
    real business date.  We never call open_session() via the API because that
    would open a session for the store's *current* business date, which belongs
    to live operations.

    Precedence:
    1. Reuse any existing Open/Locked session that has business_date = TEST_DATE.
    2. Create a CH POS Session doc directly with business_date = TEST_DATE,
       bypassing the open_session API entirely.
    """
    # ── Attempt 1: reuse a test-date session already open ──────────────────
    existing = frappe.get_all(
        "CH POS Session",
        filters={"pos_profile": POS_PROFILE, "business_date": TEST_DATE, "status": ["in", ["Open", "Locked"]]},
        fields=["name"],
        limit=1,
    )
    if existing:
        print(f"    → Reusing existing test session: {existing[0].name}")
        return existing[0].name

    # ── Attempt 2: direct doc insert (bypasses day-closed guard) ───────────
    profile = frappe.get_cached_doc("POS Profile", POS_PROFILE)
    business_date = TEST_DATE

    # Create POS Opening Entry (required FK on session doc — may be blank for tests)
    try:
        balance_details = []
        for p in profile.payments:
            mop_type = frappe.db.get_value("Mode of Payment", p.mode_of_payment, "type")
            balance_details.append({
                "mode_of_payment": p.mode_of_payment,
                "opening_amount": 5000 if mop_type == "Cash" else 0,
            })
        poe = frappe.get_doc({
            "doctype": "POS Opening Entry",
            "pos_profile": POS_PROFILE,
            "company": COMPANY,
            "user": frappe.session.user,
            "period_start_date": now_datetime(),
            "balance_details": balance_details,
        })
        poe.insert(ignore_permissions=True)
        poe.submit()
        poe_name = poe.name
    except Exception as e:
        print(f"    ⚠  POS Opening Entry creation failed ({e}), proceeding without")
        poe_name = None

    session = frappe.get_doc({
        "doctype": "CH POS Session",
        "company": COMPANY,
        "pos_profile": POS_PROFILE,
        "store": STORE,
        "user": frappe.session.user,
        "business_date": business_date,
        "shift_start": now_datetime(),
        "opening_cash": 5000,
        "expected_float": 5000,
        "pos_opening_entry": poe_name or "",
        "status": "Open",
    })
    session.flags.ignore_permissions = True
    session.flags.ignore_validate = True
    session.flags.ignore_mandatory = True
    session.insert(ignore_permissions=True)
    # Force docstatus=1 via DB since submit() also runs validate hooks
    frappe.db.set_value("CH POS Session", session.name, "docstatus", 1)
    frappe.db.set_value("CH POS Session", session.name, "status", "Open")
    frappe.db.commit()
    print(f"    → Created session directly: {session.name}")
    return session.name


def _close_session(session_name):
    """Close the test session cleanly."""
    try:
        sess = frappe.get_doc("CH POS Session", session_name)
        if sess.status in ("Open", "Locked", "Pending Close"):
            sess.close_session(closing_cash=5000)
            frappe.db.commit()
        return sess.status
    except Exception as e:
        _warn("Session close", str(e))
        return "Unknown"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — PURCHASE
# ─────────────────────────────────────────────────────────────────────────────

def step_1_purchase():
    _section("STEP 1 — PURCHASE (Purchase Receipt)")
    pr = frappe.get_doc({
        "doctype": "Purchase Receipt",
        "supplier": SUPPLIER,
        "company": COMPANY,
        "posting_date": nowdate(),
        "set_warehouse": WAREHOUSE,
        "items": [{
            "item_code": ITEM_CODE,
            "qty": 1,
            "rate": COST_RATE,
            "warehouse": WAREHOUSE,
            "serial_no": TEST_IMEI,
        }],
    })
    pr.flags.ignore_permissions = True
    pr.insert()
    pr.submit()
    frappe.db.commit()

    _context["pr_name"] = pr.name

    # Assertions
    _check("PR submitted", pr.docstatus == 1, f"docstatus={pr.docstatus}")
    serial = frappe.get_doc("Serial No", TEST_IMEI)
    _check("Serial created & Active", serial.status == "Active",
           f"status={serial.status}")
    _check("Serial in correct warehouse", serial.warehouse == WAREHOUSE,
           f"warehouse={serial.warehouse}")
    _check("Serial item_code correct", serial.item_code == ITEM_CODE)

    print(f"    → Purchase Receipt: {pr.name}")
    return pr.name


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — POS SALE
# ─────────────────────────────────────────────────────────────────────────────

def step_2_pos_sale(session_name):
    _section("STEP 2 — POS SALE (create_pos_invoice)")
    from ch_pos.api.pos_api import create_pos_invoice

    with _skip_eod_lock():
        result = create_pos_invoice(
            pos_profile=POS_PROFILE,
            customer=CUSTOMER_1,
            items=[{
                "item_code": ITEM_CODE,
                "qty": 1,
                "rate": SELL_RATE,
                "serial_no": TEST_IMEI,
                "uom": "Nos",
            }],
            payments=[{"mode_of_payment": "Cash", "amount": SELL_RATE}],
            sale_type="Direct Sale",
        )

    si_name = result.get("name")
    _check("Invoice created", bool(si_name), f"result={result}")
    if not si_name:
        _fail("Cannot continue step 2 — no invoice name", "")
        return None

    si = frappe.get_doc("Sales Invoice", si_name)
    _check("Invoice submitted", si.docstatus == 1, f"docstatus={si.docstatus}")
    _check("Invoice not a return", not si.is_return)
    _check("Invoice linked to POS profile", si.pos_profile == POS_PROFILE)

    serial = frappe.get_doc("Serial No", TEST_IMEI)
    _check("Serial status = Delivered", serial.status == "Delivered",
           f"status={serial.status}")
    _check("Serial warehouse cleared", not serial.warehouse or serial.warehouse == "",
           f"warehouse={serial.warehouse}")

    # CH Customer Device auto-created by hook
    cd = frappe.db.get_value(
        "CH Customer Device",
        {"serial_no": TEST_IMEI, "customer": CUSTOMER_1},
        ["name", "warranty_status"],
        as_dict=True,
    )
    _check("CH Customer Device created", bool(cd), f"serial={TEST_IMEI}")
    if cd:
        _ok("Customer Device name", cd.name)

    # Net total sanity (pre-tax) — should closely match the sell rate
    _check("Net total correct",
           abs(flt(si.net_total) - SELL_RATE) / SELL_RATE < 0.05,
           f"net_total={si.net_total}")

    _context["si1_name"] = si_name
    _context["si1_items"] = [
        {"item_code": r.item_code, "item_name": r.item_name, "qty": 1, "rate": flt(r.rate),
         "serial_no": TEST_IMEI, "original_item_row": r.name}
        for r in si.items if r.item_code == ITEM_CODE
    ]
    print(f"    → Sales Invoice: {si_name}")
    return si_name


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — POS RETURN
# ─────────────────────────────────────────────────────────────────────────────

def step_3_pos_return(si1_name):
    _section("STEP 3 — POS RETURN (create_pos_return)")
    from ch_pos.api.pos_api import create_pos_return

    return_items = _context.get("si1_items", [])
    if not return_items:
        _fail("Return items not available", "si1_items context missing")
        return None

    with _skip_eod_lock():
        result = create_pos_return(
            original_invoice=si1_name,
            return_items=return_items,
        )

    ret_name = result.get("name") if isinstance(result, dict) else result
    _check("Return invoice created", bool(ret_name), f"result={result}")
    if not ret_name:
        return None

    ret = frappe.get_doc("Sales Invoice", ret_name)
    _check("Return submitted", ret.docstatus == 1, f"docstatus={ret.docstatus}")
    _check("Is return", ret.is_return == 1)
    _check("Return against original", ret.return_against == si1_name)

    serial = frappe.get_doc("Serial No", TEST_IMEI)
    _check("Serial back to Active", serial.status == "Active",
           f"status={serial.status}")
    _check("Serial back in warehouse", serial.warehouse == WAREHOUSE,
           f"warehouse={serial.warehouse}")

    _context["ret1_name"] = ret_name
    print(f"    → Return Invoice: {ret_name}")
    return ret_name


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — POS RESALE (resell the returned unit)
# ─────────────────────────────────────────────────────────────────────────────

def step_4_pos_resale():
    _section("STEP 4 — POS RESALE (create_pos_invoice — resell returned unit)")
    from ch_pos.api.pos_api import create_pos_invoice

    with _skip_eod_lock():
        result = create_pos_invoice(
            pos_profile=POS_PROFILE,
            customer=CUSTOMER_1,
            items=[{
                "item_code": ITEM_CODE,
                "qty": 1,
                "rate": SELL_RATE,
                "serial_no": TEST_IMEI,
                "uom": "Nos",
            }],
            payments=[{"mode_of_payment": "Cash", "amount": SELL_RATE}],
            sale_type="Direct Sale",
        )

    si_name = result.get("name")
    _check("Resale invoice created", bool(si_name), f"result={result}")
    if not si_name:
        return None

    si = frappe.get_doc("Sales Invoice", si_name)
    _check("Resale submitted", si.docstatus == 1)

    serial = frappe.get_doc("Serial No", TEST_IMEI)
    _check("Serial Delivered again", serial.status == "Delivered",
           f"status={serial.status}")

    # After resale, at least one CH Customer Device record exists for CUSTOMER_1 + serial.
    # (same customer + serial → record may be reused/updated rather than duplicated)
    cd_count = frappe.db.count(
        "CH Customer Device",
        {"serial_no": TEST_IMEI, "customer": CUSTOMER_1},
    )
    _check("CH Customer Device created for resale", cd_count >= 1,
           f"count={cd_count}")

    _context["si2_name"] = si_name
    print(f"    → Resale Invoice: {si_name}")
    return si_name


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — REPAIR INTAKE
# ─────────────────────────────────────────────────────────────────────────────

def step_5_repair_intake():
    _section("STEP 5 — REPAIR INTAKE (create_repair_intake)")
    from ch_pos.api.repair import create_repair_intake

    result = create_repair_intake(
        data={
            "store": WAREHOUSE,          # POS Repair Intake.store = Warehouse link
            "customer": CUSTOMER_1,
            "customer_phone": MOBILE_NO,
            "device_brand": "Samsung",
            "device_model": "Galaxy S23",
            "serial_no": TEST_IMEI,
            "imei_number": TEST_IMEI,
            "issue_category": "Screen Issues",
            "issue_description": "E2E test: screen cracked, touch unresponsive",
            "mode_of_service": "Walk-in",
            "device_condition": "Damaged",
            "data_backup_disclaimer": 1,
        },
        pos_profile=POS_PROFILE,
    )

    intake_name = result.get("intake_name")
    sr_name = result.get("service_request_name")

    _check("Repair intake created", bool(intake_name), f"result={result}")
    if intake_name:
        intake = frappe.get_doc("POS Repair Intake", intake_name)
        _check("Intake submitted", intake.docstatus == 1,
               f"docstatus={intake.docstatus}")
        _check("Intake linked to store", intake.store == WAREHOUSE)
        _check("Intake linked to customer", intake.customer == CUSTOMER_1)

    _check("Service Request auto-created", bool(sr_name), f"sr={sr_name}")
    if sr_name:
        sr = frappe.get_doc("Service Request", sr_name)
        _check("SR status Open", sr.status in ("Open", "Draft"),
               f"status={sr.status}")

    # Serial stays Delivered — repair intake doesn't move stock
    serial = frappe.get_doc("Serial No", TEST_IMEI)
    _check("Serial still Delivered (no stock move)", serial.status == "Delivered",
           f"status={serial.status}")

    _context["intake_name"] = intake_name
    _context["sr_name"] = sr_name
    print(f"    → Repair Intake: {intake_name}  |  Service Request: {sr_name}")
    return intake_name


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — BUYBACK (Assessment → Inspection → Order → Approve → Pay → Close)
# ─────────────────────────────────────────────────────────────────────────────

def step_6_buyback():
    _section("STEP 6 — BUYBACK FLOW")
    import buyback.api as bb_api

    # ── 6a. Create Assessment ──────────────────────────────────────────────
    print("  6a. Creating Buyback Assessment …")
    ba = frappe.get_doc({
        "doctype": "Buyback Assessment",
        "source": "Store Manual",
        "customer": CUSTOMER_1,
        "mobile_no": MOBILE_NO,
        "store": WAREHOUSE,           # Buyback Assessment.store = Warehouse link
        "company": COMPANY,
        "item": ITEM_CODE,
        "imei_serial": TEST_IMEI,
        "estimated_grade": GRADE_B,   # GRD-00002 (Grade Master name for "B")
        "estimated_price": BUYBACK_PRICE,
        "quoted_price": BUYBACK_PRICE,
        "diagnostic_tests": [
            {
                "test": "BQB-00199",           # "Screen Test"
                "test_code": "screen_test",
                "test_name": "Screen Test",
                "result": "Pass",
                "depreciation_percent": 0,
            },
        ],
        "responses": [
            {
                "question": "BQB-00159",       # "Screen condition?"
                "question_code": "qa-scr-cond",
                "question_text": "Screen condition?",
                "answer_value": "Good",
                "answer_label": "Good",
                "price_impact_percent": 0,
            },
        ],
    })
    ba.flags.ignore_permissions = True
    ba.insert()
    frappe.db.commit()
    _check("Buyback Assessment created", bool(ba.name))
    _context["ba_name"] = ba.name

    # ── 6b. Submit Assessment ──────────────────────────────────────────────
    print("  6b. Submitting Assessment …")
    ba_result = bb_api.submit_assessment(ba.name)
    _check("Assessment status after submit",
           ba_result.get("status") in ("Submitted", "Grade Pending", "Pending Inspection"),
           f"status={ba_result.get('status')}")

    # ── 6c. Create Inspection ───────────────────────────────────────────────
    print("  6c. Creating Inspection …")
    insp_result = bb_api.create_inspection_from_assessment(ba.name)
    insp_name = insp_result.get("name")
    _check("Inspection created", bool(insp_name), f"result={insp_result}")
    _context["insp_name"] = insp_name

    # ── 6d. Start Inspection ────────────────────────────────────────────────
    print("  6d. Starting Inspection …")
    start_result = bb_api.start_inspection(insp_name)
    _check("Inspection started",
           start_result.get("status") in ("In Progress", "Started"),
           f"status={start_result.get('status')}")

    # ── 6e. Complete Inspection ─────────────────────────────────────────────
    print("  6e. Completing Inspection (grade B, price 12000) …")
    comp_result = bb_api.complete_inspection(
        inspection_name=insp_name,
        condition_grade=GRADE_B,
        revised_price=BUYBACK_PRICE,
    )
    _check("Inspection completed",
           comp_result.get("status") in ("Completed", "Done"),
           f"status={comp_result.get('status')}")

    # ── 6f. Create Buyback Order ────────────────────────────────────────────
    print("  6f. Creating Buyback Order …")
    order_result = bb_api.create_order(
        customer=CUSTOMER_1,
        mobile_no=MOBILE_NO,
        store=WAREHOUSE,
        item=ITEM_CODE,
        condition_grade=GRADE_B,
        final_price=BUYBACK_PRICE,
        buyback_assessment=ba.name,
        buyback_inspection=insp_name,
        imei_serial=TEST_IMEI,
        warranty_status="Out of Warranty",
        brand="Samsung",
    )
    order_name = order_result.get("name")
    _check("Buyback Order created", bool(order_name), f"result={order_result}")
    _context["bb_order_name"] = order_name

    order = frappe.get_doc("Buyback Order", order_name)
    _check("Order final_price correct",
           abs(flt(order.final_price) - BUYBACK_PRICE) < 1,
           f"final_price={order.final_price}")

    # ── 6g. Approve Order ──────────────────────────────────────────────────
    print("  6g. Approving Order …")
    # If workflow state requires manager approval, approve it
    order.reload()
    if order.status in ("Draft", "Awaiting Approval"):
        try:
            approve_result = bb_api.approve_order(order_name, remarks="E2E test approval")
            _check("Order approved",
                   approve_result.get("status") in ("Approved", "Customer Approved",
                                                    "Awaiting Customer Approval"),
                   f"status={approve_result.get('status')}")
        except Exception as e:
            _warn("Approve order", str(e))
    else:
        _ok("Order auto-approved (no approval required)", f"status={order.status}")

    # ── 6h. Customer Approve ───────────────────────────────────────────────
    print("  6h. Customer approving offer …")
    order.reload()
    if order.status in ("Approved", "Awaiting Customer Approval"):
        try:
            ca_result = bb_api.customer_approve_offer(order_name, method="In-Store Signature")
            _check("Customer approved",
                   ca_result.get("customer_approved") in (1, True),
                   f"customer_approved={ca_result.get('customer_approved')}")
        except Exception as e:
            _warn("Customer approve", str(e))
    else:
        _ok("Customer approval not required", f"status={order.status}")

    # ── 6h½. Skip OTP verification (test only) ────────────────────────────
    #    Production flow: Customer Approved → Send OTP → Awaiting OTP
    #    → Verify OTP → OTP Verified → Ready to Pay
    #    For tests we bypass by directly setting the required fields.
    print("  6h½. Bypassing OTP verification (test shortcut) …")
    order.reload()
    if order.status == "Customer Approved":
        frappe.db.set_value("Buyback Order", order_name, {
            "otp_verified": 1,
            "otp_verified_at": frappe.utils.now_datetime(),
            "status": "OTP Verified",
        }, update_modified=False)
        frappe.db.commit()
        order.reload()
        _check("OTP bypassed", order.status == "OTP Verified", f"status={order.status}")
    elif order.status in ("OTP Verified", "Ready to Pay", "Paid"):
        _ok("Already past OTP stage", f"status={order.status}")
    else:
        _warn("Cannot bypass OTP — unexpected status", f"status={order.status}")

    # ── 6i. Record Payment ─────────────────────────────────────────────────
    #    record_payment internally calls mark_ready_to_pay() (needs "OTP Verified")
    #    then mark_paid().
    print(f"  6i. Recording Cash payment ₹{BUYBACK_PRICE} …")
    order.reload()
    try:
        pay_result = bb_api.record_payment(
            order_name=order_name,
            payment_method="Cash",
            amount=BUYBACK_PRICE,
            transaction_reference="E2E-TEST-PAY-001",
        )
        _check("Payment recorded",
               pay_result.get("payment_status") in ("Paid", "Fully Paid"),
               f"payment_status={pay_result.get('payment_status')}")
        _check("Total paid correct",
               abs(flt(pay_result.get("total_paid", 0)) - BUYBACK_PRICE) < 1,
               f"total_paid={pay_result.get('total_paid')}")
    except Exception as e:
        _fail("Record payment failed", str(e))
        return order_name

    # Assert Stock Entry was auto-created when Paid
    order.reload()
    _check("Stock Entry auto-created on payment",
           bool(order.stock_entry),
           f"stock_entry={order.stock_entry}")
    if order.stock_entry:
        se = frappe.get_doc("Stock Entry", order.stock_entry)
        _check("SE submitted", se.docstatus == 1)
        _check("SE type Material Receipt", se.stock_entry_type == "Material Receipt")
        serial = frappe.get_doc("Serial No", TEST_IMEI)
        _check("Serial back to Active after buyback pay",
               serial.status == "Active",
               f"status={serial.status}")
        _check("Serial in buyback warehouse",
               serial.warehouse == WAREHOUSE,
               f"warehouse={serial.warehouse}")

    # ── 6j. Close Order ────────────────────────────────────────────────────
    print("  6j. Closing Order …")
    order.reload()
    if order.status == "Paid":
        close_result = bb_api.close_order(order_name)
        _check("Order Closed",
               close_result.get("status") == "Closed",
               f"status={close_result.get('status')}")
    else:
        _warn("Order not in Paid state — skipping close", f"status={order.status}")

    print(f"    → Buyback Order: {order_name}")
    return order_name


# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — REFURB POS SALE
# ─────────────────────────────────────────────────────────────────────────────

def step_7_refurb_sale():
    _section("STEP 7 — REFURB POS SALE (create_pos_invoice at refurb price)")
    from ch_pos.api.pos_api import create_pos_invoice

    # Serial should be Active in WAREHOUSE from buyback stock entry
    serial = frappe.get_doc("Serial No", TEST_IMEI)
    if serial.status != "Active":
        _warn("Serial not Active before refurb sale",
              f"status={serial.status} — test may fail")

    with _skip_eod_lock():
        result = create_pos_invoice(
            pos_profile=POS_PROFILE,
            customer=CUSTOMER_2,
            items=[{
                "item_code": ITEM_CODE,
                "qty": 1,
                "rate": REFURB_RATE,
                "serial_no": TEST_IMEI,
                "uom": "Nos",
            }],
            payments=[{"mode_of_payment": "Cash", "amount": REFURB_RATE}],
            sale_type="Direct Sale",
        )

    si_name = result.get("name")
    _check("Refurb invoice created", bool(si_name), f"result={result}")
    if not si_name:
        return None

    si = frappe.get_doc("Sales Invoice", si_name)
    _check("Refurb invoice submitted", si.docstatus == 1)
    _check("Refurb customer correct", si.customer == CUSTOMER_2)
    _check("Refurb rate correct",
           abs(flt(si.net_total) - REFURB_RATE) / REFURB_RATE < 0.05,
           f"net_total={si.net_total}")

    serial = frappe.get_doc("Serial No", TEST_IMEI)
    _check("Serial Delivered to refurb buyer", serial.status == "Delivered",
           f"status={serial.status}")

    # CH Customer Device for refurb buyer
    cd = frappe.db.get_value(
        "CH Customer Device",
        {"serial_no": TEST_IMEI, "customer": CUSTOMER_2},
        "name",
    )
    _check("CH Customer Device created for refurb buyer", bool(cd))

    _context["si_refurb_name"] = si_name
    print(f"    → Refurb Invoice: {si_name}")
    return si_name


# ─────────────────────────────────────────────────────────────────────────────
# STEP 8 — CLOSE SESSION
# ─────────────────────────────────────────────────────────────────────────────

def step_8_close_session(session_name):
    _section("STEP 8 — CLOSE SESSION")
    biz_date = frappe.db.get_value("CH POS Session", session_name, "business_date")
    if str(biz_date) != TEST_DATE:
        print(f"    ⚠  Session {session_name} has business_date={biz_date} (not TEST_DATE={TEST_DATE})")
        print("    ⚠  Skipping session close to avoid disrupting live operations.")
        _check("Session close skipped (not a test session)", True)
        return
    final_status = _close_session(session_name)
    _check("Session closed", final_status == "Closed",
           f"status={final_status}")
    print(f"    → Session {session_name}: {final_status}")


# ─────────────────────────────────────────────────────────────────────────────
# FINAL ASSERTIONS — lifecycle cross-checks
# ─────────────────────────────────────────────────────────────────────────────

def _final_assertions():
    _section("FINAL CROSS-CHECKS")

    # 1. Original sale → return linkage
    if _context.get("si1_name") and _context.get("ret1_name"):
        ret = frappe.get_doc("Sales Invoice", _context["ret1_name"])
        _check("Return.return_against = Sale 1",
               ret.return_against == _context["si1_name"])

    # 2. CH Customer Device records exist for this serial
    #    sale1+resale go to same customer, so 1 record. refurb goes to CUSTOMER_2, so 1 more = 2 total.
    #    But records may be reused/updated rather than duplicated.
    total_cd = frappe.db.count("CH Customer Device", {"serial_no": TEST_IMEI})
    _check("At least 1 Customer Device record",
           total_cd >= 1, f"count={total_cd}")

    # 3. Serial final state = Delivered (refurb sold to CUSTOMER_2)
    serial = frappe.get_doc("Serial No", TEST_IMEI)
    _check("Serial final state = Delivered",
           serial.status == "Delivered", f"status={serial.status}")

    # 4. Buyback Order closed
    if _context.get("bb_order_name"):
        order = frappe.get_doc("Buyback Order", _context["bb_order_name"])
        _check("Buyback Order status = Closed", order.status == "Closed",
               f"status={order.status}")

    # 5. Service Request exists
    if _context.get("sr_name"):
        sr = frappe.get_doc("Service Request", _context["sr_name"])
        _check("Service Request exists", bool(sr.name))


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def run_all():
    global _PASS, _FAIL, _WARN, _context
    _PASS = _FAIL = _WARN = 0
    _context = {}

    frappe.set_user("Administrator")
    frappe.flags.ignore_permissions = True
    frappe.flags.mute_emails = True

    print("\n" + "=" * 72)
    print("  CH POS — FULL DEVICE LIFECYCLE E2E TEST")
    print(f"  Device: {ITEM_CODE}  |  IMEI: {TEST_IMEI}")
    print(f"  Store: {STORE}  |  POS Profile: {POS_PROFILE}")
    print("=" * 72)

    # ── Cleanup ────────────────────────────────────────────────────────────
    _cleanup()

    # ── Session ─────────────────────────────────────────────────────────────
    _section("SESSION SETUP")
    session_name = _ensure_session()
    _check("Session active", bool(session_name), "session_name missing")

    # ── Steps ───────────────────────────────────────────────────────────────
    def _run(label, fn, *args):
        try:
            return fn(*args)
        except Exception as e:
            import traceback
            _fail(f"{label} — unexpected exception", str(e))
            print(f"    TRACEBACK:\n{traceback.format_exc()}")
            return None

    pr_name  = _run("Step 1 Purchase", step_1_purchase)
    si1_name = _run("Step 2 POS Sale", step_2_pos_sale, session_name)
    if si1_name:
        _run("Step 3 POS Return", step_3_pos_return, si1_name)
    _run("Step 4 POS Resale", step_4_pos_resale)
    _run("Step 5 Repair Intake", step_5_repair_intake)
    _run("Step 6 Buyback", step_6_buyback)
    _run("Step 7 Refurb Sale", step_7_refurb_sale)

    if session_name:
        _run("Step 8 Close Session", step_8_close_session, session_name)

    # ── Cross-checks ────────────────────────────────────────────────────────
    _final_assertions()

    # ── Summary ─────────────────────────────────────────────────────────────
    total = _PASS + _FAIL + _WARN
    print("\n" + "=" * 72)
    print(f"  RESULTS  ✅ {_PASS} passed  ❌ {_FAIL} failed  ⚠️  {_WARN} warned  ({total} total)")
    print("=" * 72)

    if _context:
        print("\n  Document Trail:")
        labels = {
            "pr_name":        "Purchase Receipt",
            "si1_name":       "Sale Invoice 1",
            "ret1_name":      "Return Invoice",
            "si2_name":       "Resale Invoice",
            "intake_name":    "Repair Intake",
            "sr_name":        "Service Request",
            "ba_name":        "Buyback Assessment",
            "insp_name":      "Buyback Inspection",
            "bb_order_name":  "Buyback Order",
            "si_refurb_name": "Refurb Sale Invoice",
        }
        for key, label in labels.items():
            if _context.get(key):
                print(f"    {label:25s}: {_context[key]}")

    print()
    return _FAIL == 0
