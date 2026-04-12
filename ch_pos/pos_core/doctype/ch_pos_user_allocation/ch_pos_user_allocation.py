"""
CH POS User Allocation — maps a user to exactly one company + store for POS.

Rules:
- One user belongs to one company for POS purposes
- User can only access allowed store(s) within same company
- Device assigned must belong to same company and store
- Block login/session opening if user-company mismatch
"""

import frappe
from frappe import _
from frappe.model.document import Document


class CHPOSUserAllocation(Document):
    def validate(self):
        self._validate_unique_active_per_company()
        self._validate_device_consistency()

    def _validate_unique_active_per_company(self):
        """One active allocation per user per company."""
        if not self.is_active:
            return
        existing = frappe.db.get_value(
            "CH POS User Allocation",
            {
                "user": self.user,
                "company": self.company,
                "is_active": 1,
                "name": ("!=", self.name),
            },
            "name",
        )
        if existing:
            frappe.throw(
                _("User {0} already has an active allocation for company {1} ({2}).").format(
                    self.user, self.company, existing
                )
            )

    def _validate_device_consistency(self):
        """Default device must belong to same company and store."""
        if not self.default_device:
            return
        device = frappe.db.get_value(
            "CH Device Master",
            self.default_device,
            ["company", "store", "is_active"],
            as_dict=True,
        )
        if not device:
            frappe.throw(_("Device {0} not found.").format(self.default_device), title=_("Ch Pos User Allocation Error"))

        if not device.is_active:
            frappe.throw(_("Device {0} is inactive.").format(self.default_device), title=_("Ch Pos User Allocation Error"))

        if device.company != self.company:
            frappe.throw(
                _("Device {0} belongs to company {1}, but this allocation is for {2}.").format(
                    self.default_device, device.company, self.company
                )
            )

        if device.store != self.store:
            frappe.throw(
                _("Device {0} belongs to store {1}, but this allocation is for store {2}.").format(
                    self.default_device, device.store, self.store
                )
            )


def get_user_allocation(user, company=None):
    """Get the active POS allocation for a user, optionally filtered by company."""
    filters = {"user": user, "is_active": 1}
    if company:
        filters["company"] = company

    return frappe.db.get_value(
        "CH POS User Allocation",
        filters,
        ["name", "user", "company", "store", "default_device",
         "can_open_session", "can_close_session", "can_approve_variance",
         "can_do_cash_drop"],
        as_dict=True,
    )


def get_user_company(user):
    """Get the company this user is allocated to for POS. Returns None if not allocated."""
    return frappe.db.get_value(
        "CH POS User Allocation",
        {"user": user, "is_active": 1},
        "company",
    )
