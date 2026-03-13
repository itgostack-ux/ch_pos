# Copyright (c) 2026, GoStack and contributors
# Return Policy Control — validate hook for POS Invoice (Credit Note).
#
# Called from ch_pos hooks.py doc_events on POS Invoice validate.
# Enforces return window: if the original sale is older than the configured
# return_policy_days, the return is blocked unless an approved exception exists.

import frappe
from frappe import _
from frappe.utils import flt, date_diff, getdate


def validate_return_policy(doc, method=None):
	"""Check return-against invoice date against configured return window.

	Only applies to POS Invoice Credit Notes (is_return=1, return_against set).
	"""
	if not doc.is_return or not doc.return_against:
		return

	company = doc.company
	if not company:
		return

	# Fetch return policy days from CH Commercial Policy
	return_days = _get_return_policy_days(company)
	if not return_days or return_days <= 0:
		return  # No return window configured — allow all returns

	# Get the original invoice date
	original_date = frappe.db.get_value("POS Invoice", doc.return_against, "posting_date")
	if not original_date:
		return

	days_since_sale = date_diff(getdate(doc.posting_date or frappe.utils.nowdate()), getdate(original_date))

	if days_since_sale <= return_days:
		return  # Within policy — no action needed

	# Beyond policy window — check if exception already approved
	exception_approved = frappe.db.exists("CH Exception Request", {
		"exception_type": "Return Beyond Policy",
		"reference_doctype": "POS Invoice",
		"reference_name": doc.return_against,
		"status": "Approved",
		"company": company,
	})

	if exception_approved:
		return  # Exception already approved

	# Log the exception request
	_create_return_exception(
		doc=doc,
		company=company,
		days_since_sale=days_since_sale,
		return_days=return_days,
		original_date=original_date,
	)

	# Hard block the return
	frappe.throw(
		_("Return of invoice {0} is beyond the {1}-day return policy "
		  "(sold {2} days ago on {3}).<br><br>"
		  "An exception request has been raised. Manager/HO approval "
		  "is required to process this return.").format(
			frappe.bold(doc.return_against),
			return_days,
			days_since_sale,
			frappe.format(original_date, {"fieldtype": "Date"}),
		),
		title=_("Return Beyond Policy"),
	)


def _get_return_policy_days(company):
	"""Fetch return_policy_days from CH Commercial Policy."""
	if not frappe.db.exists("DocType", "CH Commercial Policy"):
		return 0
	return frappe.db.get_single_value("CH Commercial Policy", "return_policy_days") or 0


def _create_return_exception(doc, company, days_since_sale, return_days, original_date):
	"""Create a CH Exception Request for the return-beyond-policy scenario."""
	if not frappe.db.exists("CH Exception Type", "Return Beyond Policy"):
		return

	try:
		from ch_item_master.ch_item_master.exception_api import raise_exception

		# Collect item/serial info from the credit note
		item_code = doc.items[0].item_code if doc.items else None
		serial_no = None
		if doc.items and doc.items[0].serial_no:
			serial_no = doc.items[0].serial_no.split("\n")[0]

		raise_exception(
			exception_type="Return Beyond Policy",
			company=company,
			reason=(
				f"Return against {doc.return_against} (sold {original_date}) "
				f"is {days_since_sale} days old — exceeds {return_days}-day policy"
			),
			requested_value=abs(flt(doc.grand_total)),
			original_value=0,
			reference_doctype="POS Invoice",
			reference_name=doc.return_against,
			item_code=item_code,
			serial_no=serial_no,
			store_warehouse=doc.set_warehouse,
			pos_profile=doc.pos_profile,
			pos_invoice=doc.return_against,
			customer=doc.customer,
		)
	except Exception:
		frappe.log_error("Return Beyond Policy exception creation failed")
