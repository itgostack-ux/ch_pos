import frappe
from frappe.model.document import Document
from frappe.utils import time_diff_in_seconds


class POSSessionLog(Document):
    def before_save(self):
        if self.status == "Closed" and self.session_start and self.session_end:
            self.duration_sec = int(
                time_diff_in_seconds(self.session_end, self.session_start)
            )
