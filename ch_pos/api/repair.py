import frappe
from frappe.utils import get_datetime, now_datetime
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


def _require_walkin_token() -> bool:
	"""Whether a Service Request must be raised against a walk-in token."""
	try:
		from gofix.config import get_setting
	except ImportError:
		return False          # gofix not installed: nothing to enforce
	try:
		# get_setting, not get_int_setting. The latter is
		# max(cint(value), minimum=1), so an unticked box collapses back to 1
		# and the setting could never be turned off. get_setting deliberately
		# exempts Check fields from its own zero-collapse.
		return bool(get_setting("require_walkin_token", 1))
	except Exception:
		return True           # setting unreadable: keep the counter honest


@frappe.whitelist()
def walkin_token_required() -> dict:
	"""Tell the Repair screen whether to freeze until a token exists."""
	return {"required": _require_walkin_token()}


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

	# Every device taken in must also be a counted walk-in. Raising a Service
	# Request straight from the Repair screen left no token, so the customer was
	# invisible to the queue, to footfall and to every token-based report -- on
	# this site 23 of 28 counter-raised requests carried no token at all.
	#
	# Checked here, before the Service Request is built, because the token is
	# only closed near the end of this function -- throwing there would leave a
	# submitted, orphaned request behind. The Repair screen freezes itself too,
	# but this is the control: a direct API call has to fail the same way.
	# GoFix Settings.require_walkin_token turns it off for a store that must be
	# able to take a device in while the queue is unavailable.
	if not (data.get("source_token") or "").strip() and _require_walkin_token():
		frappe.throw(
			_("Log the walk-in first. A Service Request must be raised against a "
			  "walk-in token so the customer is counted in the queue."),
			title=_("Walk-in Token Required"),
		)
	if data.get("company") and data.get("company") != anchors.get("company"):
		frappe.throw(_("Company does not match the active POS Profile."), frappe.PermissionError)
	if data.get("source_warehouse") and data.get("source_warehouse") != anchors.get("warehouse"):
		frappe.throw(_("Warehouse does not match the active POS Profile."), frappe.PermissionError)
	data["company"] = anchors.get("company")
	data["source_warehouse"] = anchors.get("warehouse")

	if data.get("contact_number"):
		data["contact_number"] = validate_indian_phone(data["contact_number"], "Contact Phone")

	# Accessories arrive as master names from the counter's multi-select. The
	# condition blurb still wants prose, so it is rendered from the same list
	# rather than kept as a second, separately-typed source of truth.
	accessory_rows = data.get("accessories_list") or []
	if isinstance(accessory_rows, str):
		accessory_rows = [a.strip() for a in accessory_rows.split(",") if a.strip()]
	accessories_text = data.get("accessories_received") or ", ".join(accessory_rows)

	product_condition_desc, backup_info = build_condition_and_backup(
		data.get("device_condition"),
		accessories_text,
		data.get("data_backup_disclaimer"),
	)

	# The promise is checked before anything is written: a time already in the
	# past is a typo, and catching it after the ticket exists would leave a
	# receipt with no deadline behind it (that failure used to be logged and
	# swallowed, so the counter never knew the countdown was missing).
	promised = (data.get("promised_completion_datetime") or "").strip()
	if promised and get_datetime(promised) < now_datetime():
		frappe.throw(
			_("The promised completion time {0} is already in the past. Give the customer a time that is still ahead.").format(
				frappe.format(get_datetime(promised), {"fieldtype": "Datetime"})),
			title=_("Validation Error"),
		)

	sr = frappe.new_doc("Service Request")
	for field in (
		"customer", "contact_number", "device_item", "serial_no",
		"issue_category", "issue_description",
		"warranty_status", "device_condition", "accessories_received",
		"data_backup_disclaimer", "mode_of_service",
		"company", "source_warehouse", "service_date", "priority",
		# Mandatory on the Service Request. The device Item is not: a customer
		# can bring something we have never sold, and the taxonomy is what makes
		# such a ticket reportable at all.
		"device_category", "device_brand", "device_model",
		# Needed to test the repair; the technician cannot verify a fix on a
		# locked handset.
		"device_unlock_type", "device_unlock_code",
		# Presented at the counter; the Service Request tests its validity
		# against the intake date so a later invoice still honours it.
		"coupon_code",
		# Every one of these was silently DROPPED before -- the columns exist on
		# the Service Request, the counter filled them, and the row stayed NULL.
		# advance_amount is customer money: losing it silently is not a display
		# bug, it is an unrecorded liability.
		"email", "alternate_contact", "password", "pattern",
		"customer_remarks", "internal_remarks", "referral_code",
		"advance_amount", "estimated_cost",
	):
		if data.get(field):
			sr.set(field, data[field])
	# Warranty status can arrive from any client vocabulary; coerce it to the
	# repair/sales master so the Service Request Select never rejects a value a
	# buyback/customer-device screen might send.
	if sr.get("warranty_status"):
		from ch_erp15.warranty import normalize as _normalize_warranty
		sr.warranty_status = _normalize_warranty(sr.warranty_status)
	for line in data.get("issue_lines") or []:
		sr.append("issue_lines", line)
	for accessory in accessory_rows:
		if frappe.db.exists("GoFix Accessory", accessory):
			sr.append("accessories_list", {"accessory": accessory})
	if accessories_text:
		sr.accessories_received = accessories_text

	# An IMEI the counter typed that is NOT one of our stock serials belongs to
	# the customer's own device. Record it explicitly as the actual IMEI so the
	# ticket, and the Sales Order it copies to, carry the device the customer
	# handed over rather than leaving it looking like an unmatched stock serial.
	if sr.serial_no and not frappe.db.exists("Serial No", sr.serial_no):
		if not sr.get("actual_imei"):
			sr.actual_imei = sr.serial_no
	sr.decision = "Draft"
	# How the customer actually reached us, not an assumption. Every ticket
	# used to claim POS Counter, so a WhatsApp enquiry and somebody walking
	# through the door were indistinguishable once the ticket existed -- and
	# the lead-source reporting was answering a question nobody had asked.
	sr.walkin_source = _resolve_walkin_source(data)
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
	# Which front-desk visit became this ticket. The token already points
	# forward; without the reverse link the ticket cannot say where it came
	# from, which is the first thing anyone asks of it.
	if source_token and sr.meta.get_field("front_desk_visit"):
		sr.db_set("front_desk_visit", source_token, update_modified=False)
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

	# The customer may have reached us several times before they reached the
	# counter -- a web form on Tuesday, a call on Wednesday, a walk-in today.
	# Consolidation attaches the newest of those to the ticket and closes the
	# rest against it, searching every number held for that customer rather
	# than only the one typed here, so nobody is chased twice.
	try:
		from gofix.gofix_services.identity import consolidate_into_request

		consolidate_into_request(
			sr.name,
			phone=data.get("contact_number"),
			inbox=(data.get("source_inbox") or "").strip() or None,
			token=source_token or None,
		)
	except Exception:
		# The device is already on the counter; a tidy-up must never fail intake.
		frappe.log_error(
			frappe.get_traceback(),
			f"POS intake: could not consolidate earlier contacts for {sr.name}",
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
	promise_error = None
	if promised:
		try:
			from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import (
				set_promised_completion,
			)

			set_promised_completion(sr.name, promised,
				reason=_("Promised to the customer at intake"))
		except Exception as exc:
			# Never fatal, but never silent either: the counter is told.
			promise_error = str(exc)
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
		"promised_completion_datetime": promised if promised and not promise_error else None,
		"promise_error": promise_error,
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


@frappe.whitelist()
def get_device_coverage(serial_no, company=None) -> dict:
	"""What cover a device carries, for the intake counter.

	The counter had no way to see this before taking the device in: warranty
	status was a free choice on the form, so an advisor either guessed or asked
	the customer to prove it. Three separate things can cover a repair and they
	are settled by different people, so they are reported separately rather than
	as one yes/no --

	  vas       a sold VAS plan on the serial, claimed against the policy
	  repair    our own workmanship warranty from a previous repair, our cost
	  part      a fitted spare still inside its window, recoverable from supplier

	Read-only and advisory. The Service Request runs the same lookup itself when
	it saves, so nothing here is trusted as the authority.
	"""
	serial_no = (serial_no or "").strip()
	if not serial_no:
		return {"has_cover": False, "cover": [], "warranty_status": "No Warranty"}

	frappe.has_permission("Service Request", "create", throw=True)

	try:
		from ch_item_master.ch_item_master.warranty_api import check_warranty
	except ImportError:
		return {"has_cover": False, "cover": [], "warranty_status": "No Warranty"}

	try:
		result = check_warranty(serial_no=serial_no, company=company) or {}
	except frappe.PermissionError:
		# The VAS scope guard fails closed for a serial it cannot place. For a
		# walk-in device that is the expected answer, not a fault.
		return {"has_cover": False, "cover": [], "warranty_status": "No Warranty"}
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"POS coverage lookup failed for {serial_no}")
		return {"has_cover": False, "cover": [], "warranty_status": "No Warranty"}

	cover = []
	plan = result.get("covering_plan") or {}
	if plan:
		cover.append({
			"kind": "vas",
			"label": plan.get("plan_title") or plan.get("warranty_plan") or _("VAS plan"),
			"expires_on": plan.get("end_date"),
			"deductible": plan.get("deductible_amount"),
			"active_plan": plan.get("name"),
			"claim_against": _("VAS policy"),
		})
	for row in result.get("repair_coverage") or []:
		cover.append({
			"kind": row.get("coverage_type") or "repair",
			"label": row.get("covers"),
			"expires_on": row.get("expires_on"),
			"days_left": row.get("days_left"),
			"service_request": row.get("service_request"),
			"claim_against": row.get("claim_against"),
		})

	# has_cover surfaces the advisory panel whenever ANY cover exists — a VAS
	# plan, or our own prior repair / fitted part still in window — so the
	# counter can see a returning device might qualify for rework. It is NOT a
	# warranty grant: warranty_status stays "Under Warranty" only for a VAS /
	# device warranty the API actually confirmed, and the Service Request re-runs
	# the lookup as the authority. Prior-repair cover alone no longer flips the
	# device to Under Warranty (that over-granted free repairs for unrelated
	# faults) — a same-part rework is confirmed later, at estimate time.
	return {
		"has_cover": bool(result.get("warranty_covered") or cover),
		"warranty_status": "Under Warranty" if result.get("warranty_covered") else "No Warranty",
		"cover": cover,
	}


def _resolve_walkin_source(data) -> str:
    """The Walkin Source row matching how this customer reached us.

    Reads the originating front-desk visit rather than trusting the screen,
    because the screen only knows it is the repair intake -- it does not know
    whether the person walked in or messaged three days ago.
    """
    explicit = (data.get("walkin_source") or "").strip()
    if explicit and frappe.db.exists("Walkin Source", explicit):
        return explicit

    channel = ""
    token = (data.get("source_token") or "").strip()
    if token:
        channel = frappe.db.get_value("POS Kiosk Token", token, "visit_source") or ""
    if not channel:
        inbox = (data.get("source_inbox") or "").strip()
        if inbox:
            channel = frappe.db.get_value("POS Kiosk Token", inbox, "visit_source") or ""

    if channel:
        try:
            from gofix.patches.seed_gofix_service_masters import (
                VISIT_SOURCE_TO_WALKIN_SOURCE)

            mapped = VISIT_SOURCE_TO_WALKIN_SOURCE.get(channel, channel)
        except Exception:
            mapped = channel
        if frappe.db.exists("Walkin Source", mapped):
            return mapped

    # Nothing to go on: the ticket was raised straight at the counter.
    return "POS Counter" if frappe.db.exists("Walkin Source", "POS Counter") else None
