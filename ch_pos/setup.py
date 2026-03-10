import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
    "POS Profile": [
        {
            "fieldname": "custom_pos_mode",
            "fieldtype": "Select",
            "label": "POS Mode",
            "options": "System\nTablet\nKiosk",
            "insert_after": "company",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_store",
            "fieldtype": "Link",
            "label": "Store",
            "options": "CH Store",
            "insert_after": "custom_pos_mode",
            "module": "POS Core",
        },
    ],
    "POS Invoice": [
        {
            "fieldname": "custom_kiosk_token",
            "fieldtype": "Link",
            "label": "Kiosk Token",
            "options": "POS Kiosk Token",
            "insert_after": "pos_profile",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_guided_session",
            "fieldtype": "Link",
            "label": "Guided Session",
            "options": "POS Guided Session",
            "insert_after": "custom_kiosk_token",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_repair_intake",
            "fieldtype": "Link",
            "label": "Repair Intake",
            "options": "POS Repair Intake",
            "insert_after": "custom_guided_session",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_exchange_assessment",
            "fieldtype": "Link",
            "label": "Exchange Assessment",
            "options": "Buyback Assessment",
            "insert_after": "custom_repair_intake",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_is_margin_scheme",
            "fieldtype": "Check",
            "label": "Has Margin Scheme Items",
            "insert_after": "taxes_and_charges",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_sales_executive",
            "fieldtype": "Link",
            "label": "Sales Executive",
            "options": "POS Executive",
            "insert_after": "custom_exchange_assessment",
            "module": "POS Core",
        },
    ],
    "POS Invoice Item": [
        {
            "fieldname": "custom_warranty_plan",
            "fieldtype": "Link",
            "label": "Warranty Plan",
            "options": "CH Warranty Plan",
            "insert_after": "item_code",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_is_margin_item",
            "fieldtype": "Check",
            "label": "Margin Scheme",
            "insert_after": "amount",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_taxable_value",
            "fieldtype": "Currency",
            "label": "Taxable Value (Margin)",
            "insert_after": "custom_is_margin_item",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_margin_item",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_exempted_value",
            "fieldtype": "Currency",
            "label": "Exempted Value",
            "insert_after": "custom_taxable_value",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_margin_item",
            "module": "POS Core",
        },
    ],
    "Item": [
        {
            "fieldname": "custom_pos_section",
            "fieldtype": "Section Break",
            "label": "POS Settings",
            "insert_after": "ch_item_type",
            "collapsible": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_pos_usage",
            "fieldtype": "Select",
            "label": "POS Usage",
            "options": "\nSale\nRepair Only\nSale and Repair",
            "insert_after": "custom_pos_section",
            "description": "Controls where this item appears: Sale = POS selling, Repair Only = service jobs only, Sale and Repair = both",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_pos_col_break",
            "fieldtype": "Column Break",
            "insert_after": "custom_pos_usage",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_pos_allowed_companies",
            "fieldtype": "Table MultiSelect",
            "label": "Allowed POS Companies",
            "options": "POS Allowed Company",
            "insert_after": "custom_pos_col_break",
            "description": "Leave empty to allow all companies. Set specific companies to restrict POS visibility.",
            "module": "POS Core",
        },
    ],
}


def after_install():
    _ensure_module_defs()
    create_custom_fields(CUSTOM_FIELDS, update=True)


def _ensure_module_defs():
    """Ensure all ch_pos Module Def records exist before creating custom fields."""
    for module_name in ("POS Core", "POS Kiosk", "POS AI", "POS Repair"):
        if not frappe.db.exists("Module Def", module_name):
            m = frappe.new_doc("Module Def")
            m.module_name = module_name
            m.app_name = "ch_pos"
            m.insert(ignore_permissions=True)
    frappe.db.commit()


def before_uninstall():
    _delete_custom_fields(CUSTOM_FIELDS)


def _delete_custom_fields(fields_dict):
    for dt, fields in fields_dict.items():
        for field in fields:
            frappe.db.delete(
                "Custom Field",
                {"dt": dt, "fieldname": field["fieldname"]},
            )
    frappe.db.commit()
