# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
ch_pos.audit — Business Audit Log helper.

Usage:
    from ch_pos.audit import log_business_event

    log_business_event(
        event_type="Discount Override",
        ref_doctype="Sales Invoice",
        ref_name=inv.name,
        before="0%",
        after="15%",
        remarks="Approved by manager: clearance sale",
        store=profile.warehouse,
        company=profile.company,
    )

Writes are best-effort by default. Critical state transitions can require the
audit insert to succeed in the same request transaction.
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
    raise_on_error: bool = False,
):
    """Insert a CH Business Audit Log record."""
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
        # The referenced document (ref_doctype/ref_name) may be mid-creation —
        # e.g. a new Sales Invoice being audited from inside its own validate(),
        # before it has been inserted. An audit trail describing an in-flight
        # event must not fail just because the thing it describes isn't
        # committed yet, so link existence is not enforced here.
        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Audit log failed: {event_type} on {ref_name}")
        if raise_on_error:
            raise


def _to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)
