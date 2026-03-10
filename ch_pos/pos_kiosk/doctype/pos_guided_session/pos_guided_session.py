from frappe.model.document import Document
from frappe.utils import now_datetime, time_diff_in_seconds


class POSGuidedSession(Document):
    def before_save(self):
        if self.status in ("Completed", "Abandoned") and not self.session_duration_sec:
            self.session_duration_sec = int(
                time_diff_in_seconds(now_datetime(), self.creation)
            )
