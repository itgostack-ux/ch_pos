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

import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, cint, now_datetime, getdate, nowdate, time_diff_in_seconds
from frappe.utils.password import get_encryption_key

from ch_pos.config import get_control_setting, is_privileged_user


VARIANCE_AUTO_ALLOW = 100  # ₹100 default threshold


def _get_variance_threshold():
    """Get variance threshold from control settings, or default."""
    try:
        cached = frappe.cache().get_value("ch_pos_variance_threshold")
        if cached is not None:
            return flt(cached)
        threshold = flt(frappe.db.get_single_value("CH POS Control Settings", "variance_approval_threshold")) or VARIANCE_AUTO_ALLOW
        frappe.cache().set_value("ch_pos_variance_threshold", threshold, expires_in_sec=3600)
        return threshold
    except Exception:
        return VARIANCE_AUTO_ALLOW


class CHPOSSession(Document):
    def before_insert(self):
        if not self.flags.get("ch_server_issued") and not is_privileged_user():
            frappe.throw(
                _("POS Sessions can only be opened through the Open Session action."),
                frappe.PermissionError,
            )
        profile = frappe.get_cached_doc("POS Profile", self.pos_profile)
        if self.company and self.company != profile.company:
            frappe.throw(_("Session company must match the POS Profile."), frappe.PermissionError)
        self.company = profile.company
        self.user = frappe.session.user
        self.business_date = get_store_business_date(self.store)
        self.shift_start = now_datetime()
        self.status = "Open"
        self.closing_approved_by = None
        self.closing_approved_at = None
        if self.flags.get("ch_opening_approval_verified"):
            if not self.opening_approved_by:
                frappe.throw(_("Verified opening approval is missing its manager identity."))
        elif is_privileged_user():
            self.opening_approved_by = frappe.session.user
            self.opening_approved_at = now_datetime()
        else:
            frappe.throw(_("Manager approval is required to open a POS Session."), frappe.PermissionError)
        self.opening_approved_at = self.opening_approved_at or now_datetime()
        self.opening_evidence_signature = self._expected_opening_signature()

    def validate(self):
        self._validate_company_device_consistency()
        self._validate_no_duplicate_open()
        self._validate_no_duplicate_open_for_store()
        self._validate_no_duplicate_device_date()
        self._validate_business_date()
        self._validate_user_allocation()
        # opening_evidence_signature is permlevel 1; frappe's higher-permlevel
        # reset WIPES the value minted in before_insert for any cashier
        # without that permlevel (i.e. everyone but System Manager) before
        # validate runs. Re-mint within the same server-issued request —
        # ch_server_issued can only be set by session_api.open_session, never
        # by a client-supplied document, so this is not a tamper vector.
        if (
            self.is_new()
            and self.flags.get("ch_server_issued")
            and not (self.get("opening_evidence_signature") or "").strip()
        ):
            self.opening_evidence_signature = self._expected_opening_signature()
        if not self._has_valid_opening_signature():
            frappe.throw(_("POS Session opening evidence failed integrity verification."), frappe.PermissionError)
        if self.status in ("Closing", "Pending Close"):
            self._calculate_totals()
            self._calculate_cash_variance()
            self._validate_variance()

    def on_submit(self):
        if not self._has_valid_opening_signature():
            frappe.throw(_("POS Session opening evidence failed integrity verification."), frappe.PermissionError)
        self.db_set("status", "Open")

    def before_update_after_submit(self):
        if self.flags.get("ch_server_state_update"):
            return
        previous = self.get_doc_before_save()
        if not previous:
            return
        protected = (
            "status", "user", "company", "store", "device", "pos_profile",
            "business_date", "opening_cash", "expected_float", "opening_approved_by",
            "opening_approved_at", "shift_end", "closing_cash_expected",
            "closing_cash_actual", "cash_variance", "closing_approved_by",
            "closing_approved_at", "auto_closed",
        )
        if any(str(self.get(field) or "") != str(previous.get(field) or "") for field in protected):
            frappe.throw(
                _("POS Session financial/state evidence can only be changed through session actions."),
                frappe.PermissionError,
            )

    def _opening_signature_payload(self):
        # NB: the docname must NOT be part of this payload. The signature is
        # minted in before_insert, which runs BEFORE autoname assigns
        # self.name; validate() re-verifies AFTER naming — including "name"
        # made every fresh session fail its own integrity check.
        return json.dumps(
            {
                "company": self.company or "",
                "store": self.store or "",
                "device": self.device or "",
                "pos_profile": self.pos_profile or "",
                "user": self.user or "",
                "business_date": str(self.business_date or ""),
                "shift_start": str(self.shift_start or ""),
                "opening_cash": f"{flt(self.opening_cash):.6f}",
                "expected_float": f"{flt(self.expected_float):.6f}",
                "approved_by": self.opening_approved_by or "",
                "approved_at": str(self.opening_approved_at or ""),
                "pos_opening_entry": self.pos_opening_entry or "",
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def _expected_opening_signature(self):
        return hmac.new(
            get_encryption_key().encode(),
            self._opening_signature_payload().encode(),
            hashlib.sha256,
        ).hexdigest()

    def _has_valid_opening_signature(self):
        actual = str(self.get("opening_evidence_signature") or "")
        return bool(actual) and hmac.compare_digest(actual, self._expected_opening_signature())

    def _expected_closing_signature(self):
        payload = json.dumps(
            {
                "name": self.name,
                "status": "Closed",
                "shift_end": str(self.shift_end or ""),
                "closing_cash_expected": f"{flt(self.closing_cash_expected):.6f}",
                "closing_cash_actual": f"{flt(self.closing_cash_actual):.6f}",
                "cash_variance": f"{flt(self.cash_variance):.6f}",
                "variance_reason": self.variance_reason or "",
                "closing_approved_by": self.closing_approved_by or "",
                "closing_approved_at": str(self.closing_approved_at or ""),
                "auto_closed": cint(self.auto_closed),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hmac.new(
            get_encryption_key().encode(), payload.encode(), hashlib.sha256
        ).hexdigest()

    def before_cancel(self):
        frappe.throw(_("POS Sessions cannot be cancelled. Close them instead."), title=_("Ch Pos Session Error"))

    def _validate_company_device_consistency(self):
        """Company on session must match device, store, POS Profile, and warehouse."""
        if self.device:
            device = frappe.db.get_value(
                "CH Device Master", self.device,
                ["company", "store", "is_active"], as_dict=True
            )
            if not device:
                frappe.throw(_("Device {0} not found.").format(self.device), title=_("Ch Pos Session Error"))
            if not device.is_active:
                frappe.throw(_("Device {0} is inactive. Cannot open session.").format(self.device), title=_("Ch Pos Session Error"))
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
        """Block opening a second active session for the same store."""
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
            ["name"],
            as_dict=True,
        )

        if existing:
            frappe.throw(
                _("Session {0} is still active for this store. Close it before opening another session.").format(
                    existing.name
                )
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
        """User must be assigned to this company and store via POS Executive."""
        if self.docstatus != 0:
            return
        if is_privileged_user(self.user):
            return

        # Check POS Executive — the single source of truth
        exec_exists = frappe.db.exists("POS Executive", {
            "user": self.user,
            "company": self.company,
            "store": self.store,
            "is_active": 1,
        })
        if exec_exists:
            return

        # Check if user has POS Executive for this company but different store
        exec_other_store = frappe.db.get_value("POS Executive", {
            "user": self.user,
            "company": self.company,
            "is_active": 1,
        }, "store")
        if exec_other_store:
            frappe.throw(
                _("User {0} is assigned to store {1}, not {2}. Update the POS Executive record or create a new one.").format(
                    self.user, exec_other_store, self.store
                )
            )

        # No POS Executive found
        frappe.throw(
            _("User {0} has no active POS Executive record for company {1}. "
              "Create one in POS > POS Executive.").format(
                self.user, self.company
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
        """Fetch invoice totals for this session."""
        totals = frappe.db.sql(
            """
            SELECT
                SUM(CASE WHEN COALESCE(is_return, 0) = 0 THEN 1 ELSE 0 END) AS total_invoices,
                COALESCE(SUM(CASE WHEN COALESCE(is_return, 0) = 0 THEN grand_total ELSE 0 END), 0) AS total_sales,
                SUM(CASE WHEN COALESCE(is_return, 0) = 1 THEN 1 ELSE 0 END) AS total_returns,
                COALESCE(SUM(CASE WHEN COALESCE(is_return, 0) = 1 THEN ABS(grand_total) ELSE 0 END), 0) AS total_return_amount
            FROM `tabSales Invoice`
            WHERE custom_ch_pos_session = %s AND docstatus = 1
            """,
            self.name,
            as_dict=True,
        )[0]

        total_invoices = cint(totals.total_invoices)
        total_sales = flt(totals.total_sales)
        total_returns = cint(totals.total_returns)
        total_return_amount = flt(totals.total_return_amount)

        self.total_invoices = total_invoices
        self.total_sales = total_sales
        self.total_returns = total_returns
        self.total_return_amount = total_return_amount
        self.net_sales = total_sales - total_return_amount

        # Fetch payment-wise expected amounts
        rows = frappe.db.sql("""
            SELECT sip.mode_of_payment, SUM(sip.amount) AS expected_amount
            FROM `tabSales Invoice` pi
            JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
            WHERE pi.custom_ch_pos_session = %(session)s
              AND pi.docstatus = 1
            GROUP BY sip.mode_of_payment
        """, {"session": self.name}, as_dict=True)

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

        mode_names = sorted({row.mode_of_payment for row in (self.payment_details or []) if row.mode_of_payment})
        cash_modes = {
            row.name
            for row in frappe.get_all(
                "Mode of Payment",
                filters={"name": ("in", mode_names), "type": "Cash"},
                fields=["name"],
                limit_page_length=len(mode_names),
            )
        } if mode_names else set()

        for row in (self.payment_details or []):
            if row.mode_of_payment in cash_modes:
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
        # Auto-close bypasses manager approval — variance is logged but not blocked.
        if getattr(self, "auto_closed", 0):
            return
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
            frappe.throw(_("Only an Open session can be locked."), title=_("Ch Pos Session Error"))
        self.db_set("status", "Locked")
        self.status = "Locked"

    def unlock_session(self):
        """Unlock session — resume from lock screen."""
        if self.status != "Locked":
            frappe.throw(_("Session is not locked."), title=_("Ch Pos Session Error"))
        self.db_set("status", "Open")
        self.status = "Open"

    def close_session(self, closing_cash, denomination_rows=None, variance_reason=None,
                      manager_pin_user=None):
        """Close this session — called from POS UI."""
        _lk = f"sess_close_{frappe.scrub(self.name)}"
        if not frappe.db.sql("SELECT GET_LOCK(%s, 20)", (_lk,))[0][0]:
            frappe.throw(frappe._("Session {0} is already being closed.").format(self.name))
        try:
            _fresh = frappe.db.get_value("CH POS Session", self.name, "status")
            if _fresh not in ("Open", "Locked", "Pending Close"):
                frappe.throw(frappe._("Session {0} is already {1}.").format(self.name, _fresh))

            if self.status not in ("Open", "Locked", "Pending Close"):
                self.reload()
            if self.status not in ("Open", "Locked", "Pending Close"):
                frappe.throw(_("Session is not in a closable state (current: {0})").format(self.status), title=_("Ch Pos Session Error"))

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
            update_fields["closing_evidence_signature"] = self._expected_closing_signature()

            update_fields["modified"] = now_datetime()
            self.db_set(update_fields, update_modified=False)
            self.status = "Closed"
            self.closing_evidence_signature = update_fields["closing_evidence_signature"]

            # Close through ERPNext's standard POS Closing Entry.  CH POS
            # Session retains operational evidence, but it is not an accounting
            # voucher and must never be written into POS Opening Entry's Link.
            self._mirror_close_to_opening_entry()

            self._log_close_event()
        finally:
            frappe.db.sql("SELECT RELEASE_LOCK(%s)", (_lk,))

    def _mirror_close_to_opening_entry(self) -> str | None:
        """Create and submit the authoritative ERPNext POS Closing Entry."""
        entry = getattr(self, "pos_opening_entry", None)

        if not entry:
            # Adopt a still-open entry for this profile. Scoped to the session's
            # own profile and owner so it can never swallow another cashier's
            # live till.
            entry = frappe.db.get_value(
                "POS Opening Entry",
                {
                    "pos_profile": self.pos_profile,
                    "user": self.owner,
                    "docstatus": 1,
                    "status": "Open",
                    "pos_closing_entry": ("in", ("", None)),
                },
                "name",
                order_by="creation desc",
            )
            if entry:
                self.db_set("pos_opening_entry", entry, update_modified=False)

        if not entry:
            frappe.throw(
                _(
                    "Session {0} has no submitted POS Opening Entry. "
                    "Repair the opening record before closing the till."
                ).format(self.name),
                frappe.ValidationError,
            )

        opening = frappe.get_doc("POS Opening Entry", entry)
        if opening.docstatus != 1:
            frappe.throw(_("POS Opening Entry {0} is not submitted.").format(entry))

        linked_closing = opening.get("pos_closing_entry")
        if linked_closing and frappe.db.exists("POS Closing Entry", linked_closing):
            return entry
        if linked_closing:
            # Repair the historical bug where a CH POS Session name was stored
            # in the standard POS Closing Entry link.
            if linked_closing != self.name:
                frappe.throw(
                    _("POS Opening Entry {0} has an invalid closing link {1}.").format(
                        entry, linked_closing
                    )
                )
            frappe.db.set_value(
                "POS Opening Entry",
                entry,
                {"pos_closing_entry": None, "status": "Open"},
                update_modified=False,
            )
            opening.reload()
        elif opening.status != "Open":
            frappe.db.set_value("POS Opening Entry", entry, "status", "Open", update_modified=False)
            opening.reload()

        from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
            make_closing_entry_from_opening,
        )

        closing = make_closing_entry_from_opening(opening)
        opening_by_mode = {
            row.mode_of_payment: flt(row.opening_amount) for row in opening.balance_details
        }
        session_by_mode = {row.mode_of_payment: row for row in (self.payment_details or [])}
        reconciliation = {row.mode_of_payment: row for row in closing.payment_reconciliation}
        for mode, opening_amount in opening_by_mode.items():
            if mode not in reconciliation:
                reconciliation[mode] = closing.append(
                    "payment_reconciliation",
                    {"mode_of_payment": mode, "opening_amount": 0, "expected_amount": 0},
                )

        cash_recorded = False
        for mode, row in reconciliation.items():
            opening_amount = flt(opening_by_mode.get(mode))
            row.opening_amount = opening_amount
            row.expected_amount = flt(row.expected_amount) + opening_amount
            is_cash = frappe.db.get_value("Mode of Payment", mode, "type") == "Cash"
            if is_cash and not cash_recorded:
                row.closing_amount = flt(self.closing_cash_actual) + flt(self.total_cash_drops)
                cash_recorded = True
            elif mode in session_by_mode:
                row.closing_amount = flt(session_by_mode[mode].counted_amount)
            else:
                row.closing_amount = row.expected_amount
            row.difference = flt(row.closing_amount) - flt(row.expected_amount)

        closing.flags.ignore_permissions = True
        closing.insert(ignore_permissions=True)
        closing.submit()
        if frappe.db.get_value("POS Opening Entry", entry, "pos_closing_entry") != closing.name:
            closing.update_opening_entry()
        return entry

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


def is_session_stale(session) -> bool:
	"""True when a session's business date is behind the calendar day.

	A till belongs to ONE business date. Once the calendar rolls over, that
	session may no longer take sales — it has to be counted and closed so the
	day's cash and settlement land on the date they were earned.

	auto_close_stale_sessions() below is the scheduled sweep for this, but it is
	a cron job: with the scheduler off it never runs, and a session stayed open
	for ten days taking sales against a stale date. So the rule is enforced
	synchronously at the two points that matter — resuming a session and
	billing — instead of relying on the sweep.
	"""
	if not session:
		return False
	bd = session.get("business_date") if hasattr(session, "get") else None
	if not bd:
		return False
	return getdate(bd) < getdate(nowdate())


def auto_close_stale_sessions():
    """Scheduler: force-close sessions from previous business dates.

    Runs hourly. Sessions whose business_date < today and still Open
    are closed automatically with auto_closed=1.
    """
    lock_key = "auto_close_stale_sessions_lock"
    lock_result = frappe.db.sql("SELECT GET_LOCK(%s, 5)", (lock_key,))[0][0]
    if not lock_result:
        return  # Another worker is already running this
    try:
        today = getdate(nowdate())
        stale = frappe.get_all(
            "CH POS Session",
            filters={"status": ("in", ["Open", "Locked", "Suspended"]), "docstatus": 1, "business_date": ("<", today)},
            fields=["name", "pos_profile", "store", "business_date"],
            order_by="business_date asc, name asc",
            limit_page_length=max(
                1, min(cint(get_control_setting("scheduler_batch_limit", 500)), 5000)
            ),
        )
        for index, s in enumerate(stale):
            savepoint = f"auto_close_stale_{index}"
            frappe.db.savepoint(savepoint)
            try:
                doc = frappe.get_doc("CH POS Session", s.name)
                doc.auto_closed = 1
                doc.close_session(
                    closing_cash=0,
                    variance_reason="Auto-closed: session was open past business date",
                )
                frappe.logger("session").info(f"Auto-closed stale session {s.name} (biz date: {s.business_date})")
            except Exception:
                frappe.db.rollback(save_point=savepoint)
                frappe.log_error(frappe.get_traceback(), f"Auto-close failed for {s.name}")
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))


def auto_close_overnight_sessions():
    """Scheduler (cron 0 6 * * *): force-close ALL open sessions at 6 AM.

    Runs every day at 06:00 AM. Closes any session still in Open / Locked /
    Suspended state from yesterday or earlier, so cashiers are forced to
    open a fresh session when the store opens at 10:00 AM.

    P2-13: serialised via GET_LOCK so that overlapping scheduler workers
    cannot duplicate close audit logs / settlement attempts at the cron
    boundary.
    """
    from frappe.utils import add_days
    lock_key = "auto_close_overnight_sessions_lock"
    lock_result = frappe.db.sql("SELECT GET_LOCK(%s, 5)", (lock_key,))[0][0]
    if not lock_result:
        return
    try:
        yesterday = getdate(add_days(nowdate(), -1))
        open_sessions = frappe.get_all(
            "CH POS Session",
            filters={"status": ("in", ["Open", "Locked", "Suspended"]), "docstatus": 1, "business_date": ("<=", yesterday)},
            fields=["name", "pos_profile", "store", "business_date"],
            order_by="business_date asc, name asc",
            limit_page_length=max(
                1, min(cint(get_control_setting("scheduler_batch_limit", 500)), 5000)
            ),
        )
        for index, s in enumerate(open_sessions):
            savepoint = f"auto_close_overnight_{index}"
            frappe.db.savepoint(savepoint)
            try:
                doc = frappe.get_doc("CH POS Session", s.name)
                doc.auto_closed = 1
                doc.close_session(
                    closing_cash=0,
                    variance_reason="Auto-closed: overnight session expiry (6 AM close)",
                )
                frappe.logger("session").info(
                    f"Overnight auto-close: {s.name} (store: {s.store}, biz date: {s.business_date})"
                )
            except Exception:
                frappe.db.rollback(save_point=savepoint)
                frappe.log_error(frappe.get_traceback(), f"Overnight auto-close failed for {s.name}")
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))
