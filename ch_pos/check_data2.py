import frappe

def run():
    frappe.set_user("Administrator")
    
    # Check purchase_invoice link target
    try:
        cd_meta = frappe.get_meta("CH Customer Device")
        pf = cd_meta.get_field("purchase_invoice")
        print(f"purchase_invoice: fieldtype={pf.fieldtype}, options={pf.options}")
    except Exception as e:
        print("Meta error:", e)
    
    # Check CH Discount Reason doctype
    try:
        dr_meta = frappe.get_meta("CH Discount Reason")
        print("CH Discount Reason fields:", [f.fieldname for f in dr_meta.fields[:10]])
    except Exception as e:
        print("DR meta error:", e)
    
    # Debug S6: partial payment - what is grand_total vs paid for the S6 scenario?
    prof = frappe.get_cached_doc("POS Profile", "QA Velachery POS")
    print("POS Profile taxes:", prof.taxes)
    print("POS Profile payment modes:", [p.mode_of_payment for p in (prof.payments or [])])
    
    # Check create_pos_return warehouse error
    from ch_pos.api.pos_api import create_pos_return
    import inspect
    print("create_pos_return source snippet:")
    src = inspect.getsource(create_pos_return)
    # Find warehouse usage
    for i, line in enumerate(src.split('\n')):
        if 'warehouse' in line.lower():
            print(f"  L{i}: {line}")
