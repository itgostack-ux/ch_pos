"""
CH POS Session — central session entity for POS cash control.

Lifecycle:  Draft → Open (submitted) → Locked → Pending Close → Closed

Rules:
- One open session per device per business date
- POS cannot bill without an active Open session
- Session must be closed before a new one can open
- Cash variance > threshold requires manager approval
- Company/Store/Device must all match across session and transactions
- Logout ≠ session close; Lock = temporary pause
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, cint, now_datetime, getdate, nowdate, time_diff_in_seconds


VARIANCE_AUTO_ALLOW = 100  # ₹100 default threshold


def _get_variance_threshold():
    """Get variance threshold from control settings, or default."""
    try:
        return flt(frappe.db.get_single_value("CH POS Control Settings", "variance_approval_threshold")) or VARIANCE_AUTO_ALLOW
    except Exception:
        return VARIANCE_AUTO_ALLOW


class CHPOSSession(Document):
    def validate(self):
        self._validate_company_device_consistency()
        self._validate_no_duplicate_open()
        self._validate_no_duplicate_open_for_store()
        self._validate_no_duplicate_device_date()
        self._validate_business_date()
        self._validate_user_allocation()
        if self.status in ("Closing", "Pending Close"):
            self._calculate_totals()
            self._calculate_cash_variance()
            self._validate_variance()

    def on_submit(self):
        self.db_set("status", "Open")

    def before_cancel(self):
        frappe.throw(_("POS Sessions cannot be cancelled. Close them instead."))

    def _validate_company_device_consistency(self):
        """Company on session must match device, store, POS Profile, and warehouse."""
        if self.device:
            device = frappe.db.get_value(
                "CH Device Master", self.device,
                ["company", "store", "is_active"], as_dict=True
            )
            if not device:
                frappe.throw(_("Device {0} not found.").format(self.device))
            if not device.is_active:
                frappe.throw(_("Device {0} is inactive. Cannot open session.").format(self.device))
            if self.company and device.company != self.company:
                frappe.throw(
                    _("Device {0} belongs to company {1}, but session company is {2}.").format(
                        self.device, device.company, self.company
                    )
                )
            if self.store and device.store != self.store:
                frappe.throw(
                    _("Device {0} belongs to store {1}, but session store is {2}.").format(
                        self.device, device.store, self.store
                    )
                )

        if self.pos_profile and self.company:
            profile_company = frappe.db.get_value("POS Profile", self.pos_profile, "company")
            if profile_company and profile_company != self.company:
                frappe.throw(
                    _("POS Profile {0} belongs to company {1}, but session company is {2}.").format(
                        self.pos_profile, profile_company, self.company
                    )
                )

        if self.store and self.company:
            store_company = frappe.db.get_value("CH Store", self.store, "company")
            if store_company and store_company != self.company:
                frappe.throw(
                    _("Store {0} belongs to company {1}, but session company is {2}.").format(
                        self.store, store_company, self.company
                    )
                )

    def _validate_no_duplicate_open(self):
        """Ensure no other Open session exists for this POS Profile."""
        if self.docstatus == 0:
            existing = frappe.db.exists(
                "CH POS Session",
                {
                    "pos_profile": self.pos_profile,
                    "status": ("in", ["Open", "Locked", "Suspended"]),
                    "docstatus": 1,
                    "name": ("!=", self.name),
                },
            )
            if existing:
                frappe.throw(
                    _("An open session {0} already exists for {1}. Close it first.").format(
                        existing, self.pos_profile
                    )
                )

    def _validate_no_duplicate_open_for_store(self):
        """Ensure only one active session exists per store at any point in time."""
        if self.docstatus != 0:
            return

        existing = frappe.db.get_value(
            "CH POS Session",
            {
                "store": self.store,
                "status": ("in", ["Open", "Locked", "Suspended", "Closing", "Pending Close"]),
                "docstatus": 1,
                "name": ("!=", self.name),
            },
            ["name", "pos_profile", "user"],
            as_dict=True,
        )
        if existing:
            frappe.throw(
                _(
                    "Store already has an active session {0} (Profile: {1}, Cashier: {2}). "
                    "Close it before opening a new session."
                ).format(existing.name, existing.pos_profile, existing.user)
            )

    def _validate_no_duplicate_device_date(self):
        """One session per device per business date."""
        if self.docstatus != 0 or not self.device:
            return
        existing = frappe.db.get_value(
            "CH POS Session",
            {
                "device": self.device,
                "business_date": self.business_date,
                "docstatus": 1,
                "name": ("!=", self.name),
            },
            ["name", "status"],
            as_dict=True,
        )
        if existing:
            frappe.throw(
                _("Device {0} already has a session {1} (status: {2}) for business date {3}.").format(
                    self.device, existing.name, existing.status, self.business_date
                )
            )

    def _validate_user_allocation(self):
        """User must be allocated to this company and store."""
        if self.docstatus != 0:
            return
        try:
            enforce = cint(frappe.db.get_single_value("CH POS Control Settings", "enforce_user_company_isolation"))
        except Exception:
            enforce = 0
        if not enforce:
            return

        from ch_pos.pos_core.doctype.ch_pos_user_allocation.ch_pos_user_allocation import get_user_allocation
        alloc = get_user_allocation(self.user, self.company)
        if not alloc:
            frappe.throw(
                _("User {0} is not allocated to company {1} for POS operations.").format(
                    self.user, self.company
                )
            )
        if alloc.store != self.store:
            frappe.throw(
                _("User {0} is allocated to store {1}, not {2}.").format(
                    self.user, alloc.store, self.store
                )
            )

    def _validate_business_date(self):
        """Business date must match the store's current business date."""
        store_date = get_store_business_date(self.store)
        if store_date and getdate(self.business_date) != getdate(store_date):
            frappe.throw(
                _("Business date {0} does not match store date {1}. Contact manager to override.").format(
                    self.business_date, store_date
                )
            )

    def _calculate_totals(self):
        """Fetch invoice totals for this session's date range and profile."""
        invoices = frappe.get_all(
            "POS Invoice",
            filters={
                "pos_profile": self.pos_profile,
                "docstatus": 1,
                "consolidated_invoice": ("in", [None, ""]),
                "posting_date": self.business_date,
            },
            fields=["name", "grand_total", "is_return"],
        )

        total_invoices = 0
        total_sales = 0.0
        total_returns = 0
        total_return_amount = 0.0

        for inv in invoices:
            amt = flt(inv.grand_total)
            if inv.is_return:
                total_returns += 1
                total_return_amount += abs(amt)
            else:
                total_invoices += 1
                total_sales += amt

        self.total_invoices = total_invoices
        self.total_sales = total_sales
        self.total_returns = total_returns
        self.total_return_amount = total_return_amount
        self.net_sales = total_sales - total_return_amount

        # Fetch payment-wise expected amounts
        rows = frappe.db.sql("""
            SELECT sip.mode_of_payment, SUM(sip.amount) AS expected_amount
            FROM `tabPOS Invoice` pi
            JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
            WHERE pi.pos_profile = %(pos_profile)s
              AND pi.docstatus = 1
              AND IFNULL(pi.consolidated_invoice, '') = ''
              AND pi.posting_date = %(bdate)s
            GROUP BY sip.mode_of_payment
        """, {"pos_profile": self.pos_profile, "bdate": self.business_date}, as_dict=True)

        existing = {r.mode_of_payment: r for r in (self.payment_details or [])}
        self.set("payment_details", [])
        for r in rows:
            prev = existing.get(r.mode_of_payment, {})
            counted = flt(prev.get("counted_amount", 0)) if prev else 0.0
            exp = flt(r.expected_amount)
            self.append("payment_details", {
                "mode_of_payment": r.mode_of_payment,
                "expected_amount": exp,
                "counted_amount": counted,
                "variance": counted - exp,
                "notes": prev.get("notes", "") if prev else "",
            })

    def _calculate_cash_variance(self):
        """Compute expected closing cash and variance."""
        # Cash expected = opening + cash sales - cash returns - cash drops
        cash_expected = flt(self.opening_cash)

        for row in (self.payment_details or []):
            mop_type = frappe.db.get_value("Mode of Payment", row.mode_of_payment, "type")
            if mop_type == "Cash":
                cash_expected += flt(row.expected_amount)

        # Subtract cash drops
        total_drops = flt(frappe.db.sql("""
            SELECT COALESCE(SUM(amount), 0)
            FROM `tabCH Cash Drop`
            WHERE session = %s AND docstatus = 1
        """, self.name)[0][0])
        self.total_cash_drops = total_drops
        cash_expected -= total_drops

        self.closing_cash_expected = cash_expected
        self.cash_variance = flt(self.closing_cash_actual) - cash_expected

    def _validate_variance(self):
        """Enforce variance rules using configurable threshold."""
        threshold = _get_variance_threshold()
        variance = abs(flt(self.cash_variance))
        if variance > threshold:
            if not self.variance_reason:
                frappe.throw(
                    _("Cash variance is ₹{0}. Reason is mandatory for variance above ₹{1}.").format(
                        variance, threshold
                    )
                )
            if not self.closing_approved_by:
                frappe.throw(
                    _("Cash variance ₹{0} exceeds ₹{1}. Manager approval required.").format(
                        variance, threshold
                    )
                )

    def lock_session(self):
        """Lock screen — temporary pause, no financial impact."""
        if self.status != "Open":
            frappe.throw(_("Only an Open session can be locked."))
        self.db_set("status", "Locked")
        self.status = "Locked"

    def unlock_session(self):
        """Unlock session — resume from lock screen."""
        if self.status != "Locked":
            frappe.throw(_("Session is not locked."))
        self.db_set("status", "Open")
        self.status = "Open"

    def close_session(self, closing_cash, denomination_rows=None, variance_reason=None,
                      manager_pin_user=None):
        """Close this session — called from POS UI."""
        if self.status not in ("Open", "Locked", "Pending Close"):
            frappe.throw(_("Session is not in a closable state (current: {0})").format(self.status))

        self.status = "Closing"
        self.shift_end = now_datetime()
        self.closing_cash_actual = flt(closing_cash)
        self.variance_reason = variance_reason or ""

        if self.shift_start and self.shift_end:
            self.duration_minutes = int(time_diff_in_seconds(self.shift_end, self.shift_start) / 60)

        # Denomination breakdown
        if denomination_rows:
            self.set("denomination_details", [])
            for d in denomination_rows:
                self.append("denomination_details", {
                    "denomination": flt(d.get("denomination")),
                    "count": cint(d.get("count")),
                    "amount": flt(d.get("denomination")) * cint(d.get("count")),
                })

        if manager_pin_user:
            self.closing_approved_by = manager_pin_user
            self.closing_approved_at = now_datetime()
        else:
            self.closing_approved_by = None
            self.closing_approved_at = None

        # Calculate totals and variance (runs _calculate_totals + _calculate_cash_variance + _validate_variance)
        self._calculate_totals()
        self._calculate_cash_variance()
        self._validate_variance()

        # Persist all computed fields (save() on submitted doc won't persist them)
        update_fields = {
            "status": "Closed",
            "shift_end": self.shift_end,
            "closing_cash_actual": self.closing_cash_actual,
            "closing_cash_expected": self.closing_cash_expected,
            "cash_variance": self.cash_variance,
            "total_cash_drops": self.total_cash_drops,
            "variance_reason": self.variance_reason,
            "total_invoices": self.total_invoices,
            "total_sales": self.total_sales,
            "total_returns": self.total_returns,
            "total_return_amount": self.total_return_amount,
            "net_sales": self.net_sales,
        }
        if hasattr(self, 'duration_minutes'):
            update_fields["duration_minutes"] = self.duration_minutes
        update_fields["closing_approved_by"] = self.closing_approved_by
        update_fields["closing_approved_at"] = self.closing_approved_at

        for field, value in update_fields.items():
            self.db_set(field, value, update_modified=False)

        self.db_set("modified", now_datetime())
        self.status = "Closed"
        self._log_close_event()

    def _log_close_event(self):
        try:
            from ch_pos.audit import log_business_event
            log_business_event(
                event_type="Session Closed",
                ref_doctype="CH POS Session",
                ref_name=self.name,
                before="Open",
                after="Closed",
                remarks=f"Variance: ₹{flt(self.cash_variance)}",
                company=frappe.db.get_value("POS Profile", self.pos_profile, "company") or "",
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Audit log failed for session {self.name}")


def get_store_business_date(store):
    """Get the current business date for a store, or today if not set."""
    bd = frappe.db.get_value(
        "CH Business Date",
        {"store": store, "is_active": 1},
        "business_date",
    )
    return bd or nowdate()


def get_active_session(pos_profile):
    """Return the active Open/Locked session for a POS Profile, or None."""
    return frappe.db.get_value(
        "CH POS Session",
        {"pos_profile": pos_profile, "status": ("in", ["Open", "Locked"]), "docstatus": 1},
        ["name", "user", "business_date", "opening_cash", "store", "company", "device", "status"],
        as_dict=True,
    )


def auto_close_stale_sessions():
    """Scheduler: force-close sessions from previous business dates.

    Runs hourly. Sessions whose business_date < today and still Open
    are closed automatically with auto_closed=1.
    """
    today = getdate(nowdate())
    stale = frappe.get_all(
        "CH POS Session",
        filters={"status": ("in", ["Open", "Locked", "Suspended"]), "docstatus": 1, "business_date": ("<", today)},
        fields=["name", "pos_profile", "store", "business_date"],
    )
    for s in stale:
        try:
            doc = frappe.get_doc("CH POS Session", s.name)
            doc.auto_closed = 1
            doc.close_session(
                closing_cash=0,
                variance_reason="Auto-closed: session was open past business date",
            )
            frappe.logger("session").info(f"Auto-closed stale session {s.name} (biz date: {s.business_date})")
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Auto-close failed for {s.name}")
    if stale:
        frappe.db.commit()
