"""
Executive Incentive Statement
==============================
Shows incentive earnings per sales executive for a selected period.

Modes
-----
- Manager / Accounts role  → sees ALL executives in selected store/company
- POS User (cashier)       → sees ONLY their own incentive (filtered to
  the POS Executive linked to frappe.session.user)

Sections
--------
1. Summary table  — one row per executive: billings count, billing amount,
                    total earned, paid out, pending
2. Detail table   — every billing row with item, amount, slab, incentive
"""

import frappe
from frappe import _
from frappe.utils import getdate, flt, today


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_manager():
    roles = frappe.get_roles(frappe.session.user)
    return any(r in roles for r in ("System Manager", "Accounts Manager", "Accounts User", "POS Manager"))


def _current_user_executive():
    """Return the POS Executive linked to the logged-in user, or None."""
    return frappe.db.get_value("POS Executive", {"user": frappe.session.user}, "name")


# ── Frappe Report API ─────────────────────────────────────────────────────────

def execute(filters=None):
    filters = filters or {}
    columns = get_columns(filters)
    data = get_data(filters)
    summary = get_report_summary(filters)
    return columns, data, None, None, summary


def get_columns(filters):
    view = filters.get("view", "Summary")
    if view == "Detail":
        return [
            {"label": _("Date"),            "fieldname": "posting_date",    "fieldtype": "Date",     "width": 100},
            {"label": _("Executive"),       "fieldname": "executive_name",  "fieldtype": "Data",     "width": 160},
            {"label": _("Invoice"),         "fieldname": "invoice",         "fieldtype": "Link",
             "options": "Sales Invoice",    "width": 160},
            {"label": _("Item"),            "fieldname": "item_name",       "fieldtype": "Data",     "width": 200},
            {"label": _("Brand"),           "fieldname": "brand",           "fieldtype": "Data",     "width": 100},
            {"label": _("Billing Amt"),     "fieldname": "billing_amount",  "fieldtype": "Currency", "width": 120},
            {"label": _("Slab"),            "fieldname": "incentive_slab",  "fieldtype": "Data",     "width": 140},
            {"label": _("Type"),            "fieldname": "incentive_type",  "fieldtype": "Data",     "width": 90},
            {"label": _("Value"),           "fieldname": "incentive_value", "fieldtype": "Float",    "width": 70},
            {"label": _("Incentive (₹)"),   "fieldname": "incentive_amount","fieldtype": "Currency", "width": 120},
            {"label": _("Status"),          "fieldname": "status",          "fieldtype": "Data",     "width": 90},
            {"label": _("Payout Month"),    "fieldname": "payout_month",    "fieldtype": "Data",     "width": 100},
        ]
    else:
        # Summary view
        return [
            {"label": _("Executive"),       "fieldname": "executive_name",  "fieldtype": "Data",     "width": 180},
            {"label": _("Store"),           "fieldname": "store",           "fieldtype": "Link",
             "options": "Warehouse",        "width": 160},
            {"label": _("Billings"),        "fieldname": "billings",        "fieldtype": "Int",      "width": 80},
            {"label": _("Billing Amt (₹)"), "fieldname": "billing_amount",  "fieldtype": "Currency", "width": 140},
            {"label": _("Total Earned (₹)"),"fieldname": "total_incentive", "fieldtype": "Currency", "width": 140},
            {"label": _("Paid (₹)"),        "fieldname": "paid",            "fieldtype": "Currency", "width": 120},
            {"label": _("Pending (₹)"),     "fieldname": "pending",         "fieldtype": "Currency", "width": 120},
        ]


def get_data(filters):
    conditions, values = _build_conditions(filters)
    view = filters.get("view", "Summary")

    if view == "Detail":
        rows = frappe.db.sql(f"""
            SELECT
                il.posting_date,
                COALESCE(pe.executive_name, il.pos_executive) AS executive_name,
                il.invoice,
                il.item_name,
                il.brand,
                il.billing_amount,
                il.incentive_slab,
                il.incentive_type,
                il.incentive_value,
                il.incentive_amount,
                il.status,
                il.payout_month
            FROM `tabPOS Incentive Ledger` il
            LEFT JOIN `tabPOS Executive` pe ON pe.name = il.pos_executive
            WHERE {conditions}
            ORDER BY il.posting_date DESC, il.pos_executive
        """, values, as_dict=True)
        return rows

    else:
        # Summary: group by executive
        rows = frappe.db.sql(f"""
            SELECT
                COALESCE(pe.executive_name, il.pos_executive) AS executive_name,
                il.store,
                COUNT(DISTINCT il.invoice)          AS billings,
                SUM(il.billing_amount)              AS billing_amount,
                SUM(il.incentive_amount)            AS total_incentive,
                SUM(CASE WHEN il.status = 'Paid'    THEN il.incentive_amount ELSE 0 END) AS paid,
                SUM(CASE WHEN il.status = 'Pending' THEN il.incentive_amount ELSE 0 END) AS pending
            FROM `tabPOS Incentive Ledger` il
            LEFT JOIN `tabPOS Executive` pe ON pe.name = il.pos_executive
            WHERE {conditions}
            GROUP BY il.pos_executive
            ORDER BY total_incentive DESC
        """, values, as_dict=True)
        return rows


def get_report_summary(filters):
    conditions, values = _build_conditions(filters)
    totals = frappe.db.sql(f"""
        SELECT
            COUNT(DISTINCT il.invoice)                                          AS billings,
            SUM(il.billing_amount)                                              AS billing_amount,
            SUM(il.incentive_amount)                                            AS total_incentive,
            SUM(CASE WHEN il.status = 'Paid'    THEN il.incentive_amount ELSE 0 END) AS paid,
            SUM(CASE WHEN il.status = 'Pending' THEN il.incentive_amount ELSE 0 END) AS pending
        FROM `tabPOS Incentive Ledger` il
        LEFT JOIN `tabPOS Executive` pe ON pe.name = il.pos_executive
        WHERE {conditions}
    """, values, as_dict=True)

    if not totals:
        return []

    t = totals[0]
    return [
        {"label": _("Total Billings"),    "value": int(t.billings or 0),            "datatype": "Int",      "indicator": "blue"},
        {"label": _("Billing Amount"),    "value": flt(t.billing_amount or 0),      "datatype": "Currency", "indicator": "blue"},
        {"label": _("Total Earned"),      "value": flt(t.total_incentive or 0),     "datatype": "Currency", "indicator": "green"},
        {"label": _("Paid Out"),          "value": flt(t.paid or 0),                "datatype": "Currency", "indicator": "green"},
        {"label": _("Pending Payout"),    "value": flt(t.pending or 0),             "datatype": "Currency", "indicator": "orange"},
    ]


def _build_conditions(filters):
    """Build SQL WHERE clause. Non-managers are restricted to their own executive."""
    conditions = ["il.docstatus != 2"]  # exclude cancelled; includes draft (0) and submitted (1)
    values = {}

    # Date range — default to current calendar month
    from_date = filters.get("from_date") or frappe.utils.get_first_day(today())
    to_date   = filters.get("to_date")   or frappe.utils.get_last_day(today())
    conditions.append("il.posting_date BETWEEN %(from_date)s AND %(to_date)s")
    values["from_date"] = from_date
    values["to_date"]   = to_date

    # Non-manager → restrict to own executive only
    if not _is_manager():
        own_exec = _current_user_executive()
        if not own_exec:
            # User has no linked executive — return nothing
            conditions.append("1 = 0")
        else:
            conditions.append("il.pos_executive = %(own_exec)s")
            values["own_exec"] = own_exec
    else:
        # Manager filters
        if filters.get("pos_executive"):
            conditions.append("il.pos_executive = %(pos_executive)s")
            values["pos_executive"] = filters["pos_executive"]

        if filters.get("store"):
            conditions.append("il.store = %(store)s")
            values["store"] = filters["store"]

        if filters.get("company"):
            conditions.append("il.company = %(company)s")
            values["company"] = filters["company"]

    if filters.get("status"):
        conditions.append("il.status = %(status)s")
        values["status"] = filters["status"]

    if filters.get("brand"):
        conditions.append("il.brand = %(brand)s")
        values["brand"] = filters["brand"]

    return " AND ".join(conditions), values


# ── Filter definitions (shown in report UI) ───────────────────────────────────

def get_filters():
    is_mgr = _is_manager()
    filters = [
        {
            "fieldname": "from_date",
            "label": _("From Date"),
            "fieldtype": "Date",
            "default": frappe.utils.get_first_day(today()),
            "reqd": 1,
        },
        {
            "fieldname": "to_date",
            "label": _("To Date"),
            "fieldtype": "Date",
            "default": frappe.utils.get_last_day(today()),
            "reqd": 1,
        },
        {
            "fieldname": "view",
            "label": _("View"),
            "fieldtype": "Select",
            "options": "Summary\nDetail",
            "default": "Summary",
        },
        {
            "fieldname": "status",
            "label": _("Status"),
            "fieldtype": "Select",
            "options": "\nPending\nPaid",
        },
    ]

    if is_mgr:
        filters += [
            {
                "fieldname": "pos_executive",
                "label": _("Executive"),
                "fieldtype": "Link",
                "options": "POS Executive",
            },
            {
                "fieldname": "store",
                "label": _("Store"),
                "fieldtype": "Link",
                "options": "Warehouse",
            },
            {
                "fieldname": "company",
                "label": _("Company"),
                "fieldtype": "Link",
                "options": "Company",
            },
            {
                "fieldname": "brand",
                "label": _("Brand"),
                "fieldtype": "Link",
                "options": "Brand",
            },
        ]

    return filters
