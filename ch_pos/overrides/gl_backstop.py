# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""POS ledger-integrity backstop.

WHY THIS EXISTS
---------------
Between 2026-07-20 and 2026-07-30, 26 live POS Sales Invoices
(BMTNSI26000007..00088) were submitted with Stock Ledger Entries but an
incomplete — in two cases completely empty — General Ledger. Root cause was
the Jul-15 commit f77ee0c ("Modified Gl-entries"): its `_rewrite_gl_entries`
ran `DELETE FROM tabGL Entry` + `frappe.db.commit()` and then hand-inserted
only the Debtors / Income / Tax legs by raw SQL. The payment clearing legs
(Dr Cash / Cr Debtors) and the perpetual-inventory legs (Dr COGS / Cr Stock
In Hand) that ERPNext's `on_submit` had already written were destroyed by
the DELETE and never rebuilt; on any imbalance or exception the function
merely logged and returned, leaving ZERO GL behind an already-committed
DELETE. Commit 0cd764e (Jul-24) replaced that with a framework repost under
a savepoint, but the class of bug — a post-submit step silently dropping GL
legs while the SLE stands — must never be able to half-post again.

This module is the cheap, structural assertion of that guarantee. It does
not recompute amounts (the framework's balance check owns that); it asserts
the *footprint*: a paid POS invoice must clear through `debit_to`, and a
stock-updating invoice whose SLE moved value must carry a COGS leg. If a
future regression silently skips either, submit fails loudly and the whole
transaction rolls back instead of booking a sale with no cash and no cost.

Wired twice on purpose:
  * as the last `on_submit` doc_event on Sales Invoice — catches a submit
    path that skipped GL creation;
  * explicitly at the end of the `create_pos_invoice` pipeline in
    `pos_api.py`, AFTER the post-submit tax/GL rewrite — catches a rewrite
    that deleted legs it did not restore (the exact 2026-07 failure).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

#: Amounts below this are rounding noise, not missing money.
_TOLERANCE = 0.005


def _gl_rows(voucher_no):
	"""Live GL rows for one Sales Invoice (cancelled legs excluded)."""
	return frappe.db.sql(
		"""
		SELECT account, debit, credit
		FROM `tabGL Entry`
		WHERE voucher_type = 'Sales Invoice'
		  AND voucher_no = %s
		  AND is_cancelled = 0
		""",
		(voucher_no,),
		as_dict=True,
	)


def _stock_value_moved(voucher_no):
	"""Absolute stock value moved by this invoice's SLEs.

	Zero-valuation items legitimately produce SLEs with no value change and
	therefore no stock GL legs — the COGS assertion must not fire for them.
	"""
	return flt(
		frappe.db.sql(
			"""
			SELECT COALESCE(SUM(ABS(stock_value_difference)), 0)
			FROM `tabStock Ledger Entry`
			WHERE voucher_type = 'Sales Invoice'
			  AND voucher_no = %s
			  AND is_cancelled = 0
			""",
			(voucher_no,),
		)[0][0]
	)


def assert_pos_ledger_integrity(doc, method=None):
	"""Refuse to complete a POS submit whose GL footprint is incomplete.

	Asserted structure, matching what a correct POS submit always produces:

	* ``paid_amount != 0`` → ``debit_to`` must appear on BOTH sides of the
	  GL: the receivable leg (Dr on a sale / Cr on a return) and the
	  settlement leg (Cr cash-payment or loyalty clearing on a sale / Dr
	  refund on a return). A missing side means the payment posting was
	  silently dropped — the 2026-07 incident signature.
	* ``update_stock`` with SLE value moved → at least one GL leg on an
	  item's ``expense_account`` (the COGS side of the stock pair; the GL
	  framework guarantees its warehouse counter-leg by balance).

	Loud by design: a throw here rolls the whole submit back, which is
	strictly better than a booked sale with no cash and no cost of goods.
	"""
	if cint(getattr(doc, "docstatus", 0)) != 1 or not cint(doc.get("is_pos")):
		return
	# Maintenance escapes only — migrations replay documents in states this
	# assertion was never meant to police, and a deliberate repair script may
	# set the skip flag while it rebuilds a ledger.
	if (
		frappe.flags.get("ch_pos_skip_gl_backstop")
		or frappe.flags.in_migrate
		or frappe.flags.in_install
		or frappe.flags.in_patch
	):
		return

	rows = _gl_rows(doc.name)
	paid = flt(doc.get("paid_amount"))

	if abs(paid) > _TOLERANCE:
		debtor_rows = [r for r in rows if r.account == doc.debit_to]
		has_debit_side = any(flt(r.debit) > _TOLERANCE for r in debtor_rows)
		has_credit_side = any(flt(r.credit) > _TOLERANCE for r in debtor_rows)
		if not (has_debit_side and has_credit_side):
			frappe.throw(
				_(
					"Sales Invoice {0} would be submitted without complete "
					"payment accounting on {1} (receivable and settlement legs "
					"must both exist for a paid POS invoice). The submit has "
					"been aborted so the books cannot half-post. Please retry "
					"the sale and alert the accounts team if this recurs."
				).format(doc.name, doc.debit_to),
				title=_("POS Accounting Incomplete"),
			)

	if cint(doc.get("update_stock")) and _stock_value_moved(doc.name) > _TOLERANCE:
		expense_accounts = {
			item.expense_account
			for item in (doc.get("items") or [])
			if item.get("expense_account")
		}
		has_cogs_leg = any(
			r.account in expense_accounts
			and (flt(r.debit) > _TOLERANCE or flt(r.credit) > _TOLERANCE)
			for r in rows
		)
		if expense_accounts and not has_cogs_leg:
			frappe.throw(
				_(
					"Sales Invoice {0} moved stock value but would post no "
					"Cost of Goods Sold entry. The submit has been aborted so "
					"inventory and accounting cannot diverge. Please retry the "
					"sale and alert the accounts team if this recurs."
				).format(doc.name),
				title=_("POS Accounting Incomplete"),
			)
