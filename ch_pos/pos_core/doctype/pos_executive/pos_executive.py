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
