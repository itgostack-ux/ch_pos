import frappe
from frappe import _
from frappe.utils import getdate


def execute(filters=None):
    columns = [
        {"fieldname": "session", "label": _("Session"), "fieldtype": "Link", "options": "CH POS Session", "width": 160},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 140},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 160},
        {"fieldname": "device", "label": _("Device"), "fieldtype": "Link", "options": "CH Device Master", "width": 130},
        {"fieldname": "user", "label": _("Cashier"), "fieldtype": "Link", "options": "User", "width": 140},
        {"fieldname": "business_date", "label": _("Business Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "opening_cash", "label": _("Opening"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "closing_cash_actual", "label": _("Actual Close"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "cash_variance", "label": _("Variance"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "variance_reason", "label": _("Reason"), "fieldtype": "Data", "width": 200},
        {"fieldname": "manager_approved", "label": _("Manager Approved"), "fieldtype": "Data", "width": 130},
    ]

    conditions = "s.docstatus = 1 AND s.status = 'Closed' AND s.cash_variance != 0"
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

    data = frappe.db.sql("""
        SELECT
            s.name AS session, s.store, s.company, s.device, s.user,
            s.business_date, s.opening_cash, s.closing_cash_actual,
            s.cash_variance, s.variance_reason,
            CASE WHEN s.variance_reason IS NOT NULL AND s.variance_reason != ''
                 THEN 'Yes' ELSE 'No' END AS manager_approved
        FROM `tabCH POS Session` s
        WHERE {conditions}
        ORDER BY ABS(s.cash_variance) DESC, s.business_date DESC
    """.format(conditions=conditions), params, as_dict=True)  # noqa: UP032

    return columns, data
