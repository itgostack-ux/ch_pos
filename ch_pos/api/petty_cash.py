# Copyright (c) 2026, GoGizmo and contributors
# For license information, please see license.txt
"""In-session petty cash on the imprest (float) model.

Why this exists
---------------
Petty cash used to be enterable *only* on the settlement screen, as free text
``{reason, amount, remarks}`` rows that adjusted expected cash. Nothing was
approved unless the resulting till variance happened to breach the variance
threshold, no category was recorded, and no ``CH Cash Drop`` was raised — so a
store could book any amount as "petty cash" and, as long as the drawer
reconciled, it passed unreviewed. Spend also could not be recorded when it
actually happened, only hours later at close.

Market model adopted
--------------------
This is the standard **imprest system** used by Oracle Cash Management, SAP
Cash Journal (FBCJ) and D365 Expense Management:

* each till carries a fixed float (the imprest amount) set per store,
* routine low-value spend inside that float is disbursed immediately against a
  pre-authorised expense category,
* anything outside the float, or in a category that is not pre-authorised,
  requires an approval before the cash leaves the drawer,
* the float is reconciled at period close.

So: refreshments inside the daily float post immediately; stationery, or
anything that would breach the float, raises an approval request first. Both
paths create a real ``CH Cash Drop`` so the movement is auditable either way.

Approval routing reuses the existing ``CH Exception Type`` / ``CH Exception
Request`` framework rather than introducing another bespoke approval doctype.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, nowdate, now_datetime

EXCEPTION_TYPE = "Petty Cash Approval"

#: Pre-authorised categories. Spend here is disbursed on the spot provided the
#: running total stays inside the store's float — this is the "tea/coffee"
#: case that must not need an approver for every cup.
DEFAULT_AUTO_CATEGORIES = ("Tea / Coffee", "Refreshments", "Drinking Water")

#: Categories that always need sign-off regardless of amount, because they are
#: procurement rather than consumption.
DEFAULT_APPROVAL_CATEGORIES = (
    "Stationery", "Cleaning Supplies", "Repairs & Maintenance",
    "Courier / Postage", "Local Conveyance", "Other",
)

DEFAULT_DAILY_LIMIT = 500.0


# ─────────────────────────────────────────────────────────────────────────────
# Policy resolution
# ─────────────────────────────────────────────────────────────────────────────

def get_daily_limit(pos_profile: str) -> float:
    """The store's daily petty-cash float.

    Held on POS Profile because the float is a per-store decision — a high
    street flagship and a kiosk do not carry the same drawer.
    """
    if not pos_profile:
        return 0.0
    value = frappe.db.get_value("POS Profile", pos_profile, "ch_petty_cash_daily_limit")
    if value is None:
        return DEFAULT_DAILY_LIMIT
    return flt(value)


def get_auto_categories(pos_profile: str) -> set[str]:
    raw = frappe.db.get_value("POS Profile", pos_profile, "ch_petty_cash_auto_categories") \
        if pos_profile else None
    if not raw:
        return {c.lower() for c in DEFAULT_AUTO_CATEGORIES}
    return {line.strip().lower() for line in str(raw).splitlines() if line.strip()}


def get_spent_today(pos_profile: str, business_date=None) -> float:
    """Petty cash already disbursed against today's float for this till.

    Counts everything not cancelled/rejected — a pending request still has the
    money earmarked, so it must consume the float. Otherwise a cashier could
    queue ten pending requests and each would individually look affordable.
    """
    if not pos_profile:
        return 0.0
    rows = frappe.get_all(
        "CH Cash Drop",
        filters={
            "pos_profile": pos_profile,
            "movement_type": "Petty Expense",
            "business_date": business_date or nowdate(),
            "status": ("not in", ["Cancelled", "Rejected"]),
            "docstatus": ("!=", 2),
        },
        pluck="amount",
        limit_page_length=0,
    )
    # Draft rows are included on purpose: a request awaiting approval has the
    # money earmarked, so it must consume the float. Otherwise a cashier could
    # queue several requests that each look affordable on their own.
    return sum(flt(a) for a in rows)


@frappe.whitelist()
def get_petty_cash_status(pos_profile: str) -> dict:
    """Float position for the till — drives the POS petty cash screen."""
    from ch_pos.api.scope_guard import assert_pos_profile_scope
    assert_pos_profile_scope(pos_profile)

    limit = get_daily_limit(pos_profile)
    spent = get_spent_today(pos_profile)
    auto = get_auto_categories(pos_profile)
    return {
        "daily_limit": limit,
        "spent_today": spent,
        "remaining": max(0.0, limit - spent),
        "auto_categories": sorted(c.title() for c in auto),
        "approval_categories": list(DEFAULT_APPROVAL_CATEGORIES),
    }


def requires_approval(pos_profile: str, category: str, amount: float) -> tuple[bool, str]:
    """``(needs_approval, why)`` for one proposed disbursement."""
    category = (category or "").strip()
    if not category:
        return True, _("No expense category was selected.")

    auto = get_auto_categories(pos_profile)
    if category.lower() not in auto:
        return True, _("{0} is not a pre-authorised category.").format(category)

    limit = get_daily_limit(pos_profile)
    spent = get_spent_today(pos_profile)
    if flt(amount) + spent > limit + 0.001:
        return True, _(
            "This would take today's petty cash to {0}, above the store float of {1}."
        ).format(flt(amount) + spent, limit)
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# Disbursement
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def request_petty_expense(session, amount, category, reason=None, remarks=None) -> dict:
    """Record a petty cash disbursement during an open session.

    ``CH Cash Drop`` is a *posted* cash movement — it books a Journal Entry on
    submit and its controller refuses a Petty Expense without a server-verified
    approver. So the two paths differ in kind, not just in status:

    * inside the float and pre-authorised → the policy IS the approval, so the
      drop is created and submitted immediately and the GL moves at once,
    * otherwise → nothing is booked yet. The intent is parked on the exception
      queue and the drop is only created once an approver releases it, so an
      unapproved request can never sit in the ledger.
    """
    from ch_pos.api.scope_guard import assert_session_scope
    assert_session_scope(session)

    amount = flt(amount)
    if amount <= 0:
        frappe.throw(_("Petty cash amount must be positive."), title=_("Invalid Amount"))
    category = (category or "").strip()
    if not category:
        frappe.throw(_("Select an expense category."), title=_("Category Required"))

    sess = frappe.get_doc("CH POS Session", session)
    if (sess.status or "").lower() not in ("open", "active"):
        frappe.throw(_("Petty cash can only be recorded against an open session."),
                     title=_("Session Not Open"))

    needs, why = requires_approval(sess.pos_profile, category, amount)

    if not needs:
        drop = _book_petty_drop(sess, amount, category, reason, remarks)
        return {
            "cash_drop": drop.name,
            "status": drop.status,
            "auto_approved": True,
            "reason": "",
            "exception_request": None,
            "float": get_petty_cash_status(sess.pos_profile),
        }

    drop = _draft_petty_request(sess, amount, category, reason, remarks)
    return {
        "cash_drop": drop.name,
        "status": "Pending Approval",
        "auto_approved": False,
        "reason": why,
        "exception_request": drop.name,
        "float": get_petty_cash_status(sess.pos_profile),
    }


def _book_petty_drop(sess, amount, category, reason, remarks, approver=None):
    """Create and submit the cash movement, posting its Journal Entry."""
    drop = frappe.new_doc("CH Cash Drop")
    drop.session = sess.name
    drop.movement_type = "Petty Expense"
    drop.amount = flt(amount)
    drop.reason = (reason or category or "").strip()
    drop.remarks = (remarks or "").strip()
    drop.approved_by = approver or frappe.session.user
    # The store float is a standing authorisation, so the server is the
    # approving authority here — no cashier-supplied PIN is involved and the
    # controller must mint its own approval signature.
    drop.flags.ch_manager_approval_verified = True
    drop.flags.ignore_permissions = True
    drop.insert(ignore_permissions=True)
    drop.submit()
    drop.reload()
    return drop


def _draft_petty_request(sess, amount, category, reason, remarks):
    """Park the intent as an UNSUBMITTED cash drop — requested, not yet posted.

    Deliberately not routed through ``CH Exception Request``: that doctype is
    shaped for customer transactions (it mandates a customer and applies
    doc-level Item permissions), which an operational expense has neither of.
    ``CH Cash Drop`` already models Draft -> Approved -> Posted, so the request
    and the eventual movement are one auditable record instead of two.
    """
    drop = frappe.new_doc("CH Cash Drop")
    drop.session = sess.name
    drop.movement_type = "Petty Expense"
    drop.amount = flt(amount)
    drop.reason = f"{category}: {(reason or '').strip()}".strip(": ")
    drop.remarks = (remarks or "").strip()
    drop.status = "Draft"
    drop.flags.ignore_permissions = True
    drop.insert(ignore_permissions=True)
    return drop


def _ensure_exception_type() -> None:
    if frappe.db.exists("CH Exception Type", EXCEPTION_TYPE):
        return
    doc = frappe.new_doc("CH Exception Type")
    doc.exception_type = EXCEPTION_TYPE
    if doc.meta.has_field("description"):
        doc.description = _("Petty cash disbursement outside the store float or "
                            "in a category that is not pre-authorised.")
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def approve_petty_expense(cash_drop, approve=1, remarks=None) -> dict:
    """Approver decision. Only on approval does the cash actually move."""
    drop = frappe.get_doc("CH Cash Drop", cash_drop)
    drop.check_permission("write")
    if drop.docstatus != 0:
        frappe.throw(_("This petty cash request has already been decided."),
                     title=_("Not Pending"))

    if not int(approve or 0):
        drop.db_set("status", "Cancelled", update_modified=False)
        if remarks:
            drop.db_set("remarks", f"{drop.remarks or ''}\n{remarks}".strip(),
                        update_modified=False)
        return {"status": "Rejected", "cash_drop": drop.name}

    drop.approved_by = frappe.session.user
    drop.flags.ch_manager_approval_verified = True
    if remarks:
        drop.remarks = f"{drop.remarks or ''}\n{remarks}".strip()
    drop.flags.ignore_permissions = True
    drop.save(ignore_permissions=True)
    drop.submit()
    drop.reload()
    return {"status": "Approved", "cash_drop": drop.name}
