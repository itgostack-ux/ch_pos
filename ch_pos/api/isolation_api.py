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
from frappe.utils import flt, cint, now_datetime, getdate, nowdate

from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session


def _get_executive_permissions(exec_record):
    """Derive session permissions from POS Executive role.
    Manager gets all permissions; others get basic open/close."""
    if not exec_record:
        return {"can_open_session": 0, "can_close_session": 0, "can_approve_variance": 0, "can_do_cash_drop": 0}

    role = frappe.db.get_value("POS Executive", exec_record.name, "role") or "Executive"
    is_manager = role == "Manager"
    return {
        "can_open_session": 1,
        "can_close_session": 1,
        "can_approve_variance": 1 if is_manager else 0,
        "can_do_cash_drop": 1 if is_manager else 0,
    }


def _get_store_pos_profile(store, company=None):
    """Resolve the active POS Profile for a store.

    Prefer enabled mappings whose POS Profile company matches the store company.
    This avoids ambiguous results when legacy profiles still point to the same store.
    """
    filters = {"store": store}
    if frappe.get_meta("POS Profile Extension").has_field("disabled"):
        filters["disabled"] = 0

    mappings = frappe.get_all(
        "POS Profile Extension",
        filters=filters,
        fields=["pos_profile"],
        order_by="modified desc",
    )
    if not mappings:
        return None

    for row in mappings:
        profile_name = row.pos_profile
        profile = frappe.db.get_value(
            "POS Profile",
            profile_name,
            ["name", "company", "disabled"],
            as_dict=True,
        )
        if not profile or cint(profile.disabled):
            continue
        if company and profile.company == company:
            return profile.name

    for row in mappings:
        profile_name = row.pos_profile
        if not cint(frappe.db.get_value("POS Profile", profile_name, "disabled") or 0):
            return profile_name

    return None


def _ensure_store_business_date_is_not_future(store):
    business_date = getdate(frappe.db.get_value(
        "CH Business Date",
        {"store": store, "is_active": 1},
        "business_date",
    ) or nowdate())
    if business_date > getdate(nowdate()):
        frappe.throw(
            _("Store {0} has an invalid future business date {1}. Reset it before using POS.").format(
                store, business_date
            ),
            title=_("Invalid Business Date"),
        )
    return business_date


# ── POS Launch / Context ─────────────────────────────────────────────────────


@frappe.whitelist()
def get_pos_context() -> dict:
    """Called on POS launch. Auto-detects user→company→store→device→session.

    Returns everything the POS frontend needs to render the correct UI.
    System Manager users without an allocation get a store picker instead of
    being blocked.
    """
    frappe.has_permission("Sales Invoice", "read", throw=True)
    user = frappe.session.user

    # Get user's POS Executive record (primary source of truth)
    exec_record = frappe.db.get_value(
        "POS Executive",
        {"user": user, "is_active": 1},
        ["name", "company", "store"],
        as_dict=True,
    )

    # System Managers / Administrators always get a store picker
    is_system_manager = "System Manager" in frappe.get_roles(user) or user == "Administrator"
    if is_system_manager:
        return {
            "status": "select_store",
            "message": _("You have System Manager access. Select a store to continue."),
            "stores": _get_all_active_stores(),
            "default_store": exec_record.store if exec_record else None,
        }

    if not exec_record:
        return {
            "status": "no_allocation",
            "message": _("You have no active POS Executive record. Contact your manager."),
        }

    company = exec_record.company
    store = exec_record.store
    device_name = None  # device resolved below if CH Device Master exists

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
    business_date = _ensure_store_business_date_is_not_future(store) if store else None

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
        elif getdate(business_date) < getdate(nowdate()):
            # Stale date: business date is in the past — day is unusable
            day_closed = True

    return {
        "status": "ok",
        "user": user,
        "company": company,
        "store": store,
        "device": device,
        "business_date": str(business_date) if business_date else None,
        "day_closed": day_closed,
        "allocation": _get_executive_permissions(exec_record),
        "existing_session": {
            "name": existing_session.name,
            "user": existing_session.user,
            "status": existing_session.status,
            "opening_cash": flt(existing_session.opening_cash),
            "pos_profile": existing_session.pos_profile,
        } if existing_session else None,
    }


@frappe.whitelist()
def get_pos_context_for_store(store) -> dict:
    """Admin/System Manager endpoint: get POS context for a specific store.

    Builds a synthetic allocation so the admin can operate any store.
    """
    frappe.has_permission("Sales Invoice", "read", throw=True)
    user = frappe.session.user

    if "System Manager" not in frappe.get_roles(user) and user != "Administrator":
        frappe.throw(_("Only System Managers can use store override."), title=_("API Error"))

    store_doc = frappe.db.get_value(
        "CH Store", store,
        ["name", "store_name", "company", "warehouse"],
        as_dict=True,
    )
    if not store_doc:
        frappe.throw(_("Store {0} not found.").format(store), title=_("API Error"))

    company = store_doc.company

    from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_store_business_date
    business_date = _ensure_store_business_date_is_not_future(store)

    # Check if day is closed
    day_closed = False
    bd_status = frappe.db.get_value("CH Business Date", {"store": store}, "status")
    if bd_status == "Closed":
        day_closed = True
    elif getdate(business_date) < getdate(nowdate()):
        # Stale date: business date is in the past — day is unusable
        day_closed = True

    # Find POS Profile for this store
    pos_profile = _get_store_pos_profile(store, company=company)

    return {
        "status": "ok",
        "user": user,
        "company": company,
        "store": store,
        "device": None,
        "business_date": str(business_date) if business_date else None,
        "day_closed": day_closed,
        "pos_profile": pos_profile,
        "allocation": {
            "can_open_session": 1,
            "can_close_session": 1,
            "can_approve_variance": 1,
            "can_do_cash_drop": 1,
        },
        "existing_session": None,
        "admin_override": True,
    }


def _get_all_active_stores():
    """Return list of active stores for System Manager store picker."""
    stores = frappe.get_all(
        "CH Store",
        filters={"disabled": 0},
        fields=["name", "store_name", "store_code", "company", "warehouse"],
        order_by="store_name asc",
    )
    return stores

# ── Lock / Unlock ────────────────────────────────────────────────────────────


@frappe.whitelist()
def lock_session(session_name) -> dict:
    """Lock screen — temporary pause, no financial impact."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    session = frappe.get_doc("CH POS Session", session_name)
    session.lock_session()
    return {"status": "Locked"}


@frappe.whitelist()
def unlock_session(session_name, password=None) -> dict:
    """Unlock session — resume from lock screen."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    session = frappe.get_doc("CH POS Session", session_name)
    session.unlock_session()
    return {"status": "Open"}


# ── Settlement ───────────────────────────────────────────────────────────────


@frappe.whitelist()
def create_settlement(session_name, actual_closing_cash, denominations=None,
                      variance_reason=None, manager_pin=None) -> dict:
    """Create a CH POS Settlement for a session. Called before session close."""
    frappe.has_permission("Sales Invoice", "create", throw=True)

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status not in ("Open", "Locked", "Pending Close"):
        frappe.throw(_("Session {0} is not in a settleable state.").format(session_name), title=_("API Error"))

    # Check no existing settlement
    existing = frappe.db.get_value(
        "CH POS Settlement",
        {"session": session_name, "docstatus": ("!=", 2)},
        "name",
    )
    if existing:
        frappe.throw(_("Settlement {0} already exists for this session.").format(existing), title=_("API Error"))

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
    denom_total = 0
    if denomination_rows:
        for d in denomination_rows:
            denom_amount = flt(d.get("denomination")) * int(d.get("count", d.get("quantity", 0)))
            denom_total += denom_amount
            settlement.append("denomination_details", {
                "note_or_coin": d.get("note_or_coin", "Note"),
                "denomination": flt(d.get("denomination")),
                "count": int(d.get("count", d.get("quantity", 0))),
                "amount": denom_amount,
            })

        # Validate denomination sum matches declared closing cash
        if abs(denom_total - flt(actual_closing_cash)) > 1:
            frappe.throw(
                _("Denomination total ({0}) does not match declared closing cash ({1}).").format(
                    denom_total, flt(actual_closing_cash)
                )
            )

    if manager_user:
        settlement.signoff_by_manager = manager_user
        settlement.manager_signoff_time = now_datetime()

    settlement.insert(ignore_permissions=True)
    settlement.submit()

    # Move session to Pending Close
    session.db_set("status", "Pending Close")

    return {
        "settlement_name": settlement.name,
        "expected_closing_cash": flt(settlement.expected_closing_cash),
        "actual_closing_cash": flt(settlement.actual_closing_cash),
        "variance": flt(settlement.variance_amount),
        "session_status": "Pending Close",
    }


@frappe.whitelist()
def create_cash_movement(session_name, movement_type, amount, reason,
                         manager_pin=None, remarks=None) -> dict:
    """Create a CH Cash Drop (cash movement) during an active session."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    amount = flt(amount)
    if amount <= 0:
        frappe.throw(_("Amount must be positive"), title=_("API Error"))

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status not in ("Open", "Locked"):
        frappe.throw(_("Session is not in an active state for cash movement."), title=_("API Error"))

    # Manager PIN for sensitive types
    from ch_pos.pos_core.doctype.ch_cash_drop.ch_cash_drop import APPROVAL_REQUIRED_TYPES
    manager_user = None
    if movement_type in APPROVAL_REQUIRED_TYPES or manager_pin:
        if not manager_pin:
            frappe.throw(_("{0} requires manager approval.").format(movement_type), title=_("API Error"))
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
