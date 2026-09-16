"""Rolling the business date as the person who actually rolls it.

A store executive holds no manager role by design — that is the whole point of
the System Manager cleanup. `advance_business_date` nonetheless asked for the
`CH Business Date` write DocPerm, which only System Manager has, so the two
paths built for executives both died on it:

  * the day-roll dialog, *after* the emailed code had already been accepted —
    "User X does not have doctype access via role permission for document
    CH Business Date";
  * the automatic advance at end of day, which runs inside `close_session` and
    would have taken the whole close down with it.

The saves underneath were already `ignore_permissions=True`, so the check was
never protecting the write — it was a second, contradictory gate sitting on top
of authorisation that had already happened.

These tests pin both halves: an authorised caller gets through, and an
unauthorised one still does not.
"""

import inspect
import unittest

import frappe

from ch_pos.api import session_api
from ch_pos.config import is_privileged_user
from ch_pos.pos_core.doctype.ch_business_date.ch_business_date import advance_business_date


def _plain_executive():
    """An enabled, active POS Executive who bypasses nothing.

    Chosen for the *absence* of privilege — on a site where most executives
    still hold System Manager, any other subject proves nothing.
    """
    rows = frappe.db.sql(
        """
        SELECT e.user, e.store
          FROM `tabPOS Executive` e
          JOIN `tabUser` u ON u.name = e.user AND u.enabled = 1
          JOIN `tabCH Business Date` b ON b.store = e.store
         WHERE e.is_active = 1
         LIMIT 200
        """,
        as_dict=True)
    for row in rows:
        if not is_privileged_user(row.user):
            return row
    return None


class TestAuthorisedCallerCanRollTheDay(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_the_premise_holds_an_executive_has_no_docperm(self):
        """If this ever starts passing, the fix below is moot — and worse, the
        day roll would be open to every POS user with no code at all."""
        subject = _plain_executive()
        if not subject:
            self.skipTest("no non-privileged POS Executive at a dated store")
        original = frappe.session.user
        try:
            frappe.set_user(subject.user)
            self.assertFalse(
                frappe.has_permission("CH Business Date", "write"),
                "CH Business Date write is meant to stay System-Manager-only")
        finally:
            frappe.set_user(original)

    def test_authorised_advance_succeeds_for_an_executive(self):
        subject = _plain_executive()
        if not subject:
            self.skipTest("no non-privileged POS Executive at a dated store")
        current = frappe.db.get_value("CH Business Date", subject.store, "business_date")
        original = frappe.session.user
        try:
            frappe.set_user(subject.user)
            result = advance_business_date(
                subject.store, current, reason="regression: authorised roll",
                manager_user=subject.user, authorised=True)
            self.assertEqual(str(result.get("business_date")), str(current))
        finally:
            frappe.set_user(original)

    def test_unauthorised_advance_is_still_refused(self):
        """The default stays closed: a future caller that forgets to pass the
        flag must be denied, not waved through."""
        subject = _plain_executive()
        if not subject:
            self.skipTest("no non-privileged POS Executive at a dated store")
        current = frappe.db.get_value("CH Business Date", subject.store, "business_date")
        original = frappe.session.user
        try:
            frappe.set_user(subject.user)
            with self.assertRaises(frappe.PermissionError):
                advance_business_date(
                    subject.store, current, reason="regression: unauthorised roll",
                    manager_user=subject.user)
        finally:
            frappe.set_user(original)


class TestOverrideContract(unittest.TestCase):
    """Cheap source pins — they catch a silent revert of either call site."""

    def test_override_marks_itself_authorised(self):
        src = inspect.getsource(session_api.override_business_date)
        self.assertIn("authorised=True", src)
        # and only after one of the two proofs has been consumed
        self.assertLess(src.index("consume_action_grant"), src.index("authorised=True"))

    def test_eod_auto_advance_marks_itself_authorised(self):
        self.assertIn(
            "authorised=True",
            inspect.getsource(session_api._auto_advance_business_date_after_eod))

    def test_the_response_does_not_assume_the_pin_branch_ran(self):
        """`pin_result` only exists when a manager PIN was used; naming it in
        the shared return raised UnboundLocalError on every OTP roll."""
        src = inspect.getsource(session_api.override_business_date)
        tail = src[src.rindex("return {"):]
        self.assertNotIn("pin_result", tail)
