import frappe
from frappe import _


def execute(filters=None):
    columns = [
        {"fieldname": "device", "label": _("Device"), "fieldtype": "Link", "options": "CH Device Master", "width": 150},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 140},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 160},
        {"fieldname": "session", "label": _("Session"), "fieldtype": "Link", "options": "CH POS Session", "width": 160},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
        {"fieldname": "user", "label": _("Cashier"), "fieldtype": "Link", "options": "User", "width": 150},
        {"fieldname": "business_date", "label": _("Business Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "shift_start", "label": _("Shift Start"), "fieldtype": "Datetime", "width": 160},
        {"fieldname": "opening_cash", "label": _("Opening Cash"), "fieldtype": "Currency", "width": 120},
    ]

    conditions = {"status": ("in", ["Open", "Locked", "Suspended", "Pending Close"]), "docstatus": 1}
    if filters and filters.get("company"):
        conditions["company"] = filters["company"]
    if filters and filters.get("store"):
        conditions["store"] = filters["store"]

    data = frappe.get_all(
        "CH POS Session",
        filters=conditions,
        fields=["device", "store", "company", "name as session", "status", "user",
                "business_date", "shift_start", "opening_cash"],
        order_by="shift_start desc",
    )

    return columns, data
