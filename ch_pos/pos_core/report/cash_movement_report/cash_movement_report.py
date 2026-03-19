import frappe
from frappe import _
from frappe.utils import getdate


def execute(filters=None):
    columns = [
        {"fieldname": "name", "label": _("Movement"), "fieldtype": "Link", "options": "CH Cash Drop", "width": 150},
        {"fieldname": "session", "label": _("Session"), "fieldtype": "Link", "options": "CH POS Session", "width": 150},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 140},
        {"fieldname": "company", "label": _("Company"), "fieldtype": "Link", "options": "Company", "width": 150},
        {"fieldname": "movement_type", "label": _("Type"), "fieldtype": "Data", "width": 140},
        {"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "reason", "label": _("Reason"), "fieldtype": "Data", "width": 200},
        {"fieldname": "business_date", "label": _("Business Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "created_by", "label": _("Created By"), "fieldtype": "Link", "options": "User", "width": 140},
        {"fieldname": "approved_by", "label": _("Approved By"), "fieldtype": "Link", "options": "User", "width": 140},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
    ]

    conditions = {"docstatus": 1}
    if filters:
        if filters.get("company"):
            conditions["company"] = filters["company"]
        if filters.get("store"):
            conditions["store"] = filters["store"]
        if filters.get("movement_type"):
            conditions["movement_type"] = filters["movement_type"]
        if filters.get("from_date"):
            conditions["business_date"] = (">=", getdate(filters["from_date"]))
        if filters.get("to_date"):
            conditions.setdefault("business_date", ("<=", getdate(filters["to_date"])))

    data = frappe.get_all(
        "CH Cash Drop",
        filters=conditions,
        fields=["name", "session", "store", "company", "movement_type",
                "amount", "reason", "business_date", "created_by",
                "approved_by", "status"],
        order_by="creation desc",
        limit=500,
    )

    return columns, data
