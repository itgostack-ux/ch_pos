# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Regression tests for the POS ledger-integrity backstop (C1 incident).

WHY: 26 live POS Sales Invoices (2026-07-20..30) were submitted with SLE
but missing payment and COGS GL legs — a hand-rolled post-submit GL rewrite
(commit f77ee0c) deleted the framework's entries and rebuilt only the
Debtors/Income/Tax legs, swallowing every failure. These tests prove:

1. the backstop THROWS on exactly that footprint (missing settlement leg,
   missing COGS leg, empty GL) and passes a complete one;
2. it flags the real defective July invoices still in this database while
   passing the healthy post-fix ones (read-only — no data is repaired);
3. today's `_rewrite_gl_entries` reposts through the accounting framework
   under a savepoint instead of raw-deleting GL, so the original silent
   half-post path no longer exists;
4. the backstop is actually wired: last Sales Invoice on_submit doc_event
   and called again after the pipeline's GL rewrite.

Run:
    bench --site erpnext.local console, then unittest-load this module.
"""

from __future__ import annotations

import inspect
import unittest

import frappe

from ch_pos.overrides import gl_backstop as gb


class _Item:
	def __init__(self, expense_account):
		self.expense_account = expense_account

	def get(self, key, default=None):
		return getattr(self, key, default)


class _StubInvoice:
	"""Minimal submitted POS Sales Invoice stand-in."""

	def __init__(self, **kwargs):
		self.name = kwargs.get("name", "SI-BACKSTOP-TEST")
		self.docstatus = kwargs.get("docstatus", 1)
		self.is_pos = kwargs.get("is_pos", 1)
		self.is_return = kwargs.get("is_return", 0)
		self.paid_amount = kwargs.get("paid_amount", 0)
		self.update_stock = kwargs.get("update_stock", 0)
		self.debit_to = kwargs.get("debit_to", "Debtors - BM")
		self.items = kwargs.get("items", [])

	def get(self, key, default=None):
		return getattr(self, key, default)


def _rows(*triples):
	return [frappe._dict(account=a, debit=d, credit=c) for (a, d, c) in triples]


class TestGLBackstopLogic(unittest.TestCase):
	"""Pure-logic tests: GL/SLE lookups are stubbed, nothing touches data."""

	def setUp(self):
		self._orig_gl_rows = gb._gl_rows
		self._orig_svm = gb._stock_value_moved

	def tearDown(self):
		gb._gl_rows = self._orig_gl_rows
		gb._stock_value_moved = self._orig_svm

	def _patch(self, rows, stock_value=0.0):
		gb._gl_rows = lambda name: rows
		gb._stock_value_moved = lambda name: stock_value

	def test_half_post_missing_payment_leg_is_refused(self):
		# The exact July-2026 footprint: receivable + income + tax legs only.
		self._patch(_rows(
			("Debtors - BM", 199.0, 0),
			("Sales - BM", 0, 168.64),
			("Output Tax CGST - BM", 0, 15.18),
			("Output Tax SGST - BM", 0, 15.18),
		))
		doc = _StubInvoice(paid_amount=199.0)
		with self.assertRaises(frappe.ValidationError):
			gb.assert_pos_ledger_integrity(doc)

	def test_completely_empty_gl_is_refused(self):
		self._patch([])
		doc = _StubInvoice(paid_amount=250.0)
		with self.assertRaises(frappe.ValidationError):
			gb.assert_pos_ledger_integrity(doc)

	def test_missing_cogs_leg_is_refused_when_stock_value_moved(self):
		self._patch(_rows(
			("Debtors - BM", 199.0, 0),
			("Sales - BM", 0, 199.0),
			("Cash - BM", 199.0, 0),
			("Debtors - BM", 0, 199.0),
		), stock_value=120.0)
		doc = _StubInvoice(
			paid_amount=199.0,
			update_stock=1,
			items=[_Item("Cost of Goods Sold - BM")],
		)
		with self.assertRaises(frappe.ValidationError):
			gb.assert_pos_ledger_integrity(doc)

	def test_complete_footprint_passes(self):
		self._patch(_rows(
			("Debtors - BM", 199.0, 0),
			("Sales - BM", 0, 168.64),
			("Output Tax CGST - BM", 0, 15.18),
			("Output Tax SGST - BM", 0, 15.18),
			("Cash - BM", 199.0, 0),
			("Debtors - BM", 0, 199.0),
			("Cost of Goods Sold - BM", 120.0, 0),
			("Stock In Hand - BM", 0, 120.0),
		), stock_value=120.0)
		doc = _StubInvoice(
			paid_amount=199.0,
			update_stock=1,
			items=[_Item("Cost of Goods Sold - BM")],
		)
		gb.assert_pos_ledger_integrity(doc)  # must not raise

	def test_zero_valuation_stock_does_not_false_positive(self):
		# Items at valuation_rate 0 move qty but no value — no stock GL is
		# correct there and must not be flagged.
		self._patch(_rows(
			("Debtors - BM", 199.0, 0),
			("Sales - BM", 0, 199.0),
			("Cash - BM", 199.0, 0),
			("Debtors - BM", 0, 199.0),
		), stock_value=0.0)
		doc = _StubInvoice(
			paid_amount=199.0,
			update_stock=1,
			items=[_Item("Cost of Goods Sold - BM")],
		)
		gb.assert_pos_ledger_integrity(doc)  # must not raise

	def test_unpaid_and_non_pos_invoices_are_ignored(self):
		self._patch([])
		gb.assert_pos_ledger_integrity(_StubInvoice(paid_amount=0))
		gb.assert_pos_ledger_integrity(_StubInvoice(is_pos=0, paid_amount=500.0))
		gb.assert_pos_ledger_integrity(_StubInvoice(docstatus=0, paid_amount=500.0))

	def test_return_with_refund_needs_both_debtor_sides(self):
		# Refunded return: Cr Debtors (receivable) + Dr Debtors (refund).
		self._patch(_rows(
			("Debtors - BM", 0, 500.0),
			("Sales - BM", 500.0, 0),
			("Debtors - BM", 500.0, 0),
			("Cash - BM", 0, 500.0),
		))
		gb.assert_pos_ledger_integrity(
			_StubInvoice(is_return=1, paid_amount=-500.0))
		# Same return missing the refund legs must be refused.
		self._patch(_rows(
			("Debtors - BM", 0, 500.0),
			("Sales - BM", 500.0, 0),
		))
		with self.assertRaises(frappe.ValidationError):
			gb.assert_pos_ledger_integrity(
				_StubInvoice(is_return=1, paid_amount=-500.0))

	def test_maintenance_skip_flag_is_honoured(self):
		self._patch([])
		frappe.flags.ch_pos_skip_gl_backstop = True
		try:
			gb.assert_pos_ledger_integrity(_StubInvoice(paid_amount=199.0))
		finally:
			frappe.flags.ch_pos_skip_gl_backstop = False


class TestGLBackstopAgainstRealDefect(unittest.TestCase):
	"""Prove detection on the actual C1 rows, read-only (no data repair)."""

	def _load(self, name):
		if not frappe.db.exists("Sales Invoice", name):
			self.skipTest(f"{name} not present in this database")
		return frappe.get_doc("Sales Invoice", name)

	def test_flags_a_defective_july_invoice(self):
		doc = self._load("BMTNSI26000088")
		with self.assertRaises(frappe.ValidationError):
			gb.assert_pos_ledger_integrity(doc)

	def test_flags_the_first_defective_july_invoice(self):
		# BMTNSI26000007 opened the defect window (2026-07-20): SLE present,
		# only Debtors/Sales/CGST/SGST GL legs, no payment and no COGS legs.
		doc = self._load("BMTNSI26000007")
		with self.assertRaises(frappe.ValidationError):
			gb.assert_pos_ledger_integrity(doc)

	def test_passes_a_healthy_post_fix_invoice(self):
		doc = self._load("BMTNSI26000089")
		gb.assert_pos_ledger_integrity(doc)  # must not raise


class TestGLRewritePathIsFrameworkRepost(unittest.TestCase):
	"""The original defect must stay dead in the submit path itself."""

	def test_rewrite_gl_entries_reposts_through_framework(self):
		from ch_pos.api import pos_api

		src = inspect.getsource(pos_api._rewrite_gl_entries)
		# The Jul-15 version raw-deleted GL and committed before rebuilding.
		self.assertNotIn("DELETE FROM `tabGL Entry`", src)
		self.assertNotIn("frappe.db.commit()", src)
		# Today's version must delete only under a savepoint and rebuild via
		# the accounting framework, re-raising on failure.
		self.assertIn("savepoint", src)
		self.assertIn("_delete_accounting_ledger_entries", src)
		self.assertIn("from_repost=True", src)
		self.assertIn("raise", src)

	def test_backstop_is_wired_on_submit_and_after_rewrite(self):
		from ch_pos import hooks as ch_pos_hooks
		from ch_pos.api import pos_api

		on_submit = ch_pos_hooks.doc_events["Sales Invoice"]["on_submit"]
		self.assertEqual(
			on_submit[-1],
			"ch_pos.overrides.gl_backstop.assert_pos_ledger_integrity",
			"backstop must run LAST on Sales Invoice on_submit",
		)
		create_src = inspect.getsource(pos_api.create_pos_invoice)
		self.assertIn("assert_pos_ledger_integrity", create_src)


def load_tests(loader, tests, pattern):  # unittest protocol
	suite = unittest.TestSuite()
	for cls in (
		TestGLBackstopLogic,
		TestGLBackstopAgainstRealDefect,
		TestGLRewritePathIsFrameworkRepost,
	):
		suite.addTests(loader.loadTestsFromTestCase(cls))
	return suite
