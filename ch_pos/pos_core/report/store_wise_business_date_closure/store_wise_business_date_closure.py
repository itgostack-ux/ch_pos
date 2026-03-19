import frappe
from frappe import _


def execute(filters=None):
    columns = [
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 160},
        {"fieldname": "business_date", "label": _("Business Date"), "fieldtype": "Date", "width": 110},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 120},
        {"fieldname": "is_active", "label": _("Active"), "fieldtype": "Check", "width": 60},
        {"fieldname": "opened_by", "label": _("Opened By"), "fieldtype": "Link", "options": "User", "width": 140},
        {"fieldname": "opened_on", "label": _("Opened On"), "fieldtype": "Datetime", "width": 160},
        {"fieldname": "closed_by", "label": _("Closed By"), "fieldtype": "Link", "options": "User", "width": 140},
        {"fieldname": "closed_on", "label": _("Closed On"), "fieldtype": "Datetime", "width": 160},
        {"fieldname": "open_sessions", "label": _("Open Sessions"), "fieldtype": "Int", "width": 100},
        {"fieldname": "closed_sessions", "label": _("Closed Sessions"), "fieldtype": "Int", "width": 110},
    ]

    conditions = {}
    if filters:
        if filters.get("store"):
            conditions["store"] = filters["store"]
        if filters.get("status"):
            conditions["status"] = filters["status"]

    bd_list = frappe.get_all(
        "CH Business Date",
        filters=conditions,
        fields=["store", "business_date", "status", "is_active",
                "opened_by", "opened_on", "closed_by", "closed_on"],
        order_by="business_date desc, store asc",
        limit=200,
    )

    for row in bd_list:
        row["open_sessions"] = frappe.db.count("CH POS Session", {
            "store": row["store"],
            "business_date": row["business_date"],
            "status": ("in", ["Open", "Locked", "Suspended", "Pending Close"]),
            "docstatus": 1,
        })
        row["closed_sessions"] = frappe.db.count("CH POS Session", {
            "store": row["store"],
            "business_date": row["business_date"],
            "status": "Closed",
            "docstatus": 1,
        })

    return columns, bd_list
