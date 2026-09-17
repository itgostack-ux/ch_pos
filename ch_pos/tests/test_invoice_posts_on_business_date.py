"""A POS invoice posts on the store's trading day, at the server's clock time.

`inv.posting_date` was set from the session's business date and then thrown
away: ERPNext's `validate_posting_time` rewrites BOTH posting_date and
posting_time to `now_datetime()` unless `set_posting_time` is on, and nothing
set it. Every invoice carried the server date.

The rest of the system already assumed otherwise — ch_pos_settlement and
session_vs_payment_reconciliation both join `pi.posting_date = s.business_date`,
so those matched nothing the moment the two differed.

The date is a business decision; the time is not. posting_time stays the server
clock, and the business date itself can no longer be rolled backwards, so
neither half is movable by a user.
"""

import inspect
import unittest

import frappe
from frappe.utils import add_days, nowdate

from ch_pos.api.pos_api import _pos_posting_stamp


def _unused_profile():
    rows = frappe.db.sql(
        """
        SELECT s.pos_profile, s.name AS store, s.company FROM `tabCH Store` s
         WHERE IFNULL(s.pos_profile, '') <> ''
           AND NOT EXISTS (SELECT 1 FROM `tabCH POS Session` x
                            WHERE x.pos_profile = s.pos_profile
                              AND x.status IN ('Open', 'Locked') AND x.docstatus = 1)
         LIMIT 1
        """, as_dict=True)
    return rows[0] if rows else None


class TestPostingStamp(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_no_session_falls_back_to_the_server_date(self):
        date, _time = _pos_posting_stamp(None)
        self.assertEqual(date, nowdate())

    def test_it_returns_the_session_business_date(self):
        profile = _unused_profile()
        if not profile:
            self.skipTest("every profile already has an open session")
        yesterday = add_days(nowdate(), -1)
        session = frappe.get_doc({
            "doctype": "CH POS Session", "pos_profile": profile.pos_profile,
            "store": profile.store, "company": profile.company, "user": "Administrator",
            "business_date": yesterday, "status": "Open", "opening_cash": 0})
        session.flags.ignore_permissions = True
        session.flags.ignore_mandatory = True
        session.insert(ignore_permissions=True)
        session.db_set("docstatus", 1)
        session.db_set("status", "Open")

        date, time_ = _pos_posting_stamp(profile.pos_profile)
        self.assertEqual(date, str(yesterday), "the trading day did not reach the stamp")
        self.assertNotEqual(date, nowdate())
        # the moment of entry is not a business decision
        self.assertTrue(time_)

    def test_the_date_survives_erpnexts_rewrite(self):
        """The whole defect: without set_posting_time ERPNext replaces it."""
        yesterday = str(add_days(nowdate(), -1))

        kept = frappe.new_doc("Sales Invoice")
        kept.posting_date = yesterday
        kept.posting_time = "10:00:00"
        kept.set_posting_time = 1
        kept.run_method("validate_posting_time")
        self.assertEqual(str(kept.posting_date), yesterday)

        lost = frappe.new_doc("Sales Invoice")
        lost.posting_date = yesterday
        lost.run_method("validate_posting_time")
        self.assertEqual(str(lost.posting_date), nowdate(),
                         "if this ever stops rewriting, set_posting_time is no longer needed")


class TestEveryPosInvoiceUsesIt(unittest.TestCase):
    def test_all_four_builders_share_the_helper(self):
        src = inspect.getsource(frappe.get_module("ch_pos.api.pos_api"))
        self.assertEqual(src.count("_pos_posting_stamp(") - 1, 4,
                         "a POS invoice path is setting its own posting date")
        self.assertEqual(src.count("set_posting_time = 1"), 4,
                         "a builder sets the business date but lets ERPNext overwrite it")

    def test_repair_billing_shares_the_retail_trading_day(self):
        """A repair taken after midnight must not land on a different day's
        settlement from the sale beside it."""
        src = inspect.getsource(frappe.get_module("ch_pos.api.pos_api"))
        self.assertNotIn("inv.posting_date = nowdate()", src)

    def test_billing_on_a_stale_day_is_still_refused(self):
        """This is what bounds the change: posting_date can only ever be today
        or a still-open session's own day. Remove the guard and set_posting_time
        becomes a back-dating route."""
        from ch_pos.api import scope_guard

        src = inspect.getsource(scope_guard.assert_session_not_stale)
        self.assertIn("is_session_stale", src)
        self.assertNotIn("ch_pos.api.pos_api.", scope_guard._STALE_SESSION_EXEMPT_PREFIXES)
