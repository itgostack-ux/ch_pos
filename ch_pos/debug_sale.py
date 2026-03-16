import frappe
import traceback

def run():
    frappe.set_user("Administrator")
    try:
        from ch_pos.api.pos_api import create_pos_invoice
        r = create_pos_invoice(
            pos_profile="QA Velachery POS",
            customer="Pallavi Chandolia",
            items=[{
                "item_code": "CSV000001-BLA-Lightning",
                "item_name": "Apple SVT Black Lightning",
                "qty": 1,
                "rate": 12000.0,
                "uom": "Nos",
            }],
            payments=[{"mode_of_payment": "Cash", "amount": 12000.0}],
            sale_type="Direct Sale",
        )
        print("SUCCESS:", r)
        frappe.db.rollback()
    except Exception as e:
        print("FULL ERROR:")
        traceback.print_exc()
        print("\nFrappe traceback:")
        try:
            print(frappe.get_traceback())
        except Exception:
            pass
        frappe.db.rollback()
