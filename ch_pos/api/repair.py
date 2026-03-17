import frappe


@frappe.whitelist()
def create_repair_intake(data, pos_profile=None):
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
        "device_condition", "accessories_received",
        "data_backup_disclaimer",
    ):
        if data.get(field):
            doc.set(field, data[field])

    doc.insert()
    doc.submit()

    # Create walk-in token for this repair intake
    if pos_profile:
        try:
            from ch_pos.api.token_api import log_counter_walkin
            log_counter_walkin(
                pos_profile=pos_profile,
                visit_purpose="Repair",
                customer_name=data.get("customer", "Walk-in"),
                customer_phone=data.get("customer_phone", ""),
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Repair walk-in token failed")

    return {
        "intake_name": doc.name,
        "service_request_name": doc.service_request,
    }
