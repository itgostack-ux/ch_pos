# Copyright (c) 2026, GoStack and contributors
"""Section 269ST cash-receipt limit guard.

Income-Tax Act Section 269ST prohibits any person from receiving an aggregate
of ₹2,00,000 or more in cash from a single person in a single day (or in
respect of a single transaction / single event). Penalty under section 271DA
equals the amount received.

This module wires a ``validate`` / ``before_submit`` hook on Sales Invoice
that blocks submission when same-customer same-company same-day cash receipts (across
Sales Invoice payments table + standalone Payment Entries with Cash MoP)
would breach the configured threshold.

Threshold source (resolution order):
1. Single Setting "CH Cash Receipt Settings" -> ``daily_cash_limit`` (Currency)
   — created lazily on first call so the install need not depend on fixtures.
2. Fallback: ₹2,00,000.

The check is skipped for non-cash invoices, return invoices and when
``frappe.flags.ignore_cash_receipt_limit`` is set (tests).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate

DEFAULT_LIMIT = 200000.0
SETTINGS_DOCTYPE = "Accounts Settings"
SETTINGS_FIELD = "ch_daily_cash_receipt_limit"


def _resolve_limit() -> float:
	value = frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTINGS_FIELD)
	limit = flt(value)
	return limit if limit > 0 else DEFAULT_LIMIT


def _cash_modes() -> list[str]:
	"""Return Mode of Payment names whose ``type`` is Cash."""
	rows = frappe.get_all("Mode of Payment", filters={"type": "Cash"}, pluck="name")
	return rows or ["Cash"]


def _invoice_cash_amount(doc) -> float:
	"""Cash component of *this* Sales Invoice (ignoring change_amount)."""
	cash_modes = set(_cash_modes())
	total = 0.0
	for row in doc.get("payments") or []:
		if row.mode_of_payment in cash_modes:
			total += flt(row.amount)
	# Subtract change returned to customer (already netted in payments but
	# defensive when ERPNext leaves base_amount unchanged).
	total -= flt(getattr(doc, "change_amount", 0))
	return max(total, 0.0)


def _same_day_existing_cash(customer: str, company: str, posting_date, exclude: str | None) -> float:
	cash_modes = _cash_modes()
	if not customer or not company or not posting_date:
		return 0.0

	# Sales Invoice payments (submitted only)
	si_total = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(sip.amount), 0)
		FROM `tabSales Invoice Payment` sip
		JOIN `tabSales Invoice` si ON si.name = sip.parent
		WHERE si.customer = %(customer)s
		  AND si.company = %(company)s
		  AND si.posting_date = %(posting_date)s
		  AND si.docstatus = 1
		  AND sip.mode_of_payment IN %(cash_modes)s
		  AND si.name != %(exclude)s
		""",
		{
			"customer": customer,
			"company": company,
			"posting_date": posting_date,
			"cash_modes": tuple(cash_modes),
			"exclude": exclude or "",
		},
	)[0][0] or 0.0

	# Standalone Payment Entries against the same customer
	pe_total = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(paid_amount), 0)
		FROM `tabPayment Entry`
		WHERE party_type = 'Customer'
		  AND party = %(customer)s
		  AND company = %(company)s
		  AND posting_date = %(posting_date)s
		  AND docstatus = 1
		  AND mode_of_payment IN %(cash_modes)s
		""",
		{
			"customer": customer,
			"company": company,
			"posting_date": posting_date,
			"cash_modes": tuple(cash_modes),
		},
	)[0][0] or 0.0

	return flt(si_total) + flt(pe_total)


def validate_section_269st_cash_limit(doc, method=None):
	"""doc_events hook for Sales Invoice ``validate`` / ``before_submit``.

	Blocks submission when the aggregate same-day cash receipts from the
	invoice customer within the same company would breach the configured
	limit (default ₹2,00,000).
	"""
	if getattr(frappe.flags, "ignore_cash_receipt_limit", False):
		return
	if not getattr(doc, "customer", None):
		return
	if not getattr(doc, "company", None):
		return
	if getattr(doc, "is_return", 0):
		return

	this_cash = _invoice_cash_amount(doc)
	if this_cash <= 0:
		return

	existing = _same_day_existing_cash(doc.customer, doc.company, getdate(doc.posting_date), doc.name)
	limit = _resolve_limit()
	total = this_cash + existing

	if total >= limit:
		frappe.throw(
			_(
				"Section 269ST: cash receipt from <b>{0}</b> on {1} would total "
				"<b>₹{2:,.2f}</b>, which is at or above the daily limit of "
				"<b>₹{3:,.2f}</b>. Already received in cash today: ₹{4:,.2f}. "
				"Please ask the customer to pay via non-cash mode."
			).format(
				doc.customer,
				frappe.utils.formatdate(doc.posting_date),
				total,
				limit,
				existing,
			),
			title=_("Cash Receipt Limit Exceeded"),
		)
