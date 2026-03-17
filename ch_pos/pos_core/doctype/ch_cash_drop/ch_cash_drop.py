"""CH Cash Drop — move excess cash from register to safe during shift."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class CHCashDrop(Document):
    def validate(self):
        if flt(self.amount) <= 0:
            frappe.throw(_("Drop amount must be positive"))
        session = frappe.get_doc("CH POS Session", self.session)
        if session.status != "Open":
            frappe.throw(_("Cash drops can only be made on open sessions"))
        if not self.approved_by:
            frappe.throw(_("Manager approval is required for cash drops"))

    def on_submit(self):
        self.db_set("status", "Submitted")
        self._log_event()

    def on_cancel(self):
        self.db_set("status", "Cancelled")

    def _log_event(self):
        try:
            from ch_pos.audit import log_business_event
            log_business_event(
                event_type="Cash Drop",
                ref_doctype="CH Cash Drop",
                ref_name=self.name,
                after=str(flt(self.amount)),
                remarks=self.reason or "",
                company=frappe.db.get_value("POS Profile", self.pos_profile, "company") or "",
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Audit log failed for cash drop {self.name}")
