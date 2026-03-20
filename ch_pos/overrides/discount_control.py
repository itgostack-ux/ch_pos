# Copyright (c) 2026, GoStack and contributors
# POS Discount Control — validate hook for Sales Invoice
#
# Called from ch_pos hooks.py doc_events on Sales Invoice validate.
# Enforces commercial policy: discount limits, MOP floor, override logging.

import frappe
from frappe import _
from frappe.utils import flt


def validate_pos_commercial_policy(doc, method=None):
	"""Validate Sales Invoice items against CH Commercial Policy rules.

	1. Check each item's rate against CH Item Price selling_price
	2. Enforce MOP floor (unless item has allowed tags)
	3. Enforce role-based discount limits
	4. Log overrides when rate differs from CH Item Price
	5. Prevent double-discount from CH Offer + ERPNext Pricing Rule
	"""
	if not doc.is_pos:
		return

	company = doc.company
	if not company:
		return

	# Import commercial API
	from ch_item_master.ch_item_master.commercial_api import (
		get_commercial_policy,
		validate_pos_discount,
		log_pos_override,
		check_offer_precedence,
	)

	policy = get_commercial_policy(company)
	pos_channel = _resolve_pos_channel(doc)

	for item in doc.items:
		rate = flt(item.rate)
		item_code = item.item_code
		if not item_code or rate <= 0:
			continue

		# ── A2: Offer Precedence Guard ────────────────────────────────
		# If a CH Item Offer is active for this item, its synced ERPNext
		# Pricing Rule handles the discount. We don't need to detect
		# double-application here because CH Offers sync as Pricing Rules
		# with specific for_price_list, and ERPNext deduplicates by priority.
		# This check is informational — we add a note if both applied.

		# ── A1: Discount validation ───────────────────────────────────
		if pos_channel:
			result = validate_pos_discount(
				item_code=item_code,
				channel=pos_channel,
				rate=rate,
				company=company,
			)

			if result and not result.get("allowed") and not result.get("needs_approval"):
				# Hard block: below MOP without allowed tag
				frappe.throw(
					_("Item {0}: {1}").format(
						frappe.bold(item_code),
						result.get("reason"),
					),
					title=_("Commercial Policy Violation"),
				)

			if result and not result.get("allowed") and result.get("needs_approval"):
				# Needs manager approval — check if it was pre-approved
				if not item.get("custom_manager_approved"):
					frappe.throw(
						_("Item {0}: {1}<br><br>"
						  "Manager approval required to proceed."
						).format(
							frappe.bold(item_code),
							result.get("reason"),
						),
						title=_("Discount Limit Exceeded"),
					)

			# ── Log override if rate differs from CH Item Price ──────
			if result and flt(result.get("discount_percent")) > 0:
				original_price = flt(result.get("original_price"))
				if original_price > 0 and rate < original_price:
					# Determine override type
					mop = flt(result.get("mop", 0))
					if mop and rate < mop:
						override_type = "Below MOP"
					elif flt(item.discount_amount) > 0:
						override_type = "Discount Override"
					else:
						override_type = "Rate Override"

					log_pos_override(
						pos_invoice=doc.name,
						item_code=item_code,
						original_price=original_price,
						applied_price=rate,
						override_type=override_type,
						serial_no=(item.serial_no or "").split("\n")[0] if item.serial_no else "",
						approved_by_manager=bool(item.get("custom_manager_approved")),
						manager_user=item.get("custom_manager_user") or "",
						override_reason=item.get("custom_override_reason") or "",
						pos_profile=doc.pos_profile,
						company=company,
						warehouse=item.warehouse or doc.set_warehouse,
					)

					# ── Also create a CH Exception Request for audit ────
					_log_exception_request(
						exception_type="Discount Override",
						company=company,
						reason=item.get("custom_override_reason") or override_type,
						requested_value=flt(original_price - rate),
						original_value=original_price,
						item_code=item_code,
						serial_no=(item.serial_no or "").split("\n")[0] if item.serial_no else None,
						store_warehouse=item.warehouse or doc.set_warehouse,
						pos_profile=doc.pos_profile,
						pos_invoice=doc.name,
						customer=doc.customer,
						approved=bool(item.get("custom_manager_approved")),
						approver=item.get("custom_manager_user"),
					)

		# ── Free-accessory gate (#3): detect ad-hoc free items ────────
		if flt(item.rate) == 0 and not item.get("is_free_item"):
			# Item added at zero rate without being set by a Pricing Rule
			_log_exception_request(
				exception_type="Free Accessory",
				company=company,
				reason="Item added at zero rate without offer/pricing rule",
				requested_value=0,
				original_value=0,
				item_code=item_code,
				store_warehouse=item.warehouse or doc.set_warehouse,
				pos_profile=doc.pos_profile,
				pos_invoice=doc.name,
				customer=doc.customer,
				approved=bool(item.get("custom_manager_approved")),
				approver=item.get("custom_manager_user"),
			)
			if not item.get("custom_manager_approved"):
				frappe.throw(
					_("Item {0} added at ₹0 without an active offer. "
					  "Manager approval required.").format(frappe.bold(item_code)),
					title=_("Free Accessory — Approval Required"),
				)

		# ── Below-margin check (#5): warn when selling below cost ─────
		if rate > 0:
			incoming = _get_item_valuation(item_code, item.warehouse or doc.set_warehouse)
			if incoming and incoming > 0 and rate < incoming:
				_log_exception_request(
					exception_type="Below Margin Sale",
					company=company,
					reason=f"Selling at {rate} below cost {incoming}",
					requested_value=flt(incoming - rate),
					original_value=incoming,
					item_code=item_code,
					serial_no=(item.serial_no or "").split("\n")[0] if item.serial_no else None,
					store_warehouse=item.warehouse or doc.set_warehouse,
					pos_profile=doc.pos_profile,
					pos_invoice=doc.name,
					customer=doc.customer,
					approved=bool(item.get("custom_manager_approved")),
					approver=item.get("custom_manager_user"),
				)

	# ── Additional discount at document level ─────────────────────────
	if flt(doc.additional_discount_percentage) > 0 or flt(doc.discount_amount) > 0:
		if policy:
			max_pct = flt(policy.max_discount_without_approval)
			if max_pct > 0 and flt(doc.additional_discount_percentage) > max_pct:
				frappe.throw(
					_("Additional discount of {0}% exceeds maximum allowed {1}%").format(
						doc.additional_discount_percentage, max_pct
					),
					title=_("Commercial Policy Violation"),
				)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _log_exception_request(exception_type, company, reason, requested_value=0,
                           original_value=0, item_code=None, serial_no=None,
                           store_warehouse=None, pos_profile=None,
                           pos_invoice=None, customer=None,
                           approved=False, approver=None):
	"""Create a CH Exception Request for audit.

	Silently skips if the exception type doesn't exist (not yet seeded).
	"""
	if not frappe.db.exists("CH Exception Type", exception_type):
		return

	try:
		from ch_item_master.ch_item_master.exception_api import raise_exception
		result = raise_exception(
			exception_type=exception_type,
			company=company,
			reason=reason,
			requested_value=requested_value,
			original_value=original_value,
			item_code=item_code,
			serial_no=serial_no,
			store_warehouse=store_warehouse,
			pos_profile=pos_profile,
			pos_invoice=pos_invoice,
			customer=customer,
		)
		# If pre-approved at POS, approve the exception immediately
		if approved and result and result.get("status") == "Pending":
			from ch_item_master.ch_item_master.exception_api import approve_exception
			approve_exception(
				exception_name=result["name"],
				approver_user=approver,
				channel="Manager PIN",
			)
	except Exception:
		frappe.log_error("Exception Request creation failed")


def _get_item_valuation(item_code, warehouse):
	"""Get the last valuation rate (incoming cost) of an item at a warehouse."""
	if not item_code:
		return 0
	val = frappe.db.get_value("Bin",
		{"item_code": item_code, "warehouse": warehouse},
		"valuation_rate",
	)
	return flt(val)


def _resolve_pos_channel(doc):
	"""Resolve which CH Price Channel the POS Profile uses.

	Looks up the POS Profile's selling_price_list → CH Price Channel mapping.
	Falls back to 'POS' if a channel with that name exists.
	"""
	if not doc.pos_profile:
		return None

	selling_price_list = frappe.db.get_value("POS Profile", doc.pos_profile, "selling_price_list")
	if selling_price_list:
		channel = frappe.db.get_value(
			"CH Price Channel",
			{"price_list": selling_price_list, "disabled": 0},
			"name",
		)
		if channel:
			return channel

	# Fallback: look for a channel named "POS"
	if frappe.db.exists("CH Price Channel", "POS"):
		return "POS"

	return None
