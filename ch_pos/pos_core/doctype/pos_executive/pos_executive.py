import frappe
from frappe.model.document import Document


class POSExecutive(Document):
    def validate(self):
        self._validate_unique_user_company_store()
        self._resolve_sales_person()

    def on_update(self):
        if self.is_active and self.user and self.store:
            ensure_scope_store_grant(self.user, self.store)

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


def assert_valid_sales_executive(sales_executive, store=None, company=None, msg=None) -> None:
    """Require a genuine, active POS Executive as a document's billing identity.

    A till is shared across a shift, so the login is not the person — every
    money-moving document has to name who actually served the customer.

    Deliberately NOT bypassable by ``is_privileged_user()``: a scope bypass
    lets an admin work across stores, which is legitimate, but nobody at any
    privilege level gets to leave a sale unattributed. A System Manager who
    needs to bill creates their own POS Executive row first.

    ``msg`` re-words the empty-value prompt for counters that are not billing
    (device intake, say), so the person reading it is told what THEY left out.
    """
    if not sales_executive:
        frappe.throw(
            msg or frappe._("Select who is billing this sale (Billed By) before continuing."),
            title=frappe._("Billed By Required"),
        )

    filters = {"name": sales_executive, "is_active": 1}
    if company:
        filters["company"] = company
    # Store is only asserted when the POS Profile actually resolves to a CH
    # Store. An unmapped profile is a master-data gap, and must not be able to
    # take a till down — company + active still has to hold.
    if store:
        filters["store"] = store

    if not frappe.db.exists("POS Executive", filters):
        frappe.throw(
            frappe._("Billed By must be an active POS Executive for this store."),
            title=frappe._("Invalid Billed By"),
        )


def ensure_scope_store_grant(user: str, store: str) -> None:
    """Auto-provision the CH User Scope store row implied by a POS assignment.

    The fail-closed org guards (``assert_user_has_store_scope``) refuse users
    with no scope rows — without this sync, a freshly added POS Executive is
    locked out of their own store with "not entitled to access this store".
    Provisioning the STORE row is sufficient: scope resolution derives the
    store's company and warehouse from it.

    One-way and additive by design. Deactivating an executive does NOT strip
    the scope row (it may carry deliberate back-office grants); till access
    is revoked regardless by ``assert_pos_executive`` at session open.
    Saving the scope doc also re-syncs POS Profile.applicable_for_users via
    the CH User Scope controller (pos_profile_sync).
    """
    try:
        frappe.get_meta("CH User Scope")
    except Exception:
        return
    if user in ("Administrator", "Guest") or not frappe.db.exists("CH Store", store):
        return

    scope_name = frappe.db.get_value("CH User Scope", {"user": user}, "name")
    if scope_name:
        doc = frappe.get_doc("CH User Scope", scope_name)
        if any((r.store or "") == store for r in (doc.stores or [])):
            # Row already present — still nudge the profile sync so
            # applicable_for_users converges even after manual edits.
            return
        doc.append("stores", {"store": store})
    else:
        doc = frappe.new_doc("CH User Scope")
        doc.user = user
        doc.enabled = 1
        doc.append("stores", {"store": store})

    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def provision_cashier(user, stores, role="Executive", employee=None, company=None):
    """Onboard a cashier to one or more stores in a single manager action.

    POS Executive is the till-authority master (SAP cashier-store assignment),
    deliberately separate from CH User Scope's org visibility — so cashier
    stores are designated explicitly here, never inferred from a company-wide
    back-office scope. This just removes the friction of adding the rows one by
    one: idempotent, it creates the row where missing and re-activates a
    dormant one, and reports exactly what it touched. Whoever runs it must
    themselves hold POS Executive write.

    ``stores``: a store name, a JSON array, or a comma/newline separated list.
    """
    frappe.has_permission("POS Executive", "create", throw=True)

    if isinstance(stores, str):
        try:
            parsed = frappe.parse_json(stores)
            store_list = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            store_list = [s.strip() for s in stores.replace("\n", ",").split(",")]
    else:
        store_list = list(stores or [])
    store_list = [s for s in (str(x).strip() for x in store_list) if s]
    if not store_list:
        frappe.throw(frappe._("Name at least one store."))

    if not frappe.db.get_value("User", user, "enabled"):
        frappe.throw(frappe._("User {0} is disabled — enable the account before assigning tills.").format(user))

    executive_name = frappe.db.get_value("User", user, "full_name") or user
    created, reactivated, existing, skipped = [], [], [], []

    for store in store_list:
        if not frappe.db.exists("CH Store", store):
            skipped.append({"store": store, "reason": "no such CH Store"})
            continue
        store_company = company or frappe.db.get_value("CH Store", store, "company")
        row = frappe.db.get_value(
            "POS Executive",
            {"user": user, "store": store, "company": store_company},
            ["name", "is_active"],
            as_dict=True,
        )
        if row:
            if row.is_active:
                existing.append(row.name)
            else:
                frappe.db.set_value("POS Executive", row.name, "is_active", 1)
                reactivated.append(row.name)
            continue
        doc = frappe.new_doc("POS Executive")
        doc.update({
            "executive_name": executive_name,
            "user": user,
            "store": store,
            "company": store_company,
            "role": role,
            "is_active": 1,
        })
        if employee:
            doc.employee = employee
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        created.append(doc.name)

    return {
        "user": user,
        "created": created,
        "reactivated": reactivated,
        "already_active": existing,
        "skipped": skipped,
    }
