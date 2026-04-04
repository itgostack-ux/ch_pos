import frappe
from frappe import _
from frappe.utils import getdate, flt


def execute(filters=None):
    columns = [
        {"fieldname": "session", "label": _("Session"), "fieldtype": "Link", "options": "CH POS Session", "width": 160},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 140},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 150},
        {"fieldname": "business_date", "label": _("Business Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "user", "label": _("Cashier"), "fieldtype": "Link", "options": "User", "width": 140},
        {"fieldname": "session_net_sales", "label": _("Session Net Sales"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "session_invoices", "label": _("Session Invoice Count"), "fieldtype": "Int", "width": 130},
        {"fieldname": "pos_invoice_total", "label": _("Sales Invoice Total"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "pos_invoice_count", "label": _("Sales Invoice Count"), "fieldtype": "Int", "width": 130},
        {"fieldname": "difference", "label": _("Difference (₹)"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "count_diff", "label": _("Count Diff"), "fieldtype": "Int", "width": 90},
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

    sessions = frappe.db.sql("""
        SELECT s.name AS session, s.store, s.company, s.business_date,
               s.user, s.net_sales AS session_net_sales,
               s.total_invoices AS session_invoices, s.pos_profile
        FROM `tabCH POS Session` s
        WHERE {conditions}
        ORDER BY s.business_date DESC
        LIMIT 500
    """.format(conditions=conditions), params, as_dict=True)  # noqa: UP032

    data = []
    for s in sessions:
        inv_data = frappe.db.sql("""
            SELECT COALESCE(SUM(grand_total), 0) AS total,
                   COUNT(*) AS cnt
            FROM `tabSales Invoice`
            WHERE pos_profile = %(pp)s
              AND posting_date = %(bd)s
              AND docstatus = 1
              AND is_consolidated = 0
        """, {"pp": s.pos_profile, "bd": s.business_date}, as_dict=True)[0]

        s["pos_invoice_total"] = flt(inv_data.total)
        s["pos_invoice_count"] = inv_data.cnt
        s["difference"] = flt(s.session_net_sales) - flt(inv_data.total)
        s["count_diff"] = (s.session_invoices or 0) - inv_data.cnt
        data.append(s)

    return columns, data
