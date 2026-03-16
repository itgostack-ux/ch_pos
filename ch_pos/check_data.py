import frappe

def run():
    frappe.set_user("Administrator")
    
    # CH Discount Reason
    dr = frappe.db.sql("SELECT name, allow_manual_entry, enabled FROM `tabCH Discount Reason` LIMIT 10", as_dict=True)
    print("CH Discount Reasons:", dr)
    
    # CH Customer Device fields
    try:
        cd_fields = frappe.db.sql("DESCRIBE `tabCH Customer Device`", as_dict=True)
        print("CH Customer Device fields:", [f.Field for f in cd_fields if 'purchase' in (f.Field or '').lower() or 'invoice' in (f.Field or '').lower()])
    except Exception as e:
        print("CH Customer Device error:", e)
    
    # create_pos_return signature
    from ch_pos.api.pos_api import create_pos_return
    import inspect
    print("create_pos_return signature:", inspect.signature(create_pos_return))
