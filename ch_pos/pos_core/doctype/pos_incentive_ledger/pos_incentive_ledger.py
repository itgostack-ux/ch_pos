import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from ch_pos.api.scope_guard import assert_store_scope
from ch_pos.config import has_configured_roles, is_privileged_user

ALLOWED_TRANSITIONS = {
    "Pending": {"Approved", "Cancelled"},
    "Approved": {"Paid", "Cancelled"},
    "Paid": set(),
    "Cancelled": set(),
}


def _assert_incentive_scope(doc) -> None:
    if not doc.get("store") and not doc.get("company") and not is_privileged_user():
        frappe.throw(_("Incentive entry has no store or company scope."), frappe.PermissionError)
    assert_store_scope(store=doc.get("store"), company=doc.get("company"))


class POSIncentiveLedger(Document):
    def before_insert(self):
        if not frappe.flags.get("incentive_engine_write"):
            frappe.throw(
                _("Incentive entries can only be created by the incentive engine."),
                frappe.PermissionError)
        if (self.status or "Pending") != "Pending":
            frappe.throw(_("New incentive entries must start as Pending."))

        _assert_incentive_scope(self)
        executive = frappe.db.get_value(
            "POS Executive",
            self.pos_executive,
            ["store", "company"],
            as_dict=True)
        if not executive:
            frappe.throw(_("POS Executive was not found."))
        if self.company != executive.company or self.store != executive.store:
            frappe.throw(_("Incentive entry does not match the executive's store and company."))

        if self.invoice:
            invoice = frappe.db.get_value(
                "Sales Invoice",
                self.invoice,
                ["company", "custom_sales_executive"],
                as_dict=True)
            if not invoice or invoice.company != self.company:
                frappe.throw(_("Incentive invoice does not match its company."))
            if invoice.custom_sales_executive and invoice.custom_sales_executive != self.pos_executive:
                frappe.throw(_("Incentive invoice belongs to another sales executive."))

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
                    company=self.get("company", ""))
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

        _assert_incentive_scope(self)

        allowed = ALLOWED_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            frappe.throw(
                _("Invalid status transition from {0} to {1}.").format(
                    frappe.bold(old_status), frappe.bold(new_status)
                ),
                title=_("Invalid Transition"))

        if new_status == "Approved":
            _ensure_role(
                "incentive_approval_roles",
                _("You do not have a configured incentive approval role."))
            self.approved_by = frappe.session.user
            self.approved_on = now_datetime()

        elif new_status == "Paid":
            _ensure_role(
                "incentive_payment_roles",
                _("You do not have a configured incentive payment role."))
            if old_status != "Approved":
                frappe.throw(
                    _("Incentive must be Approved before marking it Paid."),
                    title=_("Invalid Transition"))
            if not self.payout_reference:
                frappe.throw(_("Payout Reference is mandatory when status is Paid."), title=_("Validation Error"))
            self.paid_by = frappe.session.user
            self.paid_on = now_datetime()

        elif new_status == "Cancelled":
            _ensure_role(
                "incentive_cancellation_roles",
                _("You do not have a configured incentive cancellation role."))


def _ensure_role(fieldname: str, message: str):
    if has_configured_roles(fieldname):
        return
    frappe.throw(message, title=_("Not Permitted"), exc=frappe.PermissionError)


@frappe.whitelist()
def get_incentive_ui_capabilities(name: str) -> dict:
    """Return incentive actions from configured roles and exact record scope."""
    doc = frappe.get_doc("POS Incentive Ledger", name)
    doc.check_permission("read")
    _assert_incentive_scope(doc)
    return {
        "can_approve": bool(
            doc.status == "Pending"
            and has_configured_roles(
                "incentive_approval_roles")
        ),
        "can_pay": bool(
            doc.status == "Approved"
            and has_configured_roles(
                "incentive_payment_roles")
        ),
        "can_cancel": bool(
            doc.status in {"Pending", "Approved"}
            and has_configured_roles(
                "incentive_cancellation_roles")
        ),
    }


@frappe.whitelist(methods=["POST"])
def approve_incentive(name: str):
    _ensure_role(
        "incentive_approval_roles",
        _("You do not have a configured incentive approval role."))
    doc = frappe.get_doc("POS Incentive Ledger", name)
    doc.check_permission("write")
    _assert_incentive_scope(doc)
    if doc.status != "Pending":
        frappe.throw(_("Only Pending entries can be approved."), title=_("Invalid Transition"))
    doc.status = "Approved"
    doc.save()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist(methods=["POST"])
def mark_incentive_paid(name: str, payout_reference: str, payout_month: str | None = None):
    _ensure_role(
        "incentive_payment_roles",
        _("You do not have a configured incentive payment role."))
    if not payout_reference:
        frappe.throw(_("Payout Reference is mandatory."), title=_("Validation Error"))

    doc = frappe.get_doc("POS Incentive Ledger", name)
    doc.check_permission("write")
    _assert_incentive_scope(doc)
    if doc.status != "Approved":
        frappe.throw(_("Only Approved entries can be marked Paid."), title=_("Invalid Transition"))

    doc.payout_reference = payout_reference
    if payout_month:
        doc.payout_month = payout_month
    doc.status = "Paid"
    doc.save()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist(methods=["POST"])
def cancel_incentive(name: str):
    _ensure_role(
        "incentive_cancellation_roles",
        _("You do not have a configured incentive cancellation role."))
    doc = frappe.get_doc("POS Incentive Ledger", name)
    doc.check_permission("write")
    _assert_incentive_scope(doc)
    if doc.status not in {"Pending", "Approved"}:
        frappe.throw(_("Only Pending or Approved entries can be cancelled."), title=_("Invalid Transition"))
    doc.status = "Cancelled"
    doc.save()
    return {"name": doc.name, "status": doc.status}
