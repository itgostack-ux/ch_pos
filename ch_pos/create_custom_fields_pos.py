import frappe

def run():
    """Create ch_pos custom fields that are referenced in pos_api.py but may not exist."""
    frappe.set_user("Administrator")
    
    fields = [
        # Idempotency key
        {"dt": "POS Invoice", "fieldname": "custom_client_request_id", "fieldtype": "Data", "label": "Client Request ID",
         "insert_after": "remarks", "hidden": 1, "read_only": 1, "no_copy": 1, "description": "UUID for duplicate-submit prevention"},
        # Exchange assessment link
        {"dt": "POS Invoice", "fieldname": "custom_exchange_assessment", "fieldtype": "Link", "label": "Exchange Assessment",
         "options": "Buyback Assessment", "insert_after": "custom_client_request_id", "read_only": 1, "no_copy": 1},
        {"dt": "POS Invoice", "fieldname": "custom_exchange_amount", "fieldtype": "Currency", "label": "Exchange Credit Amount",
         "insert_after": "custom_exchange_assessment", "read_only": 1, "no_copy": 1},
        # Sale type
        {"dt": "POS Invoice", "fieldname": "custom_ch_sale_type", "fieldtype": "Link", "label": "Sale Type",
         "options": "CH Sale Type", "insert_after": "custom_exchange_amount"},
        {"dt": "POS Invoice", "fieldname": "custom_ch_sale_sub_type", "fieldtype": "Data", "label": "Sale Sub-Type",
         "insert_after": "custom_ch_sale_type"},
        {"dt": "POS Invoice", "fieldname": "custom_ch_sale_reference", "fieldtype": "Data", "label": "Sale Reference",
         "insert_after": "custom_ch_sale_sub_type"},
        # Discount reason
        {"dt": "POS Invoice", "fieldname": "custom_discount_reason", "fieldtype": "Link", "label": "Discount Reason",
         "options": "CH Discount Reason", "insert_after": "custom_ch_sale_reference"},
        # Sales executive  
        {"dt": "POS Invoice", "fieldname": "custom_sales_executive", "fieldtype": "Link", "label": "Sales Executive",
         "options": "POS Executive", "insert_after": "custom_discount_reason"},
        # Cancel reason
        {"dt": "POS Invoice", "fieldname": "custom_cancel_reason", "fieldtype": "Small Text", "label": "Cancel Reason",
         "insert_after": "custom_sales_executive", "no_copy": 1},
        # Margin scheme
        {"dt": "POS Invoice", "fieldname": "custom_is_margin_scheme", "fieldtype": "Check", "label": "Is Margin Scheme",
         "insert_after": "custom_cancel_reason", "read_only": 1, "hidden": 1},
        {"dt": "POS Invoice", "fieldname": "custom_margin_taxable", "fieldtype": "Currency", "label": "Margin Taxable Amount",
         "insert_after": "custom_is_margin_scheme", "read_only": 1, "hidden": 1},
        {"dt": "POS Invoice", "fieldname": "custom_margin_gst", "fieldtype": "Currency", "label": "GST on Margin",
         "insert_after": "custom_margin_taxable", "read_only": 1, "hidden": 1},
        # POS Invoice Item custom fields
        {"dt": "POS Invoice Item", "fieldname": "custom_warranty_plan", "fieldtype": "Link", "label": "Warranty Plan",
         "options": "CH Warranty Plan", "insert_after": "item_name"},
        {"dt": "POS Invoice Item", "fieldname": "custom_is_margin_item", "fieldtype": "Check", "label": "Is Margin Item",
         "insert_after": "custom_warranty_plan", "read_only": 1, "hidden": 1},
        {"dt": "POS Invoice Item", "fieldname": "custom_taxable_value", "fieldtype": "Currency", "label": "Taxable Value (Margin)",
         "insert_after": "custom_is_margin_item", "read_only": 1, "hidden": 1},
        {"dt": "POS Invoice Item", "fieldname": "custom_exempted_value", "fieldtype": "Currency", "label": "Exempted Value (Margin)",
         "insert_after": "custom_taxable_value", "read_only": 1, "hidden": 1},
        {"dt": "POS Invoice Item", "fieldname": "custom_manager_approved", "fieldtype": "Check", "label": "Manager Approved",
         "insert_after": "custom_exempted_value"},
        {"dt": "POS Invoice Item", "fieldname": "custom_manager_user", "fieldtype": "Link", "label": "Manager User",
         "options": "User", "insert_after": "custom_manager_approved"},
        {"dt": "POS Invoice Item", "fieldname": "custom_override_reason", "fieldtype": "Small Text", "label": "Override Reason",
         "insert_after": "custom_manager_user"},
        # Payment child table custom fields
        {"dt": "Sales Invoice Payment", "fieldname": "custom_upi_transaction_id", "fieldtype": "Data", "label": "UPI Transaction ID",
         "insert_after": "amount"},
        {"dt": "Sales Invoice Payment", "fieldname": "custom_card_reference", "fieldtype": "Data", "label": "Card Reference (RRN)",
         "insert_after": "custom_upi_transaction_id"},
        {"dt": "Sales Invoice Payment", "fieldname": "custom_card_last_four", "fieldtype": "Data", "label": "Card Last 4 Digits",
         "insert_after": "custom_card_reference"},
    ]
    
    created = 0
    skipped = 0
    for f in fields:
        if frappe.db.exists("Custom Field", {"dt": f["dt"], "fieldname": f["fieldname"]}):
            skipped += 1
            continue
        cf = frappe.new_doc("Custom Field")
        cf.update(f)
        cf.flags.ignore_permissions = True
        try:
            cf.insert()
            created += 1
            print(f"  ✅ Created: {f['dt']}.{f['fieldname']}")
        except Exception as e:
            print(f"  ❌ Failed: {f['dt']}.{f['fieldname']} — {e}")
    
    frappe.db.commit()
    print(f"\nDone: {created} created, {skipped} already existed")
