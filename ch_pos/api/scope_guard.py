"""Fail-closed store/company scope guards for POS API endpoints."""

from __future__ import annotations

import frappe
from frappe import _

from ch_pos.config import is_privileged_user, require_authenticated_user


def assert_store_scope(store=None, company=None, warehouse=None, user=None, msg=None):
    """Raise unless the caller may act on the supplied location anchors."""
    user = user or frappe.session.user
    if user == "Guest":
        require_authenticated_user()
    if is_privileged_user(user):
        return

    try:
        from ch_erp15.ch_erp15.scope import assert_user_has_store_scope
    except ImportError:
        frappe.throw(
            _("POS store-scope service is unavailable; access is denied."),
            frappe.PermissionError,
            title=_("Permission Denied"),
        )

    assert_user_has_store_scope(
        store=store,
        company=company,
        warehouse=warehouse,
        user=user,
        msg=msg,
    )


def has_store_scope(store=None, company=None, warehouse=None, user=None) -> bool:
    """Non-raising scope test for filtering candidate users.

    Prefer this over ``try: assert_store_scope(...) except PermissionError``.
    The throw-and-catch form leaves the denial message in
    ``frappe.local.message_log``, so every skipped candidate still surfaces a
    "You are not entitled to access this store / location." popup to whoever is
    standing at the till.
    """
    user = user or frappe.session.user
    if user == "Guest":
        return False
    if is_privileged_user(user):
        return True
    try:
        from ch_erp15.ch_erp15.scope import user_has_store_scope
    except ImportError:
        # Fail closed, consistent with assert_store_scope.
        return False
    return user_has_store_scope(
        store=store, company=company, warehouse=warehouse, user=user
    )


def get_pos_profile_anchors(pos_profile: str, allow_disabled: bool = False) -> dict:
    """Resolve a POS Profile to its authoritative company/store/warehouse.

    ``allow_disabled=True`` is for READ-side assertions (reprint, history
    view) on historical documents whose profile has since been disabled —
    the store-scope check still applies, only the enabled filter is dropped.
    WRITE/billing paths must keep the default strict behavior.
    """
    if not pos_profile:
        frappe.throw(_("POS Profile is required."))
    profile = frappe.db.get_value(
        "POS Profile", pos_profile, ["name", "company", "warehouse", "disabled"], as_dict=True
    )
    if not profile or (profile.get("disabled") and not allow_disabled):
        frappe.throw(_("POS Profile {0} is unavailable.").format(pos_profile))

    store = frappe.db.get_value(
        "POS Profile Extension", {"pos_profile": profile.name}, "store"
    )
    if not store and profile.warehouse:
        store = frappe.db.get_value("CH Store", {"warehouse": profile.warehouse}, "name")
    return {
        "pos_profile": profile.name,
        "company": profile.company,
        "warehouse": profile.warehouse,
        "store": store,
    }


def assert_pos_profile_scope(pos_profile: str, user=None) -> dict:
    anchors = get_pos_profile_anchors(pos_profile)
    assert_store_scope(
        store=anchors.get("store"),
        warehouse=anchors.get("warehouse"),
        company=anchors.get("company"),
        user=user,
    )
    return anchors


def assert_session_scope(session_name: str, user=None):
    if not session_name:
        frappe.throw(_("POS Session is required."))
    session = frappe.db.get_value(
        "CH POS Session",
        session_name,
        ["name", "store", "company", "pos_profile"],
        as_dict=True,
    )
    if not session:
        frappe.throw(_("POS Session {0} was not found.").format(session_name))
    warehouse = None
    if session.pos_profile:
        warehouse = frappe.db.get_value("POS Profile", session.pos_profile, "warehouse")
    assert_store_scope(
        store=session.store,
        warehouse=warehouse,
        company=session.company,
        user=user,
    )
    return session


def assert_sales_invoice_scope(invoice_name: str, user=None, allow_disabled=False):
    """Assert the caller's store scope covers this invoice.

    ``allow_disabled=True`` — READ paths only (reprint, history view):
    tolerate the invoice's POS Profile having been disabled since billing;
    the store/company scope check itself still runs and still fails closed.
    """
    if not invoice_name:
        frappe.throw(_("Sales Invoice is required."))
    invoice = frappe.db.get_value(
        "Sales Invoice",
        invoice_name,
        ["name", "company", "pos_profile", "custom_ch_pos_session"],
        as_dict=True,
    )
    if not invoice:
        frappe.throw(_("Sales Invoice {0} was not found.").format(invoice_name))

    store = None
    warehouse = None
    if invoice.pos_profile:
        anchors = get_pos_profile_anchors(invoice.pos_profile, allow_disabled=allow_disabled)
        store = anchors.get("store")
        warehouse = anchors.get("warehouse")
    elif invoice.custom_ch_pos_session:
        session = frappe.db.get_value(
            "CH POS Session",
            invoice.custom_ch_pos_session,
            ["store", "pos_profile"],
            as_dict=True,
        )
        if session:
            store = session.store
            if session.pos_profile:
                warehouse = frappe.db.get_value("POS Profile", session.pos_profile, "warehouse")
    if not warehouse:
        warehouse = frappe.db.get_value(
            "Sales Invoice Item", {"parent": invoice.name}, "warehouse"
        )

    assert_store_scope(store=store, warehouse=warehouse, company=invoice.company, user=user)
    return invoice


def assert_any_warehouse_scope(warehouses, company=None, user=None) -> None:
    """Allow a record when at least one of its warehouse anchors is in scope."""
    user = user or frappe.session.user
    require_authenticated_user()
    if is_privileged_user(user):
        return

    candidates = [str(warehouse).strip() for warehouse in (warehouses or []) if warehouse]
    for warehouse in dict.fromkeys(candidates):
        # Predicate, not throw-and-catch — a swallowed frappe.throw still
        # leaves its message in frappe.local.message_log, so probing N
        # warehouses used to emit N-1 stray denials before the real answer.
        if has_store_scope(warehouse=warehouse, company=company, user=user):
            return
    frappe.throw(
        _("This document is outside your assigned store scope."),
        frappe.PermissionError,
        title=_("Permission Denied"),
    )


def assert_pos_executive(store, user=None, msg=None) -> None:
    """Only staff assigned as an active POS Executive of ``store`` may operate
    its till (SAP cashier-store-assignment / Oracle Xstore home-store parity).

    CH User Scope is the ORG axis — back-office visibility, reports, list
    filters. It must never, by itself, grant till access: a company-wide
    scope would open every store's POS. The POS Executive master is the
    operational axis and the single source of truth for who can open a
    session at which store. Privileged users bypass (recovery/control-plane).
    """
    user = user or frappe.session.user
    if user == "Guest":
        require_authenticated_user()
    if is_privileged_user(user):
        return
    if frappe.db.exists(
        "POS Executive", {"user": user, "store": store, "is_active": 1}
    ):
        return
    frappe.throw(
        msg
        or _(
            "You are not an active POS Executive for store {0}. "
            "Ask your manager to add you before opening POS."
        ).format(store),
        frappe.PermissionError,
        title=_("POS Access Denied"),
    )
