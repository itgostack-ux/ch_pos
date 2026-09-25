"""A manager PIN cannot approve the manager's own request.

`verify_manager_pin` proved a manager was present. It never compared that
manager to `frappe.session.user`, so anyone holding both an approver role and a
PIN could type their own PIN to approve their own discount, return, cash drop
or force-close. `frappe.session.user` appeared exactly once in the module — in
a log line.

That is live for most of the estate rather than a corner: of 94 active PIN
holders, 72 qualify as approvers only because the legacy `Ch POS User` profile
grants POS Manager to ordinary cashiers.

A supervisor override the operator can grant themselves is not an override.
SAP Retail and Oracle Xstore both require a different operator ID, and this
codebase already applies the rule to Closure Exceptions ("HO Admin can approve
but cannot self-approve their own CER") and states the intent for POS in
system_setup: cashiers "approve their own returns, cash variance or price
overrides" is the thing being prevented.
"""

import inspect
import unittest

import frappe

from ch_pos.pos_core.doctype.ch_manager_pin.ch_manager_pin import (
    SECOND_PERSON_PERMISSIONS,
    verify_manager_pin,
)


class TestSecondPersonRule(unittest.TestCase):
    def test_the_concession_approvals_all_need_a_second_person(self):
        for permission in ("can_approve_discount", "can_approve_return",
                           "can_approve_cash_drop", "can_force_close_session"):
            self.assertIn(permission, SECOND_PERSON_PERMISSIONS)

    def test_self_service_actions_are_deliberately_excluded(self):
        """Opening a till and rolling the day are proved by a code emailed to
        the person doing it; demanding a colleague there strands a store."""
        self.assertNotIn("can_approve_opening", SECOND_PERSON_PERMISSIONS)
        self.assertNotIn("can_override_business_date", SECOND_PERSON_PERMISSIONS)

    def test_the_check_compares_the_approver_to_the_caller(self):
        src = inspect.getsource(verify_manager_pin)
        self.assertIn("mgr.user == frappe.session.user", src)
        self.assertIn("SECOND_PERSON_PERMISSIONS", src)

    def test_there_is_no_privileged_exemption(self):
        """is_privileged_user short-circuits most gates here; an administrator
        approving their own cash drop is the hole this closes."""
        src = inspect.getsource(verify_manager_pin)
        block = src[src.index("SECOND_PERSON_PERMISSIONS and"):]
        block = block[:block.index("_clear_pin_failures")]
        self.assertNotIn("is_privileged_user", block)

    def test_the_refusal_says_what_to_do(self):
        """Not folded into the Invalid PIN catch-all: that vagueness exists to
        stop PIN enumeration, and you already know your own PIN.

        "Ask a colleague" was the original wording and is no longer enough on
        its own — at a one-person store there is no colleague, so it sent the
        only member of staff round a loop of retries until the rate limit shut
        the till's approvals for fifteen minutes. The refusal now either names
        who can approve, or says plainly that nobody here can and that the fix
        is configuration rather than another attempt.
        """
        src = inspect.getsource(verify_manager_pin)
        self.assertIn("needs a second person to approve", src)
        self.assertIn(
            "_other_eligible_approvers", src,
            "The refusal must name who can actually approve here.")
        self.assertIn(
            "no one else is set up", src,
            "A store with no second approver must be told that, not told to find "
            "a colleague who does not exist.")

    def test_the_refusal_is_audited(self):
        src = inspect.getsource(verify_manager_pin)
        self.assertIn("manager_pin_self_approval_blocked", src)


class TestLiveBehaviour(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_a_holder_cannot_use_their_own_pin_for_a_concession(self):
        row = frappe.db.sql(
            """
            SELECT p.user, p.name, p.store FROM `tabCH POS Password` p
              JOIN `tabUser` u ON u.name = p.user AND u.enabled = 1
             WHERE IFNULL(p.is_active, 0) = 1 AND IFNULL(p.can_approve_discount, 0) = 1
             LIMIT 1
            """, as_dict=True)
        if not row:
            self.skipTest("no active PIN holder with discount approval")
        holder = row[0]
        try:
            pin = frappe.get_doc("CH POS Password", holder.name).get_password("pin")
        except Exception:  # noqa: BLE001 — either cause skips this test
            # Rows encrypted under another site's key, or never set — the
            # module handles both, and neither is what this test is about.
            pin = None
        if not pin:
            self.skipTest("PIN not readable on this site (different encryption key)")

        original = frappe.session.user
        try:
            frappe.set_user(holder.user)
            result = verify_manager_pin(
                pin, store=holder.store, permission="can_approve_discount")
            self.assertFalse(result.get("valid"),
                             "a holder approved their own discount with their own PIN")
            self.assertIn("second person", result.get("message", ""))
        finally:
            frappe.set_user(original)
