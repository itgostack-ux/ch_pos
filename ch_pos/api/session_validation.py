"""
Session Validation — Queue Queue Request Enforcement
Prevents user logout/session close when pending (unbilled/unrejected) tokens exist.
Enforces EOD token handling: all tokens must be either billed (Converted) or rejected.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime


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
	user = frappe.session.user
	company = frappe.db.get_value("User", user, "company") or frappe.get_cached_value("System Settings", None, "default_company")

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

	filters = {
		"docstatus": [">", 0],  # Only submitted tokens
		"status": ["in", PENDING_STATUSES],
	}

	if pos_profile:
		filters["pos_profile"] = pos_profile
	if store_code:
		filters["store"] = store_code
	if company:
		filters["company"] = company

	pending = frappe.get_list(
		"POS Kiosk Token",
		fields=["name", "status", "customer_name", "created"],
		filters=filters,
		limit_page_length=None,  # Get all pending
		order_by="creation desc",
	)

	warning = None
	if pending:
		plural = "token" if len(pending) == 1 else "tokens"
		warning = _("Cannot close session — {count} {plural} {verb} pending: {names}").format(
			count=len(pending),
			plural=plural,
			verb="is" if len(pending) == 1 else "are",
			names=", ".join(t["name"] for t in pending[:5]) + ("..." if len(pending) > 5 else "")
		)

	return {
		"count": len(pending),
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

	# Find all submitted, pending tokens
	pending_tokens = frappe.get_list(
		"POS Kiosk Token",
		fields=["name", "status", "pos_profile", "store"],
		filters={
			"docstatus": [">", 0],  # Only submitted
			"status": ["in", PENDING_STATUSES],
		},
		limit_page_length=None,
	)

	closed_count = 0
	errors = []

	for token_row in pending_tokens:
		token_name = token_row["name"]
		token_status = token_row["status"]

		try:
			# Skip if expires_at is in the future (still valid)
			expires_at = frappe.db.get_value("POS Kiosk Token", token_name, "expires_at")
			if expires_at and get_datetime(expires_at) > now:
				continue

			# Determine auto-close action based on current status
			new_status = "Cancelled" if token_status in ("Waiting", "Hold") else "Dropped"

			frappe.db.set_value(
				"POS Kiosk Token",
				token_name,
				{
					"status": new_status,
					"drop_reason": "Auto-closed at EOD" if new_status == "Dropped" else None,
				},
				update_modified=False,
			)

			log.info(f"Auto-closed token {token_name}: {token_status} → {new_status}")
			closed_count += 1

			# Log audit event
			try:
				from ch_pos.audit import log_business_event
				log_business_event(
					event_type="EOD Auto-Close",
					ref_doctype="POS Kiosk Token",
					ref_name=token_name,
					before=token_status,
					after=new_status,
					remarks=f"Auto-closed at EOD (expired or end-of-shift)",
					company=frappe.db.get_value("POS Kiosk Token", token_name, "company") or "",
				)
			except Exception as e:
				log.warning(f"Audit log failed for {token_name}: {str(e)}")

		except Exception as e:
			err_msg = f"Failed to auto-close token {token_name}: {str(e)}"
			log.error(err_msg)
			errors.append(err_msg)

	if errors:
		frappe.log_error("\n".join(errors), "EOD Token Auto-Close Errors")

	log.info(f"EOD auto-close complete: {closed_count} tokens closed, {len(errors)} errors")


def get_pending_token_count_for_user() -> int:
	"""Simple helper for dashboard: get count of pending tokens for current user's store."""
	pending_info = get_pending_tokens_for_store()
	return pending_info["count"]
