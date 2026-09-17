"""An unconfigured override gate denies, and says who must configure it.

`require_configured_roles` guards seven capabilities, and every one of their
role settings is empty on this estate: resuming a colleague's session,
reopening a closed one, bypassing customer OTP on a buyback, selling an IMEI
out of FIFO order, reviewing someone else's free-sale approval, acting on a
customer with no transaction here, and redirecting an invoice.

Being locked out of all seven is a real complaint. Letting an empty gate
through is not the answer: "nobody may do this until you say who may" is loud
and fixable, while "anybody may, because nobody said who" is silent and looks
exactly like working software. Blank meaning nobody is also deliberate —
get_setting_roles returns defaults only for a field that was never set, and
patch v50 records the policy in as many words.

What was worth fixing is the wording: the refusal used to read as the user's
fault rather than a configuration gap.
"""

import inspect
import unittest

import frappe

from ch_pos.config import require_configured_roles

GATES = (
    "session_override_roles",
    "closed_session_reopen_roles",
    "invoice_recipient_override_roles",
    "free_sale_review_roles",
    "customer_override_roles",
    "buyback_otp_bypass_roles",
    "fifo_override_roles",
)


def _plain_user():
    from ch_pos.config import is_privileged_user

    for row in frappe.get_all("POS Executive", filters={"is_active": 1},
                              fields=["user"], limit=200):
        if not is_privileged_user(row.user) and frappe.db.get_value("User", row.user, "enabled"):
            return row.user
    return None


class TestUnconfiguredGatesDeny(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_an_empty_gate_refuses(self):
        user = _plain_user()
        if not user:
            self.skipTest("no non-privileged POS Executive on this site")
        original = frappe.session.user
        try:
            frappe.set_user(user)
            for gate in GATES:
                if frappe.db.exists("CH Role Link", {"parentfield": gate}):
                    continue  # configured here; nothing to prove
                with self.assertRaises(frappe.PermissionError, msg=f"{gate} let a user through"):
                    require_configured_roles(gate, action="do the thing")
        finally:
            frappe.set_user(original)

    def test_the_refusal_names_the_setting(self):
        user = _plain_user()
        if not user:
            self.skipTest("no non-privileged POS Executive on this site")
        original = frappe.session.user
        try:
            frappe.set_user(user)
            with self.assertRaises(frappe.PermissionError) as caught:
                require_configured_roles("fifo_override_roles", action="sell out of FIFO order")
            message = str(caught.exception)
            self.assertIn("fifo_override_roles", message)
            self.assertIn("CH POS Control Settings", message)
        finally:
            frappe.set_user(original)


class TestTheGuardItself(unittest.TestCase):
    def test_guest_is_refused_before_anything_else(self):
        src = inspect.getsource(require_configured_roles)
        self.assertIn("require_authenticated_user()", src)
        self.assertLess(src.index("require_authenticated_user()"),
                        src.index("has_configured_roles"))

    def test_it_never_returns_early_on_an_empty_role_set(self):
        """The regression this replaces: `if not roles: return`."""
        src = inspect.getsource(require_configured_roles)
        self.assertNotIn("if not roles:", src)
        # the only early return is the positive one
        self.assertEqual(src.count("\t\treturn\n"), 1)

    def test_the_signature_still_accepts_defaults(self):
        params = inspect.signature(require_configured_roles).parameters
        self.assertIn("defaults", params)
        self.assertIn("action", params)
