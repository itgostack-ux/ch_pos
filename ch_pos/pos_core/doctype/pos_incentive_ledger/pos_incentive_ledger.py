import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


APPROVER_ROLES = {"System Manager", "Accounts Manager", "POS Manager"}
PAYER_ROLES = {"System Manager", "Accounts Manager"}
CANCELLER_ROLES = {"System Manager", "Accounts Manager", "POS Manager"}

ALLOWED_TRANSITIONS = {
    "Pending": {"Approved", "Cancelled"},
    "Approved": {"Paid", "Cancelled"},
    "Paid": set(),
    "Cancelled": set(),
}


class POSIncentiveLedger(Document):
    def validate(self):
        self.status = self.status or "Pending"
        self._validate_status_transition_and_stamps()

    def after_insert(self):
        """Log incentive reversals (negative entries) for compliance audit."""
        if flt(self.incentive_amount) < 0:
            try:
                from ch_pos.audit import log_business_event
                log_business_event(
                    event_type="Incentive Reversal",
                    ref_doctype="POS Incentive Ledger", ref_name=self.name,
                    before="",
                    after=f"₹{self.incentive_amount}",
                    remarks=f"Incentive reversal for {self.get('pos_executive', '')}",
                    company=self.get("company", ""),
                )
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"Audit log failed for incentive ledger {self.name}")

    def _validate_status_transition_and_stamps(self):
        if self.is_new():
            return

        before = self.get_doc_before_save()
        if not before:
            return

        old_status = before.status or "Pending"
        new_status = self.status or "Pending"

        if old_status == new_status:
            if new_status == "Paid" and not self.payout_reference:
                frappe.throw(_("Payout Reference is mandatory when status is Paid."), title=_("Validation Error"))
            return

        allowed = ALLOWED_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            frappe.throw(
                _("Invalid status transition from {0} to {1}.").format(
                    frappe.bold(old_status), frappe.bold(new_status)
                ),
                title=_("Invalid Transition"),
            )

        if new_status == "Approved":
            _ensure_role(APPROVER_ROLES, _("Only Accounts/POS managers can approve incentives."))
            self.approved_by = frappe.session.user
            self.approved_on = now_datetime()

        elif new_status == "Paid":
            _ensure_role(PAYER_ROLES, _("Only Accounts Manager/System Manager can mark incentives as Paid."))
            if old_status != "Approved":
                frappe.throw(
                    _("Incentive must be Approved before marking it Paid."),
                    title=_("Invalid Transition"),
                )
            if not self.payout_reference:
                frappe.throw(_("Payout Reference is mandatory when status is Paid."), title=_("Validation Error"))
            self.paid_by = frappe.session.user
            self.paid_on = now_datetime()

        elif new_status == "Cancelled":
            _ensure_role(CANCELLER_ROLES, _("Only Accounts/POS managers can cancel incentive entries."))


def _ensure_role(required_roles: set[str], message: str):
    user_roles = set(frappe.get_roles(frappe.session.user))
    if user_roles.intersection(required_roles):
        return
    frappe.throw(message, title=_("Not Permitted"), exc=frappe.PermissionError)


@frappe.whitelist()
def approve_incentive(name: str):
    _ensure_role(APPROVER_ROLES, _("Only Accounts/POS managers can approve incentives."))
    doc = frappe.get_doc("POS Incentive Ledger", name)
    if doc.status != "Pending":
        frappe.throw(_("Only Pending entries can be approved."), title=_("Invalid Transition"))
    doc.status = "Approved"
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def mark_incentive_paid(name: str, payout_reference: str, payout_month: str | None = None):
    _ensure_role(PAYER_ROLES, _("Only Accounts Manager/System Manager can mark incentives as Paid."))
    if not payout_reference:
        frappe.throw(_("Payout Reference is mandatory."), title=_("Validation Error"))

    doc = frappe.get_doc("POS Incentive Ledger", name)
    if doc.status != "Approved":
        frappe.throw(_("Only Approved entries can be marked Paid."), title=_("Invalid Transition"))

    doc.payout_reference = payout_reference
    if payout_month:
        doc.payout_month = payout_month
    doc.status = "Paid"
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def cancel_incentive(name: str):
    _ensure_role(CANCELLER_ROLES, _("Only Accounts/POS managers can cancel incentive entries."))
    doc = frappe.get_doc("POS Incentive Ledger", name)
    if doc.status not in {"Pending", "Approved"}:
        frappe.throw(_("Only Pending or Approved entries can be cancelled."), title=_("Invalid Transition"))
    doc.status = "Cancelled"
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}
