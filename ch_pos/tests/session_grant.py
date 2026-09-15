"""Mint the session-open grant that the email-OTP step issues.

Opening a till now requires a single-use grant from
``verify_session_open_otp``. These suites are not exercising email delivery —
``test_session_open_otp`` covers that end to end — they just need a session,
so they take the same token the OTP step would hand back.
"""

import frappe

from ch_pos.api.manager_approval import issue_action_grant


def test_session_grant(pos_profile, user=None) -> str:
    store = frappe.db.get_value(
        "POS Profile Extension", {"pos_profile": pos_profile}, "store")
    if not store:
        warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
        if warehouse:
            store = frappe.db.get_value("CH Store", {"warehouse": warehouse}, "name")
    return issue_action_grant(
        "session_open",
        {
            "user": user or frappe.session.user,
            "store": store,
            "pos_profile": pos_profile,
        },
    )
