import frappe


@frappe.whitelist()
def create_repair_intake(data):
    """Create POS Repair Intake and auto-generate Service Request on submit."""
    if isinstance(data, str):
        data = frappe.parse_json(data)

    doc = frappe.new_doc("POS Repair Intake")
    for field in (
        "store", "customer", "customer_phone",
        "device_category", "device_brand", "device_model",
        "serial_no", "imei_number",
        "issue_category", "issue_description",
        "mode_of_service", "password_pattern",
    ):
        if data.get(field):
            doc.set(field, data[field])

    doc.insert()
    doc.submit()

    return {
        "intake_name": doc.name,
        "service_request_name": doc.service_request,
    }
