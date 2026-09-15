"""POS Executive Roster — one view of who can work a till, and where.

Answering "who can log into POS, at which store" used to mean cross-referencing
POS Executive against CH User Scope by hand, in two different apps. This joins
them, and flags the two conditions that silently break attribution: a POS
Executive with no Sales Person (incentives cannot pay) and one holding System
Manager (every scope and identity gate is bypassed for that account).
"""

import frappe
from frappe import _

from ch_erp15.ch_erp15.report_scope import narrow_filters_by_store_scope


def execute(filters=None):
    columns = [
        {"fieldname": "user", "label": _("User"), "fieldtype": "Link", "options": "User", "width": 200},
        {"fieldname": "executive_name", "label": _("Executive"), "fieldtype": "Data", "width": 160},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 150},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 160},
        {"fieldname": "role", "label": _("POS Role"), "fieldtype": "Data", "width": 120},
        {"fieldname": "is_active", "label": _("Active"), "fieldtype": "Check", "width": 70},
        {"fieldname": "sales_person", "label": _("Sales Person"), "fieldtype": "Link", "options": "Sales Person", "width": 150},
        {"fieldname": "sales_person_missing", "label": _("No Sales Person"), "fieldtype": "Check", "width": 120},
        {"fieldname": "role_profile", "label": _("Scope Role Profile"), "fieldtype": "Link", "options": "Role Profile", "width": 180},
        {"fieldname": "scope_enabled", "label": _("Scope Enabled"), "fieldtype": "Check", "width": 110},
        {"fieldname": "no_user_scope", "label": _("No User Scope"), "fieldtype": "Check", "width": 110},
        {"fieldname": "holds_system_manager", "label": _("System Manager"), "fieldtype": "Check", "width": 130},
    ]

    conditions = {}
    if filters and filters.get("company"):
        conditions["company"] = filters["company"]
    if filters and filters.get("store"):
        conditions["store"] = filters["store"]

    # Tier 4 — CH User Scope narrowing on `store` (fail-closed), same contract as
    # Device Wise Open Sessions: a store-scoped manager sees only their own
    # store's roster; scope-bypass users see the estate.
    if not narrow_filters_by_store_scope(conditions, store_field="store"):
        return columns, []

    executives = frappe.get_all(
        "POS Executive",
        filters=conditions,
        fields=["user", "executive_name", "store", "company", "role", "is_active", "sales_person"],
        order_by="store asc, executive_name asc",
    )
    if not executives:
        return columns, []

    users = list({row.user for row in executives if row.user})

    scopes = {
        row.user: row
        for row in frappe.get_all(
            "CH User Scope",
            filters={"user": ["in", users]},
            fields=["user", "role_profile", "enabled"],
        )
    }
    system_managers = set(
        frappe.get_all(
            "Has Role",
            filters={"role": "System Manager", "parenttype": "User", "parent": ["in", users]},
            pluck="parent",
        )
    )

    data = []
    for row in executives:
        scope = scopes.get(row.user)
        data.append(
            {
                **row,
                "sales_person_missing": 0 if row.sales_person else 1,
                "role_profile": scope.role_profile if scope else None,
                "scope_enabled": scope.enabled if scope else 0,
                "no_user_scope": 0 if scope else 1,
                "holds_system_manager": 1 if row.user in system_managers else 0,
            }
        )

    return columns, data
