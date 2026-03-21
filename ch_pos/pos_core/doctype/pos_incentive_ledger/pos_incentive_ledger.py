import frappe
from frappe.model.document import Document
from frappe.utils import flt


class POSIncentiveLedger(Document):
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
