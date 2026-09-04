# from __future__ import annotations

# import re

# import frappe
# from frappe import _


# PRIVILEGED_USER = "Administrator"
# PRIVILEGED_ROLE = "System Manager"


# def is_privileged_user(user: str | None = None) -> bool:
# 	"""Return whether ``user`` has the immutable POS administration bypass.

# 	The bypass is deliberately not stored in CH POS Control Settings: removing a
# 	role from configurable action lists must never lock Administrator or System
# 	Manager out of recovery and control-plane operations.
# 	"""
# 	user = user or frappe.session.user
# 	if user == PRIVILEGED_USER:
# 		return True
# 	return PRIVILEGED_ROLE in set(frappe.get_roles(user))


# def require_authenticated_user() -> None:
# 	if frappe.session.user == "Guest":
# 		frappe.throw(_("You must be signed in to perform this action."), frappe.PermissionError)


# def require_privileged_user(action: str | None = None) -> None:
# 	require_authenticated_user()
# 	if is_privileged_user():
# 		return
# 	frappe.throw(
# 		_("Only Administrator or System Manager may {0}.").format(
# 			action or _("perform this action")
# 		),
# 		frappe.PermissionError,
# 		title=_("Permission Denied"))


# _NUMERIC_FIELDTYPES = ("Int", "Float", "Currency", "Percent")


# def get_control_setting(fieldname: str, default=None):
# 	"""Read one POS control setting, treating an untouched numeric as unset.

# 	A Single stores an Int nobody has ever edited as 0, not NULL, so falling
# 	back only on None meant the declared default never arrived. Every caller
# 	then clamped that 0 up to its floor: `max(1, min(cint(setting), cap))` is 1
# 	when the setting reads 0. Row limits across the app were therefore 1 — which
# 	is why the POS company bar offered a single company and the Billed By list
# 	came back empty, and why scheduler sweeps handled one record per run.

# 	So: for a numeric field whose docfield declares a non-zero default, a falsy
# 	stored value means "never configured" and the caller's default applies. A
# 	field genuinely meant to be zero declares zero, and is returned untouched.
# 	"""
# 	try:
# 		meta = frappe.get_meta("CH POS Control Settings")
# 		df = meta.get_field(fieldname)
# 		if not df:
# 			return default
# 		value = frappe.get_cached_value("CH POS Control Settings", None, fieldname)
# 	except Exception:
# 		return default
# 	if value is None:
# 		return default
# 	if (
# 		default is not None
# 		and not value
# 		and df.fieldtype in _NUMERIC_FIELDTYPES
# 		and frappe.utils.flt(df.default) != 0
# 	):
# 		return default
# 	return value


# # ---------------------------------------------------------------------------
# # Operational override gates only. Everything else is enforced by native
# # Frappe DocPerm via frappe.has_permission(...).
# # ---------------------------------------------------------------------------
# def get_configured_roles(fieldname: str, defaults=()) -> set[str]:
# 	from ch_erp15.role_settings import get_setting_roles

# 	return set(get_setting_roles("CH POS Control Settings", fieldname, defaults))


# # Groups whose items are fitted on a repair rather than sold over the counter.
# # Seeded into CH POS Control Settings by setup; the setting is authoritative
# # from then on, so an operator can add or remove a group without a code change.
# DEFAULT_REPAIR_CONSUMABLE_GROUPS = ("Spares", "Sub Assemblies")


# def get_repair_consumable_item_groups() -> list[str]:
# 	"""Item groups the POS sell catalogue must not offer.

# 	A spare is stock a technician fits and the customer pays for on the service
# 	invoice — never something a counter sells on its own. These groups used to
# 	be written into the search SQL (including two that exist as Item Groups on
# 	no site here), so the rule could be neither seen nor changed.

# 	The configured list is authoritative, empty included: emptying it means no
# 	group is restricted. The defaults apply only where the setting has not been
# 	installed yet.
# 	"""
# 	try:
# 		if not frappe.get_meta("CH POS Control Settings").has_field(
# 			"repair_consumable_item_groups"
# 		):
# 			return list(DEFAULT_REPAIR_CONSUMABLE_GROUPS)
# 		rows = frappe.get_all(
# 			"CH POS Item Group Link",
# 			filters={"parenttype": "CH POS Control Settings",
# 			         "parentfield": "repair_consumable_item_groups"},
# 			pluck="item_group",
# 		)
# 	except Exception:
# 		return list(DEFAULT_REPAIR_CONSUMABLE_GROUPS)
# 	return [g for g in rows if g]


# def has_app_permission(user: str | None = None) -> bool:
# 	user = user or frappe.session.user
# 	if not user or user == "Guest":
# 		return False
# 	return has_configured_roles("app_access_roles", user=user)


# def has_configured_roles(fieldname: str, defaults=(), user: str | None = None) -> bool:
# 	"""Return a server-authoritative capability for a configured role set."""
# 	user = user or frappe.session.user
# 	if not user or user == "Guest":
# 		return False
# 	if is_privileged_user(user):
# 		return True
# 	return bool(set(frappe.get_roles(user)) & get_configured_roles(fieldname, defaults))


# def has_any_roles(roles, user: str | None = None) -> bool:
# 	user = user or frappe.session.user
# 	if not user or user == "Guest":
# 		return False
# 	if is_privileged_user(user):
# 		return True
# 	return bool(set(frappe.get_roles(user)).intersection(role for role in roles if role))


# def require_configured_roles(fieldname: str, defaults=(), action: str | None = None) -> None:
# 	require_authenticated_user()
# 	if has_configured_roles(fieldname, defaults):
# 		return
# 	frappe.throw(
# 		_("You do not have permission to {0}. Required role: {1}").format(
# 			action or _("perform this action"),
# 			", ".join(sorted(get_configured_roles(fieldname, defaults))) or _("none configured")),
# 		frappe.PermissionError,
# 		title=_("Permission Denied"))


# def assert_session_operator(session, action: str) -> None:
# 	"""Allow a session owner, privileged user, or configured override role."""
# 	if session.user == frappe.session.user or is_privileged_user():
# 		return
# 	require_configured_roles(
# 		"session_override_roles",
# 		action=action)










































from __future__ import annotations

import re
from typing import Iterable

import frappe
from frappe import _


PRIVILEGED_USER = "Administrator"
PRIVILEGED_ROLE = "System Manager"


def is_privileged_user(user: str | None = None) -> bool:
	"""Return whether ``user`` has the immutable POS administration bypass."""
	try:
		user = user or frappe.session.user
		if user == PRIVILEGED_USER:
			return True
		return PRIVILEGED_ROLE in set(frappe.get_roles(user))
	except Exception:
		return False


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
		title=_("Permission Denied"),
	)


_NUMERIC_FIELDTYPES = ("Int", "Float", "Currency", "Percent")


def get_control_setting(fieldname: str, default=None):
	"""Read one POS control setting safely."""
	try:
		meta = frappe.get_meta("CH POS Control Settings")
		df = meta.get_field(fieldname)
		if not df:
			return default
		value = frappe.get_cached_value("CH POS Control Settings", None, fieldname)
	except Exception:
		return default

	if value is None:
		return default

	if (
		default is not None
		and not value
		and df.fieldtype in _NUMERIC_FIELDTYPES
		and frappe.utils.flt(df.default) != 0
	):
		return default

	return value


def _safe_role_set(val) -> set[str]:
	"""Safely parse string, list, tuple, or set into a clean set of role names.

	Prevents: TypeError: expected string or bytes-like object, got 'list'
	"""
	if not val:
		return set()

	# If it's already a list, tuple, or set of roles
	if isinstance(val, (list, tuple, set)):
		out: set[str] = set()
		for item in val:
			out.update(_safe_role_set(item))
		return out

	# If it's a string (e.g. "POS User, Sales User")
	if isinstance(val, str):
		return {r.strip() for r in re.split(r"[,;\n]+", val) if r.strip()}

	return set()


def get_configured_roles(fieldname: str, defaults: Iterable[str] | None = ()) -> set[str]:
	"""Return set of configured roles safely without throwing 500 errors."""
	try:
		from ch_erp15.role_settings import get_setting_roles

		raw_roles = get_setting_roles("CH POS Control Settings", fieldname, defaults)
		roles = _safe_role_set(raw_roles)
		if roles:
			return roles
	except Exception:
		pass

	return _safe_role_set(defaults)


DEFAULT_REPAIR_CONSUMABLE_GROUPS = ("Spares", "Sub Assemblies")


def get_repair_consumable_item_groups() -> list[str]:
	"""Item groups the POS sell catalogue must not offer."""
	try:
		if not frappe.get_meta("CH POS Control Settings").has_field(
			"repair_consumable_item_groups"
		):
			return list(DEFAULT_REPAIR_CONSUMABLE_GROUPS)

		rows = frappe.get_all(
			"CH POS Item Group Link",
			filters={
				"parenttype": "CH POS Control Settings",
				"parentfield": "repair_consumable_item_groups",
			},
			pluck="item_group",
		)
	except Exception:
		return list(DEFAULT_REPAIR_CONSUMABLE_GROUPS)

	return [g for g in rows if g]


def has_app_permission(user: str | None = None) -> bool:
	# This is a Workspace ``has_permission`` hook — it runs for every user during
	# desk boot (load_desktop_data). If it RAISES, boot fails with
	# SessionBootFailed and the user cannot log in at all; only Administrator /
	# System Manager escape, because has_configured_roles short-circuits them
	# before any role lookup. So it must fail CLOSED, never loud: any internal
	# error hides the workspace (safe) instead of bricking login (catastrophic).
	# The classic trigger was a roles field that changed from free text to a
	# Table MultiSelect — old code parsed the resulting list as a string.
	"""Check app permission for Desk icon loading without crashing."""
	try:
		user = user or frappe.session.user
		if not user or user == "Guest":
			return False
		return has_configured_roles("app_access_roles", user=user)
	except Exception:
		frappe.log_error(
			title="ch_pos has_app_permission failed — denying, not crashing boot",
			message=frappe.get_traceback(),
		)
		# Fail gracefully so desk loads without 500 Server Error
		return False


def has_configured_roles(
	fieldname: str,
	defaults: Iterable[str] | None = (),
	user: str | None = None,
) -> bool:
	"""Return capability for a user without throwing exceptions."""
	try:
		user = user or frappe.session.user
		if not user or user == "Guest":
			return False

		# System Manager / Administrator bypass
		if is_privileged_user(user):
			return True

		configured = get_configured_roles(fieldname, defaults)
		
		# If no specific roles are configured, allow standard logged-in users
		if not configured:
			return True

		user_roles = set(frappe.get_roles(user))
		return bool(user_roles & configured)
	except Exception:
		# If anything goes wrong, return False gracefully instead of 500 Error
		return False


def has_any_roles(roles, user: str | None = None) -> bool:
	try:
		user = user or frappe.session.user
		if not user or user == "Guest":
			return False

		if is_privileged_user(user):
			return True

		return bool(set(frappe.get_roles(user)).intersection(_safe_role_set(roles)))
	except Exception:
		return False


def require_configured_roles(
	fieldname: str,
	defaults: Iterable[str] | None = (),
	action: str | None = None,
) -> None:
	require_authenticated_user()
	if has_configured_roles(fieldname, defaults):
		return

	needed = ", ".join(sorted(get_configured_roles(fieldname, defaults))) or _("none configured")
	frappe.throw(
		_("You do not have permission to {0}. Required role: {1}").format(
			action or _("perform this action"),
			needed,
		),
		frappe.PermissionError,
		title=_("Permission Denied"),
	)


def assert_session_operator(session, action: str) -> None:
	"""Allow a session owner, privileged user, or configured override role."""
	if getattr(session, "user", None) == frappe.session.user or is_privileged_user():
		return
	require_configured_roles("session_override_roles", action=action)




