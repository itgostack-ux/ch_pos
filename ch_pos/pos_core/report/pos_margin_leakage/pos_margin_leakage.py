# Copyright (c) 2026, GoStack and contributors
# POS Margin Leakage Report
#
# Compares the expected margin (selling price - incoming/purchase cost) against
# actual collected amounts after discounts, flagging items where margin went
# negative (Loss) or fell below 10% (Leaking).
#
# Uses:
#   - Sales Invoice Item: rate, qty, discount_amount, serial_no, incoming_rate
#   - CH Serial Lifecycle: purchase_rate (for refurbished items)

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
        {"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link",
         "options": "Sales Invoice", "width": 155},
        {"label": _("Store"), "fieldname": "warehouse", "fieldtype": "Link",
         "options": "Warehouse", "width": 130},
        {"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link",
         "options": "Item", "width": 140},
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
        {"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 60},
        {"label": _("Selling Rate"), "fieldname": "rate", "fieldtype": "Currency", "width": 110},
        {"label": _("Discount"), "fieldname": "discount_amount", "fieldtype": "Currency", "width": 100},
        {"label": _("Net Rate"), "fieldname": "net_rate", "fieldtype": "Currency", "width": 110},
        {"label": _("Purchase Cost"), "fieldname": "incoming_rate", "fieldtype": "Currency", "width": 120},
        {"label": _("Margin Amount"), "fieldname": "margin_amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Margin %"), "fieldname": "margin_pct", "fieldtype": "Percent", "width": 90},
        {"label": _("Status"), "fieldname": "flag", "fieldtype": "Data", "width": 90},
    ]


def get_data(filters):
    conditions = _build_conditions(filters)

    rows = frappe.db.sql("""
        SELECT
            pi.posting_date,
            pi.name AS invoice,
            pi.warehouse,
            pii.item_code,
            pii.item_name,
            pii.qty,
            pii.rate,
            pii.discount_amount,
            pii.serial_no,
            pii.incoming_rate,
            pii.custom_is_margin_item
        FROM `tabSales Invoice` pi
        JOIN `tabSales Invoice Item` pii ON pii.parent = pi.name
        WHERE pi.docstatus = 1
          AND pi.is_return = 0
          {conditions}
        ORDER BY pi.posting_date DESC, pi.name
    """.format(conditions=conditions), filters, as_dict=True)

    data = []
    for row in rows:
        rate = flt(row.rate)
        discount = flt(row.discount_amount)
        net_rate = rate - discount

        # Get purchase cost: prefer CH Serial Lifecycle, fallback to incoming_rate
        incoming = 0.0
        serial = (row.serial_no or "").strip().split("\n")[0].strip()
        if serial:
            purchase_rate = frappe.db.get_value(
                "CH Serial Lifecycle", {"serial_no": serial}, "purchase_rate"
            )
            if purchase_rate:
                incoming = flt(purchase_rate)

        if not incoming:
            incoming = flt(row.incoming_rate)

        margin = net_rate - incoming
        margin_pct = (margin / net_rate * 100) if net_rate > 0 else 0

        if margin < 0:
            flag = "Loss"
        elif margin_pct < 10:
            flag = "Leaking"
        else:
            flag = "Healthy"

        data.append({
            "posting_date": row.posting_date,
            "invoice": row.invoice,
            "warehouse": row.warehouse,
            "item_code": row.item_code,
            "item_name": row.item_name,
            "qty": flt(row.qty),
            "rate": rate,
            "discount_amount": discount,
            "net_rate": net_rate,
            "incoming_rate": incoming,
            "margin_amount": margin,
            "margin_pct": margin_pct,
            "flag": flag,
        })

    return data


def _build_conditions(filters):
    conditions = []
    if filters.get("from_date"):
        conditions.append("pi.posting_date >= %(from_date)s")
    if filters.get("to_date"):
        conditions.append("pi.posting_date <= %(to_date)s")
    if filters.get("company"):
        conditions.append("pi.company = %(company)s")
    if filters.get("store"):
        conditions.append("pi.warehouse = %(store)s")
    if filters.get("item_group"):
        conditions.append("pii.item_code IN (SELECT name FROM tabItem WHERE item_group = %(item_group)s)")
    return ("AND " + " AND ".join(conditions)) if conditions else ""


def get_filters():
    return [
        {
            "fieldname": "from_date",
            "label": _("From Date"),
            "fieldtype": "Date",
            "default": frappe.utils.get_first_day(frappe.utils.nowdate()),
            "reqd": 1,
        },
        {
            "fieldname": "to_date",
            "label": _("To Date"),
            "fieldtype": "Date",
            "default": frappe.utils.nowdate(),
            "reqd": 1,
        },
        {
            "fieldname": "company",
            "label": _("Company"),
            "fieldtype": "Link",
            "options": "Company",
        },
        {
            "fieldname": "store",
            "label": _("Store"),
            "fieldtype": "Link",
            "options": "Warehouse",
        },
        {
            "fieldname": "item_group",
            "label": _("Item Group"),
            "fieldtype": "Link",
            "options": "Item Group",
        },
    ]
