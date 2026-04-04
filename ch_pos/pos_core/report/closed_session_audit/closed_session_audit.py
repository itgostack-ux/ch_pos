import frappe
from frappe import _
from frappe.utils import getdate


def execute(filters=None):
    columns = [
        {"fieldname": "session", "label": _("Session"), "fieldtype": "Link", "options": "CH POS Session", "width": 160},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 140},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 150},
        {"fieldname": "device", "label": _("Device"), "fieldtype": "Link", "options": "CH Device Master", "width": 130},
        {"fieldname": "user", "label": _("Cashier"), "fieldtype": "Link", "options": "User", "width": 140},
        {"fieldname": "business_date", "label": _("Business Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "shift_start", "label": _("Shift Start"), "fieldtype": "Datetime", "width": 150},
        {"fieldname": "shift_end", "label": _("Shift End"), "fieldtype": "Datetime", "width": 150},
        {"fieldname": "opening_cash", "label": _("Opening Cash"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "closing_cash_actual", "label": _("Closing Cash"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "cash_variance", "label": _("Variance"), "fieldtype": "Currency", "width": 100},
        {"fieldname": "total_invoices", "label": _("Invoices"), "fieldtype": "Int", "width": 80},
        {"fieldname": "net_sales", "label": _("Net Sales"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "total_cash_drops", "label": _("Cash Drops"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "has_settlement", "label": _("Settlement"), "fieldtype": "Data", "width": 90},
        {"fieldname": "settlement_status", "label": _("Settlement Status"), "fieldtype": "Data", "width": 120},
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
        SELECT
            s.name AS session, s.store, s.company, s.device, s.user,
            s.business_date, s.shift_start, s.shift_end,
            s.opening_cash, s.closing_cash_actual, s.cash_variance,
            s.total_invoices, s.net_sales, s.total_cash_drops
        FROM `tabCH POS Session` s
        WHERE {conditions}
        ORDER BY s.business_date DESC, s.shift_end DESC
        LIMIT 500
    """.format(conditions=conditions), params, as_dict=True)  # noqa: UP032

    # Enrich with settlement data
    for row in sessions:
        settlement = frappe.db.get_value(
            "CH POS Settlement",
            {"session": row["session"], "docstatus": 1},
            "settlement_status",
        )
        row["has_settlement"] = "Yes" if settlement else "No"
        row["settlement_status"] = settlement or ""

    return columns, sessions
