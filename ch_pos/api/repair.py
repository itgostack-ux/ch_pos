import frappe
from buyback.utils import validate_indian_phone


@frappe.whitelist()
def create_repair_intake(data, pos_profile=None) -> dict:
    """Create POS Repair Intake and auto-generate Service Request on submit."""
    if isinstance(data, str):
        data = frappe.parse_json(data)

    # Default store (GoGizmo source warehouse) from POS Profile when caller did not pass one.
    # Ensures Service Request gets correctly tagged to the originating store even
    # if the kiosk/POS UI omits it from the payload.
    if not data.get("store") and pos_profile:
        ws = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
        if ws:
            data["store"] = ws

    # Validate and normalise phone number if provided
    if data.get("customer_phone"):
        data["customer_phone"] = validate_indian_phone(
            data["customer_phone"], "Customer Phone"
        )

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
