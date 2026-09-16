"""Email-OTP session open, and same-store session sharing.

Opening a till used to need a manager PIN, which proves that *a* manager
approved and never which person was standing there — PINs get shared. The
opener now enters a code sent to their own inbox, so `opening_approved_by`
is a real identity.

These tests pin the parts that fail quietly: that a code is never mailed to
someone who could not open the till anyway, that a grant cannot be replayed,
and that sharing a session stops at the store boundary.
"""

import inspect
import unittest
from unittest.mock import patch

import frappe

from ch_pos.api import manager_approval as ma
from ch_pos.api.session_api import open_session
from ch_pos.config import assert_session_operator


class TestSessionOpenContract(unittest.TestCase):
    """Shape checks — cheap, and they catch a silent revert."""

    def test_open_session_takes_a_grant_and_keeps_the_pin_fallback(self):
        """Both are accepted on purpose.

        Python and the JS bundle deploy separately: a till still running the
        previous bundle can only post manager_pin, and refusing that took the
        whole estate offline. The OTP grant stays the intended path; the PIN
        is the compatibility fallback.
        """
        params = inspect.signature(open_session).parameters
        self.assertIn("session_grant", params)
        self.assertIn("manager_pin", params)

    def test_open_session_refuses_when_neither_is_supplied(self):
        src = inspect.getsource(open_session)
        # the else branch that throws must still exist
        self.assertIn("Verification Required", src)
        self.assertIn("verify_manager_pin", src)

    def test_open_session_consumes_the_grant(self):
        src = inspect.getsource(open_session)
        self.assertIn("consume_action_grant", src)
        self.assertIn("session_open", src)

    def test_grant_is_restored_if_the_insert_rolls_back(self):
        """A failed open must not burn the cashier's code."""
        self.assertIn("restore_on_rollback=True", inspect.getsource(open_session))

    def test_otp_is_bound_to_the_opener_not_a_manager(self):
        src = inspect.getsource(ma.request_session_open_otp)
        self.assertIn("frappe.session.user", src)
        # entitlement is checked before anything is sent
        self.assertLess(src.index("assert_pos_executive"), src.index("generate_otp"))

    def test_verify_rechecks_entitlement(self):
        """Authorisation can be withdrawn between send and verify."""
        self.assertIn("assert_pos_executive", inspect.getsource(ma.verify_session_open_otp))

    def test_email_is_masked_in_responses(self):
        self.assertEqual(ma._mask_email("jsmith@example.com"), "j****h@example.com")
        self.assertEqual(ma._mask_email("ab@x.io"), "a*@x.io")
        self.assertEqual(ma._mask_email(""), "your registered email")

    def test_no_second_rate_limiter_was_added(self):
        """CH OTP Log already rate-limits per identity; a second one would
        silently halve the real allowance."""
        src = inspect.getsource(ma.request_session_open_otp)
        self.assertNotIn("increment_fixed_window", src)


class TestSessionSharing(unittest.TestCase):
    """A colleague at the same store may pick up an open till."""

    def tearDown(self):
        frappe.db.rollback()

    def _session(self, user, store):
        return frappe._dict({"user": user, "store": store, "get": lambda k, d=None: {"user": user, "store": store}.get(k, d)})

    def test_owner_always_passes(self):
        s = self._session(frappe.session.user, "ANY-STORE")
        assert_session_operator(s, "test")  # must not raise

    def test_same_store_executive_may_join(self):
        row = frappe.get_all(
            "POS Executive", filters={"is_active": 1}, fields=["user", "store"], limit=1)
        if not row:
            self.skipTest("no active POS Executive on this site")
        exec_row = row[0]
        original = frappe.session.user
        try:
            frappe.set_user(exec_row.user)
            # a session opened by somebody else, at the store they work
            s = self._session("someone.else@example.com", exec_row.store)
            assert_session_operator(s, "resume another cashier's POS session")
        finally:
            frappe.set_user(original)

    def test_other_store_is_still_refused(self):
        """The store boundary only binds a non-privileged user.

        System Manager short-circuits every gate, and on this estate 92 of 95
        POS executives hold it — so the subject must be chosen for the absence
        of that role, or the test proves nothing.
        """
        from ch_pos.config import is_privileged_user

        rows = frappe.db.sql(
            """SELECT DISTINCT user, store FROM `tabPOS Executive` WHERE is_active=1 LIMIT 200""",
            as_dict=True)
        stores = {r.store for r in rows}
        if len(stores) < 2:
            self.skipTest("need two stores to prove the boundary")

        subject = foreign_store = None
        for row in rows:
            if is_privileged_user(row.user):
                continue
            if not frappe.db.get_value("User", row.user, "enabled"):
                continue
            other = next(
                (s for s in stores
                 if s != row.store
                 and not frappe.db.exists("POS Executive",
                                          {"user": row.user, "store": s, "is_active": 1})),
                None)
            if other:
                subject, foreign_store = row, other
                break
        if not subject:
            self.skipTest("every POS executive here is privileged — nothing to bind")

        original = frappe.session.user
        try:
            frappe.set_user(subject.user)
            s = self._session("someone.else@example.com", foreign_store)
            with self.assertRaises(frappe.PermissionError):
                assert_session_operator(s, "resume another cashier's POS session")
        finally:
            frappe.set_user(original)


class TestGrantSingleUse(unittest.TestCase):
    """The grant is the whole security boundary — it must not be replayable."""

    def tearDown(self):
        frappe.db.rollback()

    def test_grant_verifies_once_then_is_gone(self):
        payload = {"user": frappe.session.user, "store": "S1", "pos_profile": "P1"}
        token = ma.issue_action_grant("session_open", payload)
        first = ma.consume_action_grant("session_open", token, expected=payload)
        self.assertEqual(first["store"], "S1")
        with self.assertRaises(frappe.PermissionError):
            ma.consume_action_grant("session_open", token, expected=payload)

    def test_grant_will_not_open_a_different_store(self):
        token = ma.issue_action_grant(
            "session_open", {"user": frappe.session.user, "store": "S1", "pos_profile": "P1"})
        with self.assertRaises(frappe.PermissionError):
            ma.consume_action_grant(
                "session_open", token,
                expected={"user": frappe.session.user, "store": "S2", "pos_profile": "P1"})

    def test_missing_grant_is_refused(self):
        with self.assertRaises(frappe.PermissionError):
            ma.consume_action_grant("session_open", None, expected={})


class TestOtpDelivery(unittest.TestCase):
    """The code must actually be mailed, and only to the entitled opener."""

    def tearDown(self):
        frappe.db.rollback()

    def test_a_code_is_mailed_to_the_opener(self):
        # Pick an executive whose store actually maps to a POS Profile —
        # plenty of stores on this estate do not, and skipping on the first
        # one would quietly stop testing the thing that matters.
        pair = frappe.db.sql(
            """
            SELECT e.user, e.store, x.pos_profile
              FROM `tabPOS Executive` e
              JOIN `tabPOS Profile Extension` x ON x.store = e.store
              JOIN `tabUser` u ON u.name = e.user AND u.enabled = 1
              JOIN `tabPOS Profile` p ON p.name = x.pos_profile AND IFNULL(p.disabled,0) = 0
             WHERE e.is_active = 1 AND IFNULL(x.pos_profile,'') <> ''
             LIMIT 1
            """,
            as_dict=True)
        if not pair:
            self.skipTest("no active POS Executive at a profile-mapped store")
        row, profile = pair[0], pair[0].pos_profile

        original = frappe.session.user
        sent = {}
        try:
            frappe.set_user(row.user)

            def capture(**kwargs):
                sent.update(kwargs)

            with patch.object(frappe, "sendmail", capture):
                result = ma.request_session_open_otp(profile)

            self.assertTrue(result["sent"])
            self.assertIn("@", result["sent_to"])
            self.assertIn("*", result["sent_to"], "the address must be masked")
            self.assertTrue(sent, "no email was dispatched")
            self.assertEqual(sent["recipients"], [frappe.session.user])
            # the code itself must never appear in the API response
            self.assertNotIn("otp", {k.lower() for k in result})
        finally:
            frappe.set_user(original)
