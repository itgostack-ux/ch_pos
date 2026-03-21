import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from textwrap import dedent


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
    "Sales Invoice": [
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
            "fieldname": "custom_exchange_amount",
            "fieldtype": "Currency",
            "label": "Exchange Credit",
            "insert_after": "custom_exchange_assessment",
            "read_only": 1,
            "depends_on": "eval:doc.custom_exchange_assessment",
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
            "fieldname": "custom_margin_taxable",
            "fieldtype": "Currency",
            "label": "Margin Taxable",
            "insert_after": "custom_is_margin_scheme",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_margin_scheme",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_margin_gst",
            "fieldtype": "Currency",
            "label": "GST on Margin",
            "insert_after": "custom_margin_taxable",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_margin_scheme",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_margin_exempted",
            "fieldtype": "Currency",
            "label": "Exempted Value",
            "insert_after": "custom_margin_gst",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_margin_scheme",
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
        {
            "fieldname": "custom_discount_reason",
            "fieldtype": "Link",
            "label": "Discount Reason",
            "options": "CH Discount Reason",
            "insert_after": "discount_amount",
            "depends_on": "eval:doc.additional_discount_percentage || doc.discount_amount",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_client_request_id",
            "fieldtype": "Data",
            "label": "Client Request ID",
            "insert_after": "custom_discount_reason",
            "read_only": 1,
            "no_copy": 1,
            "hidden": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_cancel_reason",
            "fieldtype": "Small Text",
            "label": "Cancellation Reason",
            "insert_after": "custom_client_request_id",
            "no_copy": 1,
            "module": "POS Core",
        },
        # ── Payment type fields ──────────────────────────────────
        {
            "fieldname": "custom_payment_type_section",
            "fieldtype": "Section Break",
            "label": "Payment Type Details",
            "insert_after": "custom_cancel_reason",
            "collapsible": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_is_credit_sale",
            "fieldtype": "Check",
            "label": "Credit Sale",
            "insert_after": "custom_payment_type_section",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_credit_days",
            "fieldtype": "Int",
            "label": "Credit Days",
            "insert_after": "custom_is_credit_sale",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_credit_sale",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_payment_col_break",
            "fieldtype": "Column Break",
            "insert_after": "custom_credit_days",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_is_free_sale",
            "fieldtype": "Check",
            "label": "Free Sale",
            "insert_after": "custom_payment_col_break",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_free_sale_reason",
            "fieldtype": "Small Text",
            "label": "Free Sale Reason",
            "insert_after": "custom_is_free_sale",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_free_sale",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_free_sale_approved_by",
            "fieldtype": "Data",
            "label": "Free Sale Approved By",
            "insert_after": "custom_free_sale_reason",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_free_sale",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_advance_adjusted",
            "fieldtype": "Currency",
            "label": "Advance Adjusted",
            "insert_after": "custom_free_sale_approved_by",
            "read_only": 1,
            "depends_on": "eval:doc.custom_advance_adjusted",
            "module": "POS Core",
        },
    ],
    "Sales Invoice Item": [
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
        {
            "fieldname": "custom_manager_approved",
            "fieldtype": "Check",
            "label": "Manager Approved",
            "insert_after": "custom_exempted_value",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_manager_user",
            "fieldtype": "Link",
            "label": "Approved By",
            "options": "User",
            "insert_after": "custom_manager_approved",
            "read_only": 1,
            "depends_on": "eval:doc.custom_manager_approved",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_override_reason",
            "fieldtype": "Small Text",
            "label": "Override Reason",
            "insert_after": "custom_manager_user",
            "depends_on": "eval:doc.custom_manager_approved",
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
    # Payment row custom fields — capture UPI TxID, card reference, and finance details per payment line
    "Sales Invoice Payment": [
        {
            "fieldname": "custom_upi_transaction_id",
            "fieldtype": "Data",
            "label": "UPI Transaction ID",
            "insert_after": "amount",
            "depends_on": "eval:doc.mode_of_payment && doc.mode_of_payment.toLowerCase().includes('upi')",
            "description": "UPI Transaction ID / UTR from the UPI app",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_card_reference",
            "fieldtype": "Data",
            "label": "Card Reference / RRN",
            "insert_after": "custom_upi_transaction_id",
            "description": "EDC retrieval reference number for card transactions",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_card_last_four",
            "fieldtype": "Data",
            "label": "Card Last 4 Digits",
            "insert_after": "custom_card_reference",
            "module": "POS Core",
        },
        # Finance / EMI payment fields
        {
            "fieldname": "custom_finance_provider",
            "fieldtype": "Data",
            "label": "Finance Provider",
            "insert_after": "custom_card_last_four",
            "description": "Finance company name (Bajaj, HDFC, TVS, etc.)",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_finance_tenure",
            "fieldtype": "Data",
            "label": "Finance Tenure",
            "insert_after": "custom_finance_provider",
            "description": "EMI tenure (e.g. 6M, 12M, 18M)",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_finance_approval_id",
            "fieldtype": "Data",
            "label": "Finance Approval / Loan ID",
            "insert_after": "custom_finance_tenure",
            "description": "Approval or loan ID from finance provider",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_finance_down_payment",
            "fieldtype": "Currency",
            "label": "Finance Down Payment",
            "insert_after": "custom_finance_approval_id",
            "description": "Down payment amount collected for EMI/Finance",
            "module": "POS Core",
        },
    ],
    # Company — gift card liability account for GL posting
    "Company": [
        {
            "fieldname": "custom_gift_card_account",
            "fieldtype": "Link",
            "label": "Gift Card Liability Account",
            "options": "Account",
            "insert_after": "default_income_account",
            "description": "Liability account for unredeemed gift card / store credit balances. Required for GL entry on voucher redemption.",
            "module": "POS Core",
        },
    ],
}


def after_install():
    _ensure_module_defs()
    create_custom_fields(CUSTOM_FIELDS, update=True)
    sync_margin_receipt_format()


def after_migrate():
    sync_margin_receipt_format()


def sync_margin_receipt_format():
    """Keep the shared Sales Invoice print format margin-aware for POS flows."""
    if not frappe.db.exists("Print Format", "Custom Sales Invoice"):
        return

    pf = frappe.get_doc("Print Format", "Custom Sales Invoice")
    html = (pf.html or "").replace("\r\n", "\n")

    old_hsn_block = dedent(
        """
        {% set hsn_map = {} %}
        {% for item in doc.items %}
        {% set hsn = item.gst_hsn_code or "NA" %}
        {% if hsn not in hsn_map %}
        {% set _ = hsn_map.update({
        hsn: {
        "taxable": 0,
        "cgst_rate": 0, "cgst_amt": 0,
        "sgst_rate": 0, "sgst_amt": 0,
        "igst_rate": 0, "igst_amt": 0
        }
        }) %}
        {% endif %}
        {% set _ = hsn_map[hsn].update({
        "taxable": hsn_map[hsn].taxable + item.net_amount
        }) %}
        {% endfor %}

        {% for tax in doc.taxes %}
        {% for hsn in hsn_map %}
        {% if "CGST" in tax.account_head %}
        {% set _ = hsn_map[hsn].update({
        "cgst_rate": tax.rate,
        "cgst_amt": hsn_map[hsn].cgst_amt + tax.tax_amount
        }) %}
        {% elif "SGST" in tax.account_head %}
        {% set _ = hsn_map[hsn].update({
        "sgst_rate": tax.rate,
        "sgst_amt": hsn_map[hsn].sgst_amt + tax.tax_amount
        }) %}
        {% elif "IGST" in tax.account_head %}
        {% set _ = hsn_map[hsn].update({
        "igst_rate": tax.rate,
        "igst_amt": hsn_map[hsn].igst_amt + tax.tax_amount
        }) %}
        {% endif %}
        {% endfor %}
        {% endfor %}
        """
    ).strip()

    new_hsn_block = dedent(
        """
        {% set hsn_map = {} %}
        {% set line_taxable_map = {} %}
        {% set line_gst_map = {} %}
        {% for item in doc.items %}
        {% set hsn = item.gst_hsn_code or "NA" %}
        {% set item_type = frappe.db.get_value("Item", item.item_code, "ch_item_type") or "" %}
        {% set is_margin_item = item.custom_is_margin_item or item_type in ["Refurbished", "Pre-Owned"] %}
        {% set item_taxable = item.custom_taxable_value if is_margin_item else item.net_amount %}
        {% if hsn not in hsn_map %}
        {% set _ = hsn_map.update({
        hsn: {
        "taxable": 0,
        "cgst_rate": 0, "cgst_amt": 0,
        "sgst_rate": 0, "sgst_amt": 0,
        "igst_rate": 0, "igst_amt": 0
        }
        }) %}
        {% endif %}
        {% set _ = line_taxable_map.update({item.name: item_taxable}) %}
        {% set _ = line_gst_map.update({item.name: 0}) %}
        {% set _ = hsn_map[hsn].update({
        "taxable": hsn_map[hsn].taxable + item_taxable
        }) %}
        {% for tax in doc.taxes %}
        {% if tax.charge_type == "On Net Total" %}
        {% set component = (item_taxable * tax.rate) / 100 %}
        {% set _ = line_gst_map.update({item.name: line_gst_map[item.name] + component}) %}
        {% if "CGST" in tax.account_head %}
        {% set _ = hsn_map[hsn].update({
        "cgst_rate": tax.rate,
        "cgst_amt": hsn_map[hsn].cgst_amt + component
        }) %}
        {% elif "SGST" in tax.account_head %}
        {% set _ = hsn_map[hsn].update({
        "sgst_rate": tax.rate,
        "sgst_amt": hsn_map[hsn].sgst_amt + component
        }) %}
        {% elif "IGST" in tax.account_head %}
        {% set _ = hsn_map[hsn].update({
        "igst_rate": tax.rate,
        "igst_amt": hsn_map[hsn].igst_amt + component
        }) %}
        {% endif %}
        {% endif %}
        {% endfor %}
        {% endfor %}
        """
    ).strip()

    replacements = {
        old_hsn_block: new_hsn_block,
        '{{ item.net_amount or ""}}': '{{ "%.2f"|format(line_taxable_map.get(item.name, 0)) }}',
        '{{ item.item_wise_tax_detail or ""}}': '{{ "%.2f"|format(line_gst_map.get(item.name, 0)) }}',
        '{% set total_tax = total_taxes_and_charges + row_tax %}': '{% set total_taxes_and_charges = total_taxes_and_charges + row_tax %}',
    }

    updated = html
    for old, new in replacements.items():
        if old in updated:
            updated = updated.replace(old, new)

    if updated != html:
        pf.html = updated.replace("\n", "\r\n")
        pf.save(ignore_permissions=True)

    blank_profiles = frappe.get_all(
        "POS Profile",
        filters={"print_format": ["in", ["", None]]},
        pluck="name",
    )
    for profile_name in blank_profiles:
        frappe.db.set_value("POS Profile", profile_name, "print_format", "Custom Sales Invoice", update_modified=False)

    frappe.db.commit()


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
