"""CH Free Sale Approval — tracks category-manager approval for free sales."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class CHFreeSaleApproval(Document):
    def validate(self):
        if not self.approvals:
            frappe.throw(_("At least one category manager approval is required"), title=_("Ch Free Sale Approval Error"))
        if not self.reason:
            frappe.throw(_("Reason is required for free sale approval"), title=_("Ch Free Sale Approval Error"))

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
        self.save(ignore_permissions=True)
