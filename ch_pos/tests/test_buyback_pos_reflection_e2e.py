"""
E2E guard for buyback fields reflecting in POS after customer approval and settlement.

Run:
  cd /home/palla/erpnext-bench
  bench --site erpnext.local execute ch_pos.tests.test_buyback_pos_reflection_e2e.run
"""

import frappe
from frappe.utils import flt


def _ensure_customer():
    name = "QA Buyback POS Reflection"
    existing_by_mobile = frappe.db.get_value("Customer", {"mobile_no": "9876543210"}, "name")
    if existing_by_mobile:
        return existing_by_mobile
    if frappe.db.exists("Customer", name):
        return name
    doc = frappe.new_doc("Customer")
    doc.customer_name = name
    doc.customer_type = "Individual"
    doc.mobile_no = "9876543210"
    doc.insert(ignore_permissions=True)
    return doc.name


def _pick_item():
    row = frappe.db.sql(
        """
        SELECT name
        FROM `tabItem`
        WHERE disabled = 0
          AND is_stock_item = 1
          AND COALESCE(has_serial_no, 0) = 0
          AND COALESCE(has_batch_no, 0) = 0
          AND COALESCE(ch_lifecycle_status, 'Active') = 'Active'
        ORDER BY modified DESC
        LIMIT 1
        """,
        as_dict=True,
    )
    if not row:
        raise AssertionError("No active non-serial stock Item available for buyback POS reflection test")
    return row[0].name


def _pick_pos_profile():
    profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
    if not profile:
        raise AssertionError("No active POS Profile found")
    return profile


def _create_assessment():
    item = _pick_item()
    customer = _ensure_customer()
    imei = "QA-POS-BB-" + frappe.generate_hash(length=10).upper()

    doc = frappe.new_doc("Buyback Assessment")
    doc.source = "Store Manual"
    doc.customer = customer
    doc.customer_name = frappe.db.get_value("Customer", customer, "customer_name")
    doc.mobile_no = "9876543210"
    doc.item = item
    doc.imei_serial = imei
    doc.device_age_months = "0-3 Months"
    doc.estimated_price = 1000
    doc.quoted_price = 1000
    doc.flags.skip_duplicate_check = True
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc.name


def run():
    frappe.set_user("Administrator")

    from ch_pos.api.pos_api import (
        get_pos_buyback_detail,
        pos_approve_customer_buyback,
        pos_settle_buyback_cashback,
        pos_start_buyback_order,
        pos_submit_imei_validation,
    )

    assessment = _create_assessment()
    profile = _pick_pos_profile()
    started = pos_start_buyback_order(
        assessment_name=assessment,
        pos_profile=profile,
        final_price=1000,
        account_lock_cleared=1,
        account_lock_check_notes="QA reflection test",
    )
    order_name = started["order_name"]
    order = frappe.get_doc("Buyback Order", order_name)
    if order.status != "Approved":
        order.db_set("status", "Approved", update_modified=True)

    pos_submit_imei_validation(
        order_name=order_name,
        status="Verified Clean",
        screenshot="/files/qa-pos-reflection-imei.png",
        remarks="QA reflection test",
    )

    approval = pos_approve_customer_buyback(
        order_name=order_name,
        method="In-Store Signature",
        kyc_id_type="Aadhar Card",
        kyc_id_number="123412341234",
        settlement_type="Buyback",
        payout_mode="UPI",
        upi_id="qa.reflection@upi",
    )
    assert approval.get("order_name") == order_name, "Approval response order mismatch"

    detail = get_pos_buyback_detail(assessment)
    assert detail["order"]["customer_payout_mode"] == "UPI", (
        "POS detail did not reflect approval payout mode"
    )
    assert detail["order"]["customer_upi_id"] == "qa.reflection@upi", (
        "POS detail did not reflect approval UPI ID"
    )
    assert detail["order"]["customer_approval_method"], (
        "POS detail did not reflect customer approval method"
    )

    settled = pos_settle_buyback_cashback(order_name=order_name, payment_method="UPI")
    assert settled.get("status") in ("Paid", "Closed"), f"Unexpected settlement status: {settled}"

    order.reload()
    assert order.customer_payout_mode == "UPI", "POS settlement did not persist payout mode"
    assert order.settlement_type == "Buyback", "POS settlement did not persist settlement type"
    assert any(
        frappe.db.exists("Mode of Payment", p.payment_method)
        and flt(p.amount) > 0
        and (p.transaction_reference or "").startswith("POS-Cashback-")
        for p in (order.payments or [])
    ), (
        "POS settlement did not create a valid payment row"
    )

    detail = get_pos_buyback_detail(assessment)
    assert detail["order"]["customer_payout_mode"] == "UPI", (
        "POS detail did not reflect settlement payout mode"
    )
    assert detail["order"]["status"] in ("Paid", "Closed"), (
        "POS detail did not reflect settled status"
    )

    print(
        {
            "assessment": assessment,
            "order": order_name,
            "status": order.status,
            "payout_mode": order.customer_payout_mode,
            "settlement_type": order.settlement_type,
        }
    )
