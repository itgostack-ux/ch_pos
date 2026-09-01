# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""Row-level scope for ch_pos masters — POS Executive.

WHY: POS Executive rows key till access, discount ceilings and sales-team
attribution per store. Before this module every reader with a matching
DocPerm role (e.g. Stock Manager) saw the WHOLE bench — a Bestbuy-scoped
user could list every GOFIX executive, their stores and discount limits.
That is the same leak class ch_erp15's txn_scope closed for the standard
transaction doctypes, so this module applies the identical fail-closed
contract (see ch_erp15.ch_erp15.txn_scope):

1. Bypass users (System Manager / Administrator / configured bypass roles,
   as resolved by ``get_user_scope``) — no filter.
2. Scoped user — rows whose ``store`` is inside the resolved store set, plus
   store-less rows of a directly granted company (an executive provisioned
   at company level before store assignment must stay visible to the
   company's own admins, not vanish).
3. Authenticated user with no scope — sees nothing (``1=0`` / False).

Both hooks are wired in ch_pos/hooks.py so list views, link searches,
dashboard counts and direct-URL document reads agree on one truth.
"""

from __future__ import annotations

import frappe
from ch_erp15.ch_erp15.scope import get_user_scope


def pos_executive_query(user: str | None = None) -> str | None:
	"""``permission_query_conditions`` for POS Executive."""
	user = user or frappe.session.user
	scope = get_user_scope(user)
	if scope.get("bypass"):
		return None

	stores = scope.get("stores") or set()
	direct_companies = scope.get("direct_companies") or set()

	clauses: list[str] = []
	if stores:
		store_sql = ", ".join(frappe.db.escape(s) for s in stores)
		clauses.append(f"`tabPOS Executive`.`store` IN ({store_sql})")
	if direct_companies:
		company_sql = ", ".join(frappe.db.escape(c) for c in direct_companies)
		clauses.append(
			"(COALESCE(`tabPOS Executive`.`store`, '') = '' "
			f"AND `tabPOS Executive`.`company` IN ({company_sql}))"
		)
	if not clauses:
		# Fail closed: an authenticated user without any resolved scope has
		# no business browsing till-access assignments.
		return "1=0"
	return "(" + " OR ".join(clauses) + ")"


def has_pos_executive_permission(doc=None, ptype=None, user=None):
	"""``has_permission`` gate for direct POS Executive reads by name."""
	if doc is None:
		return None
	user = user or frappe.session.user
	scope = get_user_scope(user)
	if scope.get("bypass"):
		return True

	stores = scope.get("stores") or set()
	direct_companies = scope.get("direct_companies") or set()

	store = doc.get("store") if hasattr(doc, "get") else getattr(doc, "store", None)
	company = doc.get("company") if hasattr(doc, "get") else getattr(doc, "company", None)

	if store:
		return store in stores
	# Store-less row: only a direct company grant may see it (mirrors
	# has_ch_store_permission's use of direct_* to distinguish an explicit
	# company-wide grant from a store-derived company value).
	return bool(company and company in direct_companies)
