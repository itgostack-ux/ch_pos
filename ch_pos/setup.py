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
            "fieldname": "custom_ch_pos_session",
            "fieldtype": "Link",
            "label": "POS Session",
            "options": "CH POS Session",
            "insert_after": "custom_kiosk_token",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_guided_session",
            "fieldtype": "Link",
            "label": "Guided Session",
            "options": "POS Guided Session",
            "insert_after": "custom_ch_pos_session",
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
            "fieldname": "custom_ch_sale_type",
            "fieldtype": "Link",
            "label": "Sale Type",
            "options": "CH Sale Type",
            "insert_after": "custom_sales_executive",
            "read_only": 1,
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
            "fieldname": "custom_discount_proof_image",
            "fieldtype": "Attach Image",
            "label": "Discount Proof Image",
            "insert_after": "custom_discount_reason",
            "depends_on": "eval:doc.custom_discount_reason",
            "description": "Upload proof screenshot for Price Match discounts",
            "module": "POS Core",
        },
        # ── Voucher & Bank Offer — stored as distinct fields per BRD ─────────
        {
            "fieldname": "custom_voucher_code",
            "fieldtype": "Data",
            "label": "Voucher Code",
            "insert_after": "custom_discount_proof_image",
            "read_only": 1,
            "description": "Gift voucher / loyalty voucher code redeemed on this invoice.",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_voucher_amount",
            "fieldtype": "Currency",
            "label": "Voucher Amount",
            "insert_after": "custom_voucher_code",
            "read_only": 1,
            "depends_on": "eval:doc.custom_voucher_code",
            "description": "Voucher discount applied. Stored separately from regular discount_amount for P&L tracking.",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_bank_offer_name",
            "fieldtype": "Data",
            "label": "Bank Offer",
            "insert_after": "custom_voucher_amount",
            "read_only": 1,
            "description": "Bank offer / card discount offer name applied at billing.",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_bank_offer_discount",
            "fieldtype": "Currency",
            "label": "Bank Offer Discount",
            "insert_after": "custom_bank_offer_name",
            "read_only": 1,
            "depends_on": "eval:doc.custom_bank_offer_name",
            "description": "Bank offer discount amount. Stored separately for campaign reconciliation.",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_coupon_code",
            "fieldtype": "Link",
            "label": "Coupon Code",
            "options": "Coupon Code",
            "insert_after": "custom_bank_offer_discount",
            "read_only": 1,
            "module": "POS Core",
        },
        {
            "fieldname": "custom_coupon_discount_amount",
            "fieldtype": "Currency",
            "label": "Coupon Discount Amount",
            "insert_after": "custom_coupon_code",
            "read_only": 1,
            "depends_on": "eval:doc.custom_coupon_code",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_client_request_id",
            "fieldtype": "Data",
            "label": "Client Request ID",
            "insert_after": "custom_discount_proof_image",
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
            "fieldname": "custom_credit_reference",
            "fieldtype": "Data",
            "label": "Credit Reference / PO No",
            "insert_after": "custom_credit_days",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_credit_sale",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_credit_notes",
            "fieldtype": "Small Text",
            "label": "Credit Notes",
            "insert_after": "custom_credit_reference",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_credit_sale",
            "module": "POS Core",
        },
        # ── Credit sale extended fields (market standard) ────────────────────
        {
            "fieldname": "custom_credit_terms",
            "fieldtype": "Select",
            "label": "Credit Terms",
            "insert_after": "custom_credit_notes",
            "options": "\nNet 15\nNet 30\nNet 45\nNet 60\nNet 90\nCustom",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_credit_sale",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_credit_interest_rate",
            "fieldtype": "Float",
            "label": "Interest Rate (% p.a.)",
            "insert_after": "custom_credit_terms",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_credit_sale",
            "description": "Annual interest rate charged on overdue amounts. Default: 0 (no interest).",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_credit_grace_period",
            "fieldtype": "Int",
            "label": "Grace Period (days)",
            "insert_after": "custom_credit_interest_rate",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_credit_sale",
            "description": "Days after due date before interest starts accruing.",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_credit_partial_payment",
            "fieldtype": "Currency",
            "label": "Partial Payment Collected",
            "insert_after": "custom_credit_grace_period",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_credit_sale",
            "description": "Amount collected upfront at POS. Remaining balance is on credit.",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_credit_approved_by",
            "fieldtype": "Data",
            "label": "Credit Approved By",
            "insert_after": "custom_credit_partial_payment",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_credit_sale",
            "description": "Manager who authorized the credit sale (PIN verified).",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_credit_reminder_date",
            "fieldtype": "Date",
            "label": "Payment Reminder Date",
            "insert_after": "custom_credit_approved_by",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_credit_sale",
            "description": "Auto-computed: 5 days before due date. Used for payment follow-up alerts.",
            "module": "POS Core",
        },
        # ─────────────────────────────────────────────────────────────────────
        {
            "fieldname": "custom_payment_col_break",
            "fieldtype": "Column Break",
            "insert_after": "custom_credit_reminder_date",
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
            "fieldname": "custom_free_sale_approved_at",
            "fieldtype": "Datetime",
            "label": "Free Sale Approved At",
            "insert_after": "custom_free_sale_approved_by",
            "read_only": 1,
            "no_copy": 1,
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
        {
            "fieldname": "custom_promo_write_off_je",
            "fieldtype": "Link",
            "label": "Promotional Write-off JE",
            "options": "Journal Entry",
            "insert_after": "custom_free_sale_approved_at",
            "read_only": 1,
            "no_copy": 1,
            "depends_on": "eval:doc.custom_is_free_sale",
            "module": "POS Core",
        },
        # Exception & Warranty links (applied during POS billing)
        {
            "fieldname": "custom_exception_request",
            "fieldtype": "Link",
            "label": "Exception Request",
            "options": "CH Exception Request",
            "insert_after": "custom_advance_adjusted",
            "read_only": 1,
            "depends_on": "eval:doc.custom_exception_request",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_warranty_claim",
            "fieldtype": "Link",
            "label": "Warranty Claim",
            "options": "CH Warranty Claim",
            "insert_after": "custom_exception_request",
            "read_only": 1,
            "depends_on": "eval:doc.custom_warranty_claim",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_buyback_order",
            "fieldtype": "Link",
            "label": "Buyback Order",
            "options": "Buyback Order",
            "insert_after": "custom_warranty_claim",
            "read_only": 1,
            "depends_on": "eval:doc.custom_buyback_order",
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
        {
            "fieldname": "custom_item_category",
            "fieldtype": "Select",
            "label": "Item Category",
            "options": "\nProduct\nWarranty\nVAS\nAccessory\nService",
            "insert_after": "custom_override_reason",
            "read_only": 1,
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
            "fieldtype": "Link",
            "options": "CH Finance Partner",
            "label": "Finance Partner",
            "insert_after": "custom_card_last_four",
            "description": "Third-party finance partner (Bajaj Finserv, HDFC, TVS Credit, etc.)",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_finance_tenure",
            "fieldtype": "Int",
            "label": "EMI Tenure (Months)",
            "insert_after": "custom_finance_provider",
            "description": "EMI tenure in months",
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
        {
            "fieldname": "custom_gateway_provider",
            "fieldtype": "Data",
            "label": "Gateway Provider",
            "insert_after": "custom_finance_down_payment",
            "description": "Payment gateway provider used for this payment line",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_payment_machine",
            "fieldtype": "Link",
            "options": "CH Payment Machine",
            "label": "Payment Machine",
            "insert_after": "custom_gateway_provider",
            "description": "Selected machine / terminal for Pay Now",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_gateway_order_id",
            "fieldtype": "Data",
            "label": "Gateway Order ID",
            "insert_after": "custom_payment_machine",
            "module": "POS Core",
        },
        {
            "fieldname": "custom_gateway_status",
            "fieldtype": "Data",
            "label": "Gateway Status",
            "insert_after": "custom_gateway_order_id",
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
    create_custom_fields(CUSTOM_FIELDS, update=False)
    sync_margin_receipt_format()


def before_migrate():
    """Skip erpnext patches that are incompatible with ch_pos's POS Closing Entry.

    ch_pos owns POS Closing Entry (module POS Core) and does not carry the
    standard erpnext fields (pos_invoices, sales_invoices, etc.).  Certain
    erpnext patches try to rename those fields with validate=False, which
    crashes when the fields don't exist.  Pre-inserting the Patch Log entry
    causes the migrate runner to skip them harmlessly.
    """
    _skip_incompatible_patches = [
        "erpnext.patches.v15_0.rename_pos_closing_entry_fields #2025-06-13",
    ]
    for patch_name in _skip_incompatible_patches:
        if not frappe.db.exists("Patch Log", {"patch": patch_name}):
            frappe.get_doc({"doctype": "Patch Log", "patch": patch_name}).insert(
                ignore_permissions=True
            )
            frappe.db.commit()


def after_migrate():
    create_custom_fields(CUSTOM_FIELDS, update=False)
    sync_margin_receipt_format()
    _ensure_sale_types()


# ── Sale Type seed data ─────────────────────────────────────────────
SALE_TYPE_SEED = [
    {
        "name": "Direct Sale", "code": "DS", "is_default": 1,
        "requires_customer": 1, "requires_payment": 1,
    },
    {
        "name": "Credit Sale", "code": "CS", "is_default": 0,
        "requires_customer": 1, "requires_payment": 0,
    },
    {
        "name": "Finance Sale", "code": "FS", "is_default": 0,
        "requires_customer": 1, "requires_payment": 1,
        "sub_types": [
            {"sale_sub_type": "Bajaj Finance", "requires_reference": 1},
            {"sale_sub_type": "Bajaj Finserv", "requires_reference": 1},
            {"sale_sub_type": "HDFC", "requires_reference": 1},
            {"sale_sub_type": "Tata Capital", "requires_reference": 1},
        ],
    },
    {
        "name": "Supplier Sale", "code": "SS", "is_default": 0,
        "requires_customer": 1, "requires_payment": 1,
    },
    {
        "name": "Free Sale", "code": "FREE", "is_default": 0,
        "requires_customer": 0, "requires_payment": 0,
        "sub_types": [
            {"sale_sub_type": "Scratch Card", "requires_reference": 1},
            {"sale_sub_type": "Spin Wheel", "requires_reference": 1},
            {"sale_sub_type": "Loyalty Redemption", "requires_reference": 0},
        ],
    },
]


def _ensure_sale_types():
    """Create default CH Sale Type records if they don't exist."""
    for st in SALE_TYPE_SEED:
        # Skip if already exists by name or by code
        if frappe.db.exists("CH Sale Type", st["name"]):
            continue
        if frappe.db.exists("CH Sale Type", {"code": st["code"]}):
            continue
        doc = frappe.new_doc("CH Sale Type")
        doc.sale_type_name = st["name"]
        doc.code = st["code"]
        doc.is_default = st.get("is_default", 0)
        doc.enabled = 1
        doc.requires_customer = st.get("requires_customer", 1)
        doc.requires_payment = st.get("requires_payment", 1)
        for sub in st.get("sub_types", []):
            doc.append("sub_types", {
                "sale_sub_type": sub["sale_sub_type"],
                "requires_reference": sub.get("requires_reference", 0),
            })
        doc.insert(ignore_permissions=True)
    frappe.db.commit()


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
