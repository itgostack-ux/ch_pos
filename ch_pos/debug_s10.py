import frappe, traceback
def run():
    frappe.set_user("Administrator")
    from ch_pos.api.pos_api import create_pos_return
    orig = 'ACC-PSINV-2026-00001'
    items_q = frappe.get_all('POS Invoice Item', filters={'parent': orig}, fields=['item_code','item_name','qty','rate','serial_no'], limit=1)
    print('Items:', items_q)
    try:
        result = create_pos_return(
            original_invoice=orig,
            return_items=[{'item_code': items_q[0].item_code, 'item_name': items_q[0].item_name, 'qty': 1, 'rate': float(items_q[0].rate or 0), 'return_reason': 'Test return'}]
        )
        print('Result:', result)
    except Exception as e:
        traceback.print_exc()
    finally:
        frappe.db.rollback()
