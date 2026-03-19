"""CH Cash Drop — tracks all cash movements in/out of register during a session.

Types: Cash Drop, Petty Expense, Cash Adjustment, Refund Payout, Buyback Cash Payout

Rules:
- Must link to an active open session
- Affects expected cash in settlement
- Sensitive types require manager approval
- No cross-company movement
- No post-close cash movement
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

# Types that require manager approval
APPROVAL_REQUIRED_TYPES = {"Petty Expense", "Cash Adjustment", "Buyback Cash Payout"}


class CHCashDrop(Document):
    def validate(self):
        self._validate_session_active()
        self._validate_company_match()
        self._validate_amount()
        self._validate_approval()

    def on_submit(self):
        self.db_set("status", "Approved" if self.approved_by else "Submitted")
        self._log_event()

    def on_cancel(self):
        self.db_set("status", "Cancelled")

    def _validate_session_active(self):
        """Cash movement must link to an active open session."""
        if not self.session:
            frappe.throw(_("Session is required for cash movement."))
        session_status = frappe.db.get_value("CH POS Session", self.session, "status")
        if session_status not in ("Open", "Locked"):
            frappe.throw(
                _("Cash movement can only be created on an Open or Locked session. "
                  "Session {0} status is {1}.").format(self.session, session_status)
            )

    def _validate_company_match(self):
        """No cross-company cash movement."""
        if self.session and self.company:
            session_company = frappe.db.get_value("CH POS Session", self.session, "company")
            if session_company and session_company != self.company:
                frappe.throw(
                    _("Cash movement company {0} does not match session company {1}.").format(
                        self.company, session_company
                    )
                )

    def _validate_amount(self):
        if flt(self.amount) <= 0:
            frappe.throw(_("Amount must be positive."))

    def _validate_approval(self):
        """Sensitive movement types require manager approval."""
        mt = self.movement_type or "Cash Drop"
        if mt in APPROVAL_REQUIRED_TYPES and not self.approved_by:
            frappe.throw(
                _("{0} requires manager approval before submission.").format(mt)
            )

    def _log_event(self):
        try:
            from ch_pos.audit import log_business_event
            log_business_event(
                event_type=self.movement_type or "Cash Drop",
                ref_doctype="CH Cash Drop",
                ref_name=self.name,
                after=str(flt(self.amount)),
                remarks=self.reason or "",
                company=self.company or frappe.db.get_value("POS Profile", self.pos_profile, "company") or "",
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Audit log failed for cash drop {self.name}")
