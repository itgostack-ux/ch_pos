from __future__ import annotations

import re

import frappe
from frappe import _


PRIVILEGED_USER = "Administrator"
PRIVILEGED_ROLE = "System Manager"


def is_privileged_user(user: str | None = None) -> bool:
	"""Return whether ``user`` has the immutable POS administration bypass.

	The bypass is deliberately not stored in CH POS Control Settings: removing a
	role from configurable action lists must never lock Administrator or System
	Manager out of recovery and control-plane operations.
	"""
	user = user or frappe.session.user
	if user == PRIVILEGED_USER:
		return True
	return PRIVILEGED_ROLE in set(frappe.get_roles(user))


def require_authenticated_user() -> None:
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be signed in to perform this action."), frappe.PermissionError)


def require_privileged_user(action: str | None = None) -> None:
	require_authenticated_user()
	if is_privileged_user():
		return
	frappe.throw(
		_("Only Administrator or System Manager may {0}.").format(
			action or _("perform this action")
		),
		frappe.PermissionError,
		title=_("Permission Denied"))


def get_control_setting(fieldname: str, default=None):
	try:
		if not frappe.get_meta("CH POS Control Settings").has_field(fieldname):
			return default
		value = frappe.get_cached_value("CH POS Control Settings", None, fieldname)
	except Exception:
		return default
	return default if value is None else value


# ---------------------------------------------------------------------------
# Operational override gates only. Everything else is enforced by native
# Frappe DocPerm via frappe.has_permission(...).
# ---------------------------------------------------------------------------
def get_configured_roles(fieldname: str, defaults=()) -> set[str]:
	from ch_erp15.role_settings import get_setting_roles

	return set(get_setting_roles("CH POS Control Settings", fieldname, defaults))


def has_app_permission(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	return has_configured_roles("app_access_roles", user=user)


def has_configured_roles(fieldname: str, defaults=(), user: str | None = None) -> bool:
	"""Return a server-authoritative capability for a configured role set."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	if is_privileged_user(user):
		return True
	return bool(set(frappe.get_roles(user)) & get_configured_roles(fieldname, defaults))


def has_any_roles(roles, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	if is_privileged_user(user):
		return True
	return bool(set(frappe.get_roles(user)).intersection(role for role in roles if role))


def require_configured_roles(fieldname: str, defaults=(), action: str | None = None) -> None:
	require_authenticated_user()
	if has_configured_roles(fieldname, defaults):
		return
	frappe.throw(
		_("You do not have permission to {0}. Required role: {1}").format(
			action or _("perform this action"),
			", ".join(sorted(get_configured_roles(fieldname, defaults))) or _("none configured")),
		frappe.PermissionError,
		title=_("Permission Denied"))


def assert_session_operator(session, action: str) -> None:
	"""Allow a session owner, privileged user, or configured override role."""
	if session.user == frappe.session.user or is_privileged_user():
		return
	require_configured_roles(
		"session_override_roles",
		action=action)
