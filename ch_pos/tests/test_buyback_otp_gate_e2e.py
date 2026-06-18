"""
E2E: Buyback OTP gate and completion flow

Run:
  cd /home/palla/erpnext-bench
  bench --site erpnext.local execute ch_pos.tests.test_buyback_otp_gate_e2e.run
"""

import frappe
from frappe.utils import flt


def _pick_assessment() -> str:
    # Pick an assessment whose item is in Active lifecycle AND is not
    # serial-tracked. Serial-tracked items would require a Serial &
    # Batch Bundle to be created during the close-order Stock Entry —
    # outside the scope of this OTP-gate e2e.
    row = frappe.db.sql(
        """
        SELECT a.name
        FROM `tabBuyback Assessment` a
        JOIN `tabItem` i ON i.name = a.item
        WHERE a.docstatus < 2
          AND COALESCE(i.ch_lifecycle_status, 'Active') = 'Active'
          AND COALESCE(i.disabled, 0) = 0
          AND COALESCE(i.has_serial_no, 0) = 0
          AND COALESCE(i.has_batch_no, 0) = 0
          AND NOT EXISTS (
              SELECT 1
              FROM `tabBuyback Order` o
              WHERE o.buyback_assessment = a.name
                AND o.docstatus != 2
                AND o.status IN ('Paid', 'Closed')
          )
        ORDER BY a.modified DESC
        LIMIT 1
        """,
        as_dict=True,
    )
    if not row:
        # Fallback: any Active, non-disabled item
        row = frappe.db.sql(
            """
            SELECT a.name
            FROM `tabBuyback Assessment` a
            JOIN `tabItem` i ON i.name = a.item
            WHERE a.docstatus < 2
              AND COALESCE(i.ch_lifecycle_status, 'Active') = 'Active'
              AND COALESCE(i.disabled, 0) = 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM `tabBuyback Order` o
                  WHERE o.buyback_assessment = a.name
                    AND o.docstatus != 2
                    AND o.status IN ('Paid', 'Closed')
              )
            ORDER BY a.modified DESC
            LIMIT 1
            """,
            as_dict=True,
        )
    if not row:
        raise AssertionError("No Buyback Assessment with an Active item found")
    return row[0].name


def _ensure_order(assessment_name: str) -> str:
    from ch_pos.api.pos_api import pos_start_buyback_order

    existing = frappe.db.get_value(
        "Buyback Order",
        {"buyback_assessment": assessment_name, "docstatus": ["!=", 2]},
        "name",
        order_by="creation desc",
    )
    if existing:
        return existing

    pos_profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
    if not pos_profile:
        raise AssertionError("No active POS Profile found")

    out = pos_start_buyback_order(assessment_name=assessment_name, pos_profile=pos_profile)
    return out["order_name"]


def _latest_pending_otp(order_name: str) -> str:
    row = frappe.db.sql(
        """
        SELECT otp_code
        FROM `tabCH OTP Log`
        WHERE reference_doctype = 'Buyback Order'
          AND reference_name = %(order_name)s
          AND purpose = 'Buyback Confirmation'
          AND status = 'Pending'
        ORDER BY creation DESC
        LIMIT 1
        """,
        {"order_name": order_name},
        as_dict=True,
    )
    if not row or not row[0].otp_code:
        raise AssertionError("No pending Buyback Confirmation OTP found")
    return row[0].otp_code


def run():
    frappe.set_user("Administrator")

    from ch_pos.api.pos_api import (
        pos_verify_otp_direct,
        pos_approve_customer_buyback,
    )
    from buyback.api import record_payment, close_order
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    assessment_name = _pick_assessment()
    order_name = _ensure_order(assessment_name)

    order = frappe.get_doc("Buyback Order", order_name)
    if not order.mobile_no:
        order.db_set("mobile_no", "9876543210", update_modified=True)
        order.reload()

    # Ensure a non-zero final_price so record_payment has something to
    # post. Picked assessments may have final_price=0 when the source
    # quote was a placeholder; we set a deterministic test value.
    if flt(order.final_price) <= 0:
        order.db_set("final_price", 1000.0, update_modified=True)
        order.reload()

    if order.status not in ("Approved", "Awaiting Customer Approval", "Awaiting OTP", "OTP Verified", "Ready to Pay", "Paid", "Closed"):
        order.db_set("status", "Approved", update_modified=True)
        order.reload()

    if order.status not in ("OTP Verified", "Ready to Pay", "Paid", "Closed"):
        # Keep this e2e deterministic in local/dev where SMTP may be unavailable.
        # We create the OTP log directly and run the same verify API the UI uses.
        if order.status != "Awaiting OTP":
            order.db_set("status", "Awaiting OTP", update_modified=True)
        otp_code = CHOTPLog.generate_otp(
            order.mobile_no,
            "Buyback Confirmation",
            reference_doctype="Buyback Order",
            reference_name=order_name,
        )
        verify_out = pos_verify_otp_direct(order_name=order_name, otp_code=otp_code)
        assert verify_out.get("verified"), "OTP verification did not return verified=true"

    order.reload()
    assert int(order.otp_verified or 0) == 1, f"otp_verified not set on {order.name}"
    assert int(order.customer_approved or 0) == 1, f"customer_approved not set on {order.name}"

    approve_out = pos_approve_customer_buyback(
        order_name=order_name,
        method="In-Store Signature",
        kyc_id_type="Aadhar Card",
        kyc_id_number="123412341234",
        settlement_type="Buyback",
        payout_mode="Cash",
    )
    assert approve_out.get("order_name") == order_name, "Approval response order mismatch"

    order.reload()
    if order.status not in ("Paid", "Closed"):
        payment_out = record_payment(
            order_name=order_name,
            payment_method="Cash",
            amount=flt(order.final_price),
        )
        assert payment_out.get("status") in ("Paid", "Ready to Pay", "OTP Verified"), (
            f"Unexpected status after record_payment: {payment_out}"
        )

    order.reload()
    if order.status != "Closed":
        close_out = close_order(order_name=order_name)
        assert close_out.get("status") == "Closed", f"Close failed: {close_out}"

    order.reload()
    assert order.status == "Closed", f"Order not closed, current status={order.status}"

    print(
        {
            "assessment": assessment_name,
            "order": order_name,
            "status": order.status,
            "otp_verified": int(order.otp_verified or 0),
            "customer_approved": int(order.customer_approved or 0),
            "kyc_verified": int(order.kyc_verified or 0),
            "final_price": flt(order.final_price),
        }
    )
