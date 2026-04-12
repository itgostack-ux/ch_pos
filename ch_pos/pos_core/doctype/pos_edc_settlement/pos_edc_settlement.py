# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""
POS EDC Settlement — manual upload and matching of card terminal batch statements.

Workflow:
  1. Cashier receives EDC batch report from bank at end of day.
  2. Creates POS EDC Settlement, enters settlement_date + terminal_id.
  3. Uploads transactions via upload_edc_transactions() API or enters manually.
  4. Clicks "Auto Match" to match each transaction against a Sales Invoice by RRN
     or by (amount + date) approximation.
  5. Reviews unmatched rows and either manually links or marks as Disputed.
  6. Submits when satisfied — status becomes Matched (if 100%) or Discrepancy.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, cint


class POSEDCSettlement(Document):
	def validate(self):
		self._compute_totals()
		self._update_status()

	def on_submit(self):
		status = "Matched" if flt(self.unmatched_amount) == 0 else "Discrepancy"
		self.db_set("status", status)

	def on_cancel(self):
		self.db_set("status", "Draft")

	# ── Helpers ────────────────────────────────────────────────────────────────

	def _compute_totals(self):
		txns = self.transactions or []
		self.total_transactions = len(txns)
		self.total_amount = sum(flt(r.amount) for r in txns)
		self.matched_amount = sum(flt(r.amount) for r in txns if r.match_status == "Matched")
		self.unmatched_amount = sum(flt(r.amount) for r in txns if r.match_status == "Unmatched")
		self.variance = self.total_amount - self.matched_amount
		if self.total_amount > 0:
			self.match_rate = (self.matched_amount / self.total_amount) * 100
		else:
			self.match_rate = 0

	def _update_status(self):
		if self.docstatus in (1, 2):
			return
		if not self.transactions:
			self.status = "Draft"
			return
		unmatched = [r for r in self.transactions if r.match_status == "Unmatched"]
		self.status = "Draft" if unmatched else "Matched"

	@frappe.whitelist()
	def auto_match(self) -> None:
		"""Attempt to auto-match each Unmatched transaction to a Sales Invoice.

		Match strategy (in order):
		  1. RRN match — look for custom_card_reference = rrn on Sales Invoice payments
		  2. Amount + date match — single Sales Invoice with exact amount on settlement_date
		     with a card payment mode
		"""
		matched_count = 0
		for row in self.transactions:
			if row.match_status == "Matched":
				continue

			# Strategy 1: RRN exact match
			if row.rrn:
				invoice = frappe.db.get_value(
					"Sales Invoice Payment",
					{"custom_card_reference": row.rrn, "parenttype": "Sales Invoice"},
					"parent",
				)
				if invoice:
					row.matched_pos_invoice = invoice
					row.match_status = "Matched"
					matched_count += 1
					continue

			# Strategy 2: Amount + date match (must be unique)
			if row.amount and row.transaction_date:
				mop_type_filter = """
					AND sip.mode_of_payment IN (
						SELECT name FROM `tabMode of Payment`
						WHERE type = 'Bank'
					)
				"""
				results = frappe.db.sql("""
					SELECT pi.name
					FROM `tabSales Invoice` pi
					JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
					WHERE pi.posting_date = %(date)s
					  AND pi.docstatus = 1
					  AND sip.amount = %(amount)s
					  {mop_filter}
					LIMIT 2
				""".format(mop_filter=mop_type_filter), {  # noqa: UP032
					"date": row.transaction_date,
					"amount": flt(row.amount),
				})
				if len(results) == 1:
					row.matched_pos_invoice = results[0][0]
					row.match_status = "Matched"
					matched_count += 1

		self._compute_totals()
		self._update_status()
		self.save(ignore_permissions=True)
		return {"matched": matched_count, "total": len(self.transactions)}


@frappe.whitelist()
def upload_edc_transactions(settlement_name, transactions_json) -> dict:
	"""Bulk-upload EDC transactions (from CSV parse on frontend).

	Args:
		settlement_name: POS EDC Settlement name
		transactions_json: JSON list of {rrn, card_last_four, card_network,
		                   transaction_date, transaction_time, amount}
	Returns:
		{inserted: N}
	"""
	import frappe

	if isinstance(transactions_json, str):
		transactions_json = frappe.parse_json(transactions_json)

	doc = frappe.get_doc("POS EDC Settlement", settlement_name)
	if doc.docstatus != 0:
		frappe.throw(_("Can only upload transactions to a Draft settlement"), title=_("Pos Edc Settlement Error"))

	for txn in transactions_json:
		doc.append("transactions", {
			"rrn": txn.get("rrn") or "",
			"card_last_four": txn.get("card_last_four") or "",
			"card_network": txn.get("card_network") or "",
			"transaction_date": txn.get("transaction_date"),
			"transaction_time": txn.get("transaction_time") or "",
			"amount": flt(txn.get("amount", 0)),
			"match_status": "Unmatched",
		})

	doc.save(ignore_permissions=True)
	return {"inserted": len(transactions_json)}
