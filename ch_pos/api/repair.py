import frappe
from frappe import _
from buyback.utils import validate_indian_phone

from ch_pos.api.scope_guard import assert_pos_profile_scope


def build_condition_and_backup(device_condition, accessories, data_disclaimer):
	"""Map the POS condition inputs onto the Service Request fields that are
	mandatory at submit time (validate_mandatory_fields), so a POS-raised
	request can go straight to Submitted and appear in Service Hub / GoFix
	Ops Hub without a manual round-trip through the draft form.
	"""
	condition_bits = []
	if device_condition:
		condition_bits.append(f"Device condition: {device_condition}")
	if accessories:
		condition_bits.append(f"Accessories received: {accessories}")
	product_condition_desc = (
		". ".join(condition_bits) or "Not assessed at POS counter — verify at service hub."
	)
	backup_info = (
		"Customer acknowledged at POS that data may be lost during repair; no backup taken by store."
		if frappe.utils.cint(data_disclaimer)
		else "No backup recorded at POS intake."
	)
	return product_condition_desc, backup_info


def resolve_legacy_device_item(device_brand=None, device_model=None):
	"""Resolve old POS brand/model payloads without retaining a wrapper DocType."""
	search = f"{device_brand or ''} {device_model or ''}".strip()
	if not search:
		return None
	return frappe.db.get_value(
		"Item",
		{
			"item_name": ("like", f"%{search}%"),
			"disabled": 0,
			"ch_lifecycle_status": ("in", ("Active", "Obsolete")),
		},
		"name",
	)


@frappe.whitelist(methods=["POST"])
def create_service_intake_from_pos(data, pos_profile=None) -> dict:
	"""Create and SUBMIT a GoFix Service Request from the POS Service Intake form.

	POS-raised requests must land as submitted documents so they are
	immediately actionable in Service Hub and GoFix Ops Hub (which filters
	docstatus = 1). The device-condition select and the data-loss disclaimer
	checkbox are mapped onto the submit-mandatory text fields.
	"""
	if isinstance(data, str):
		data = frappe.parse_json(data)

	frappe.has_permission("Service Request", "create", throw=True)
	anchors = assert_pos_profile_scope(pos_profile)
	if data.get("company") and data.get("company") != anchors.get("company"):
		frappe.throw(_("Company does not match the active POS Profile."), frappe.PermissionError)
	if data.get("source_warehouse") and data.get("source_warehouse") != anchors.get("warehouse"):
		frappe.throw(_("Warehouse does not match the active POS Profile."), frappe.PermissionError)
	data["company"] = anchors.get("company")
	data["source_warehouse"] = anchors.get("warehouse")

	if data.get("contact_number"):
		data["contact_number"] = validate_indian_phone(data["contact_number"], "Contact Phone")

	product_condition_desc, backup_info = build_condition_and_backup(
		data.get("device_condition"),
		data.get("accessories_received"),
		data.get("data_backup_disclaimer"),
	)

	sr = frappe.new_doc("Service Request")
	for field in (
		"customer", "contact_number", "device_item", "serial_no",
		"issue_category", "issue_description",
		"warranty_status", "device_condition", "accessories_received",
		"data_backup_disclaimer", "mode_of_service",
		"company", "source_warehouse", "service_date", "priority",
	):
		if data.get(field):
			sr.set(field, data[field])
	for line in data.get("issue_lines") or []:
		sr.append("issue_lines", line)
	sr.decision = "Draft"
	sr.walkin_source = data.get("walkin_source") or "POS Counter"
	sr.product_condition_desc = product_condition_desc
	sr.backup_info = backup_info
	if not sr.service_date:
		sr.service_date = frappe.utils.today()

	sr.insert()
	sr.submit()

	return {"name": sr.name, "docstatus": sr.docstatus, "status": sr.decision}


@frappe.whitelist(methods=["POST"])
def create_repair_intake(data, pos_profile=None) -> dict:
	"""Compatibility endpoint: create the canonical Service Request directly."""
	if isinstance(data, str):
		data = frappe.parse_json(data)
	payload = dict(data or {})
	payload["contact_number"] = payload.get("contact_number") or payload.get("customer_phone")
	payload["serial_no"] = payload.get("serial_no") or payload.get("imei_number")
	payload["source_warehouse"] = payload.get("source_warehouse") or payload.get("store")
	if not payload.get("device_item"):
		payload["device_item"] = resolve_legacy_device_item(
			payload.get("device_brand"), payload.get("device_model")
		)
	result = create_service_intake_from_pos(payload, pos_profile=pos_profile)
	return {
		"intake_name": None,
		"service_request_name": result["name"],
		"name": result["name"],
		"docstatus": result["docstatus"],
		"status": result["status"],
	}
