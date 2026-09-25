"""Cash over/short tolerance — the one place the bands are decided.

A flat rupee threshold is the wrong control for a chain whose tills range from
a kiosk taking a few hundred rupees a day to a store taking six figures. The
previous model was a single ``variance_approval_threshold`` of Rs 100 applied
to every till, and it was also **hard-coded a second time in the POS
JavaScript** — so the figure the cashier was shown and the figure the server
enforced could differ, and on a till that took Rs 92,400 in a day the tolerance
was 0.1%: mathematically certain to breach on any real count.

The model here is the one Oracle Xstore ("over/short thresholds"), SAP Retail
("tender declaration difference limits") and Dynamics 365 Commerce ("shift
close tolerance, amount or percentage") all use — a band, not a line:

    |variance| <= accept_limit      accepted, no friction
    accept_limit < |v| <= approval  a reason is required (the "warn" band)
    |variance| > approval_limit     reason AND a manager's approval

Each limit is ``max(fixed amount, percent of the cash that moved)`` so the
small till keeps a sensible absolute floor and the big till gets a tolerance
proportional to what it handled.

Every consumer — settlement validation, session close, and the POS UI via
``get_settlement_policy`` — reads these numbers from here. Nothing recomputes
them, and nothing hard-codes a threshold again.
"""

import frappe
from frappe.utils import cint, flt

from ch_pos.config import get_control_setting

#: Band defaults. Deliberately conservative: the accept band is roughly a
#: miscounted note or two, the approval band is real money. Both are editable
#: in CH POS Control Settings without a code change.
DEFAULT_ACCEPT_AMOUNT = 200.0
DEFAULT_ACCEPT_PERCENT = 0.5
DEFAULT_APPROVAL_AMOUNT = 1000.0
DEFAULT_APPROVAL_PERCENT = 2.0


def get_variance_policy(cash_basis=0) -> dict:
    """Resolve the over/short bands for a till that handled ``cash_basis``.

    ``cash_basis`` is total cash accountability — the opening float plus cash
    taken over the counter — not the expected closing balance. A till that
    banks its takings through cash drops during the day has a small expected
    close and a large exposure, and it is the exposure the tolerance should
    track.
    """
    basis = abs(flt(cash_basis))

    accept_amount = flt(get_control_setting("variance_accept_amount", DEFAULT_ACCEPT_AMOUNT))
    accept_percent = flt(get_control_setting("variance_accept_percent", DEFAULT_ACCEPT_PERCENT))
    approval_amount = flt(
        get_control_setting("variance_approval_threshold", DEFAULT_APPROVAL_AMOUNT)
    )
    approval_percent = flt(
        get_control_setting("variance_approval_percent", DEFAULT_APPROVAL_PERCENT)
    )

    accept_limit = max(accept_amount, basis * accept_percent / 100.0)
    approval_limit = max(approval_amount, basis * approval_percent / 100.0)

    # A misconfiguration that put the approval band below the accept band would
    # create a variance that is simultaneously auto-accepted and blocked. Clamp
    # rather than throw: the till must never be un-closable because of a typo in
    # a settings form.
    approval_limit = max(approval_limit, accept_limit)

    return {
        "cash_basis": basis,
        "accept_limit": round(accept_limit, 2),
        "approval_limit": round(approval_limit, 2),
        "blind_close": bool(cint(get_control_setting("blind_close", 1))),
        "accept_amount": accept_amount,
        "accept_percent": accept_percent,
        "approval_amount": approval_amount,
        "approval_percent": approval_percent,
    }


def classify_variance(variance, cash_basis=0) -> dict:
    """Return what a given variance demands of the person closing the till."""
    policy = get_variance_policy(cash_basis)
    magnitude = abs(flt(variance))

    if magnitude <= policy["accept_limit"]:
        band = "accepted"
    elif magnitude <= policy["approval_limit"]:
        band = "reason"
    else:
        band = "approval"

    return {
        **policy,
        "variance": flt(variance),
        "band": band,
        "requires_reason": band in ("reason", "approval"),
        "requires_approval": band == "approval",
    }


def variance_band_message(verdict: dict) -> str:
    """One line a cashier can act on, with the limit that was actually applied."""
    from frappe import _

    currency = frappe.utils.fmt_money
    if verdict["band"] == "approval":
        return _(
            "Cash variance {0} is above the {1} approval limit for a till that handled {2}. "
            "A manager must approve this close."
        ).format(
            currency(abs(verdict["variance"])),
            currency(verdict["approval_limit"]),
            currency(verdict["cash_basis"]),
        )
    return _(
        "Cash variance {0} is above the {1} auto-accept limit. Record why before closing."
    ).format(currency(abs(verdict["variance"])), currency(verdict["accept_limit"]))
