import frappe
from ch_pos.setup import CUSTOM_FIELDS
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(CUSTOM_FIELDS, update=True)
