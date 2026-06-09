"""Regression checks for POS Incentive Ledger approval cycle.

Run:
    bench --site erpnext.local execute ch_pos.tests.test_pos_incentive_approval_cycle.run
"""

from __future__ import annotations

import frappe
from frappe.utils import nowdate

from ch_pos.pos_core.doctype.pos_incentive_ledger.pos_incentive_ledger import (
	approve_incentive,
	cancel_incentive,
	mark_incentive_paid,
)


def _pick_pos_executive() -> str:
	exec_name = frappe.db.get_value("POS Executive", {"is_active": 1}, "name")
	if exec_name:
		return exec_name

	exec_name = frappe.db.get_value("POS Executive", {}, "name")
	if exec_name:
		return exec_name

	raise AssertionError("No POS Executive found; cannot run incentive approval cycle test")


def run():
	exec_name = _pick_pos_executive()
	doc = frappe.get_doc(
		{
			"doctype": "POS Incentive Ledger",
			"pos_executive": exec_name,
			"posting_date": nowdate(),
			"transaction_type": "Sale",
			"qty": 1,
			"billing_amount": 1000,
			"incentive_amount": 50,
			"status": "Pending",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	try:
		# Cannot pay directly from Pending.
		try:
			mark_incentive_paid(doc.name, payout_reference="JE-TEST-001", payout_month="2026-06")
		except frappe.ValidationError:
			print("[PASS] Pending cannot move directly to Paid")
		else:
			raise AssertionError("Pending -> Paid should be blocked")

		approve_incentive(doc.name)
		doc.reload()
		if doc.status != "Approved":
			raise AssertionError(f"Expected Approved after approval action, got {doc.status}")
		if not doc.approved_by:
			raise AssertionError("approved_by was not stamped")
		if not doc.approved_on:
			raise AssertionError("approved_on was not stamped")
		print("[PASS] Pending -> Approved stamps approver")

		mark_incentive_paid(doc.name, payout_reference="JE-TEST-001", payout_month="2026-06")
		doc.reload()
		if doc.status != "Paid":
			raise AssertionError(f"Expected Paid after payout action, got {doc.status}")
		if doc.payout_reference != "JE-TEST-001":
			raise AssertionError("payout_reference was not saved")
		if not doc.paid_by:
			raise AssertionError("paid_by was not stamped")
		if not doc.paid_on:
			raise AssertionError("paid_on was not stamped")
		print("[PASS] Approved -> Paid stamps payout metadata")

		# Cannot cancel after Paid.
		try:
			cancel_incentive(doc.name)
		except frappe.ValidationError:
			print("[PASS] Paid cannot be cancelled")
		else:
			raise AssertionError("Paid -> Cancelled should be blocked")

		print("POS Incentive approval cycle regression: ALL PASS")
	finally:
		if frappe.db.exists("POS Incentive Ledger", doc.name):
			frappe.delete_doc("POS Incentive Ledger", doc.name, ignore_permissions=True, force=True)
