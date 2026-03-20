# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
POS Closing Entry — end-of-day reconciliation for a POS terminal.

Lifecycle:  Draft → Submitted  (or Discrepancy if variance found)

Fetches expected amounts from submitted Sales Invoices for the given
date range and POS Profile, then compares against counted amounts
entered by the cashier.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, cint, nowdate


class POSClosingEntry(Document):
	def validate(self):
		self._set_closed_by()
		self._fetch_invoice_summary()
		self._sync_payment_details()
		self._compute_cash_variance()
		self._update_status()

	def on_submit(self):
		self.db_set("status", "Submitted" if not self._has_discrepancy() else "Discrepancy")

	def on_cancel(self):
		self.db_set("status", "Draft")

	# ── Internal helpers ───────────────────────────────────────────────────────

	def _set_closed_by(self):
		if not self.closed_by:
			self.closed_by = frappe.session.user

	def _invoice_filter_args(self):
		"""Build the SQL WHERE clause arguments for Sales Invoice queries."""
		return {
			"pos_profile": self.pos_profile,
			"docstatus": 1,
			"is_consolidated": 0,
			"posting_date": ["between", [self.from_date, self.to_date]],
		}

	def _fetch_invoice_summary(self):
		"""Query submitted Sales Invoices and populate summary totals."""
		f = self._invoice_filter_args()

		invoices = frappe.get_all(
			"Sales Invoice",
			filters=f,
			fields=[
				"name", "grand_total", "is_return",
				"custom_margin_gst", "custom_is_margin_scheme",
				"total_taxes_and_charges",
			],
		)

		total_invoices = 0
		total_amount = 0.0
		total_returns = 0
		total_return_amount = 0.0
		total_tax = 0.0
		total_margin_gst = 0.0

		for inv in invoices:
			amt = flt(inv.grand_total)
			tax = flt(inv.total_taxes_and_charges)
			if inv.is_return:
				total_returns += 1
				total_return_amount += abs(amt)
			else:
				total_invoices += 1
				total_amount += amt
				total_tax += tax
				total_margin_gst += flt(inv.custom_margin_gst)

		self.total_invoices = total_invoices
		self.total_amount = total_amount
		self.total_returns = total_returns
		self.total_return_amount = total_return_amount
		self.total_tax = total_tax
		self.total_margin_gst = total_margin_gst
		self.total_regular_gst = total_tax - total_margin_gst
		self.net_sales = total_amount - total_return_amount

	def _sync_payment_details(self):
		"""Fetch expected amounts per payment mode from Sales Invoice payments."""
		f = self._invoice_filter_args()

		rows = frappe.db.sql("""
			SELECT
				sip.mode_of_payment,
				SUM(sip.amount) AS expected_amount
			FROM `tabSales Invoice` pi
			JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
			WHERE pi.pos_profile = %(pos_profile)s
			  AND pi.docstatus = 1
			  AND pi.is_consolidated = 0
			  AND pi.posting_date BETWEEN %(from_date)s AND %(to_date)s
			GROUP BY sip.mode_of_payment
		""", {
			"pos_profile": self.pos_profile,
			"from_date": self.from_date,
			"to_date": self.to_date,
		}, as_dict=True)

		# Build map from existing rows so counted_amount is preserved
		existing = {row.mode_of_payment: row for row in (self.payment_details or [])}

		self.set("payment_details", [])
		expected_cash = 0.0
		for r in rows:
			prev = existing.get(r.mode_of_payment, {})
			counted = flt(prev.get("counted_amount", 0)) if prev else 0.0
			exp = flt(r.expected_amount)
			self.append("payment_details", {
				"mode_of_payment": r.mode_of_payment,
				"expected_amount": exp,
				"counted_amount": counted,
				"variance": counted - exp,
				"notes": prev.get("notes", "") if prev else "",
			})
			# Track cash separately for header-level field
			mop_type = frappe.db.get_value("Mode of Payment", r.mode_of_payment, "type")
			if mop_type == "Cash":
				expected_cash += exp

		self.expected_cash = expected_cash + flt(self.opening_cash)

	def _compute_cash_variance(self):
		counted = flt(self.counted_cash)
		expected = flt(self.expected_cash)
		self.cash_variance = counted - expected

	def _update_status(self):
		if self.docstatus == 2:
			return
		self.status = "Discrepancy" if self._has_discrepancy() else "Draft"

	def _has_discrepancy(self):
		if flt(self.cash_variance) != 0:
			return True
		for row in (self.payment_details or []):
			if flt(row.variance) != 0:
				return True
		return False
