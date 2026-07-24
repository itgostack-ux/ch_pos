"""
Session Validation — Queue Queue Request Enforcement
Prevents user logout/session close when pending (unbilled/unrejected) tokens exist.
Enforces EOD token handling: all tokens must be either billed (Converted) or rejected.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from ch_pos.api.scope_guard import assert_pos_profile_scope
from ch_pos.config import get_control_setting, require_authenticated_user, require_configured_roles


PENDING_STATUSES = ("Waiting", "Hold", "Engaged", "In Progress")
CLOSED_STATUSES = ("Completed", "Converted", "Cancelled", "Dropped", "Expired")


@frappe.whitelist()
@frappe.read_only()
def get_pending_tokens_for_store(store_code: str = None, pos_profile: str = None) -> dict:
	"""
	Get count and list of pending tokens for a store.
	Used to warn users before logout.

	Args:
		store_code: Warehouse name (store)
		pos_profile: POS Profile name

	Returns:
		{
			"count": <int>,
			"tokens": [{"name": "...", "status": "...", "customer_name": "..."}],
			"warning": <str or None>
		}
	"""
	require_authenticated_user()
	require_configured_roles(
		"token_view_roles",
		defaults=("POS User", "POS Manager", "Store Manager", "Technician"),
		action=_("view pending store tokens"),
	)
	user = frappe.session.user

	# If pos_profile not provided, try to get it from recent POS sessions
	if not pos_profile:
		recent_session = frappe.get_value(
			"CH POS Session",
			{"user": user, "docstatus": [">", 0]},  # Only submitted sessions
			["pos_profile"],
			order_by="creation desc",
		)
		if recent_session:
			pos_profile = recent_session[0] if isinstance(recent_session, (list, tuple)) else recent_session
	if not pos_profile:
		return {"count": 0, "tokens": [], "warning": None}

	anchors = assert_pos_profile_scope(pos_profile)
	if store_code and store_code not in {anchors.get("store"), anchors.get("warehouse")}:
		frappe.throw(_("Store does not match the selected POS Profile."), frappe.PermissionError)

	filters = {
		"docstatus": [">", 0],  # Only submitted tokens
		"status": ["in", PENDING_STATUSES],
	}

	filters["pos_profile"] = pos_profile
	filters["company"] = anchors.get("company")

	total = frappe.db.count("POS Kiosk Token", filters=filters)
	pending = frappe.get_all(
		"POS Kiosk Token",
		fields=["name", "status", "customer_name", "creation"],
		filters=filters,
		limit_page_length=100,
		order_by="creation desc",
	)

	warning = None
	if total:
		plural = "token" if total == 1 else "tokens"
		warning = _("Cannot close session — {count} {plural} {verb} pending: {names}").format(
			count=total,
			plural=plural,
			verb="is" if total == 1 else "are",
			names=", ".join(t["name"] for t in pending[:5]) + ("..." if total > 5 else "")
		)

	return {
		"count": total,
		"tokens": pending,
		"warning": warning,
	}


def validate_no_pending_tokens_on_logout() -> None:
	"""
	Hook: Prevent user logout if pending tokens exist for their assigned store.
	Called during session-end event.
	"""
	user = frappe.session.user

	# Try to get user's POS profile from recent sessions
	pos_profile = None
	recent_session = frappe.db.get_value(
		"CH POS Session",
		{"user": user, "docstatus": [">", 0]},
		["pos_profile"],
		order_by="creation desc",
	)
	if recent_session:
		pos_profile = recent_session[0] if isinstance(recent_session, (list, tuple)) else recent_session

	# If no recent session found, user is not a POS operator
	if not pos_profile:
		return

	pending_info = get_pending_tokens_for_store(pos_profile=pos_profile)

	if pending_info["count"] > 0:
		frappe.throw(
			_("Cannot close session — {count} queue tokens are still pending.\n"
			  "Please handle all tokens (bill/close or reject) before logging out.\n"
			  "Pending: {names}").format(
				count=pending_info["count"],
				names=", ".join(t["name"] for t in pending_info["tokens"][:10])
			),
			title=_("Pending Queue Tokens"),
		)


def auto_close_pending_tokens_at_eod() -> None:
	"""
	Scheduler: Auto-expire or auto-reject tokens that haven't been handled by EOD.
	Runs daily at close-of-business (11:59 PM).

	Strategy:
	- For tokens in "Waiting"/"Hold": auto-reject (set to "Cancelled")
	- For tokens in "Engaged"/"In Progress": set to "Dropped" (unfinished service)
	"""
	import logging
	log = logging.getLogger("ch_pos.session_validation")

	now = now_datetime()

	batch_limit = max(1, min(cint(get_control_setting("scheduler_batch_limit", 500)), 5000))
	pending_tokens = frappe.db.sql(
		"""
		SELECT name, status, pos_profile, store, company, expires_at
		FROM `tabPOS Kiosk Token`
		WHERE docstatus > 0
		  AND status IN %(statuses)s
		  AND (expires_at IS NULL OR expires_at <= %(now)s)
		ORDER BY expires_at ASC, name ASC
		LIMIT %(limit)s
		FOR UPDATE
		""",
		{"statuses": PENDING_STATUSES, "now": now, "limit": batch_limit},
		as_dict=True,
	)

	closed_count = 0
	errors = []

	cancelled_names = [row.name for row in pending_tokens if row.status in ("Waiting", "Hold")]
	dropped_names = [row.name for row in pending_tokens if row.status in ("Engaged", "In Progress")]

	try:
		if cancelled_names:
			frappe.db.set_value(
				"POS Kiosk Token",
				{"name": ("in", cancelled_names)},
				{"status": "Cancelled", "drop_reason": None},
				update_modified=False,
			)
		if dropped_names:
			frappe.db.set_value(
				"POS Kiosk Token",
				{"name": ("in", dropped_names)},
				{"status": "Dropped", "drop_reason": "Auto-closed at EOD"},
				update_modified=False,
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "EOD Token Auto-Close Update Failed")
		raise

	from ch_pos.audit import log_business_event
	for token_row in pending_tokens:
		new_status = "Cancelled" if token_row.status in ("Waiting", "Hold") else "Dropped"
		closed_count += 1
		log.info(f"Auto-closed token {token_row.name}: {token_row.status} → {new_status}")
		try:
			log_business_event(
				event_type="EOD Auto-Close",
				ref_doctype="POS Kiosk Token",
				ref_name=token_row.name,
				before=token_row.status,
				after=new_status,
				remarks="Auto-closed at EOD (expired or end-of-shift)",
				store=token_row.store,
				company=token_row.company or "",
			)
		except Exception as exc:
			err_msg = f"Audit log failed for {token_row.name}: {str(exc)}"
			log.warning(err_msg)
			errors.append(err_msg)

	if errors:
		frappe.log_error("\n".join(errors), "EOD Token Auto-Close Errors")

	log.info(f"EOD auto-close complete: {closed_count} tokens closed, {len(errors)} errors")


def get_pending_token_count_for_user() -> int:
	"""Simple helper for dashboard: get count of pending tokens for current user's store."""
	pending_info = get_pending_tokens_for_store()
	return pending_info["count"]
