"""
CH Device Master — company-bound POS device/register.

Rules:
- One device belongs to one company only
- One device belongs to one store only
- Device links to one default POS Profile and one warehouse
- Inactive device cannot open a session
- Company on device must match company on POS Profile and warehouse
"""

import frappe
from frappe import _
from frappe.model.document import Document


class CHDeviceMaster(Document):
    def validate(self):
        self._validate_company_consistency()
        self._validate_no_duplicate_active()

    def _validate_company_consistency(self):
        """Company on device must match company on POS Profile and warehouse."""
        if self.pos_profile:
            profile_company = frappe.db.get_value("POS Profile", self.pos_profile, "company")
            if profile_company and profile_company != self.company:
                frappe.throw(
                    _("POS Profile {0} belongs to company {1}, but this device is assigned to {2}.").format(
                        self.pos_profile, profile_company, self.company
                    )
                )

        if self.warehouse:
            wh_company = frappe.db.get_value("Warehouse", self.warehouse, "company")
            if wh_company and wh_company != self.company:
                frappe.throw(
                    _("Warehouse {0} belongs to company {1}, but this device is assigned to {2}.").format(
                        self.warehouse, wh_company, self.company
                    )
                )

        # Verify store belongs to company if store has a company field
        if self.store:
            store_company = frappe.db.get_value("CH Store", self.store, "company")
            if store_company and store_company != self.company:
                frappe.throw(
                    _("Store {0} belongs to company {1}, but this device is assigned to {2}.").format(
                        self.store, store_company, self.company
                    )
                )

    def _validate_no_duplicate_active(self):
        """No duplicate active device mapping to conflicting company/store."""
        if not self.is_active:
            return
        existing = frappe.db.get_value(
            "CH Device Master",
            {
                "device_id": self.device_id,
                "is_active": 1,
                "name": ("!=", self.name),
            },
            "name",
        )
        if existing:
            frappe.throw(
                _("An active device with ID {0} already exists ({1}).").format(
                    self.device_id, existing
                )
            )


def get_device_for_user(user, company=None, store=None):
    """Find the default device allocated to a user. Used for auto-detection on login."""
    filters = {"is_active": 1}
    if company:
        filters["company"] = company
    if store:
        filters["store"] = store

    # Look up from CH POS User Allocation
    alloc = frappe.db.get_value(
        "CH POS User Allocation",
        {"user": user, "is_active": 1, **({"company": company} if company else {})},
        "default_device",
    )
    if alloc:
        device = frappe.db.get_value("CH Device Master", alloc, ["name", "company", "store", "pos_profile", "warehouse", "is_active"], as_dict=True)
        if device and device.is_active:
            return device
    return None
