import hashlib

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import (
    add_to_date,
    cint,
    flt,
    get_datetime,
    getdate,
    now_datetime,
    time_diff_in_seconds,
)


from buyback.utils import validate_indian_phone
from ch_pos.config import get_control_setting

# Statuses after which a token can no longer move. Shared by the API layer,
# the tablet queue position and the quick-intake job-card handoff.
TERMINAL_STATUSES = ("Completed", "Cancelled", "Converted", "Dropped", "Expired")


def _gofix_int_setting(fieldname: str, default: int) -> int:
    """Read a GoFix Settings integer when gofix is installed, else the default.

    The self check-in rules (max symptoms per token) are GoFix's business rule,
    so they stay in GoFix Settings even though the token itself lives here.
    """
    try:
        from gofix.config import get_int_setting
    except ImportError:
        return default
    try:
        return get_int_setting(fieldname, default)
    except Exception:
        return default


class POSKioskToken(Document):
    def before_validate(self):
        if (
            self.meta.has_field("token_scope_key")
            and self.pos_profile
            and self.token_display
        ):
            token_date = getdate(self.creation or now_datetime()).isoformat()
            material = f"{self.pos_profile}\x1f{token_date}\x1f{self.token_display}"
            self.token_scope_key = hashlib.sha256(material.encode()).hexdigest()

    def validate(self):
        if self.customer_phone:
            self.customer_phone = validate_indian_phone(self.customer_phone, "Customer Phone")
        for row in self.items:
            row.amount = flt(row.qty or 0) * flt(row.rate or 0)
        self._calculate_total()
        self._calculate_handling_duration()
        self._validate_symptom_rules()

    def before_submit(self):
        if not self.expires_at:
            self.expires_at = add_to_date(now_datetime(), minutes=30)

    def on_cancel(self):
        """Desk-cancel of a submitted token closes it as Cancelled."""
        before = self.status
        if self.status not in TERMINAL_STATUSES:
            self.db_set("status", "Cancelled")

        try:
            from ch_pos.audit import log_business_event
            log_business_event(
                event_type="Token Cancelled",
                ref_doctype="POS Kiosk Token", ref_name=self.name,
                before=before,
                after="Cancelled",
                remarks=f"Token cancelled for customer {self.get('customer_name', '')}",
                company=self.get("company", ""),
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Audit log failed for token {self.name}")

    def _calculate_total(self):
        self.total_estimate = sum(row.amount or 0 for row in self.items)

    def _calculate_handling_duration(self):
        """Calculate handling duration in minutes from engaged_at or creation to exit_at or now."""
        start = get_datetime(self.engaged_at) if self.engaged_at else get_datetime(self.creation)
        end = get_datetime(self.exit_at) if self.exit_at else None
        if end and start:
            self.handling_duration = max(0, cint(time_diff_in_seconds(end, start) / 60))

    def _validate_symptom_rules(self):
        """Self check-in rules: bounded symptom count; expert-check is exclusive."""
        rows = list(self.get("symptoms") or [])
        if not rows:
            return
        max_issues = _gofix_int_setting("max_selected_issues", 3)
        if len(rows) > max_issues:
            frappe.throw(
                _("At most {0} symptoms can be selected. Please remove extras.").format(max_issues)
            )
        if any(r.is_expert_check for r in rows) and len(rows) > 1:
            frappe.throw(_("\"Not sure / expert check\" cannot be combined with other symptoms."))


def expire_old_tokens():
    """Scheduler job: mark expired tokens."""
    batch_limit = max(1, min(cint(get_control_setting("scheduler_batch_limit", 500)), 5000))
    tokens = frappe.get_all(
        "POS Kiosk Token",
        filters={"status": ("in", ["Waiting", "Engaged"]), "expires_at": ("<", now_datetime()), "docstatus": 1},
        pluck="name",
        order_by="expires_at asc, name asc",
        limit_page_length=batch_limit,
    )
    if tokens:
        frappe.db.set_value(
            "POS Kiosk Token",
            {"name": ("in", tokens)},
            "status",
            "Expired",
            update_modified=False,
        )
