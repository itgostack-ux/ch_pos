# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""CH Gift Redemption — one-time spin-wheel freebie code.

Lifecycle:
    Issued  → (customer clicks link, spins wheel) → Revealed
    Revealed → (cashier enters code within TTL) → Redeemed
    Issued|Revealed → (TTL elapsed) → Expired
    Any → (admin) → Cancelled

The DocType is purposefully thin — all state transitions are performed by
:mod:`ch_pos.api.gift_redemption` under advisory locks / row locks so that
concurrent spins/redemptions cannot double-issue the free invoice.
"""

import hashlib
import hmac
import secrets

import frappe
from frappe import _
from frappe.model.document import Document

from ch_pos.config import is_privileged_user


TERMINAL_STATUSES = ("Redeemed", "Expired", "Cancelled")


class CHGiftRedemption(Document):
	def before_insert(self):
		if not self.flags.get("gift_redemption_engine_write") and not is_privileged_user():
			frappe.throw(_("Gift redemptions can only be issued by the gift engine."), frappe.PermissionError)
		self.status = "Issued"
		self.spin_token = secrets.token_urlsafe(32)
		self.spin_token_digest = hashlib.sha256(self.spin_token.encode()).hexdigest()
		self.spin_token_consumed_at = None

	def validate(self):
		expected_digest = hashlib.sha256(str(self.spin_token or "").encode()).hexdigest()
		if not self.spin_token or not hmac.compare_digest(
			str(self.spin_token_digest or ""), expected_digest
		):
			frappe.throw(_("Gift spin token evidence is invalid."), frappe.PermissionError)
		previous = self.get_doc_before_save()
		if previous and not is_privileged_user():
			if previous.spin_token != self.spin_token or previous.spin_token_digest != self.spin_token_digest:
				frappe.throw(_("Gift spin tokens cannot be changed."), frappe.PermissionError)
			if previous.status != self.status and not self.flags.get("ch_gift_state_update"):
				frappe.throw(_("Gift status can only be changed through gift actions."), frappe.PermissionError)
		# Enforce single active gift per parent invoice — the user's rule:
		# "only 1 spin freebee be allowed per invoice".
		if self.is_new():
			existing = frappe.db.exists(
				"CH Gift Redemption",
				{
					"parent_sales_invoice": self.parent_sales_invoice,
					"status": ("not in", ("Expired", "Cancelled")),
					"name": ("!=", self.name or ""),
				},
			)
			if existing:
				frappe.throw(
					_("A gift redemption ({0}) already exists for invoice {1}.").format(
						existing, self.parent_sales_invoice
					),
					title=_("Duplicate Gift"),
				)

	def is_expired(self) -> bool:
		"""Return True when the TTL has elapsed and status is still open."""
		if self.status in TERMINAL_STATUSES:
			return False
		from frappe.utils import now_datetime, get_datetime

		return bool(self.expires_at) and get_datetime(self.expires_at) < now_datetime()
