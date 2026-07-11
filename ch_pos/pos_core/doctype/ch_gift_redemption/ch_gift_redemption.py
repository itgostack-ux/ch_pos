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

import frappe
from frappe import _
from frappe.model.document import Document


TERMINAL_STATUSES = ("Redeemed", "Expired", "Cancelled")


class CHGiftRedemption(Document):
	def validate(self):
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
