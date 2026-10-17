"""A User's "Is Sales Person" box keeps a Sales Person for them.

Ticking the box creates the person's Sales Person, disabled: someone then sets
its Store and enables it, and only then is it offered in that store's POS
Billed By (pos_api._get_executive_access). The Sales Person is created once and
left to whoever manages it -- saving the user again does not re-enable it or
change its store. Unticking the box disables it, so it drops out of Billed By;
past bills keep their name.
"""

import frappe
from frappe import _
from frappe.utils import cint


def sales_person_user_defaults(doc, method=None):
    """User.before_validate: a Sales Person only appears in Billed By and never
    logs in. So no welcome / set-password email (frappe sends it in
    on_update), and with no roles they are a Website User -- set that before
    User.validate, which otherwise warns "No Roles Specified" because a new
    user starts as System User. Given roles, frappe still makes them System
    User."""
    if not cint(doc.get("custom_is_sales_person")):
        return
    doc.send_welcome_email = 0
    if not doc.get("roles"):
        doc.user_type = "Website User"


def sync_sales_person(doc, method=None):
    """User.on_update."""
    if not doc.meta.has_field("custom_is_sales_person"):
        return
    if not frappe.get_meta("Sales Person").has_field("custom_user"):
        return

    linked = frappe.db.get_value(
        "Sales Person", {"custom_user": doc.name}, ["name", "enabled"], as_dict=True)

    if cint(doc.custom_is_sales_person):
        if linked:
            return
        from ch_pos.setup import _incentive_sales_person_root

        name = (doc.full_name or doc.first_name or doc.name).strip()
        # Sales Person is named by its name; two people can share one, so the
        # login tells them apart rather than merging two people into one.
        if frappe.db.exists("Sales Person", name):
            name = f"{name} ({doc.name})"
        sp = frappe.get_doc({
            "doctype": "Sales Person",
            "sales_person_name": name,
            "parent_sales_person": _incentive_sales_person_root(),
            "is_group": 0,
            "enabled": 0,
            "custom_user": doc.name,
            "custom_email": doc.email,
            "custom_mobile_no": doc.mobile_no,
        })
        sp.insert(ignore_permissions=True)
        frappe.msgprint(
            _("Sales Person {0} created (disabled). Set its Store and enable it to "
              "list it in POS Billed By.").format(frappe.bold(sp.name)),
            alert=True,
        )
    elif linked and cint(linked.enabled):
        frappe.db.set_value("Sales Person", linked.name, "enabled", 0)
        frappe.msgprint(
            _("Sales Person {0} disabled; it no longer appears in POS Billed By.").format(
                frappe.bold(linked.name)),
            alert=True,
        )


def validate_sales_person_store(doc, method=None):
    """Sales Person.validate: the POS Sales By store must belong to the
    Sales Person's company (Company is picked first, then a store of it);
    one User is one Sales Person; Email / Mobile No come from the User when
    left blank."""
    user = doc.get("custom_user")
    if user:
        other = frappe.db.get_value(
            "Sales Person", {"custom_user": user, "name": ("!=", doc.name)}, "name")
        if other:
            frappe.throw(
                _("User {0} is already linked to Sales Person {1}.").format(
                    frappe.bold(user), frappe.bold(other)),
                title=_("User Already Linked"))
        u = frappe.db.get_value("User", user, ["email", "mobile_no"], as_dict=True) or {}
        if not doc.get("custom_email") and u.get("email"):
            doc.custom_email = u.email
        if not doc.get("custom_mobile_no") and u.get("mobile_no"):
            doc.custom_mobile_no = u.mobile_no

    store = doc.get("custom_store")
    if not store:
        return
    store_company = frappe.db.get_value("CH Store", store, "company")
    if not doc.get("custom_company"):
        doc.custom_company = store_company
    elif store_company and store_company != doc.custom_company:
        frappe.throw(
            _("Store {0} belongs to {1}, not {2}.").format(
                frappe.bold(store), store_company, doc.custom_company),
            title=_("Store Not in Company"))
