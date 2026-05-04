import frappe


def execute():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    from ch_pos.setup import CUSTOM_FIELDS

    create_custom_fields({"Sales Invoice": CUSTOM_FIELDS.get("Sales Invoice", [])}, update=True)
    frappe.clear_cache(doctype="Sales Invoice")
