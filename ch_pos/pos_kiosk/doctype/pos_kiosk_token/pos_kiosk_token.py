import frappe
from frappe.model.document import Document
from frappe.utils import add_to_date, flt, now_datetime

from buyback.utils import validate_indian_phone


class POSKioskToken(Document):
    def validate(self):
        if self.customer_phone:
            self.customer_phone = validate_indian_phone(self.customer_phone, "Customer Phone")
        for row in self.items:
            row.amount = flt(row.qty or 0) * flt(row.rate or 0)
        self._calculate_total()

    def before_submit(self):
        if not self.expires_at:
            self.expires_at = add_to_date(now_datetime(), minutes=30)

    def on_cancel(self):
        """Handle token cancellation — expire the token."""
        if self.status == "Active":
            self.db_set("status", "Expired")

        try:
            from ch_pos.audit import log_business_event
            log_business_event(
                event_type="Token Expiry",
                ref_doctype="POS Kiosk Token", ref_name=self.name,
                before="Active",
                after="Expired",
                remarks=f"Token cancelled for customer {self.get('customer', '')}",
                company=self.get("company", ""),
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Audit log failed for token {self.name}")

    def _calculate_total(self):
        self.total_estimate = sum(row.amount or 0 for row in self.items)


def expire_old_tokens():
    """Scheduler job: mark expired tokens."""
    tokens = frappe.get_all(
        "POS Kiosk Token",
        filters={"status": "Active", "expires_at": ("<", now_datetime()), "docstatus": 1},
        pluck="name",
    )
    for name in tokens:
        frappe.db.set_value("POS Kiosk Token", name, "status", "Expired")
    if tokens:
        frappe.db.commit()
