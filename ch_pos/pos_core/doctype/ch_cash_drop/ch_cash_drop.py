"""CH Cash Drop — tracks all cash movements in/out of register during a session.

Types: Cash Drop, Petty Expense, Cash Adjustment, Refund Payout, Buyback Cash Payout

Rules:
- Must link to an active open session
- Affects expected cash in settlement
- Sensitive types require manager approval
- No cross-company movement
- No post-close cash movement
"""

import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from ch_pos.api.scope_guard import assert_session_scope
from ch_pos.config import assert_session_operator, is_privileged_user

# Types that require manager approval
APPROVAL_REQUIRED_TYPES = {"Petty Expense", "Cash Adjustment", "Buyback Cash Payout"}


def _approval_payload(doc) -> bytes:
    payload = {
        "session": str(doc.get("session") or ""),
        "company": str(doc.get("company") or ""),
        "store": str(doc.get("store") or ""),
        "created_by": str(doc.get("created_by") or ""),
        "movement_type": str(doc.get("movement_type") or ""),
        "amount": str(flt(doc.get("amount"), 2)),
        "reason": str(doc.get("reason") or ""),
        "approved_by": str(doc.get("approved_by") or ""),
        "approved_at": str(doc.get("approved_at") or ""),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def make_approval_signature(doc) -> str:
    from frappe.utils.password import get_encryption_key

    return hmac.new(
        get_encryption_key().encode(), _approval_payload(doc), hashlib.sha256
    ).hexdigest()


def has_valid_approval_signature(doc) -> bool:
    supplied = str(doc.get("approval_signature") or "")
    return len(supplied) == 64 and hmac.compare_digest(
        supplied, make_approval_signature(doc)
    )


class CHCashDrop(Document):
    def before_validate(self):
        if not self.session:
            return
        session = frappe.get_doc("CH POS Session", self.session)
        if not is_privileged_user():
            assert_session_scope(self.session)
            assert_session_operator(
                session, _("record a cash movement on another cashier's session")
            )
        self.company = session.company
        self.store = session.store
        self.pos_profile = session.pos_profile
        self.device = session.device
        self.business_date = session.business_date
        if self.is_new():
            self.user = frappe.session.user
            self.created_by = frappe.session.user
            self.posting_time = now_datetime()
            self.status = "Draft"

        if self.approved_by:
            if self.flags.get("ch_manager_approval_verified") or is_privileged_user():
                self.approved_at = self.approved_at or now_datetime()
                self.approval_signature = make_approval_signature(self)
            elif not has_valid_approval_signature(self):
                frappe.throw(
                    _("Cash-movement manager approval was not verified by the server."),
                    frappe.PermissionError,
                )
        else:
            self.approved_at = None
            self.approval_signature = None

    def validate(self):
        self._validate_session_active()
        self._validate_company_match()
        self._validate_amount()

    def before_submit(self):
        # Approval gates POSTING, not drafting. The status field already models
        # Draft -> Submitted -> Approved, so a sensitive movement must be able
        # to exist as an unapproved request; enforcing the approver at validate
        # made that impossible and forced petty cash to be captured only at
        # settlement, after the money had already left the drawer.
        self._validate_approval()

    def on_submit(self):
        self.db_set("status", "Approved" if self.approved_by else "Submitted")
        self._post_gl_entry()
        self._log_event()

    def on_cancel(self):
        if self.gl_entry:
            je = frappe.get_doc("Journal Entry", self.gl_entry)
            if je.company != self.company:
                frappe.throw(
                    _("Linked Journal Entry belongs to another company."),
                    frappe.PermissionError,
                )
            if je.docstatus == 1:
                je.flags.ignore_permissions = True
                je.cancel()
            elif je.docstatus == 0:
                frappe.delete_doc(
                    "Journal Entry", je.name, ignore_permissions=True, force=True
                )
        self.db_set("status", "Cancelled")

    def _validate_session_active(self):
        """Cash movement must link to an active open session."""
        if not self.session:
            frappe.throw(_("Session is required for cash movement."), title=_("Ch Cash Drop Error"))
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
            frappe.throw(_("Amount must be positive."), title=_("Ch Cash Drop Error"))

    def _validate_approval(self):
        """Sensitive movement types require manager approval."""
        mt = self.movement_type or "Cash Drop"
        if mt in APPROVAL_REQUIRED_TYPES and not self.approved_by:
            frappe.throw(
                _("{0} requires manager approval before submission.").format(mt)
            )
        if self.approved_by and not has_valid_approval_signature(self):
            frappe.throw(
                _("Cash-movement manager approval was not verified by the server."),
                frappe.PermissionError,
            )

    def _post_gl_entry(self) -> None:
        """Post journal entry for this cash movement.

        Mapping by movement_type:
          Cash Drop        → Dr Safe/Vault Account,    Cr POS Cash Account
          Petty Expense    → Dr Petty Expense Account, Cr POS Cash Account
          Refund Payout    → Dr Sales Returns/Debtor,  Cr POS Cash Account
          Buyback Cash Payout → handled via Buyback JE; skip here
          Cash Adjustment  → no GL (variance note only)
        """
        mt = self.movement_type or "Cash Drop"
        if mt in ("Cash Adjustment", "Buyback Cash Payout"):
            return  # Buyback has its own JE; adjustment is a variance note

        company = self.company
        if not company:
            return

        # POS Profile cash account is the Cr side (money leaves the till)
        pos_cash_account = frappe.db.get_value("Company", company, "default_cash_account")
        if not pos_cash_account:
            frappe.log_error(
                f"Cash Drop GL skipped for {self.name}: no POS cash account found.",
                "Cash Drop GL"
            )
            return

        settings = frappe.get_cached_doc("CH POS Control Settings")
        cost_center = frappe.db.get_value("Company", company, "cost_center")
        amount = flt(self.amount)

        if mt == "Cash Drop":
            debit_account = settings.get("safe_account")
            if not debit_account:
                debit_account = frappe.db.get_value("Company", company, "default_bank_account")
            remark = _("Cash drop to safe — session {0}").format(self.session)

        elif mt == "Petty Expense":
            debit_account = settings.get("petty_expense_account")
            remark = _("Petty expense — {0} — session {1}").format(
                self.reason or "Unspecified", self.session
            )

        elif mt == "Refund Payout":
            debit_account = frappe.db.get_value("Company", company, "default_receivable_account")
            remark = _("Cash refund payout — session {0}").format(self.session)

        else:
            return  # unknown type — skip silently

        if not debit_account:
            frappe.log_error(
                f"Cash Drop GL skipped for {self.name} ({mt}): debit account not configured. "
                "Set accounts in CH POS Control Settings → GL Accounts.",
                "Cash Drop GL"
            )
            return

        try:
            je = frappe.new_doc("Journal Entry")
            je.update({
                "voucher_type": "Cash Entry" if mt == "Cash Drop" else "Journal Entry",
                "company": company,
                "posting_date": self.business_date or frappe.utils.today(),
                "cheque_no": self.name,
                "cheque_date": self.business_date or frappe.utils.today(),
                "remark": remark,
                "accounts": [
                    {
                        "account": debit_account,
                        "debit_in_account_currency": amount,
                        "cost_center": cost_center,
                        "reference_type": "CH Cash Drop",
                        "reference_name": self.name,
                    },
                    {
                        "account": pos_cash_account,
                        "credit_in_account_currency": amount,
                        "cost_center": cost_center,
                        "reference_type": "CH Cash Drop",
                        "reference_name": self.name,
                    },
                ],
            })
            je.flags.ignore_permissions = True
            je.insert(ignore_permissions=True)
            je.submit()
            self.db_set("gl_entry", je.name, update_modified=False)
        except Exception:
            frappe.log_error(frappe.get_traceback(),
                             f"Cash Drop GL failed for {self.name}")
            raise

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
