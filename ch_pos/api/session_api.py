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
from frappe.utils import flt, cint, nowdate, now_datetime, getdate, add_days

from ch_pos.pos_core.doctype.ch_manager_pin.ch_manager_pin import verify_manager_pin
from ch_pos.pos_core.doctype.ch_pos_settlement.ch_pos_settlement import build_settlement_snapshot
from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import (
    get_active_session,
    get_store_business_date,
)


# ── Session Lifecycle ────────────────────────────────────────────────────────


@frappe.whitelist()
def get_session_status(pos_profile):
    """Check if an active session exists for this profile.
    Called on POS startup to decide: show opening screen or resume."""
    frappe.has_permission("Sales Invoice", "read", throw=True)

    session = get_active_session(pos_profile)
    if session:
        return {
            "has_session": True,
            "session_name": session.name,
            "user": session.user,
            "business_date": str(session.business_date),
            "opening_cash": flt(session.opening_cash),
            "store": session.store,
            "company": session.get("company"),
            "device": session.get("device"),
            "session_status": session.get("status"),
        }

    # Resolve store for this profile so we can enforce one active session per store.
    store = frappe.db.get_value("POS Profile Extension", {"pos_profile": pos_profile}, "store")
    if not store:
        warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
        if warehouse:
            store = frappe.db.get_value("CH Store", {"warehouse": warehouse}, "name")

    # Check for unclosed sessions (must close before opening new), at store level first.
    unclosed = None
    if store:
        unclosed = frappe.db.get_value(
            "CH POS Session",
            {
                "store": store,
                "status": ("in", ["Open", "Suspended", "Closing"]),
                "docstatus": 1,
            },
            ["name", "user", "business_date", "pos_profile"],
            as_dict=True,
        )

    if not unclosed:
        unclosed = frappe.db.get_value(
            "CH POS Session",
            {"pos_profile": pos_profile, "status": ("in", ["Open", "Suspended", "Closing"]), "docstatus": 1},
            ["name", "user", "business_date", "pos_profile"],
            as_dict=True,
        )

    if unclosed:
        return {
            "has_session": False,
            "unclosed_session": unclosed.name,
            "unclosed_user": unclosed.user,
            "unclosed_date": str(unclosed.business_date),
            "unclosed_profile": unclosed.pos_profile,
        }

    # If the day is already closed for this store, don't allow reopening.
    if store:
        business_date = get_store_business_date(store)
        day_closed = False
        bd_status = frappe.db.get_value("CH Business Date", {"store": store}, "status")
        if bd_status == "Closed":
            day_closed = True
        elif getdate(business_date) < getdate(nowdate()):
            # Stale date: business date is in the past — day is unusable
            day_closed = True
        else:
            # Check for a closed session on this business date
            day_closed = frappe.db.exists(
                "CH POS Session",
                {
                    "store": store,
                    "business_date": business_date,
                    "status": "Closed",
                    "docstatus": 1,
                },
            )
        if day_closed:
            return {
                "has_session": False,
                "day_closed": True,
                "store": store,
                "business_date": str(business_date),
                "message": _(
                    "Business date {0} is already closed for store {1}. "
                    "New session can start only after settlement completion and business-date advance."
                ).format(business_date, store),
            }

    return {"has_session": False}


@frappe.whitelist()
def open_session(pos_profile, opening_cash, manager_pin=None, device=None):
    """Open a new POS session. Called from the POS opening screen."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
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

    # Resolve company from POS Profile
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    company = profile.company or frappe.defaults.get_global_default("company")

    # Resolve device — from parameter, from user allocation, or None
    device_doc = None
    if device:
        device_doc = frappe.db.get_value(
            "CH Device Master", device,
            ["name", "company", "store", "pos_profile", "warehouse", "is_active"],
            as_dict=True,
        )
        if device_doc and not device_doc.is_active:
            frappe.throw(_("Device {0} is inactive.").format(device))
        if device_doc and device_doc.company != company:
            frappe.throw(
                _("Device {0} belongs to company {1}, but POS Profile company is {2}.").format(
                    device, device_doc.company, company
                )
            )

    # Get business date
    business_date = get_store_business_date(store)

    # Acquire advisory lock to prevent race condition on session creation
    lock_key = f"pos_session_{store}_{business_date}"
    frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lock_key,))
    try:
        # Check for unclosed sessions (strict store-level single session)
        unclosed = frappe.db.get_value(
            "CH POS Session",
            {
                "store": store,
                "status": ("in", ["Open", "Suspended", "Closing"]),
                "docstatus": 1,
            },
            ["name", "pos_profile", "user"],
            as_dict=True,
        )
        if unclosed:
            frappe.throw(
                _("Session {0} (Profile: {1}, Cashier: {2}) is still active. Close it before opening a new one.").format(
                    unclosed.name, unclosed.pos_profile, unclosed.user
                )
            )

        # Do not allow reopening for the same business date once store day is closed.
        closed_for_day = frappe.db.exists(
            "CH POS Session",
            {
                "store": store,
                "business_date": business_date,
                "status": "Closed",
                "docstatus": 1,
            },
        )
        if closed_for_day:
            frappe.throw(
                _(
                    "Business date {0} for store {1} is already closed. "
                    "Complete settlement and advance business date before opening a new session."
                ).format(business_date, store)
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

        # Update Business Date status to Open if not already
        bd_doc_name = frappe.db.get_value("CH Business Date", {"store": store}, "name")
        if bd_doc_name:
            bd_status = frappe.db.get_value("CH Business Date", bd_doc_name, "status")
            if not bd_status or bd_status == "Closed":
                frappe.db.set_value("CH Business Date", bd_doc_name, {
                    "status": "Open",
                    "opened_on": now_datetime(),
                    "opened_by": frappe.session.user,
                    "closed_on": None,
                    "closed_by": None,
                })

        # Create CH POS Session
        session = frappe.get_doc({
            "doctype": "CH POS Session",
            "company": company,
            "pos_profile": pos_profile,
            "store": store,
            "device": device_doc.name if device_doc else None,
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
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))

    frappe.db.commit()

    return {
        "session_name": session.name,
        "business_date": str(business_date),
        "store": store,
        "company": company,
        "device": device_doc.name if device_doc else None,
        "opening_cash": opening_cash,
        "expected_float": expected_float,
        "pos_opening_entry": opening_entry.name,
    }


@frappe.whitelist()
def close_session(session_name, closing_cash, denominations=None,
                  variance_reason=None, manager_pin=None):
    """Close a POS session with cash reconciliation. Called from POS closing dashboard."""
    frappe.has_permission("Sales Invoice", "create", throw=True)

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status not in ("Open", "Locked", "Pending Close"):
        frappe.throw(_("Session {0} cannot be closed (status: {1})").format(
            session_name, session.status))

    # Settlement gate: if require_settlement_before_session_close is enabled,
    # a submitted CH POS Settlement must exist for this session.
    settlement_name = frappe.db.get_value(
        "CH POS Settlement",
        {"session": session_name, "docstatus": 1},
        "name",
    )
    if _is_settlement_required() and not settlement_name:
            frappe.throw(
                _("Settlement must be completed before closing session {0}. "
                  "Please complete the settlement process first.").format(session_name)
            )

    settlement_doc = frappe.get_doc("CH POS Settlement", settlement_name) if settlement_name else None

    # Parse denominations
    denomination_rows = frappe.parse_json(denominations) if denominations else []
    authoritative_closing_cash = flt(closing_cash)
    authoritative_variance_reason = variance_reason

    # Manager PIN for variance approval (checked inside close_session if variance > threshold)
    manager_user = None
    if manager_pin:
        pin_result = verify_manager_pin(
            manager_pin, store=session.store, permission="can_approve_closing"
        )
        if not pin_result.get("valid"):
            frappe.throw(pin_result.get("message", _("Invalid manager PIN")))
        manager_user = pin_result["user"]

    if settlement_doc:
        authoritative_closing_cash = flt(settlement_doc.actual_closing_cash)
        authoritative_variance_reason = settlement_doc.variance_reason or authoritative_variance_reason
        if settlement_doc.signoff_by_manager:
            manager_user = settlement_doc.signoff_by_manager
        if settlement_doc.denomination_details:
            denomination_rows = [
                {
                    "denomination": row.denomination,
                    "count": row.count,
                }
                for row in settlement_doc.denomination_details
            ]

    session.close_session(
        closing_cash=authoritative_closing_cash,
        denomination_rows=denomination_rows,
        variance_reason=authoritative_variance_reason,
        manager_pin_user=manager_user,
    )

    # Update Business Date status to Closing Pending if other sessions still active
    _update_business_date_status_after_close(session.store, session.business_date)

    # Advance business date only after full EOD completion (all sessions closed + settlement complete).
    date_advance = _auto_advance_business_date_after_eod(
        store=session.store,
        closed_business_date=getdate(session.business_date),
    )

    frappe.db.commit()

    return {
        "status": "Closed",
        "cash_variance": flt(session.cash_variance),
        "total_invoices": session.total_invoices,
        "net_sales": flt(session.net_sales),
        "total_cash_drops": flt(session.total_cash_drops),
        "company": session.get("company"),
        "device": session.get("device"),
        "business_date_advanced": date_advance.get("advanced", False),
        "next_business_date": str(date_advance.get("next_business_date")) if date_advance.get("next_business_date") else None,
        "advance_message": date_advance.get("message"),
    }


@frappe.whitelist()
def switch_user(session_name, new_user, pwd=None):
    """Switch cashier — new cashier must authenticate with their credentials."""
    frappe.has_permission("Sales Invoice", "create", throw=True)

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status != "Open":
        frappe.throw(_("Session is not open"))

    if not frappe.db.exists("User", new_user):
        frappe.throw(_("User {0} does not exist").format(new_user))

    # Authenticate the new cashier
    if not pwd:
        frappe.throw(_("Password is required"))
    from frappe.utils.password import check_password
    try:
        check_password(new_user, pwd)
    except frappe.AuthenticationError:
        frappe.throw(_("Invalid password for {0}").format(new_user))

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
            remarks=f"Switched by {frappe.session.user}",
        )
    except Exception:
        pass

    # Return executive access for the new user so Billed By updates
    executive_access = None
    try:
        from ch_pos.api.pos_api import _get_executive_access
        profile = frappe.get_cached_doc("POS Profile", session.pos_profile)
        executive_access = _get_executive_access(new_user, profile.warehouse)
    except Exception:
        pass

    return {
        "user": new_user,
        "full_name": frappe.db.get_value("User", new_user, "full_name") or new_user,
        "executive_access": executive_access,
    }


# ── Cash Drop ────────────────────────────────────────────────────────────────


@frappe.whitelist()
def create_cash_drop(session_name, amount, reason, manager_pin):
    """Create a cash drop (register → safe) during an active session."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    amount = flt(amount)
    if amount <= 0:
        frappe.throw(_("Amount must be positive"))

    session = frappe.get_doc("CH POS Session", session_name)
    if session.status != "Open":
        frappe.throw(_("Session is not open"))

    # Validate cash drop amount does not exceed estimated cash in drawer
    estimated_cash = flt(session.opening_cash) - flt(session.total_cash_drops or 0)
    if amount > estimated_cash and estimated_cash > 0:
        frappe.throw(
            _("Cash drop amount (₹{0}) exceeds estimated cash in drawer (₹{1}).").format(
                amount, estimated_cash
            )
        )

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
    frappe.has_permission("Sales Invoice", "create", throw=True)

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
    frappe.has_permission("Sales Invoice", "read", throw=True)
    return verify_manager_pin(pin, store=store, permission=permission)


# ── X Report / Z Report ─────────────────────────────────────────────────────


@frappe.whitelist()
def get_x_report(session_name):
    """X Report — interim session report (during shift). Does not close session."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    session = frappe.get_doc("CH POS Session", session_name)
    snapshot = build_settlement_snapshot(session)
    settlement_name = frappe.db.get_value(
        "CH POS Settlement",
        {"session": session_name, "docstatus": 1},
        "name",
    )
    settlement = frappe.get_doc("CH POS Settlement", settlement_name) if settlement_name else None

    # Fetch live invoice data
    invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "pos_profile": session.pos_profile,
            "docstatus": 1,
            "is_consolidated": 0,
            "posting_date": session.business_date,
        },
        fields=["name", "grand_total", "is_return", "total_taxes_and_charges",
                "posting_time", "customer_name"],
    )

    total_sales = sum(flt(i.grand_total) for i in invoices if not i.is_return)
    total_returns = sum(abs(flt(i.grand_total)) for i in invoices if i.is_return)
    total_tax = sum(flt(i.total_taxes_and_charges) for i in invoices if not i.is_return)

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
        "payment_modes": [{"mode": r.mode_of_payment, "total": flt(r.total)} for r in snapshot["payment_rows"]],
        "cash_in_drawer": snapshot["expected_closing_cash"],
        "total_cash_drops": snapshot["cash_drop_total"],
        "refund_cash_out": snapshot["refund_cash_out"],
        "petty_cash_out": snapshot["petty_cash_out"],
        "buyback_cash_out": snapshot["buyback_cash_out"],
        "settlement": {
            "name": settlement.name,
            "status": settlement.settlement_status,
            "actual_closing_cash": flt(settlement.actual_closing_cash),
            "variance_amount": flt(settlement.variance_amount),
        } if settlement else None,
    }


@frappe.whitelist()
def get_z_report(store, business_date):
    """Z Report — end-of-day store summary across all sessions."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    business_date = getdate(business_date)

    sessions = frappe.get_all(
        "CH POS Session",
        filters={"store": store, "business_date": business_date, "docstatus": 1},
        fields=["name", "user", "status", "shift_start", "shift_end",
                "opening_cash", "closing_cash_actual", "cash_variance",
                "total_invoices", "net_sales", "total_cash_drops"],
        order_by="shift_start asc",
    )

    # Aggregate payment modes across all sessions (sales only — returns tracked separately)
    payment_rows = frappe.db.sql("""
        SELECT sip.mode_of_payment, SUM(sip.amount) AS total
        FROM `tabSales Invoice` pi
        JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
        WHERE pi.docstatus = 1
          AND pi.is_consolidated = 0
          AND pi.is_return = 0
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


def _auto_advance_business_date_after_eod(store, closed_business_date):
    """Advance store business date to next day only after EOD is fully complete.

    EOD completion criteria:
    - No active (Open/Suspended/Closing) CH POS Session remains for the store.
    - Card/Bank settlement is complete when card receipts exist for that business date.
    - Current store business date equals the just-closed business date.
    """
    # If any session still active for the store, this is not end-of-day yet.
    active_exists = frappe.db.exists(
        "CH POS Session",
        {
            "store": store,
            "status": ("in", ["Open", "Suspended", "Locked", "Closing", "Pending Close"]),
            "docstatus": 1,
        },
    )
    if active_exists:
        return {
            "advanced": False,
            "message": _("Business date not advanced: another active session is still open for this store."),
        }

    settlement_ok, settlement_message = _is_settlement_complete_for_store(store, closed_business_date)
    if not settlement_ok:
        return {
            "advanced": False,
            "message": settlement_message,
        }

    current_bd = getdate(get_store_business_date(store))
    if current_bd != getdate(closed_business_date):
        return {
            "advanced": False,
            "message": _("Business date already updated; no further change required."),
            "next_business_date": current_bd,
        }

    next_bd = getdate(add_days(closed_business_date, 1))
    from ch_pos.pos_core.doctype.ch_business_date.ch_business_date import advance_business_date

    advance_business_date(
        store=store,
        new_date=next_bd,
        reason=f"Auto advance after EOD close for {closed_business_date}",
        manager_user=frappe.session.user,
    )

    return {
        "advanced": True,
        "next_business_date": next_bd,
        "message": _("Business date advanced to {0}.").format(next_bd),
    }


def _is_settlement_complete_for_store(store, business_date):
    """Check if settlement is complete for a store/date before date advancement.

    Rule:
    - If no card/bank POS receipts exist, settlement is considered complete.
    - If card/bank receipts exist, POS EDC Settlement (Matched, submitted) must
      cover the card receipt total for that store/date.
    """
    card_total = flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(sip.amount), 0)
            FROM `tabSales Invoice` pi
            JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
            JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
            WHERE pi.docstatus = 1
              AND pi.is_consolidated = 0
              AND pi.posting_date = %(bd)s
              AND mop.type = 'Bank'
              AND pi.pos_profile IN (
                  SELECT DISTINCT pos_profile
                  FROM `tabCH POS Session`
                  WHERE store = %(store)s
                    AND business_date = %(bd)s
                    AND docstatus = 1
              )
            """,
            {"store": store, "bd": business_date},
        )[0][0]
    )

    if card_total <= 0:
        return True, _("Settlement complete (no card/bank receipts for the day).")

    store_warehouse = frappe.db.get_value("CH Store", store, "warehouse")
    if not store_warehouse:
        return False, _("Business date not advanced: card receipts exist but store warehouse mapping is missing for EDC settlement validation.")

    matched_settlement_total = flt(
        frappe.db.sql(
            """
            SELECT COALESCE(SUM(matched_amount), 0)
            FROM `tabPOS EDC Settlement`
            WHERE docstatus = 1
              AND status = 'Matched'
              AND settlement_date = %(bd)s
              AND store = %(warehouse)s
            """,
            {"bd": business_date, "warehouse": store_warehouse},
        )[0][0]
    )

    if matched_settlement_total + 0.01 < card_total:
        return (
            False,
            _("Business date not advanced: EDC settlement pending. Card receipts: ₹{0}, Matched settlement: ₹{1}.").format(
                card_total, matched_settlement_total
            ),
        )

    return True, _("Settlement complete.")


def _is_settlement_required():
    """Check if CH POS Control Settings mandates settlement before session close."""
    try:
        return cint(frappe.db.get_single_value(
            "CH POS Control Settings", "require_settlement_before_session_close"
        ))
    except Exception:
        return False


def _update_business_date_status_after_close(store, business_date):
    """Update CH Business Date status after a session closes.

    If all sessions for the store/date are closed → set status to Closed.
    Otherwise set to Closing Pending.
    """
    bd_name = frappe.db.get_value(
        "CH Business Date",
        {"store": store, "business_date": business_date, "is_active": 1},
    )
    if not bd_name:
        return

    active_sessions = frappe.db.count(
        "CH POS Session",
        {
            "store": store,
            "business_date": business_date,
            "status": ("in", ["Open", "Suspended", "Locked", "Closing", "Pending Close"]),
            "docstatus": 1,
        },
    )

    if active_sessions == 0:
        frappe.db.set_value("CH Business Date", bd_name, {
            "status": "Closed",
            "closed_on": now_datetime(),
            "closed_by": frappe.session.user,
        })
    else:
        frappe.db.set_value("CH Business Date", bd_name, "status", "Closing Pending")
