"""The token's status vocabulary must exist in exactly one place.

Written after Service Intake's "Pick from Queue" said "Nothing waiting" while
the Front Desk, on the next screen, showed the same store with "1 in store,
1 active". GF-AMBATTUR-001 was sitting In Progress; the picker kept its own list
of ("Waiting", "Hold", "Engaged") and had never been told the status existed.

The same three-status list was in the phone lookup that reunites a typed number
with a queued customer, so an engaged customer was counted twice -- the exact
double-count that function's docstring says it prevents. The store hub filtered
on "In Queue", a status this doctype has never had.
"""

import frappe

from ch_pos.pos_kiosk.doctype.pos_kiosk_token.pos_kiosk_token import (
	EXPIRABLE_STATUSES, OPEN_STATUSES, QUEUE_STATUSES, SERVING_STATUSES,
	TERMINAL_STATUSES,
)

_results = []


def _check(label, cond, detail=""):
	_results.append(("PASS" if cond else "FAIL", label, str(detail)[:74]))


def run_all():
	_results.clear()
	frappe.set_user("Administrator")

	# ── 1. The constants match the doctype's own Select field ────────────
	options = frappe.get_meta("POS Kiosk Token").get_field("status").options or ""
	declared = tuple(o.strip() for o in options.split("\n") if o.strip())
	_check("the doctype still declares the statuses we assume",
	       set(OPEN_STATUSES) | set(TERMINAL_STATUSES) == set(declared),
	       sorted(set(declared) ^ (set(OPEN_STATUSES) | set(TERMINAL_STATUSES))))
	_check("open and terminal do not overlap",
	       not (set(OPEN_STATUSES) & set(TERMINAL_STATUSES)),
	       set(OPEN_STATUSES) & set(TERMINAL_STATUSES))
	_check("queue + serving is exactly open",
	       set(QUEUE_STATUSES) | set(SERVING_STATUSES) == set(OPEN_STATUSES),
	       set(OPEN_STATUSES) ^ (set(QUEUE_STATUSES) | set(SERVING_STATUSES)))
	_check("expiry never closes a token being actively served",
	       "In Progress" not in EXPIRABLE_STATUSES, EXPIRABLE_STATUSES)
	_check("'In Queue' is not a status anyone should filter on",
	       "In Queue" not in declared, declared)

	# ── 2. No screen keeps its own copy any more ─────────────────────────
	import re
	from pathlib import Path

	root = Path(frappe.get_app_path("ch_pos"))
	strays = []
	pattern = re.compile(r'[\(\[]\s*["\']Waiting["\']\s*,\s*["\']Hold["\']\s*,'
	                     r'\s*["\']Engaged["\']\s*[\)\]]')
	for path in list(root.rglob("*.py")) + list(root.rglob("*.js")):
		if ("node_modules" in str(path) or "/dist/" in str(path)
				or path.name.startswith("test_")
				# The canonical file is allowed to name them: EXPIRABLE_STATUSES
				# really is Waiting/Hold/Engaged, and deliberately so.
				or path.name == "pos_kiosk_token.py"):
			continue
		for lineno, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
			stripped = line.strip()
			if stripped.startswith(("#", "//", "*")):
				continue  # a comment quoting the old list is documentation
			if pattern.search(line):
				strays.append(f"{path.name}:{lineno}")
	_check("no file re-declares the three-status list that caused this",
	       not strays, strays)

	# ── 3. An In Progress token is still 'open' everywhere it matters ────
	live = frappe.db.sql("""
		SELECT name, token_display, status, pos_profile, company
		FROM `tabPOS Kiosk Token`
		WHERE status = 'In Progress' AND IFNULL(linked_service_request, '') = ''
		LIMIT 4""", as_dict=True)
	_check("there is an In Progress token to reason about", bool(live), len(live))

	for tok in live:
		from ch_pos.api.token_api import get_pos_waiting_tokens

		rows = get_pos_waiting_tokens(tok.pos_profile)
		match = [r for r in rows if r.get("name") == tok.name]
		_check(f"{tok.token_display or tok.name} ({tok.company.split()[0]}) reaches the queue API",
		       bool(match), f"{len(rows)} rows for {tok.pos_profile}")
		if match:
			_check(f"{tok.token_display or tok.name} is labelled open for the client",
			       match[0].get("is_open") is True, match[0].get("is_open"))
			# This is the filter Service Intake now applies.
			pickable = [r for r in rows
			            if not r.get("linked_service_request") and r.get("is_open") is not False]
			_check(f"{tok.token_display or tok.name} is pickable from Service Intake",
			       any(r.get("name") == tok.name for r in pickable),
			       f"{len(pickable)} pickable")

		# ── 4. The phone lookup finds them too (the double-count fix) ────
		from ch_pos.api.token_api import find_waiting_token_by_phone

		phone = frappe.db.get_value("POS Kiosk Token", tok.name, "customer_phone")
		if phone:
			found = find_waiting_token_by_phone(tok.pos_profile, phone)
			_check(f"typing {phone} finds the open token instead of raising a second one",
			       (found or {}).get("name") == tok.name, (found or {}).get("name"))

	for status, label, detail in _results:
		print(f"{status}  {label:<62} {detail}")
	failed = sum(1 for s, _l, _d in _results if s == "FAIL")
	print(f"TOTAL: {len(_results) - failed} passed, {failed} failed")
	return {"passed": len(_results) - failed, "failed": failed}
