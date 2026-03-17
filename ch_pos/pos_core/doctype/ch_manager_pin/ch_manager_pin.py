"""CH Manager PIN — quick PIN authentication for in-store approvals."""

import hashlib

import frappe
from frappe import _
from frappe.model.document import Document


class CHManagerPIN(Document):
    def validate(self):
        pin = self.get_password("pin_hash") or ""
        if not pin.isdigit() or len(pin) < 4 or len(pin) > 6:
            frappe.throw(_("PIN must be 4-6 digits"))


def verify_manager_pin(pin, store=None, permission=None):
    """Verify a manager PIN and return the manager's user if valid.

    Args:
        pin: The plain-text PIN entered by the manager
        store: Optional store to restrict to
        permission: Optional permission field to check (e.g. "can_approve_closing")

    Returns:
        dict: {"valid": True, "user": "manager@example.com", "name": "Manager Name"}
        or {"valid": False, "message": "..."}
    """
    if not pin or not pin.strip().isdigit():
        return {"valid": False, "message": _("Invalid PIN format")}

    filters = {"is_active": 1}
    if store:
        filters["store"] = ("in", [store, None, ""])

    managers = frappe.get_all(
        "CH Manager PIN",
        filters=filters,
        fields=["name", "user", "employee_name", "pin_hash"],
    )

    for mgr in managers:
        stored_pin = frappe.utils.password.get_decrypted_password(
            "CH Manager PIN", mgr.name, "pin_hash"
        )
        if stored_pin == pin:
            # Check specific permission if requested
            if permission:
                has_perm = frappe.db.get_value("CH Manager PIN", mgr.name, permission)
                if not has_perm:
                    return {
                        "valid": False,
                        "message": _("{0} does not have {1} permission").format(
                            mgr.employee_name, permission
                        ),
                    }
            return {
                "valid": True,
                "user": mgr.user,
                "name": mgr.employee_name,
            }

    return {"valid": False, "message": _("Invalid PIN")}
