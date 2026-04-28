"""Create CH Payment Machine for GoGizmo Retail / QA-VEL testing."""
import frappe

def run():
    machine_id = "gogizmo-qa-vel-001"

    if frappe.db.exists("CH Payment Machine", machine_id):
        doc = frappe.get_doc("CH Payment Machine", machine_id)
        print(f"Machine already exists: {doc.name}  provider={doc.provider}  store={doc.store}  company={doc.company}  enabled={doc.enabled}")
        return

    doc = frappe.get_doc({
        "doctype": "CH Payment Machine",
        "machine_id": machine_id,
        "machine_name": "GoGizmo QA-VEL EDC (Pine Labs UAT)",
        "provider": "Pine Labs",
        "enabled": 1,
        "company": "GoGizmo Retail Pvt Ltd",
        "store": "QA-VEL",
        "pos_profile": "QA Velachery POS",
        "environment": "UAT",
        "terminal_id": "TEST-TERMINAL-VEL-01",
        # Supported for all card + UPI flows
        "supported_payment_modes": "CARD,UPI",
        "remarks": "Test machine for QA-VEL GoGizmo POS — Pine Labs UAT environment",
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Created: {doc.name}  provider={doc.provider}  store={doc.store}  company={doc.company}  environment={doc.environment}")
