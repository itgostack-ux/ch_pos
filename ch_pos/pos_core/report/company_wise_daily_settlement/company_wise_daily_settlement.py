import frappe
from frappe import _
from frappe.utils import getdate, flt


def execute(filters=None):
    columns = [
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 180},
        {"fieldname": "business_date", "label": _("Business Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "total_sessions", "label": _("Sessions"), "fieldtype": "Int", "width": 80},
        {"fieldname": "total_expected", "label": _("Expected Cash"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "total_actual", "label": _("Actual Cash"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "total_variance", "label": _("Variance"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "total_cash_sales", "label": _("Cash Sales"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "total_card_sales", "label": _("Card Sales"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "total_upi_sales", "label": _("UPI Sales"), "fieldtype": "Currency", "width": 130},
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

    data = frappe.db.sql("""
        SELECT
            s.company,
            s.business_date,
            COUNT(*) AS total_sessions,
            SUM(s.expected_closing_cash) AS total_expected,
            SUM(s.actual_closing_cash) AS total_actual,
            SUM(s.variance_amount) AS total_variance,
            SUM(s.total_sales_cash) AS total_cash_sales,
            SUM(s.total_sales_card) AS total_card_sales,
            SUM(s.total_sales_upi) AS total_upi_sales
        FROM `tabCH POS Settlement` s
        WHERE {conditions}
        GROUP BY s.company, s.business_date
        ORDER BY s.business_date DESC, s.company
    """.format(conditions=conditions), params, as_dict=True)  # noqa: UP032

    return columns, data
