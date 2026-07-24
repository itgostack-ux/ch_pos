"""CH Free Sale Approval — tracks category-manager approval for free sales."""

import hashlib
import hmac
import json
import secrets

import frappe
from frappe import _
from frappe.model.document import Document

from ch_pos.config import is_privileged_user


_SEALED_FIELDS = (
    "requested_by",
    "store",
    "company",
    "customer",
    "cart_hash",
    "approval_token",
)


def _signature_secret() -> bytes:
    secret = str(frappe.conf.get("encryption_key") or "").strip()
    if not secret:
        frappe.throw(_("Site encryption key is required for free-sale approvals."))
    return secret.encode()


def _signature_payload(doc) -> bytes:
    approval_rows = sorted(
        (
            str(row.get("category") or ""),
            str(row.get("manager") or ""),
        )
        for row in (doc.get("approvals") or [])
    )
    payload = {
        fieldname: str(doc.get(fieldname) or "") for fieldname in _SEALED_FIELDS
    }
    payload["approvals"] = approval_rows
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def make_server_signature(doc) -> str:
    return hmac.new(_signature_secret(), _signature_payload(doc), hashlib.sha256).hexdigest()


def has_valid_server_signature(doc) -> bool:
    supplied = str(doc.get("server_signature") or "")
    if len(supplied) != 64:
        return False
    return hmac.compare_digest(supplied, make_server_signature(doc))


class CHFreeSaleApproval(Document):
    def before_insert(self):
        if not self.flags.get("ch_server_issued") and not is_privileged_user():
            frappe.throw(
                _("Free-sale approvals must be requested through the approved server flow."),
                frappe.PermissionError,
            )
        self.status = "Pending"
        self.requested_by = frappe.session.user
        self.approval_token = secrets.token_urlsafe(32)
        self.used = 0
        self.used_in_invoice = None
        self.server_signature = make_server_signature(self)

    def validate(self):
        if not self.approvals:
            frappe.throw(_("At least one category manager approval is required"), title=_("Ch Free Sale Approval Error"))
        if not self.reason:
            frappe.throw(_("Reason is required for free sale approval"), title=_("Ch Free Sale Approval Error"))
        if not self.company or not self.store or not self.cart_hash:
            frappe.throw(_("Company, store and the server cart hash are required."))
        if not has_valid_server_signature(self):
            frappe.throw(
                _("Free-sale approval integrity verification failed."),
                frappe.PermissionError,
            )

        if self.is_new() or self.flags.get("ch_server_state_update") or is_privileged_user():
            return
        previous = self.get_doc_before_save()
        if not previous:
            return
        protected = _SEALED_FIELDS + ("server_signature", "status", "used", "used_in_invoice")
        if any(self.get(fieldname) != previous.get(fieldname) for fieldname in protected):
            frappe.throw(
                _("Free-sale approval state is server-managed."),
                frappe.PermissionError,
            )
        old_rows = sorted(
            (row.category, row.manager, row.status, row.responded_at)
            for row in (previous.approvals or [])
        )
        new_rows = sorted(
            (row.category, row.manager, row.status, row.responded_at)
            for row in (self.approvals or [])
        )
        if old_rows != new_rows:
            frappe.throw(
                _("Free-sale manager responses are server-managed."),
                frappe.PermissionError,
            )

    def check_all_approved(self):
        """Return True if every category manager has approved."""
        for row in self.approvals:
            if row.status != "Approved":
                return False
        return True

    def update_status(self):
        """Recalculate parent status based on child approval rows."""
        if any(r.status == "Rejected" for r in self.approvals):
            self.status = "Rejected"
        elif all(r.status == "Approved" for r in self.approvals):
            self.status = "Approved"
        else:
            self.status = "Pending"
        self.flags.ch_server_state_update = True
        self.save(ignore_permissions=True)
