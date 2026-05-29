"""Section 269ST cash-receipt limit validator — unit tests.

Run:
    bench --site erpnext.local execute \
        ch_pos.tests.test_cash_receipt_269st.run

Tests focus on the pure validator logic — DB lookups are stubbed so the
suite runs in milliseconds without needing fixtures.
"""

from __future__ import annotations

import frappe
from frappe.utils import nowdate

from ch_pos.overrides import cash_receipt_limit as crl


class _StubDoc:
	"""Minimal Sales Invoice stand-in for validator tests."""

	def __init__(self, customer, payments, posting_date=None, name="SI-TEST", is_return=0, change_amount=0):
		self.customer = customer
		self.payments = payments
		self.posting_date = posting_date or nowdate()
		self.name = name
		self.is_return = is_return
		self.change_amount = change_amount

	def get(self, key, default=None):
		return getattr(self, key, default)


class _Row:
	def __init__(self, mode_of_payment, amount):
		self.mode_of_payment = mode_of_payment
		self.amount = amount


def _patch(existing, limit=200000.0, cash_modes=("Cash",)):
	crl._same_day_existing_cash = lambda *_a, **_k: float(existing)
	crl._resolve_limit = lambda: float(limit)
	crl._cash_modes = lambda: list(cash_modes)


def _expect_throw(doc, label):
	try:
		crl.validate_section_269st_cash_limit(doc)
	except frappe.ValidationError as exc:
		print(f"✅  {label}  →  blocked ({str(exc)[:60]}…)")
		return True
	print(f"❌  {label}  →  NOT blocked (expected ValidationError)")
	return False


def _expect_pass(doc, label):
	try:
		crl.validate_section_269st_cash_limit(doc)
	except frappe.ValidationError as exc:
		print(f"❌  {label}  →  unexpectedly blocked: {exc}")
		return False
	print(f"✅  {label}  →  passed")
	return True


def run():
	frappe.set_user("Administrator")
	results = []

	# 1. New invoice alone above limit
	_patch(existing=0)
	doc = _StubDoc("CUST-001", [_Row("Cash", 250000)])
	results.append(_expect_throw(doc, "T1: single 2.5L cash invoice"))

	# 2. New invoice + prior receipts crossing limit
	_patch(existing=150000)
	doc = _StubDoc("CUST-001", [_Row("Cash", 60000)])
	results.append(_expect_throw(doc, "T2: 1.5L prior + 60k new = 2.1L"))

	# 3. New invoice + prior receipts exactly at limit
	_patch(existing=150000)
	doc = _StubDoc("CUST-001", [_Row("Cash", 50000)])
	results.append(_expect_throw(doc, "T3: 1.5L prior + 50k new = 2.0L (>= block)"))

	# 4. Within limit
	_patch(existing=100000)
	doc = _StubDoc("CUST-001", [_Row("Cash", 50000)])
	results.append(_expect_pass(doc, "T4: 1.0L prior + 50k new = 1.5L"))

	# 5. Non-cash payment ignored
	_patch(existing=0)
	doc = _StubDoc("CUST-001", [_Row("UPI", 500000)])
	results.append(_expect_pass(doc, "T5: 5L UPI (non-cash)"))

	# 6. Return invoice skipped
	_patch(existing=300000)
	doc = _StubDoc("CUST-001", [_Row("Cash", 250000)], is_return=1)
	results.append(_expect_pass(doc, "T6: return invoice skipped"))

	# 7. Flag bypass
	_patch(existing=300000)
	doc = _StubDoc("CUST-001", [_Row("Cash", 250000)])
	frappe.flags.ignore_cash_receipt_limit = True
	try:
		results.append(_expect_pass(doc, "T7: ignore_cash_receipt_limit flag"))
	finally:
		frappe.flags.ignore_cash_receipt_limit = False

	# 8. change_amount netted out
	_patch(existing=150000)
	doc = _StubDoc("CUST-001", [_Row("Cash", 70000)], change_amount=30000)
	# effective cash 40k + 150k = 190k → below 2L
	results.append(_expect_pass(doc, "T8: 70k cash − 30k change + 1.5L prior = 1.9L"))

	passed = sum(1 for r in results if r)
	total = len(results)
	print(f"\n— Section 269ST guard: {passed}/{total} cases passed —")
	if passed != total:
		raise frappe.ValidationError(f"{total - passed} test case(s) failed")
