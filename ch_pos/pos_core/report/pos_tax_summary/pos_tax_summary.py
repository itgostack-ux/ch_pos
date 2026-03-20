# Copyright (c) 2026, GoStack and contributors
# POS Tax Summary Report — GST breakdown by scheme, store, and company.
#
# Shows per-invoice: gross amount, discount, taxable portion, exempted portion,
# GST collected, and whether the invoice used regular or margin scheme GST.
# Supports GSTR-1 / GSTR-3B compilation review.

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
        {"label": _("Invoice"), "fieldname": "name", "fieldtype": "Link",
         "options": "Sales Invoice", "width": 160},
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link",
         "options": "Customer", "width": 140},
        {"label": _("Store"), "fieldname": "warehouse", "fieldtype": "Link",
         "options": "Warehouse", "width": 130},
        {"label": _("Company"), "fieldname": "company", "fieldtype": "Link",
         "options": "Company", "width": 130},
        {"label": _("Tax Scheme"), "fieldname": "tax_scheme", "fieldtype": "Data", "width": 100},
        {"label": _("Gross Amount"), "fieldname": "gross_amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Discount"), "fieldname": "discount_amount", "fieldtype": "Currency", "width": 100},
        {"label": _("Taxable Value"), "fieldname": "taxable_value", "fieldtype": "Currency", "width": 120},
        {"label": _("Exempted Value"), "fieldname": "exempted_value", "fieldtype": "Currency", "width": 120},
        {"label": _("GST Amount"), "fieldname": "gst_amount", "fieldtype": "Currency", "width": 110},
        {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 120},
        {"label": _("Mode of Payment"), "fieldname": "mode_of_payment", "fieldtype": "Data", "width": 120},
        {"label": _("Sale Type"), "fieldname": "sale_type", "fieldtype": "Data", "width": 100},
    ]


def get_data(filters):
    conditions = _build_conditions(filters)

    invoices = frappe.db.sql("""
        SELECT
            pi.name,
            pi.posting_date,
            pi.customer,
            pi.warehouse,
            pi.company,
            pi.grand_total,
            pi.discount_amount,
            pi.net_total,
            pi.total_taxes_and_charges,
            pi.custom_is_margin_scheme,
            pi.custom_margin_taxable,
            pi.custom_margin_gst,
            pi.custom_margin_exempted,
            pi.custom_ch_sale_type,
            pi.is_return,
            GROUP_CONCAT(DISTINCT sip.mode_of_payment ORDER BY sip.mode_of_payment SEPARATOR ', ')
                AS mode_of_payment
        FROM `tabSales Invoice` pi
        LEFT JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
        WHERE pi.docstatus = 1
          {conditions}
        GROUP BY pi.name
        ORDER BY pi.posting_date DESC, pi.name DESC
    """.format(conditions=conditions), filters, as_dict=True)

    data = []
    for inv in invoices:
        is_margin = bool(inv.custom_is_margin_scheme)

        taxable_value = flt(inv.custom_margin_taxable) if is_margin else flt(inv.net_total)
        exempted_value = flt(inv.custom_margin_exempted) if is_margin else 0
        gst_amount = flt(inv.custom_margin_gst) if is_margin else flt(inv.total_taxes_and_charges)
        tax_scheme = "Margin Scheme" if is_margin else "Regular"

        if inv.is_return:
            tax_scheme = f"Return ({tax_scheme})"

        data.append({
            "posting_date": inv.posting_date,
            "name": inv.name,
            "customer": inv.customer,
            "warehouse": inv.warehouse,
            "company": inv.company,
            "tax_scheme": tax_scheme,
            "gross_amount": flt(inv.grand_total),
            "discount_amount": flt(inv.discount_amount),
            "taxable_value": taxable_value,
            "exempted_value": exempted_value,
            "gst_amount": gst_amount,
            "grand_total": flt(inv.grand_total),
            "mode_of_payment": inv.mode_of_payment or "",
            "sale_type": inv.custom_ch_sale_type or "",
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
    if filters.get("tax_scheme") == "Margin Scheme":
        conditions.append("pi.custom_is_margin_scheme = 1")
    elif filters.get("tax_scheme") == "Regular":
        conditions.append("(pi.custom_is_margin_scheme = 0 OR pi.custom_is_margin_scheme IS NULL)")
    if filters.get("mode_of_payment"):
        conditions.append("""pi.name IN (
            SELECT parent FROM `tabSales Invoice Payment`
            WHERE mode_of_payment = %(mode_of_payment)s AND parenttype = 'Sales Invoice'
        )""")
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
            "fieldname": "tax_scheme",
            "label": _("Tax Scheme"),
            "fieldtype": "Select",
            "options": "\nAll\nRegular\nMargin Scheme",
        },
        {
            "fieldname": "mode_of_payment",
            "label": _("Mode of Payment"),
            "fieldtype": "Link",
            "options": "Mode of Payment",
        },
    ]
