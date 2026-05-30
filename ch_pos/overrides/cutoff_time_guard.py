"""B2 — POS daily cut-off time enforcement.

Each POS Profile may define a daily ``ch_cutoff_time`` (Time). Once the
server clock passes that time on the invoice's ``posting_date``, new
Sales Invoices on that profile are blocked unless the current user
holds the ``ch_cutoff_override_role`` configured on the same profile
(default: ``POS Manager``).

Why a server-side validator (in addition to the existing EOD-lock
``validate_eod_lock``): EOD-lock only kicks in *after* the cashier
closes the session; the cut-off draws a hard line on the wall clock
so late-night sales cannot bleed into the next business day.

Bypass: ``frappe.flags.ignore_pos_cutoff`` (used by unit tests and
by batch jobs that legitimately back-fill yesterday's invoices).
"""

from __future__ import annotations

import datetime as _dt

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, now_datetime


def _coerce_time(value) -> _dt.time | None:
	if value in (None, ""):
		return None
	if isinstance(value, _dt.time):
		return value
	if isinstance(value, _dt.datetime):
		return value.time()
	if isinstance(value, _dt.timedelta):
		total = int(value.total_seconds())
		h, rem = divmod(total, 3600)
		m, s = divmod(rem, 60)
		return _dt.time(h % 24, m, s)
	# String "HH:MM:SS" / "HH:MM"
	text = str(value).strip()
	for fmt in ("%H:%M:%S", "%H:%M"):
		try:
			return _dt.datetime.strptime(text, fmt).time()
		except ValueError:
			continue
	return None


def validate_pos_cutoff_time(doc, method=None):
	"""doc_events ``validate`` hook for Sales Invoice."""
	if getattr(frappe.flags, "ignore_pos_cutoff", False):
		return
	if not getattr(doc, "pos_profile", None):
		return
	# Only enforce on new docs — let edits / cancels through.
	if not doc.is_new() and doc.docstatus == 0:
		return

	profile = frappe.db.get_value(
		"POS Profile",
		doc.pos_profile,
		["ch_cutoff_time", "ch_cutoff_override_role"],
		as_dict=True,
	)
	if not profile:
		return

	cutoff = _coerce_time(profile.get("ch_cutoff_time"))
	if cutoff is None:
		return

	now = now_datetime()
	# Cut-off applies to TODAY's business date; back-dated and future-dated
	# invoices are out of scope (handled by frozen-period / accounting_date).
	posting_date = getdate(doc.posting_date) if doc.posting_date else now.date()
	if posting_date != now.date():
		return

	if now.time() <= cutoff:
		return

	override_role = (profile.get("ch_cutoff_override_role") or "POS Manager").strip()
	user_roles = set(frappe.get_roles(frappe.session.user))
	if override_role and override_role in user_roles:
		return
	if "System Manager" in user_roles:
		return

	frappe.throw(
		_(
			"POS cut-off time {0} for profile <b>{1}</b> has passed (server "
			"time {2}). New invoices are blocked. Override role required: "
			"<b>{3}</b>."
		).format(
			cutoff.strftime("%H:%M"),
			doc.pos_profile,
			now.strftime("%H:%M"),
			override_role,
		),
		title=_("POS Cut-off Reached"),
	)
