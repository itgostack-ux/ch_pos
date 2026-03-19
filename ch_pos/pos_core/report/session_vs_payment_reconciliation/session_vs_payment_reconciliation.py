import frappe
from frappe import _
from frappe.utils import getdate, flt


def execute(filters=None):
    columns = [
        {"fieldname": "session", "label": _("Session"), "fieldtype": "Link", "options": "CH POS Session", "width": 160},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 140},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 150},
        {"fieldname": "business_date", "label": _("Business Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "mode_of_payment", "label": _("Payment Mode"), "fieldtype": "Data", "width": 140},
        {"fieldname": "payment_type", "label": _("Type"), "fieldtype": "Data", "width": 80},
        {"fieldname": "payment_total", "label": _("Payment Total"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "invoice_count", "label": _("Invoices"), "fieldtype": "Int", "width": 80},
    ]

    conditions = "s.docstatus = 1 AND s.status = 'Closed'"
    params = {}
    if filters:
        if filters.get("company"):
            conditions += " AND s.company = %(company)s"
            params["company"] = filters["company"]
        if filters.get("store"):
            conditions += " AND s.store = %(store)s"
            params["store"] = filters["store"]
        if filters.get("from_date"):
            conditions += " AND s.business_date >= %(from_date)s"
            params["from_date"] = getdate(filters["from_date"])
        if filters.get("to_date"):
            conditions += " AND s.business_date <= %(to_date)s"
            params["to_date"] = getdate(filters["to_date"])

    data = frappe.db.sql(f"""
        SELECT
            s.name AS session,
            s.store,
            s.company,
            s.business_date,
            sip.mode_of_payment,
            mop.type AS payment_type,
            SUM(sip.amount) AS payment_total,
            COUNT(DISTINCT pi.name) AS invoice_count
        FROM `tabCH POS Session` s
        JOIN `tabPOS Invoice` pi
            ON pi.pos_profile = s.pos_profile
            AND pi.posting_date = s.business_date
            AND pi.docstatus = 1
            AND IFNULL(pi.consolidated_invoice, '') = ''
        JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
        LEFT JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
        WHERE {conditions}
        GROUP BY s.name, s.store, s.company, s.business_date,
                 sip.mode_of_payment, mop.type
        ORDER BY s.business_date DESC, s.name, payment_total DESC
        LIMIT 1000
    """, params, as_dict=True)

    return columns, data
