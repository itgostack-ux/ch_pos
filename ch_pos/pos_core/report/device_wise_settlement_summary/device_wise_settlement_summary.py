import frappe
from frappe import _
from frappe.utils import getdate


def execute(filters=None):
    columns = [
        {"fieldname": "device", "label": _("Device"), "fieldtype": "Link", "options": "CH Device Master", "width": 150},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 140},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 160},
        {"fieldname": "session", "label": _("Session"), "fieldtype": "Link", "options": "CH POS Session", "width": 160},
        {"fieldname": "business_date", "label": _("Business Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "settlement_status", "label": _("Settlement Status"), "fieldtype": "Data", "width": 120},
        {"fieldname": "expected_closing_cash", "label": _("Expected Cash"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "actual_closing_cash", "label": _("Actual Cash"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "variance_amount", "label": _("Variance"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "total_sales_cash", "label": _("Cash Sales"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "total_sales_card", "label": _("Card Sales"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "total_sales_upi", "label": _("UPI Sales"), "fieldtype": "Currency", "width": 120},
    ]

    conditions = {"docstatus": 1}
    if filters:
        if filters.get("company"):
            conditions["company"] = filters["company"]
        if filters.get("store"):
            conditions["store"] = filters["store"]
        if filters.get("from_date"):
            conditions["business_date"] = (">=", getdate(filters["from_date"]))
        if filters.get("to_date"):
            conditions.setdefault("business_date", ("<=", getdate(filters["to_date"])))

    data = frappe.get_all(
        "CH POS Settlement",
        filters=conditions,
        fields=["device", "store", "company", "session", "business_date",
                "settlement_status", "expected_closing_cash", "actual_closing_cash",
            "variance_amount", "total_sales_cash", "total_sales_card", "total_sales_upi"],
        order_by="business_date desc, device asc",
    )

    return columns, data
