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


@frappe.whitelist()
def describe_device_serial(serial_no: str) -> dict:
    """Say whether an IMEI is a serial this company already tracks.

    The counter needs to know which of two cases it is in, and NEITHER is an
    error: a serial we sold (bind to it, warranty may apply) or the customer's
    own device (accept it as typed). Read-only, so an unknown serial gets an
    answer rather than an exception.
    """
    serial_no = (serial_no or "").strip()
    if not serial_no:
        return {"known": False}

    frappe.has_permission("Serial No", "read", throw=True)
    row = frappe.db.get_value(
        "Serial No", serial_no,
        ["name", "item_code", "item_name", "warehouse", "warranty_expiry_date"],
        as_dict=True,
    )
    if not row:
        return {"known": False, "serial_no": serial_no}

    warranty = ""
    expiry = row.get("warranty_expiry_date")
    if expiry:
        template = (
            _("In warranty to {0}")
            if frappe.utils.getdate(expiry) >= frappe.utils.getdate()
            else _("Warranty expired {0}")
        )
        warranty = template.format(frappe.format(expiry, {"fieldtype": "Date"}))

    return {
        "known": True,
        "serial_no": row.name,
        "item_code": row.item_code,
        "item_name": row.item_name,
        "warehouse": row.warehouse,
        "warranty": warranty,
    }


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

	# An IMEI the counter typed that is NOT one of our stock serials belongs to
	# the customer's own device. Record it explicitly as the actual IMEI so the
	# ticket, and the Sales Order it copies to, carry the device the customer
	# handed over rather than leaving it looking like an unmatched stock serial.
	if sr.serial_no and not frappe.db.exists("Serial No", sr.serial_no):
		if not sr.get("actual_imei"):
			sr.actual_imei = sr.serial_no
	sr.decision = "Draft"
	sr.walkin_source = data.get("walkin_source") or "POS Counter"
	sr.product_condition_desc = product_condition_desc
	sr.backup_info = backup_info
	if not sr.service_date:
		sr.service_date = frappe.utils.today()

	sr.insert()
	sr.submit()

	# Converting a queue token: close it against this ticket. The queue used to
	# run its own conversion dialog with its own, smaller field list, so a
	# token-raised ticket could not carry a technician, a promised time or a
	# serial. One intake form now feeds both, and the token is closed here
	# rather than by a second endpoint that built a second Service Request.
	source_token = (data.get("source_token") or "").strip()
	if source_token:
		try:
			from ch_pos.api.token_api import link_token_to_service_request

			link_token_to_service_request(source_token, sr.name)
		except Exception:
			# The ticket is the receipt for a device already on the counter.
			# A token left open is a queue tidy-up, not a reason to fail intake.
			frappe.log_error(
				frappe.get_traceback(),
				f"POS intake: could not close token {source_token} for {sr.name}",
			)

	# A counter check-in IS the acceptance. The customer has handed the device
	# over and signed the intake, so parking the ticket in a Draft queue for
	# someone to press "Accept" is a wait with no decision behind it — the
	# device is already in the building. Open the job and let diagnosis start.
	# The accept/reject gate belongs to remotely raised requests, where the
	# device has not arrived yet.
	opened = False
	try:
		from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import open_walkin_job

		open_walkin_job(sr.name)
		opened = True
	except Exception:
		# An intake must never fail because the job could not be opened — the
		# device is already at the counter and the SR is the receipt for it.
		# It falls back to Draft for the hub to accept: the old behaviour.
		frappe.log_error(
			frappe.get_traceback(), f"POS intake: could not open job for {sr.name}"
		)

	# Record the completion time promised at the counter. Done after the SR
	# exists so the revision row can reference it, and never fatal: the device
	# is already taken in, and a missing promise is a gap to fill, not a reason
	# to void the receipt.
	promised = (data.get("promised_completion_datetime") or "").strip()
	if promised:
		try:
			from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import (
				set_promised_completion,
			)

			set_promised_completion(sr.name, promised,
				reason=_("Promised to the customer at intake"))
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"POS intake: could not record promised completion for {sr.name}",
			)

	# Hand the device to a named technician at intake. Analysis is real work and
	# somebody has to own it — without this the ticket sits in the Analysis
	# queue with no owner and no clock until the Assign stage, hours later.
	assigned_to = (data.get("diagnosis_technician") or "").strip()
	diagnosis_assignment = None
	if assigned_to:
		try:
			from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import (
				assign_diagnosis_technician,
			)

			res = assign_diagnosis_technician(sr.name, assigned_to)
			diagnosis_assignment = res.get("job_assignment")
		except Exception:
			# Same rule as opening the job: the device is already at the counter,
			# so an assignment problem must not void the intake receipt.
			frappe.log_error(
				frappe.get_traceback(),
				f"POS intake: could not assign {assigned_to} to {sr.name}",
			)

	sr.reload()
	return {
		"name": sr.name,
		"docstatus": sr.docstatus,
		"status": sr.decision,
		"job_opened": opened,
		"diagnosis_assignment": diagnosis_assignment,
		"diagnosis_technician": assigned_to or None,
		"promised_completion_datetime": promised or None,
	}


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
