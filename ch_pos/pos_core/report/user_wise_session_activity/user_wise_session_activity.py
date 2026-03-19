import frappe
from frappe import _
from frappe.utils import getdate


def execute(filters=None):
    columns = [
        {"fieldname": "user", "label": _("Cashier"), "fieldtype": "Link", "options": "User", "width": 160},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 160},
        {"fieldname": "total_sessions", "label": _("Total Sessions"), "fieldtype": "Int", "width": 110},
        {"fieldname": "open_sessions", "label": _("Open"), "fieldtype": "Int", "width": 70},
        {"fieldname": "closed_sessions", "label": _("Closed"), "fieldtype": "Int", "width": 70},
        {"fieldname": "total_net_sales", "label": _("Net Sales"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "total_invoices", "label": _("Invoice Count"), "fieldtype": "Int", "width": 100},
        {"fieldname": "total_variance", "label": _("Total Variance"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "avg_variance", "label": _("Avg Variance"), "fieldtype": "Currency", "width": 110},
    ]

    conditions = "s.docstatus = 1"
    params = {}
    if filters:
        if filters.get("company"):
            conditions += " AND s.company = %(company)s"
            params["company"] = filters["company"]
        if filters.get("from_date"):
            conditions += " AND s.business_date >= %(from_date)s"
            params["from_date"] = getdate(filters["from_date"])
        if filters.get("to_date"):
            conditions += " AND s.business_date <= %(to_date)s"
            params["to_date"] = getdate(filters["to_date"])

    data = frappe.db.sql(f"""
        SELECT
            s.user,
            s.company,
            COUNT(*) AS total_sessions,
            SUM(CASE WHEN s.status IN ('Open', 'Locked', 'Suspended') THEN 1 ELSE 0 END) AS open_sessions,
            SUM(CASE WHEN s.status = 'Closed' THEN 1 ELSE 0 END) AS closed_sessions,
            SUM(IFNULL(s.net_sales, 0)) AS total_net_sales,
            SUM(IFNULL(s.total_invoices, 0)) AS total_invoices,
            SUM(IFNULL(s.cash_variance, 0)) AS total_variance,
            AVG(IFNULL(s.cash_variance, 0)) AS avg_variance
        FROM `tabCH POS Session` s
        WHERE {conditions}
        GROUP BY s.user, s.company
        ORDER BY total_net_sales DESC
    """, params, as_dict=True)

    return columns, data
