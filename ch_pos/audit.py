# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
ch_pos.audit — Business Audit Log helper.

Usage:
    from ch_pos.audit import log_business_event

    log_business_event(
        event_type="Discount Override",
        ref_doctype="POS Invoice",
        ref_name=inv.name,
        before="0%",
        after="15%",
        remarks="Approved by manager: clearance sale",
        store=profile.warehouse,
        company=profile.company,
    )

All writes are best-effort — a failure to audit must never block a business
transaction. Errors are captured in frappe.log_error for ops review.
"""

import frappe
from frappe.utils import now_datetime


def log_business_event(
    event_type: str,
    ref_doctype: str = None,
    ref_name: str = None,
    before=None,
    after=None,
    remarks: str = None,
    store: str = None,
    company: str = None,
    user: str = None,
):
    """Insert a CH Business Audit Log record.

    Best-effort: exceptions are logged but never re-raised.
    """
    try:
        doc = frappe.new_doc("CH Business Audit Log")
        doc.event_type = event_type
        doc.reference_doctype = ref_doctype
        doc.reference_name = ref_name
        doc.before_value = _to_str(before)
        doc.after_value = _to_str(after)
        doc.remarks = remarks
        doc.store = store
        doc.company = company
        doc.user = user or frappe.session.user
        doc.timestamp = now_datetime()
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Audit log failed: {event_type} on {ref_name}")


def _to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)
