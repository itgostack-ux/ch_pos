import frappe
from frappe.model.document import Document


class POSExecutive(Document):
    def validate(self):
        self._validate_unique_user_company_store()
        self._resolve_sales_person()

    def _validate_unique_user_company_store(self):
        """Ensure a user doesn't have duplicate active records for same store + company."""
        if not self.is_active:
            return
        existing = frappe.db.exists(
            "POS Executive",
            {
                "user": self.user,
                "store": self.store,
                "company": self.company,
                "is_active": 1,
                "name": ("!=", self.name),
            },
        )
        if existing:
            frappe.throw(
                f"Active POS Executive record already exists for {self.user} "
                f"at store {self.store} under {self.company}: {existing}"
            )

    def _resolve_sales_person(self):
        """Auto-resolve sales_person from Employee if not explicitly set."""
        if self.sales_person or not self.employee:
            return
        sp = frappe.db.get_value("Sales Person", {"employee": self.employee, "enabled": 1})
        if sp:
            self.sales_person = sp
