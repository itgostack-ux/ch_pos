# Copyright (c) 2026, GoStack and contributors
# POS Exchange Reconciliation Report
#
# Reconciles exchange credit given to customer at POS vs. actual buyback
# payout from the Buyback Order. The delta reveals the gross margin captured
# on each exchange transaction.
#
# Joins:
#   Sales Invoice  (sells new device, gives exchange credit)
#   Buyback Assessment (the valuation quoted)
#   Buyback Order (the actual final price paid to original owner)

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": _("Invoice Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
        {"label": _("Sales Invoice"), "fieldname": "invoice", "fieldtype": "Link",
         "options": "Sales Invoice", "width": 155},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link",
         "options": "Customer", "width": 130},
        {"label": _("Store"), "fieldname": "warehouse", "fieldtype": "Link",
         "options": "Warehouse", "width": 120},
        {"label": _("Exchange Assessment"), "fieldname": "exchange_assessment",
         "fieldtype": "Link", "options": "Buyback Assessment", "width": 155},
        {"label": _("Assessment Status"), "fieldname": "assessment_status",
         "fieldtype": "Data", "width": 120},
        {"label": _("Exchange Credit Given"), "fieldname": "exchange_amount",
         "fieldtype": "Currency", "width": 140},
        {"label": _("Buyback Order"), "fieldname": "buyback_order",
         "fieldtype": "Link", "options": "Buyback Order", "width": 140},
        {"label": _("Buyback Payout"), "fieldname": "buyback_payout",
         "fieldtype": "Currency", "width": 130},
        {"label": _("Delta (Credit - Payout)"), "fieldname": "delta",
         "fieldtype": "Currency", "width": 150,
         "description": "Positive = profit on exchange, Negative = gave more than received"},
        {"label": _("Assessment Grade"), "fieldname": "condition_grade",
         "fieldtype": "Data", "width": 100},
    ]


def get_data(filters):
    conditions = _build_conditions(filters)

    invoices = frappe.db.sql("""
        SELECT
            pi.posting_date,
            pi.name AS invoice,
            pi.customer,
            pi.warehouse,
            pi.custom_exchange_assessment AS exchange_assessment,
            pi.custom_exchange_amount AS exchange_amount
        FROM `tabSales Invoice` pi
        WHERE pi.docstatus = 1
          AND pi.custom_exchange_assessment IS NOT NULL
          AND pi.custom_exchange_assessment != ''
          {conditions}
        ORDER BY pi.posting_date DESC
    """.format(conditions=conditions), filters, as_dict=True)

    data = []
    for inv in invoices:
        assessment = inv.exchange_assessment
        if not assessment:
            continue

        ba = frappe.db.get_value(
            "Buyback Assessment", assessment,
            ["status", "condition_grade"], as_dict=True,
        ) or {}

        # Find Buyback Order linked to this assessment
        order_name = frappe.db.get_value(
            "Buyback Order",
            {"buyback_assessment": assessment, "docstatus": 1},
            "name",
        )
        buyback_payout = 0.0
        if order_name:
            buyback_payout = flt(frappe.db.get_value("Buyback Order", order_name, "final_price"))

        exchange_amount = flt(inv.exchange_amount)
        delta = exchange_amount - buyback_payout

        data.append({
            "posting_date": inv.posting_date,
            "invoice": inv.invoice,
            "customer": inv.customer,
            "warehouse": inv.warehouse,
            "exchange_assessment": assessment,
            "assessment_status": ba.get("status", ""),
            "exchange_amount": exchange_amount,
            "buyback_order": order_name or "",
            "buyback_payout": buyback_payout,
            "delta": delta,
            "condition_grade": ba.get("condition_grade", ""),
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
    ]
