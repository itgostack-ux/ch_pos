"""
Session & Cash Control API endpoints for the CH POS frontend.

All endpoints are whitelisted and called from the POS UI.
They integrate with:
- CH POS Session (session lifecycle)
- CH Business Date (store business date)
- CH Manager PIN (quick approval)
- CH Cash Drop (safe transfers)
- ERPNext POS Opening Entry (GL linkage)
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, nowdate, now_datetime, getdate

from ch_pos.pos_core.doctype.ch_manager_pin.ch_manager_pin import verify_manager_pin
from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import (
    get_active_session,
    get_store_business_date,
)


# ── Session Lifecycle ────────────────────────────────────────────────────────


@frappe.whitelist()
def get_session_status(pos_profile):
    """Check if an active session exists for this profile.
    Called on POS startup to decide: show opening screen or resume."""
    frappe.has_permission("POS Invoice", "read", throw=True)

    session = get_active_session(pos_profile)
    if session:
        return {
            "has_session": True,
            "session_name": session.name,
            "user": session.user,
            "business_date": str(session.business_date),
            "opening_cash": flt(session.opening_cash),
            "store": session.store,
        }

    # Check for unclosed sessions (must close before opening new)
    unclosed = frappe.db.get_value(
        "CH POS Session",
        {"pos_profile": pos_profile, "status": ("in", ["Open", "Suspended"]), "docstatus": 1},
        ["name", "user", "business_date"],
        as_dict=True,
    )
    if unclosed:
        return {
            "has_session": False,
            "unclosed_session": unclosed.name,
            "unclosed_user": unclosed.user,
            "unclosed_date": str(unclosed.business_date),
        }

    return {"has_session": False}


@frappe.whitelist()
def open_session(pos_profile, opening_cash, manager_pin=None):
    """Open a new POS session. Called from the POS opening screen."""
    frappe.has_permission("POS Invoice", "create", throw=True)
    opening_cash = flt(opening_cash)

    # Get store from POS Profile Extension
    store = frappe.db.get_value(
        "POS Profile Extension", {"pos_profile": pos_profile}, "store"
    )
    if not store:
        # Fallback: look up CH Store via warehouse on POS Profile
        warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
        if warehouse:
            store = frappe.db.get_value("CH Store", {"warehouse": warehouse}, "name")
    if not store:
        frappe.throw(_("No CH Store configured for POS Profile {0}. Set it on POS Profile Extension.").format(pos_profile))

    # Get business date
    business_date = get_store_business_date(store)

    # Check for unclosed sessions
    unclosed = frappe.db.exists(
        "CH POS Session",
        {"pos_profile": pos_profile, "status": ("in", ["Open", "Suspended"]), "docstatus": 1},
    )
    if unclosed:
        frappe.throw(
            _("Session {0} is still open. Close it before opening a new one.").format(unclosed)
        )

    # Manager PIN verification for opening approval
    manager_user = None
    if manager_pin:
        pin_result = verify_manager_pin(manager_pin, store=store, permission="can_approve_opening")
        if not pin_result.get("valid"):
            frappe.throw(pin_result.get("message", _("Invalid manager PIN")))
        manager_user = pin_result["user"]

    # Validate opening cash against previous closing / expected float
    expected_float = _get_expected_float(pos_profile, store)

    # Create ERPNext POS Opening Entry (for GL linkage)
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    company = profile.company or frappe.defaults.get_default("company")
    balance_details = []
    for p in (profile.payments or []):
        amt = opening_cash if (frappe.db.get_value("Mode of Payment", p.mode_of_payment, "type") == "Cash") else 0
        balance_details.append({
            "mode_of_payment": p.mode_of_payment,
            "opening_amount": amt,
        })

    opening_entry = frappe.get_doc({
        "doctype": "POS Opening Entry",
        "pos_profile": pos_profile,
        "company": company,
        "user": frappe.session.user,
        "period_start_date": now_datetime(),
        "balance_details": balance_details,
    })
    opening_entry.insert(ignore_permissions=True)
    opening_entry.submit()

    # Create CH POS Session
    session = frappe.get_doc({
        "doctype": "CH POS Session",
        "pos_profile": pos_profile,
        "store": store,
        "user": frappe.session.user,
        "business_date": business_date,
        "shift_start": now_datetime(),
        "opening_cash": opening_cash,
        "expected_float": expected_float,
        "opening_approved_by": manager_user or "",
        "opening_approved_at": now_datetime() if manager_user else None,
        "pos_opening_entry": opening_entry.name,
        "status": "Open",
    })
    session.insert(ignore_permissions=True)
    session.submit()

    frappe.db.commit()

    return {
        "session_name": session.name,
        "business_date": str(business_date),
        "store": store,
        "opening_cash": opening_cash,
        "expected_float": expected_float,
        "pos_opening_entry": opening_entry.name,
    }


@frappe.whitelist()
def close_session(session_name, closing_cash, denominations=None,
                  variance_reason=None, manager_pin=None):
    """Close a POS session with cash reconciliation. Called from POS closing dashboard."""
    frappe.has_permission("POS Invoice", "create", throw=True)

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status != "Open":
        frappe.throw(_("Session {0} is not open").format(session_name))

    # Parse denominations
    denomination_rows = frappe.parse_json(denominations) if denominations else []

    # Manager PIN for variance approval (checked inside close_session if variance > threshold)
    manager_user = None
    if manager_pin:
        pin_result = verify_manager_pin(
            manager_pin, store=session.store, permission="can_approve_closing"
        )
        if not pin_result.get("valid"):
            frappe.throw(pin_result.get("message", _("Invalid manager PIN")))
        manager_user = pin_result["user"]

    session.close_session(
        closing_cash=closing_cash,
        denomination_rows=denomination_rows,
        variance_reason=variance_reason,
        manager_pin_user=manager_user,
    )

    frappe.db.commit()

    return {
        "status": "Closed",
        "cash_variance": flt(session.cash_variance),
        "total_invoices": session.total_invoices,
        "net_sales": flt(session.net_sales),
        "total_cash_drops": flt(session.total_cash_drops),
    }


@frappe.whitelist()
def switch_user(session_name, new_user, manager_pin):
    """Switch cashier on an active session. Requires manager PIN."""
    frappe.has_permission("POS Invoice", "create", throw=True)

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status != "Open":
        frappe.throw(_("Session is not open"))

    pin_result = verify_manager_pin(manager_pin, store=session.store, permission="can_approve_opening")
    if not pin_result.get("valid"):
        frappe.throw(pin_result.get("message", _("Invalid manager PIN")))

    old_user = session.user
    session.db_set("user", new_user)

    try:
        from ch_pos.audit import log_business_event
        log_business_event(
            event_type="Cashier Switch",
            ref_doctype="CH POS Session",
            ref_name=session_name,
            before=old_user,
            after=new_user,
            remarks=f"Approved by {pin_result['name']}",
        )
    except Exception:
        pass

    return {"user": new_user, "approved_by": pin_result["name"]}


# ── Cash Drop ────────────────────────────────────────────────────────────────


@frappe.whitelist()
def create_cash_drop(session_name, amount, reason, manager_pin):
    """Create a cash drop (register → safe) during an active session."""
    frappe.has_permission("POS Invoice", "create", throw=True)
    amount = flt(amount)
    if amount <= 0:
        frappe.throw(_("Amount must be positive"))

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status != "Open":
        frappe.throw(_("Session is not open"))

    pin_result = verify_manager_pin(manager_pin, store=session.store, permission="can_approve_cash_drop")
    if not pin_result.get("valid"):
        frappe.throw(pin_result.get("message", _("Invalid manager PIN")))

    drop = frappe.get_doc({
        "doctype": "CH Cash Drop",
        "session": session_name,
        "user": frappe.session.user,
        "amount": amount,
        "reason": reason,
        "approved_by": pin_result["user"],
        "approved_at": now_datetime(),
    })
    drop.insert(ignore_permissions=True)
    drop.submit()
    frappe.db.commit()

    return {
        "drop_name": drop.name,
        "amount": amount,
        "approved_by": pin_result["name"],
    }


# ── Business Date ────────────────────────────────────────────────────────────


@frappe.whitelist()
def get_business_date(store):
    """Get the current business date for a store."""
    return {
        "business_date": str(get_store_business_date(store)),
        "system_date": str(nowdate()),
    }


@frappe.whitelist()
def override_business_date(store, new_date, reason, manager_pin):
    """Override the business date. Requires manager with override permission."""
    frappe.has_permission("POS Invoice", "create", throw=True)

    pin_result = verify_manager_pin(
        manager_pin, store=store, permission="can_override_business_date"
    )
    if not pin_result.get("valid"):
        frappe.throw(pin_result.get("message", _("Invalid manager PIN")))

    from ch_pos.pos_core.doctype.ch_business_date.ch_business_date import advance_business_date
    result = advance_business_date(store, new_date, reason, manager_user=pin_result["user"])
    return {
        "business_date": str(result.get("business_date")),
        "set_by": pin_result["name"],
    }


# ── Manager PIN verification (for POS UI) ───────────────────────────────────


@frappe.whitelist()
def verify_pin(pin, store=None, permission=None):
    """Verify a manager PIN from the POS UI."""
    frappe.has_permission("POS Invoice", "read", throw=True)
    return verify_manager_pin(pin, store=store, permission=permission)


# ── X Report / Z Report ─────────────────────────────────────────────────────


@frappe.whitelist()
def get_x_report(session_name):
    """X Report — interim session report (during shift). Does not close session."""
    frappe.has_permission("POS Invoice", "read", throw=True)
    session = frappe.get_doc("CH POS Session", session_name)

    # Fetch live invoice data
    invoices = frappe.get_all(
        "POS Invoice",
        filters={
            "pos_profile": session.pos_profile,
            "docstatus": 1,
            "consolidated_invoice": ("in", [None, ""]),
            "posting_date": session.business_date,
        },
        fields=["name", "grand_total", "is_return", "total_taxes_and_charges",
                "posting_time", "customer_name"],
    )

    # Payment mode breakdown
    payment_rows = frappe.db.sql("""
        SELECT sip.mode_of_payment, SUM(sip.amount) AS total
        FROM `tabPOS Invoice` pi
        JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
        WHERE pi.pos_profile = %(pp)s
          AND pi.docstatus = 1
          AND IFNULL(pi.consolidated_invoice, '') = ''
          AND pi.posting_date = %(bd)s
        GROUP BY sip.mode_of_payment
    """, {"pp": session.pos_profile, "bd": session.business_date}, as_dict=True)

    total_sales = sum(flt(i.grand_total) for i in invoices if not i.is_return)
    total_returns = sum(abs(flt(i.grand_total)) for i in invoices if i.is_return)
    total_tax = sum(flt(i.total_taxes_and_charges) for i in invoices if not i.is_return)

    # Cash drops
    total_drops = flt(frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0)
        FROM `tabCH Cash Drop`
        WHERE session = %s AND docstatus = 1
    """, session_name)[0][0])

    # Cash expected
    cash_mode_total = sum(flt(r.total) for r in payment_rows
                          if frappe.db.get_value("Mode of Payment", r.mode_of_payment, "type") == "Cash")

    return {
        "session_name": session.name,
        "store": session.store,
        "pos_profile": session.pos_profile,
        "business_date": str(session.business_date),
        "cashier": session.user,
        "shift_start": str(session.shift_start),
        "opening_cash": flt(session.opening_cash),
        "invoices_count": len([i for i in invoices if not i.is_return]),
        "returns_count": len([i for i in invoices if i.is_return]),
        "total_sales": total_sales,
        "total_returns": total_returns,
        "net_sales": total_sales - total_returns,
        "total_tax": total_tax,
        "payment_modes": [{"mode": r.mode_of_payment, "total": flt(r.total)} for r in payment_rows],
        "cash_in_drawer": flt(session.opening_cash) + cash_mode_total - total_drops,
        "total_cash_drops": total_drops,
    }


@frappe.whitelist()
def get_z_report(store, business_date):
    """Z Report — end-of-day store summary across all sessions."""
    frappe.has_permission("POS Invoice", "read", throw=True)
    business_date = getdate(business_date)

    sessions = frappe.get_all(
        "CH POS Session",
        filters={"store": store, "business_date": business_date, "docstatus": 1},
        fields=["name", "user", "status", "shift_start", "shift_end",
                "opening_cash", "closing_cash_actual", "cash_variance",
                "total_invoices", "net_sales", "total_cash_drops"],
        order_by="shift_start asc",
    )

    # Aggregate payment modes across all sessions
    payment_rows = frappe.db.sql("""
        SELECT sip.mode_of_payment, SUM(sip.amount) AS total
        FROM `tabPOS Invoice` pi
        JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
        WHERE pi.docstatus = 1
          AND IFNULL(pi.consolidated_invoice, '') = ''
          AND pi.posting_date = %(bd)s
          AND pi.pos_profile IN (
              SELECT pos_profile FROM `tabCH POS Session`
              WHERE store = %(store)s AND business_date = %(bd)s AND docstatus = 1
          )
        GROUP BY sip.mode_of_payment
    """, {"store": store, "bd": business_date}, as_dict=True)

    total_invoices = sum(s.total_invoices or 0 for s in sessions)
    total_net_sales = sum(flt(s.net_sales) for s in sessions)
    total_variance = sum(flt(s.cash_variance) for s in sessions)
    total_drops = sum(flt(s.total_cash_drops) for s in sessions)
    all_closed = all(s.status == "Closed" for s in sessions) if sessions else False

    return {
        "store": store,
        "business_date": str(business_date),
        "sessions": sessions,
        "total_sessions": len(sessions),
        "total_invoices": total_invoices,
        "total_net_sales": total_net_sales,
        "total_variance": total_variance,
        "total_cash_drops": total_drops,
        "all_sessions_closed": all_closed,
        "payment_modes": [{"mode": r.mode_of_payment, "total": flt(r.total)} for r in payment_rows],
    }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_expected_float(pos_profile, store):
    """Get expected opening float from previous session's closing, or store default."""
    last_session = frappe.db.get_value(
        "CH POS Session",
        {"pos_profile": pos_profile, "status": "Closed", "docstatus": 1},
        "closing_cash_actual",
        order_by="shift_end desc",
    )
    if last_session is not None:
        return flt(last_session)
    # Default float from POS Profile Extension
    default_float = frappe.db.get_value(
        "POS Profile Extension", {"pos_profile": pos_profile}, "default_float"
    )
    return flt(default_float) if default_float else 0.0
