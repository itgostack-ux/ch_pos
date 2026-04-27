import frappe
from frappe import _
from frappe.model.document import Document


class CHPaymentMachine(Document):
    def validate(self):
        self.provider = (self.provider or "").strip()
        self.machine_id = (self.machine_id or "").strip()
        self.machine_name = (self.machine_name or "").strip()
        self.supported_payment_modes = self._normalize_modes(self.supported_payment_modes)
        self._validate_company_consistency()

    def _validate_company_consistency(self):
        if self.store:
            store_company = frappe.db.get_value("CH Store", self.store, "company")
            if store_company and self.company and store_company != self.company:
                frappe.throw(
                    _("Store {0} belongs to company {1}, but payment machine is assigned to {2}.").format(
                        self.store, store_company, self.company
                    )
                )

        if self.pos_profile:
            profile_company = frappe.db.get_value("POS Profile", self.pos_profile, "company")
            if profile_company and self.company and profile_company != self.company:
                frappe.throw(
                    _("POS Profile {0} belongs to company {1}, but payment machine is assigned to {2}.").format(
                        self.pos_profile, profile_company, self.company
                    )
                )

    @staticmethod
    def _normalize_modes(value):
        parts = []
        for raw in (value or "").replace("\n", ",").split(","):
            mode = raw.strip().upper()
            if mode and mode not in parts:
                parts.append(mode)
        return ", ".join(parts)
