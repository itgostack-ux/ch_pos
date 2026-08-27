"""
Session & Cash Control API endpoints for the CH POS frontend.

All endpoints are whitelisted and called from the POS UI.
They integrate with:
- CH POS Session (session lifecycle)
- CH Business Date (store business date)
- CH POS Password (quick approval)
- CH Cash Drop (safe transfers)
- ERPNext POS Opening Entry (GL linkage)
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, nowdate, now_datetime, getdate, add_days

from ch_pos.api.scope_guard import (
    assert_pos_profile_scope,
    assert_session_scope,
    assert_store_scope)
from ch_pos.config import (
    assert_session_operator,
    get_control_setting,
    is_privileged_user,
    require_configured_roles)
from ch_pos.pos_core.doctype.ch_manager_pin.ch_manager_pin import verify_manager_pin
from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import is_session_stale
from ch_pos.pos_core.doctype.ch_pos_settlement.ch_pos_settlement import build_settlement_snapshot
from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import (
    get_active_session,
    get_store_business_date)


def _ensure_store_business_date_is_not_future(store):
    business_date = getdate(get_store_business_date(store))
    today = getdate(nowdate())
    if business_date > today:
        frappe.throw(
            _(
                "Store {0} has an invalid future business date {1}. Reset it to today or an earlier date before opening POS."
            ).format(store, business_date),
            title=_("Invalid Business Date"))
    return business_date


def _session_report_limit() -> int:
    value = cint(get_control_setting("session_report_row_limit", 5000)) or 5000
    return max(1, min(value, 20000))


def _ensure_session_report_limit(rows, limit, label):
    if len(rows) > limit:
        frappe.throw(
            _("{0} exceeds the configured session-report limit of {1} rows.").format(
                label, limit
            )
        )
    return rows


# ── Session Lifecycle ────────────────────────────────────────────────────────


@frappe.whitelist()
def get_session_status(pos_profile) -> dict:
    """Check if an active session exists for this profile.
    Called on POS startup to decide: show opening screen or resume."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    # Explicitly exempt: this endpoint is how the UI LEARNS the till is stale.
    # Relying on the cmd-based exemption would break it for internal callers,
    # where there is no request cmd to match.
    assert_pos_profile_scope(pos_profile, allow_stale_session=True)

    session = get_active_session(pos_profile)
    if session and is_session_stale(session):
        # The calendar has moved past this till's business date. Do NOT
        # resume it for selling, and do NOT auto-close it with a placeholder
        # ₹0 count either — that skips real cash reconciliation for a day
        # that actually had business on it. Route the operator to properly
        # settle + close THIS session (its own real business_date, own real
        # sales) via the Closing Dashboard before a new one can start; see
        # _show_must_close in session_opening_screen.js, which opens that
        # dashboard rather than just erroring out.
        return {
            "has_session": False,
            "unclosed_session": session.name,
            "unclosed_user": session.user,
            "unclosed_date": str(session.business_date),
            "unclosed_profile": session.get("pos_profile") or pos_profile,
            "stale": True,
            "message": _(
                "Session for business date {0} is still open. Close it before "
                "billing on {1}."
            ).format(session.business_date, nowdate()),
        }

    if session:
        assert_session_operator(session, _("resume another cashier's POS session"))
        _ensure_store_business_date_is_not_future(session.store)
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

    # Check for unclosed sessions at store level — this is the only real
    # hard block: one physical till/cash drawer can't have two concurrent
    # open sessions. Must be closed before opening a NEW one at this store.
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
            as_dict=True)

    if unclosed:
        return {
            "has_session": False,
            "unclosed_session": unclosed.name,
            "unclosed_user": unclosed.user,
            "unclosed_date": str(unclosed.business_date),
            "unclosed_profile": unclosed.pos_profile,
        }

    # A session left open under this SAME POS Profile but at a DIFFERENT
    # store no longer blocks opening here — the cashier can log in and keep
    # working at the new store; a non-blocking warning nudges them to go
    # settle + close the old one instead of silently forgetting it. Real
    # cash-drawer safety is the store-level check above (two sessions for
    # the same physical till), which this does not weaken.
    warning_unclosed = frappe.db.get_value(
        "CH POS Session",
        {"pos_profile": pos_profile, "status": ("in", ["Open", "Suspended", "Closing"]), "docstatus": 1},
        ["name", "user", "business_date", "pos_profile", "store"],
        as_dict=True)

    # If the day is already closed for this store, don't allow reopening.
    if store:
        business_date = _ensure_store_business_date_is_not_future(store)
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
                })
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

    result = {"has_session": False}
    if warning_unclosed:
        result.update({
            "warning_unclosed_session": warning_unclosed.name,
            "warning_unclosed_user": warning_unclosed.user,
            "warning_unclosed_date": str(warning_unclosed.business_date),
            "warning_unclosed_store": warning_unclosed.store,
        })
    return result


@frappe.whitelist(methods=["POST"])
def open_session(pos_profile, opening_cash, manager_pin=None, device=None) -> dict:
    """Open a new POS session. Called from the POS opening screen."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    assert_pos_profile_scope(pos_profile)
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
        frappe.throw(_("No CH Store configured for POS Profile {0}. Set it on POS Profile Extension.").format(pos_profile), title=_("API Error"))

    # Operational gate: till access requires an active POS Executive
    # assignment at THIS store — org scope (CH User Scope) alone is
    # back-office visibility and never opens a session.
    from ch_pos.api.scope_guard import assert_pos_executive
    assert_pos_executive(store)

    # Resolve company from POS Profile
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    company = profile.company
    if not company:
        frappe.throw(_("POS Profile {0} has no company configured.").format(pos_profile))

    # Resolve device — from parameter, from user allocation, or None
    device_doc = None
    if device:
        device_doc = frappe.db.get_value(
            "CH Device Master", device,
            ["name", "company", "store", "pos_profile", "warehouse", "is_active"],
            as_dict=True)
        if not device_doc:
            frappe.throw(_("Device {0} was not found.").format(device))
        if not device_doc.is_active:
            frappe.throw(_("Device {0} is inactive.").format(device), title=_("API Error"))
        if device_doc.company != company:
            frappe.throw(
                _("Device {0} belongs to company {1}, but POS Profile company is {2}.").format(
                    device, device_doc.company, company
                )
            )
        if device_doc.store and device_doc.store != store:
            frappe.throw(_("Device {0} is assigned to a different store.").format(device))
        if device_doc.pos_profile and device_doc.pos_profile != pos_profile:
            frappe.throw(_("Device {0} is assigned to a different POS Profile.").format(device))
        if device_doc.warehouse and device_doc.warehouse != profile.warehouse:
            frappe.throw(_("Device {0} is assigned to a different warehouse.").format(device))

    # Get business date
    business_date = _ensure_store_business_date_is_not_future(store)

    # Acquire advisory lock to prevent race condition on session creation
    lock_key = f"pos_session_{store}_{business_date}"
    lock_result = frappe.db.sql("SELECT GET_LOCK(%s, 30)", (lock_key))[0][0]
    if not lock_result:
        frappe.throw(_("Store is busy processing another session request. Please try again in a moment."), title=_("Session Busy"))
    try:
        # Check for unclosed sessions (strict store-level single session).
        # Deliberately no auto-close here even for a stale (past-date)
        # session — that would skip real cash reconciliation for a day that
        # actually had business on it. get_session_status is what routes the
        # operator to properly settle + close a stale session first (via the
        # Closing Dashboard, showing that session's own real business_date).
        unclosed = frappe.db.get_value(
            "CH POS Session",
            {
                "store": store,
                "status": ("in", ["Open", "Suspended", "Closing"]),
                "docstatus": 1,
            },
            ["name", "pos_profile", "user"],
            as_dict=True)
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
            })
        if closed_for_day:
            frappe.throw(
                _(
                    "Business date {0} for store {1} is already closed. "
                    "Complete settlement and advance business date before opening a new session."
                ).format(business_date, store)
            )

    # ── Mandatory validations ────────────────────────────────
        if not opening_cash:
            frappe.throw(_("Opening Cash is mandatory. Count the cash in the drawer before starting."), title=_("API Error"))
        if not manager_pin:
            frappe.throw(_("Manager PIN is mandatory to open a POS session."), title=_("API Error"))

    # Manager PIN verification for opening approval
        manager_user = None
        pin_result = verify_manager_pin(manager_pin, store=store, permission="can_approve_opening")
        if not pin_result.get("valid"):
            frappe.throw(pin_result.get("message", _("Invalid manager PIN")))
        manager_user = pin_result["user"]

        # Validate opening cash against previous closing / expected float
        expected_float = _get_expected_float(pos_profile, store)

        # Close orphaned POS Opening Entries (Open but no active CH POS Session)
        # This prevents ERPNext's check_open_pos_exists from blocking new entries.
        # Close stale entries for this profile AND for this user (cross-profile).
        stale_entries = frappe.db.get_all(
            "POS Opening Entry",
            filters={
                "pos_profile": pos_profile,
                "status": "Open",
                "docstatus": 1,
                "pos_closing_entry": ("in", ["", None]),
            },
            pluck="name")
        # Also close any Open entries for the same user on OTHER profiles
        # (e.g. cashier logged into Velachery, now logging into Anna Nagar)
        user_stale = frappe.db.get_all(
            "POS Opening Entry",
            filters={
                "user": frappe.session.user,
                "status": "Open",
                "docstatus": 1,
                "pos_closing_entry": ("in", ["", None]),
            },
            pluck="name")
        all_stale = set(stale_entries + user_stale)
        for se in all_stale:
            frappe.db.set_value("POS Opening Entry", se, "status", "Closed", update_modified=False)

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
        opening_entry.insert()
        # SECURITY (H9): Validate POS Manager role or approval before submitting
        if not frappe.has_permission("POS Opening Entry", "submit", throw=False):
            frappe.throw(
                frappe._("You do not have permission to submit POS Opening Entries. "
                         "Contact your Store Manager for approval."),
                frappe.PermissionError)
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
        session.flags.ch_server_issued = True
        session.flags.ch_opening_approval_verified = True
        session.insert()
        session.submit()
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key))

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


@frappe.whitelist(methods=["POST"])
def close_session(session_name, closing_cash, denominations=None,
                  variance_reason=None, manager_pin=None) -> dict:
    """Close a POS session with cash reconciliation. Called from POS closing dashboard."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    assert_session_scope(session_name)

    session = frappe.get_doc("CH POS Session", session_name)
    assert_session_operator(session, _("close another cashier's POS session"))
    if session.status not in ("Open", "Locked", "Pending Close"):
        frappe.throw(_("Session {0} cannot be closed (status: {1})").format(
            session_name, session.status))

    # Settlement gate: if require_settlement_before_session_close is enabled,
    # a submitted CH POS Settlement must exist for this session.
    settlement_name = frappe.db.get_value(
        "CH POS Settlement",
        {"session": session_name, "docstatus": 1},
        "name")
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
        from ch_pos.pos_core.doctype.ch_pos_settlement.ch_pos_settlement import (
            has_valid_settlement_signature)

        if not has_valid_settlement_signature(settlement_doc):
            frappe.throw(
                _("Settlement approval evidence failed integrity verification."),
                frappe.PermissionError)
        if (
            settlement_doc.session != session.name
            or settlement_doc.company != session.company
            or settlement_doc.store != session.store
        ):
            frappe.throw(
                _("Settlement does not belong to this session/company/store."),
                frappe.PermissionError)
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
        manager_pin_user=manager_user)

    # Update Business Date status to Closing Pending if other sessions still active
    _update_business_date_status_after_close(session.store, session.business_date)

    # Advance business date only after full EOD completion (all sessions closed + settlement complete).
    date_advance = _auto_advance_business_date_after_eod(
        store=session.store,
        closed_business_date=getdate(session.business_date))

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
def can_reopen_closed_session(session_name) -> bool:
    """Return whether the caller may see the audited closed-session control."""
    try:
        require_configured_roles(
            "closed_session_reopen_roles",
            action=_("re-open a closed POS session"))
        assert_session_scope(session_name)
        session = frappe.get_doc("CH POS Session", session_name)
        assert_session_operator(session, _("re-open another cashier's POS session"))
        return session.status == "Closed"
    except frappe.PermissionError:
        return False


@frappe.whitelist(methods=["POST"])
def admin_reopen_session(session_name, reason) -> dict:
    """Re-open a Closed POS session for authorized accounting corrections.

    Use when a session was closed prematurely / by mistake and the store
    needs to resume billing on the same business date without creating a
    fresh opening entry.

    Access is controlled by ``closed_session_reopen_roles``. Administrator and
    System Manager retain the immutable platform bypass. The operation reverses
    a terminal state and is therefore fully audited.

    Effects (mirrors the existing ``reopen_settlement`` pattern for the
    Pending-Close case, extended to handle the Closed terminal state):
      * CH POS Session: status Closed → Open, close-side fields cleared so a
        re-close re-computes them from scratch. db_set bypasses the
        ``_VALID_TRANSITIONS`` state machine (Closed is terminal).
      * Linked CH POS Settlement (submitted): cancelled so a fresh settlement
        can be raised at re-close.
      * Linked standard POS Closing Entry is cancelled; ERPNext then reopens
        the POS Opening Entry through its own voucher lifecycle.
      * CH Business Date: if it had advanced past this session's business_date
        (auto-advance after EOD), it is rolled back to the session's date so
        new invoices post on the correct day.
      * CH Business Date: if its status was ``Closed``, it is demoted to
        ``Closing Pending`` (the same state ``_update_business_date_status_after_close``
        would have used if other sessions had still been active).
      * CH Business Audit Log entry with full before/after snapshot.

    Args:
      session_name: CH POS Session name (must be in status ``Closed``).
      reason: Non-empty audit reason — required.
    """
    assert_session_scope(session_name)

    if not reason or not str(reason).strip():
        frappe.throw(_("Reason is mandatory to re-open a Closed session."),
                     title=_("Reopen Session"))
    reason = str(reason).strip()

    session = frappe.get_doc("CH POS Session", session_name)
    assert_session_operator(session, _("re-open another cashier's POS session"))
    if session.status != "Closed":
        frappe.throw(
            _("Session {0} is in status '{1}', not 'Closed'. "
              "For Pending Close sessions use Reopen Settlement.").format(
                session_name, session.status
            ),
            title=_("Reopen Session"))

    # Serialise against concurrent close/reopen activity on the same session.
    lock_key = f"sess_control_reopen_{frappe.scrub(session_name)}"
    if not frappe.db.sql("SELECT GET_LOCK(%s, 15)", (lock_key))[0][0]:
        frappe.throw(_("Session {0} is busy. Please retry.").format(session_name),
                     title=_("Session Busy"))

    try:
        # Re-read after lock to avoid TOCTOU.
        fresh_status = frappe.db.get_value("CH POS Session", session_name, "status")
        if fresh_status != "Closed":
            frappe.throw(_("Session {0} is no longer Closed (now: {1}).").format(
                session_name, fresh_status))

        before_snapshot = {
            "status": "Closed",
            "shift_end": str(session.shift_end) if session.shift_end else None,
            "closing_cash_actual": flt(session.closing_cash_actual),
            "cash_variance": flt(session.cash_variance),
            "closing_approved_by": session.closing_approved_by,
        }

        # 1. Cancel the linked submitted settlement (if any) so a fresh
        #    settlement is required at re-close. Cancel does not invoke
        #    validate(), so the "session already closed" guard does not fire.
        settlement_name = frappe.db.get_value(
            "CH POS Settlement",
            {"session": session_name, "docstatus": 1},
            "name")
        if settlement_name:
            settlement = frappe.get_doc("CH POS Settlement", settlement_name)
            settlement.check_permission("cancel")
            settlement.cancel()

        # 2. Cancel the authoritative standard POS Closing Entry first. If
        #    ERPNext refuses the reversal, the custom session remains Closed.
        if getattr(session, "pos_opening_entry", None):
            closing_name = frappe.db.get_value(
                "POS Opening Entry", session.pos_opening_entry, "pos_closing_entry"
            )
            if closing_name:
                if not frappe.db.exists("POS Closing Entry", closing_name):
                    frappe.throw(
                        _("POS Opening Entry has an invalid closing link: {0}.").format(closing_name)
                    )
                closing = frappe.get_doc("POS Closing Entry", closing_name)
                if closing.docstatus == 1:
                    closing.check_permission("cancel")
                    closing.cancel()

        # 3. Reverse the session record. db_set bypasses _VALID_TRANSITIONS,
        #    which is necessary because Closed is a terminal state by design.
        frappe.db.set_value("CH POS Session", session_name, {
            "status": "Open",
            "shift_end": None,
            "closing_cash_actual": 0,
            "closing_cash_expected": 0,
            "cash_variance": 0,
            "variance_reason": "",
            "closing_approved_by": None,
            "closing_approved_at": None,
            "duration_minutes": 0,
        }, update_modified=True)

        # 4. Verify/repair the linked POS Opening Entry after standard cancel.
        if getattr(session, "pos_opening_entry", None):
            frappe.db.set_value(
                "POS Opening Entry",
                session.pos_opening_entry,
                {"pos_closing_entry": None, "status": "Open"},
                update_modified=True)

        # 5. Rewind business date if auto-advance had moved it forward.
        bd_name = frappe.db.get_value(
            "CH Business Date",
            {"store": session.store, "is_active": 1})
        bd_rollback = None
        bd_status_change = None
        if bd_name:
            current_bd, current_bd_status = frappe.db.get_value(
                "CH Business Date", bd_name, ["business_date", "status"]
            )
            session_bd = getdate(session.business_date)
            if current_bd and getdate(current_bd) > session_bd:
                # Roll the store date back to the session's date so new
                # invoices post on the correct day.
                from ch_pos.pos_core.doctype.ch_business_date.ch_business_date import (
                    advance_business_date)
                advance_business_date(
                    store=session.store,
                    new_date=session_bd,
                    reason="Admin reopen of session {0}: {1}".format(session_name, reason),
                    manager_user=frappe.session.user)
                bd_rollback = {"from": str(current_bd), "to": str(session_bd)}
            elif current_bd_status == "Closed":
                # Same business date — just demote the status because the
                # store has an active session again.
                frappe.db.set_value("CH Business Date", bd_name, {
                    "status": "Closing Pending",
                    "closed_on": None,
                    "closed_by": None,
                }, update_modified=True)
                bd_status_change = "Closed → Closing Pending"

        # 6. Audit log (best-effort).
        try:
            from ch_pos.audit import log_business_event
            log_business_event(
                event_type="Other",
                ref_doctype="CH POS Session",
                ref_name=session_name,
                before=before_snapshot,
                after={
                    "status": "Open",
                    "cancelled_settlement": settlement_name,
                    "business_date_rollback": bd_rollback,
                    "business_date_status_change": bd_status_change,
                },
                remarks=(
                    "Closed POS session reopened. "
                    "Reason: {0}".format(reason)
                ),
                # NOTE: `store` field on CH Business Audit Log is a Link to
                # Warehouse, not CH Store, so we deliberately omit it here
                # (matches the existing `_log_close_event` pattern).
                company=session.get("company"),
                user=frappe.session.user)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "POS session reopen audit failed")
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key))

    return {
        "session_name": session_name,
        "session_status": "Open",
        "cancelled_settlement": settlement_name,
        "business_date_rollback": bd_rollback,
        "business_date_status_change": bd_status_change,
        "reopened_by": frappe.session.user,
        "message": _(
            "Session {0} re-opened. Settlement {1} cancelled. "
            "Complete a fresh settlement before closing again."
        ).format(session_name, settlement_name or _("(none)")),
    }


@frappe.whitelist(methods=["POST"])
def switch_user(session_name, new_user, pwd=None) -> dict:
    """Switch cashier — new cashier must authenticate with their credentials."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    assert_session_scope(session_name)

    session = frappe.get_doc("CH POS Session", session_name)
    assert_session_operator(session, _("switch the cashier on another user's session"))
    if session.status != "Open":
        frappe.throw(_("Session is not open"), title=_("API Error"))

    if not frappe.db.exists("User", new_user):
        frappe.throw(_("User {0} does not exist").format(new_user), title=_("API Error"))
    if not frappe.db.get_value("User", new_user, "enabled"):
        frappe.throw(_("User {0} is disabled").format(new_user), frappe.PermissionError)
    if not frappe.has_permission("Sales Invoice", "create", user=new_user):
        frappe.throw(_("The new cashier cannot create Sales Invoices."), frappe.PermissionError)
    assert_session_scope(session_name, user=new_user)

    # Authenticate the new cashier
    if not pwd:
        frappe.throw(_("Password is required"), title=_("API Error"))
    from frappe.utils.password import check_password
    try:
        check_password(new_user, pwd)
    except frappe.AuthenticationError:
        frappe.throw(_("Invalid password for {0}").format(new_user), title=_("API Error"))

    lock_key = f"session_switch_{session_name}"
    lock_result = frappe.db.sql("SELECT GET_LOCK(%s, 5)", (lock_key))[0][0]
    if not lock_result:
        frappe.throw(_("Session is busy. Please try again."), title=_("Session Busy"))
    try:
        old_user = session.user
        session.db_set("user", new_user)
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key))

    try:
        from ch_pos.audit import log_business_event
        log_business_event(
            event_type="Cashier Switch",
            ref_doctype="CH POS Session",
            ref_name=session_name,
            before=old_user,
            after=new_user,
            remarks=f"Switched by {frappe.session.user}")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "POS cashier switch audit failed")

    # Return executive access for the new user so Billed By updates
    executive_access = None
    try:
        from ch_pos.api.pos_api import _get_executive_access
        profile = frappe.get_cached_doc("POS Profile", session.pos_profile)
        executive_access = _get_executive_access(new_user, profile.warehouse)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "POS cashier executive access lookup failed")

    return {
        "user": new_user,
        "full_name": frappe.db.get_value("User", new_user, "full_name") or new_user,
        "executive_access": executive_access,
    }


# ── Cash Drop ────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def create_cash_drop(session_name, amount, reason, manager_pin) -> dict:
    """Create a cash drop (register → safe) during an active session."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    assert_session_scope(session_name)
    amount = flt(amount)
    if amount <= 0:
        frappe.throw(_("Amount must be positive"), title=_("API Error"))

    session = frappe.get_doc("CH POS Session", session_name)
    assert_session_operator(session, _("create a cash drop for another user's session"))
    if session.status != "Open":
        frappe.throw(_("Session is not open"), title=_("API Error"))

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
    drop.flags.ch_manager_approval_verified = True
    drop.insert()
    drop.submit()
    return {
        "drop_name": drop.name,
        "amount": amount,
        "approved_by": pin_result["name"],
    }


# ── Business Date ────────────────────────────────────────────────────────────


@frappe.whitelist()
def get_business_date(store) -> dict:
    """Get the current business date for a store."""
    store_company = frappe.db.get_value("CH Store", store, "company")
    assert_store_scope(store=store, company=store_company)
    return {
        "business_date": str(get_store_business_date(store)),
        "system_date": str(nowdate()),
    }


@frappe.whitelist(methods=["POST"])
def override_business_date(store, new_date, reason, manager_pin) -> dict:
    """Override the business date. Requires manager with override permission."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    store_company = frappe.db.get_value("CH Store", store, "company")
    assert_store_scope(store=store, company=store_company)

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


@frappe.whitelist(methods=["POST"])
def verify_pin(pin, store=None, permission=None) -> dict:
    """Verify a manager PIN from the POS UI."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    if not store:
        frappe.throw(_("Store is required to verify an approval PIN."))
    allowed_permissions = {
        "can_approve_opening",
        "can_approve_closing",
        "can_approve_cash_drop",
        "can_override_business_date",
        "can_approve_discount",
        "can_force_close_session",
    }
    if permission and permission not in allowed_permissions:
        frappe.throw(_("Invalid approval permission."), frappe.PermissionError)
    assert_store_scope(
        store=store,
        company=frappe.db.get_value("CH Store", store, "company"))
    return verify_manager_pin(pin, store=store, permission=permission)


# ── X Report / Z Report ─────────────────────────────────────────────────────


@frappe.whitelist()
def get_x_report(session_name) -> dict:
    """X Report — interim session report (during shift). Does not close session."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    assert_session_scope(session_name)
    session = frappe.get_doc("CH POS Session", session_name)
    snapshot = build_settlement_snapshot(session)
    settlement_name = frappe.db.get_value(
        "CH POS Settlement",
        {"session": session_name, "docstatus": 1},
        "name")
    settlement = frappe.get_doc("CH POS Settlement", settlement_name) if settlement_name else None

    # Fetch live invoice data
    report_limit = _session_report_limit()
    invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "pos_profile": session.pos_profile,
            "docstatus": 1,
            "is_consolidated": 0,
            "posting_date": session.business_date,
            "custom_ch_pos_session": session.name,
        },
        fields=["name", "grand_total", "is_return", "total_taxes_and_charges",
                "posting_time", "customer_name", "custom_ch_sale_type"],
        limit_page_length=report_limit + 1)
    _ensure_session_report_limit(invoices, report_limit, _("X-report invoices"))

    petty_cash_rows = frappe.get_list(
        "CH Cash Drop",
        filters={
            "session": session.name,
            "movement_type": "Petty Expense",
            "docstatus": 1,
        },
        fields=["name", "reason", "amount", "remarks", "posting_time", "approved_by"],
        order_by="posting_time asc, creation asc",
        limit_page_length=report_limit + 1)
    _ensure_session_report_limit(petty_cash_rows, report_limit, _("X-report petty cash rows"))

    total_sales = sum(flt(i.grand_total) for i in invoices if not i.is_return)
    total_returns = sum(abs(flt(i.grand_total)) for i in invoices if i.is_return)
    total_tax = sum(flt(i.total_taxes_and_charges) for i in invoices if not i.is_return)

    # Sales bifurcation by sale type (Direct Sale / Finance / Exchange / Free …).
    _by_type = {}
    for i in invoices:
        if i.is_return:
            continue
        key = i.custom_ch_sale_type or _("Direct Sale")
        agg = _by_type.setdefault(key, {"total": 0.0, "count": 0})
        agg["total"] += flt(i.grand_total)
        agg["count"] += 1
    sales_by_type = [
        {"type": k, "total": round(v["total"], 2), "count": v["count"]}
        for k, v in sorted(_by_type.items(), key=lambda kv: kv[1]["total"], reverse=True)
    ]

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
        "total_sales_cash": snapshot["total_sales_cash"],
        "total_sales_card": snapshot["total_sales_card"],
        "total_sales_upi": snapshot["total_sales_upi"],
        "total_sales_wallet": snapshot["total_sales_wallet"],
        "total_sales_bank": snapshot["total_sales_bank"],
        "total_gross_sales": snapshot["total_gross_sales"],
        "sales_by_type": sales_by_type,
        "cash_in_drawer": snapshot["expected_closing_cash"],
        "total_cash_drops": snapshot["cash_drop_total"],
        "refund_cash_out": snapshot["refund_cash_out"],
        "petty_cash_out": snapshot["petty_cash_out"],
        "buyback_cash_out": snapshot["buyback_cash_out"],
        "petty_cash_rows": petty_cash_rows,
        "settlement": {
            "name": settlement.name,
            "status": settlement.settlement_status,
            "actual_closing_cash": flt(settlement.actual_closing_cash),
            "variance_amount": flt(settlement.variance_amount),
        } if settlement else None,
    }


@frappe.whitelist()
def get_z_report(store, business_date) -> dict:
    """Z Report — end-of-day store summary across all sessions."""
    frappe.has_permission("Sales Invoice", "read", throw=True)
    assert_store_scope(
        store=store,
        company=frappe.db.get_value("CH Store", store, "company"))
    business_date = getdate(business_date)

    report_limit = _session_report_limit()
    sessions = frappe.get_all(
        "CH POS Session",
        filters={"store": store, "business_date": business_date, "docstatus": 1},
        fields=["name", "user", "status", "shift_start", "shift_end",
                "opening_cash", "closing_cash_actual", "cash_variance",
                "total_invoices", "net_sales", "total_cash_drops"],
        order_by="shift_start asc",
        limit_page_length=report_limit + 1)
    _ensure_session_report_limit(sessions, report_limit, _("Z-report sessions"))

    session_names = [s.name for s in sessions]

    invoice_map = {}
    if session_names:
        invoice_rows = frappe.db.sql(
            """
            SELECT pi.custom_ch_pos_session AS session_name,
                   SUM(CASE WHEN pi.is_return = 0 THEN 1 ELSE 0 END) AS total_invoices,
                   SUM(CASE WHEN pi.is_return = 0 THEN pi.grand_total ELSE 0 END) AS total_sales,
                   SUM(CASE WHEN pi.is_return = 1 THEN ABS(pi.grand_total) ELSE 0 END) AS total_returns,
                   SUM(CASE WHEN pi.is_return = 0 THEN pi.total_taxes_and_charges ELSE 0 END) AS total_tax
            FROM `tabSales Invoice` pi
            WHERE pi.docstatus = 1
              AND pi.is_consolidated = 0
              AND pi.custom_ch_pos_session IN %(sessions)s
            GROUP BY pi.custom_ch_pos_session
            """,
            {"sessions": session_names},
            as_dict=True)
        invoice_map = {
            row.session_name: {
                "total_invoices": cint(row.total_invoices),
                "total_sales": flt(row.total_sales),
                "total_returns": flt(row.total_returns),
                "total_tax": flt(row.total_tax),
                "net_sales": flt(row.total_sales) - flt(row.total_returns),
            }
            for row in invoice_rows
        }

    live_sessions = []
    for session in sessions:
        row = dict(session)
        live = invoice_map.get(session.name)
        if live:
            row["total_invoices"] = live["total_invoices"]
            row["net_sales"] = live["net_sales"]
        else:
            row["total_invoices"] = cint(session.total_invoices)
            row["net_sales"] = flt(session.net_sales)
        row["cash_variance"] = flt(session.cash_variance)
        row["total_cash_drops"] = flt(session.total_cash_drops)
        live_sessions.append(row)

    # Aggregate payment modes across all sessions (sales only — returns tracked separately)
    payment_rows = []
    if session_names:
        payment_rows = frappe.db.sql(
            """
            SELECT sip.mode_of_payment, SUM(sip.amount) AS total
            FROM `tabSales Invoice` pi
            JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
            WHERE pi.docstatus = 1
              AND pi.is_consolidated = 0
              AND pi.is_return = 0
              AND pi.custom_ch_pos_session IN %(sessions)s
            GROUP BY sip.mode_of_payment
            """,
            {"sessions": session_names},
            as_dict=True)

    total_invoices = sum(cint(s.get("total_invoices")) for s in live_sessions)
    total_net_sales = sum(flt(s.get("net_sales")) for s in live_sessions)
    total_variance = sum(flt(s.get("cash_variance")) for s in live_sessions)
    total_drops = sum(flt(s.get("total_cash_drops")) for s in live_sessions)
    all_closed = all(s.get("status") == "Closed" for s in live_sessions) if live_sessions else False

    return {
        "store": store,
        "business_date": str(business_date),
        "sessions": live_sessions,
        "total_sessions": len(live_sessions),
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
        order_by="shift_end desc")
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
        })
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
    if next_bd > getdate(nowdate()):
        return {
            "advanced": False,
            "message": _("Next business date {0} is in the future; advance manually when ready.").format(next_bd),
            "next_business_date": next_bd,
        }

    from ch_pos.pos_core.doctype.ch_business_date.ch_business_date import advance_business_date

    advance_business_date(
        store=store,
        new_date=next_bd,
        reason=f"Auto advance after EOD close for {closed_business_date}",
        manager_user=frappe.session.user)

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
    # Pre-fetch pos_profiles for this store/date to avoid nested subquery
    report_limit = _session_report_limit()
    profiles = frappe.get_all(
        "CH POS Session",
        filters={"store": store, "business_date": business_date, "docstatus": 1},
        pluck="pos_profile",
        limit_page_length=report_limit + 1)
    _ensure_session_report_limit(profiles, report_limit, _("Settlement sessions"))
    if not profiles:
        return True, _("Settlement complete (no sessions for the day).")

    profiles = list(set(p for p in profiles if p))
    if not profiles:
        return True, _("Settlement complete (no sessions for the day).")

    card_total = flt(
        frappe.db.sql("""
            SELECT COALESCE(SUM(sip.amount), 0)
            FROM `tabSales Invoice` pi
            JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
            JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
            WHERE pi.docstatus = 1
              AND pi.is_consolidated = 0
              AND pi.posting_date = %(bd)s
              AND mop.type = 'Bank'
              AND pi.pos_profile IN %(profiles)s
        """, {"bd": business_date, "profiles": tuple(profiles)})[0][0]
    )

    if card_total <= 0:
        return True, _("Settlement complete (no card/bank receipts for the day).")

    store_warehouse = frappe.db.get_value("CH Store", store, "warehouse")
    if not store_warehouse:
        return False, _("Business date not advanced: card receipts exist but store warehouse mapping is missing for EDC settlement validation.")

    matched_settlement_total = flt(
        frappe.db.sql("""
            SELECT COALESCE(SUM(matched_amount), 0)
            FROM `tabPOS EDC Settlement`
            WHERE docstatus = 1
              AND status = 'Matched'
              AND settlement_date = %(bd)s
              AND store = %(warehouse)s
        """, {"bd": business_date, "warehouse": store_warehouse})[0][0]
    )

    if matched_settlement_total + 0.01 < card_total:
        return (
            False,
            _("Business date not advanced: EDC settlement pending. Card receipts: ₹{0}, Matched settlement: ₹{1}.").format(
                card_total, matched_settlement_total
            ))

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
        {"store": store, "business_date": business_date, "is_active": 1})
    if not bd_name:
        return

    active_sessions = frappe.db.count(
        "CH POS Session",
        {
            "store": store,
            "business_date": business_date,
            "status": ("in", ["Open", "Suspended", "Locked", "Closing", "Pending Close"]),
            "docstatus": 1,
        })

    if active_sessions == 0:
        frappe.db.set_value("CH Business Date", bd_name, {
            "status": "Closed",
            "closed_on": now_datetime(),
            "closed_by": frappe.session.user,
        })
    else:
        frappe.db.set_value("CH Business Date", bd_name, "status", "Closing Pending")
