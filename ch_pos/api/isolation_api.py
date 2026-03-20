"""
POS Isolation API — strict company/device/session enforcement for POS operations.

This module provides:
1. Auto-detection of user's company, store, device on login
2. Session context retrieval for POS frontend
3. Server-side isolation validations (doc_events)
4. Lock/unlock/settlement APIs
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, getdate

from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session
from ch_pos.pos_core.doctype.ch_pos_user_allocation.ch_pos_user_allocation import (
    get_user_allocation,
    get_user_company,
)


# ── POS Launch / Context ─────────────────────────────────────────────────────


@frappe.whitelist()
def get_pos_context():
    """Called on POS launch. Auto-detects user→company→store→device→session.

    Returns everything the POS frontend needs to render the correct UI.
    """
    frappe.has_permission("Sales Invoice", "read", throw=True)
    user = frappe.session.user

    # Get user allocation
    alloc = get_user_allocation(user)
    if not alloc:
        return {
            "status": "no_allocation",
            "message": _("You are not allocated to any company for POS operations. Contact your manager."),
        }

    company = alloc.company
    store = alloc.store
    device_name = alloc.default_device

    # Resolve device
    device = None
    if device_name:
        device = frappe.db.get_value(
            "CH Device Master", device_name,
            ["name", "device_name", "company", "store", "pos_profile", "warehouse", "is_active"],
            as_dict=True,
        )
        if device and not device.is_active:
            device = None

    # Get business date
    from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_store_business_date
    business_date = get_store_business_date(store) if store else None

    # Check for existing open session
    existing_session = None
    if device:
        existing_session = frappe.db.get_value(
            "CH POS Session",
            {
                "device": device.name,
                "business_date": business_date,
                "status": ("in", ["Open", "Locked", "Suspended"]),
                "docstatus": 1,
            },
            ["name", "user", "status", "opening_cash", "pos_profile"],
            as_dict=True,
        )

    # Check if day is closed
    day_closed = False
    if store and business_date:
        bd_status = frappe.db.get_value("CH Business Date", {"store": store}, "status")
        if bd_status == "Closed":
            day_closed = True

    return {
        "status": "ok",
        "user": user,
        "company": company,
        "store": store,
        "device": device,
        "business_date": str(business_date) if business_date else None,
        "day_closed": day_closed,
        "allocation": {
            "can_open_session": cint(alloc.can_open_session),
            "can_close_session": cint(alloc.can_close_session),
            "can_approve_variance": cint(alloc.can_approve_variance),
            "can_do_cash_drop": cint(alloc.can_do_cash_drop),
        },
        "existing_session": {
            "name": existing_session.name,
            "user": existing_session.user,
            "status": existing_session.status,
            "opening_cash": flt(existing_session.opening_cash),
            "pos_profile": existing_session.pos_profile,
        } if existing_session else None,
    }


# ── Lock / Unlock ────────────────────────────────────────────────────────────


@frappe.whitelist()
def lock_session(session_name):
    """Lock screen — temporary pause, no financial impact."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    session = frappe.get_doc("CH POS Session", session_name)
    session.lock_session()
    frappe.db.commit()
    return {"status": "Locked"}


@frappe.whitelist()
def unlock_session(session_name, password=None):
    """Unlock session — resume from lock screen."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    session = frappe.get_doc("CH POS Session", session_name)
    session.unlock_session()
    frappe.db.commit()
    return {"status": "Open"}


# ── Settlement ───────────────────────────────────────────────────────────────


@frappe.whitelist()
def create_settlement(session_name, actual_closing_cash, denominations=None,
                      variance_reason=None, manager_pin=None):
    """Create a CH POS Settlement for a session. Called before session close."""
    frappe.has_permission("Sales Invoice", "create", throw=True)

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status not in ("Open", "Locked", "Pending Close"):
        frappe.throw(_("Session {0} is not in a settleable state.").format(session_name))

    # Check no existing settlement
    existing = frappe.db.get_value(
        "CH POS Settlement",
        {"session": session_name, "docstatus": ("!=", 2)},
        "name",
    )
    if existing:
        frappe.throw(_("Settlement {0} already exists for this session.").format(existing))

    denomination_rows = frappe.parse_json(denominations) if denominations else []

    # Manager PIN for variance approval
    manager_user = None
    if manager_pin:
        from ch_pos.pos_core.doctype.ch_manager_pin.ch_manager_pin import verify_manager_pin
        pin_result = verify_manager_pin(manager_pin, store=session.store, permission="can_approve_closing")
        if not pin_result.get("valid"):
            frappe.throw(pin_result.get("message", _("Invalid manager PIN")))
        manager_user = pin_result["user"]

    # Create settlement
    settlement = frappe.get_doc({
        "doctype": "CH POS Settlement",
        "session": session_name,
        "company": session.company,
        "store": session.store,
        "device": session.device,
        "business_date": session.business_date,
        "actual_closing_cash": flt(actual_closing_cash),
        "variance_reason": variance_reason or "",
        "signoff_by_user": frappe.session.user,
        "signoff_time": now_datetime(),
    })

    # Auto-compute sales/cash totals from transactions
    settlement.calculate_from_transactions()

    # Denomination breakdown
    if denomination_rows:
        for d in denomination_rows:
            settlement.append("denomination_details", {
                "note_or_coin": d.get("note_or_coin", "Note"),
                "denomination": flt(d.get("denomination")),
                "count": int(d.get("count", d.get("quantity", 0))),
                "amount": flt(d.get("denomination")) * int(d.get("count", d.get("quantity", 0))),
            })

    if manager_user:
        settlement.signoff_by_manager = manager_user
        settlement.manager_signoff_time = now_datetime()

    settlement.insert(ignore_permissions=True)
    settlement.submit()

    # Move session to Pending Close
    session.db_set("status", "Pending Close")

    frappe.db.commit()

    return {
        "settlement_name": settlement.name,
        "expected_closing_cash": flt(settlement.expected_closing_cash),
        "actual_closing_cash": flt(settlement.actual_closing_cash),
        "variance": flt(settlement.variance_amount),
        "session_status": "Pending Close",
    }


@frappe.whitelist()
def create_cash_movement(session_name, movement_type, amount, reason,
                         manager_pin=None, remarks=None):
    """Create a CH Cash Drop (cash movement) during an active session."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    amount = flt(amount)
    if amount <= 0:
        frappe.throw(_("Amount must be positive"))

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status not in ("Open", "Locked"):
        frappe.throw(_("Session is not in an active state for cash movement."))

    # Manager PIN for sensitive types
    from ch_pos.pos_core.doctype.ch_cash_drop.ch_cash_drop import APPROVAL_REQUIRED_TYPES
    manager_user = None
    if movement_type in APPROVAL_REQUIRED_TYPES or manager_pin:
        if not manager_pin:
            frappe.throw(_("{0} requires manager approval.").format(movement_type))
        from ch_pos.pos_core.doctype.ch_manager_pin.ch_manager_pin import verify_manager_pin
        pin_result = verify_manager_pin(manager_pin, store=session.store, permission="can_approve_cash_drop")
        if not pin_result.get("valid"):
            frappe.throw(pin_result.get("message", _("Invalid manager PIN")))
        manager_user = pin_result["user"]

    movement = frappe.get_doc({
        "doctype": "CH Cash Drop",
        "session": session_name,
        "company": session.company,
        "store": session.store,
        "device": session.device,
        "business_date": session.business_date,
        "movement_type": movement_type,
        "amount": amount,
        "reason": reason,
        "remarks": remarks or "",
        "created_by": frappe.session.user,
        "posting_time": now_datetime(),
        "approved_by": manager_user or "",
        "approved_at": now_datetime() if manager_user else None,
    })
    movement.insert(ignore_permissions=True)
    movement.submit()
    frappe.db.commit()

    return {
        "movement_name": movement.name,
        "amount": amount,
        "approved_by": manager_user,
    }


# ── Sales Invoice Isolation Validators (doc_events) ───────────────────────────


def validate_pos_invoice_isolation(doc, method=None):
    """Validate that Sales Invoice company/session/device are consistent.

    Called as doc_event on Sales Invoice validate.
    """
    if not doc.pos_profile:
        return

    # Only enforce if isolation settings are enabled
    try:
        enforce = cint(frappe.db.get_single_value("CH POS Control Settings", "block_cross_company_transactions"))
    except Exception:
        enforce = 0

    if not enforce:
        return

    # Get active session for this POS Profile
    session = get_active_session(doc.pos_profile)
    if not session:
        frappe.throw(
            _("No active POS session for profile {0}. Open a session before billing.").format(
                doc.pos_profile
            )
        )

    # Validate company match
    if session.company and doc.company != session.company:
        frappe.throw(
            _("Invoice company {0} does not match session company {1}. "
              "Cross-company billing is blocked.").format(doc.company, session.company)
        )

    # Set session/device/business_date on invoice custom fields (if they exist)
    if frappe.get_meta("Sales Invoice").has_field("ch_pos_session"):
        doc.ch_pos_session = session.name
    if frappe.get_meta("Sales Invoice").has_field("ch_device"):
        doc.ch_device = session.get("device")
    if frappe.get_meta("Sales Invoice").has_field("ch_business_date"):
        doc.ch_business_date = session.business_date


def validate_no_post_close_transaction(doc, method=None):
    """Block billing after session is closed."""
    if not doc.pos_profile:
        return

    session = get_active_session(doc.pos_profile)
    if not session:
        # Check if there's a closed session for today (post-close attempt)
        from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_store_business_date
        store = frappe.db.get_value("POS Profile Extension", {"pos_profile": doc.pos_profile}, "store")
        if store:
            business_date = get_store_business_date(store)
            closed = frappe.db.exists(
                "CH POS Session",
                {"pos_profile": doc.pos_profile, "business_date": business_date,
                 "status": "Closed", "docstatus": 1},
            )
            if closed:
                frappe.throw(
                    _("Session for {0} on {1} is already closed. No billing allowed after session close.").format(
                        doc.pos_profile, business_date
                    )
                )
