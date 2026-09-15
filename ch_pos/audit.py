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
from frappe.utils import now_datetime, nowdate


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


def log_privileged_bypass(
    gate: str,
    user: str | None = None,
    store: str | None = None,
    company: str | None = None,
    remarks: str | None = None,
    throttle: bool = False,
):
    """Record that a privileged-user check (is_privileged_user) let someone skip
    an identity/scope gate.

    `user` is the privileged identity whose check was skipped — e.g. the
    manager candidate in a PIN match, not necessarily the calling session —
    so a bypass can always be traced to whose privilege caused it, with the
    acting session (when different) folded into `remarks` for context.

    `throttle=True` collapses a gate to one row per user/store/day. Reserve it
    for the read-path gates that run on every request — without it they bury
    the rare, deliberate bypasses (a settlement signature re-stamp, a
    self-approved cash drop) under thousands of identical rows. Never throttle
    a money or identity event: each one is its own occurrence.
    """
    if throttle and _bypass_seen_today(gate, user or frappe.session.user, store):
        return
    note = gate if not remarks else f"{gate} — {remarks}"
    log_business_event(
        event_type="Privileged Bypass",
        remarks=note,
        store=store,
        company=company,
        user=user,
    )


def _bypass_seen_today(gate: str, user: str, store: str | None) -> bool:
    """True once this user has already tripped this gate at this store today.

    Cache failures fall through to logging: a missing throttle costs a
    duplicate row, a swallowed one costs the audit trail.
    """
    key = f"ch_pos:bypass:{gate}:{user}:{store or '-'}:{nowdate()}"
    try:
        cache = frappe.cache()
        if cache.get_value(key):
            return True
        cache.set_value(key, 1, expires_in_sec=86400)
    except Exception:  # noqa: BLE001 — any cache/redis fault must still let the gate log
        return False
    return False


def _to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)
