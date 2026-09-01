# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Direct credit-note bypass guard — unit tests.

WHY: every POS return control (return window, free-item clawback,
maker-checker, refund tender rules) keys off ``return_against``; a credit
note with ``is_return=1`` and no link used to skip ALL of them. These tests
prove the validate hook now refuses that shape on the POS surface while
leaving back-office (non-POS) credit notes to native DocPerm control.

Pure validator tests — the doc is stubbed, nothing is written.
"""

from __future__ import annotations

import unittest

import frappe

from ch_pos.overrides import return_policy as rp


class _StubCreditNote:
	def __init__(self, **kwargs):
		self.name = kwargs.get("name", "CN-GUARD-TEST")
		self.is_return = kwargs.get("is_return", 1)
		self.return_against = kwargs.get("return_against")
		self.is_pos = kwargs.get("is_pos", 0)
		self.is_created_using_pos = kwargs.get("is_created_using_pos", 0)
		self.company = kwargs.get("company", "_Test Company")
		self.posting_date = kwargs.get("posting_date")

	def get(self, key, default=None):
		return getattr(self, key, default)


class TestDirectCreditNoteGuard(unittest.TestCase):
	def setUp(self):
		self._orig_days = rp._get_return_policy_days
		frappe.flags.ch_pos_allow_unlinked_credit_note = False

	def tearDown(self):
		rp._get_return_policy_days = self._orig_days
		frappe.flags.ch_pos_allow_unlinked_credit_note = False

	def test_pos_credit_note_without_return_against_is_refused(self):
		doc = _StubCreditNote(is_pos=1, return_against=None)
		with self.assertRaises(frappe.ValidationError):
			rp.validate_return_policy(doc)

	def test_pos_created_flag_alone_is_also_refused(self):
		# The POS billing surface stamps is_created_using_pos; unticking
		# is_pos must not reopen the hole.
		doc = _StubCreditNote(is_pos=0, is_created_using_pos=1, return_against=None)
		with self.assertRaises(frappe.ValidationError):
			rp.validate_return_policy(doc)

	def test_non_pos_credit_note_stays_with_native_controls(self):
		doc = _StubCreditNote(is_pos=0, return_against=None)
		rp.validate_return_policy(doc)  # must not raise

	def test_non_return_invoice_is_untouched(self):
		doc = _StubCreditNote(is_return=0, is_pos=1, return_against=None)
		rp.validate_return_policy(doc)  # must not raise

	def test_linked_pos_return_proceeds_to_window_check(self):
		# With return_against set the guard steps aside; a 0-day (unset)
		# policy window then allows the return.
		rp._get_return_policy_days = lambda company: 0
		doc = _StubCreditNote(is_pos=1, return_against="SI-ORIGINAL-0001")
		rp.validate_return_policy(doc)  # must not raise

	def test_explicit_code_flag_is_the_only_escape(self):
		doc = _StubCreditNote(is_pos=1, return_against=None)
		frappe.flags.ch_pos_allow_unlinked_credit_note = True
		try:
			rp.validate_return_policy(doc)  # must not raise
		finally:
			frappe.flags.ch_pos_allow_unlinked_credit_note = False


def load_tests(loader, tests, pattern):  # unittest protocol
	return loader.loadTestsFromTestCase(TestDirectCreditNoteGuard)
