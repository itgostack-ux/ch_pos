"""Migrate custom fields from POS Invoice to Sales Invoice.

POS now creates Sales Invoice (is_pos=1) instead of POS Invoice.
This patch:
1. Creates all custom fields on Sales Invoice / Sales Invoice Item
2. Migrates existing POS Invoice data is NOT handled here — POS Invoice
   data remains as-is for historical records. New transactions will use
   Sales Invoice.
"""
import frappe
from ch_pos.setup import CUSTOM_FIELDS
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    # Create custom fields on Sales Invoice and Sales Invoice Item
    create_custom_fields(CUSTOM_FIELDS, update=True)

    # Update Link fields in ch_pos doctypes that pointed to POS Invoice
    _update_link_options("POS Reprint Queue", "pos_invoice", "Sales Invoice")
    _update_link_options("POS Kiosk Token", "converted_invoice", "Sales Invoice")
    _update_link_options("POS EDC Transaction", "matched_pos_invoice", "Sales Invoice")
    _update_link_options("POS Incentive Ledger", "invoice", "Sales Invoice")
    _update_link_options("POS Incentive Ledger", "return_invoice", "Sales Invoice")

    frappe.db.commit()


def _update_link_options(doctype, fieldname, new_options):
    """Update a Link field's options from POS Invoice to Sales Invoice."""
    cf_name = frappe.db.get_value(
        "Custom Field", {"dt": doctype, "fieldname": fieldname}
    )
    if cf_name:
        frappe.db.set_value("Custom Field", cf_name, "options", new_options)
    else:
        # Check if it's a standard field in the doctype JSON
        meta = frappe.get_meta(doctype)
        field = meta.get_field(fieldname)
        if field and field.fieldtype == "Link":
            frappe.db.sql(
                """UPDATE `tabDocField`
                   SET options = %s
                   WHERE parent = %s AND fieldname = %s""",
                (new_options, doctype, fieldname),
            )
