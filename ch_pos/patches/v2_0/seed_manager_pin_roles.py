"""Give the manager-PIN gate the roles it was always meant to have.

``CH POS Control Settings.manager_pin_roles`` is a Table MultiSelect whose
docfield default reads ``Store Manager\nPOS Manager\nSales Manager``. A
docfield default never materialises into child rows, so on every site the
table was empty, and ``get_configured_roles`` returned an empty set.

``verify_manager_pin`` then computed::

    allowed_roles = get_configured_roles("manager_pin_roles") | {"System Manager"}

which collapsed to just ``System Manager``. Its candidate SQL only considers
Administrator, System Manager holders, or holders of ``allowed_roles`` — so a
genuine Store Manager was never even a candidate and their correct PIN came
back as "Invalid PIN". That is the estate-wide "it only works if you give them
System Admin" report.

Seeds the documented default only when the table is empty, so a deliberately
narrowed list is never overwritten.
"""

import frappe
from ch_erp15.role_settings import get_setting_roles, set_setting_roles

SETTINGS = "CH POS Control Settings"
FIELD = "manager_pin_roles"
DEFAULT_ROLES = ("Store Manager", "POS Manager", "Sales Manager")


def execute():
    if not frappe.db.exists("DocType", SETTINGS):
        return
    meta = frappe.get_meta(SETTINGS)
    if not meta.get_field(FIELD):
        return

    if get_setting_roles(SETTINGS, FIELD):
        return  # already configured — leave the operator's choice alone

    roles = [r for r in DEFAULT_ROLES if frappe.db.exists("Role", r)]
    missing = [r for r in DEFAULT_ROLES if r not in roles]
    if missing:
        print(f"seed_manager_pin_roles: skipped roles that do not exist: {missing}")
    if not roles:
        return

    set_setting_roles(SETTINGS, FIELD, roles)
    print(f"seed_manager_pin_roles: seeded {FIELD} = {roles}")
