# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""POS Executive row-level scope — unit + live-user tests.

WHY: POS Executive rows (store, discount ceiling, till assignment) were
visible bench-wide to any role with a matching DocPerm — a Bestbuy-scoped
user could list every GOFIX executive. These tests prove the new
permission_query_conditions + has_permission pair (ch_pos.scope, following
the ch_erp15.txn_scope precedent) is fail-closed:

* bypass users keep full visibility;
* scoped users only see rows of their resolved stores (plus store-less
  rows of a directly granted company);
* users with no scope see nothing;
* the known scoped user kevin@gmail.com can no longer list or open
  out-of-scope rows (read-only assertions, rolled back).
"""

from __future__ import annotations

import unittest

import frappe

from ch_pos import scope as pos_scope

_KEVIN = "kevin@gmail.com"


def _fake_scope(**kwargs):
	base = {
		"bypass": False,
		"stores": set(),
		"direct_companies": set(),
	}
	base.update(kwargs)
	return base


class TestPosExecutiveScopeLogic(unittest.TestCase):
	"""Pure-logic tests with get_user_scope stubbed out."""

	def setUp(self):
		self._orig = pos_scope.get_user_scope

	def tearDown(self):
		pos_scope.get_user_scope = self._orig

	def test_bypass_user_gets_no_filter(self):
		pos_scope.get_user_scope = lambda user: _fake_scope(bypass=True)
		self.assertIsNone(pos_scope.pos_executive_query("someone@x.com"))
		doc = frappe._dict(store="ANY", company="ANY")
		self.assertTrue(pos_scope.has_pos_executive_permission(doc, "read", "someone@x.com"))

	def test_scoped_user_is_filtered_to_own_stores(self):
		pos_scope.get_user_scope = lambda user: _fake_scope(stores={"GG-KELLYS"})
		clause = pos_scope.pos_executive_query("someone@x.com")
		self.assertIn("`tabPOS Executive`.`store` IN ('GG-KELLYS')", clause)
		self.assertTrue(pos_scope.has_pos_executive_permission(
			frappe._dict(store="GG-KELLYS", company="X"), "read", "someone@x.com"))
		self.assertFalse(pos_scope.has_pos_executive_permission(
			frappe._dict(store="GF-VEPERY", company="Y"), "read", "someone@x.com"))

	def test_no_scope_fails_closed(self):
		pos_scope.get_user_scope = lambda user: _fake_scope()
		self.assertEqual(pos_scope.pos_executive_query("someone@x.com"), "1=0")
		self.assertFalse(pos_scope.has_pos_executive_permission(
			frappe._dict(store="GG-KELLYS", company="X"), "read", "someone@x.com"))

	def test_storeless_row_visible_only_via_direct_company_grant(self):
		pos_scope.get_user_scope = lambda user: _fake_scope(
			stores={"GG-KELLYS"}, direct_companies={"Bestbuy Mobiles Private Limited"})
		self.assertTrue(pos_scope.has_pos_executive_permission(
			frappe._dict(store=None, company="Bestbuy Mobiles Private Limited"),
			"read", "someone@x.com"))
		self.assertFalse(pos_scope.has_pos_executive_permission(
			frappe._dict(store=None, company="GOFIX SOLUTIONS PRIVATE LIMITED"),
			"read", "someone@x.com"))
		clause = pos_scope.pos_executive_query("someone@x.com")
		self.assertIn("COALESCE(`tabPOS Executive`.`store`, '') = ''", clause)


class TestPosExecutiveScopeLive(unittest.TestCase):
	"""End-to-end as the known scoped user — read-only, rolled back."""

	@classmethod
	def setUpClass(cls):
		if not frappe.db.exists("User", _KEVIN):
			raise unittest.SkipTest(f"{_KEVIN} not present in this database")
		from ch_erp15.ch_erp15.scope import get_user_scope

		cls.kevin_scope = get_user_scope(_KEVIN)
		if cls.kevin_scope.get("bypass"):
			raise unittest.SkipTest(f"{_KEVIN} has bypass — cannot exercise scoping")

	def setUp(self):
		self._session_user = frappe.session.user

	def tearDown(self):
		frappe.set_user(self._session_user)
		frappe.db.rollback()

	def test_kevin_list_only_contains_in_scope_stores(self):
		allowed_stores = set(self.kevin_scope.get("stores") or set())
		direct_companies = set(self.kevin_scope.get("direct_companies") or set())
		frappe.set_user(_KEVIN)
		rows = frappe.get_list(
			"POS Executive", fields=["name", "store", "company"], limit=0)
		self.assertTrue(rows, "kevin should still see his own stores' executives")
		for row in rows:
			if row.store:
				self.assertIn(
					row.store, allowed_stores,
					f"{row.name} leaked: store {row.store} is outside kevin's scope")
			else:
				self.assertIn(
					row.company, direct_companies,
					f"{row.name} leaked: store-less row of foreign company {row.company}")

	def test_kevin_cannot_open_out_of_scope_row_by_name(self):
		allowed_stores = set(self.kevin_scope.get("stores") or set())
		foreign = frappe.get_all(
			"POS Executive",
			filters={"store": ("not in", list(allowed_stores))},
			fields=["name", "store"],
			limit=50,
		)
		foreign = [r for r in foreign if r.store and r.store not in allowed_stores]
		if not foreign:
			self.skipTest("no out-of-scope POS Executive rows to test against")
		target = foreign[0]
		self.assertFalse(
			frappe.has_permission(
				"POS Executive", ptype="read",
				doc=frappe.get_doc("POS Executive", target.name), user=_KEVIN),
			f"kevin must not read {target.name} (store {target.store})")

	def test_kevin_can_still_open_in_scope_row(self):
		allowed_stores = list(self.kevin_scope.get("stores") or set())
		own = frappe.get_all(
			"POS Executive",
			filters={"store": ("in", allowed_stores)},
			fields=["name"],
			limit=1,
		)
		if not own:
			self.skipTest("no in-scope POS Executive rows for kevin")
		self.assertTrue(
			frappe.has_permission(
				"POS Executive", ptype="read",
				doc=frappe.get_doc("POS Executive", own[0].name), user=_KEVIN))


def load_tests(loader, tests, pattern):  # unittest protocol
	suite = unittest.TestSuite()
	for cls in (TestPosExecutiveScopeLogic, TestPosExecutiveScopeLive):
		suite.addTests(loader.loadTestsFromTestCase(cls))
	return suite
