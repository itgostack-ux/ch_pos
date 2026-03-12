import frappe
from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime


class POSKioskToken(Document):
    def before_submit(self):
        if not self.expires_at:
            self.expires_at = add_to_date(now_datetime(), minutes=30)
        self._calculate_total()

    def on_cancel(self):
        """Handle token cancellation — expire the token."""
        if self.status == "Active":
            self.db_set("status", "Expired")

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
