"""ensure_pos_payment_modes — make UPI + Credit Card available for split tender.

Background
----------
At go-live audit (2026-06-26) we discovered that all enabled `POS Profile`
records only had `Cash` configured under `POS Payment Method`. There was no
`UPI` Mode of Payment in the system either. This blocked two market-standard
behaviours used in every major retail POS (Oracle Xstore, SAP Hybris,
Microsoft D365 Commerce, Odoo POS, Zoho Inventory):

  1. Split-tender across distinct UPI apps / card devices with one row per
     instrument (each with its own UTR / RRN), and
  2. Cash / UPI / Card down-payment instruments alongside an EMI / Finance
     sale (the financed remainder posts to outstanding while down-payment
     rows post to their own clearing accounts via the standard MOP →
     `default_account` mapping).

What this patch does (idempotent)
---------------------------------
A. Create the `UPI` Mode of Payment if it does not already exist
   (type = Bank). It is added with a per-company `default_account` mapping
   to the existing "Payment Gateway Clearing — UPI - <abbr>" account that
   was already provisioned for every active company. Companies without a
   matching UPI clearing account are silently skipped — admins can map them
   later via Mode of Payment → Default Account.

B. For every enabled `POS Profile`, append `UPI` and `Credit Card` to its
   `payments` child table if missing. The existing `Cash` row keeps its
   `default = 1` flag, so this is a pure additive change.

Standard ERPNext ledger posting handles the rest:
  - Each `Sales Invoice Payment` row resolves its credit account from the
    POS Profile's MOP table (falling back to Mode of Payment Account) and
    the `Payment Entry` (or POS Invoice GL) posts a debit to that clearing
    account and a credit to the customer / income account as normal.
  - Per-row reference capture (`custom_upi_transaction_id`,
    `custom_card_reference`, `custom_card_last_four`) is already in place
    via `ch_pos.setup.CUSTOM_FIELDS` — no schema change required here.

Safe to re-run.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint


UPI_MOP_NAME = "UPI"
CARD_MOP_NAME = "Credit Card"  # Standard ERPNext MOP — reused for Debit / Card / EDC
DEFAULT_POS_MOPS = (UPI_MOP_NAME, CARD_MOP_NAME)


def execute() -> None:
	_ensure_upi_mode_of_payment()
	_ensure_pos_profile_payment_methods()


# ── A. Mode of Payment: UPI ───────────────────────────────────────────────

def _ensure_upi_mode_of_payment() -> None:
	"""Create the UPI MOP and wire per-company default accounts."""
	if not frappe.db.exists("Mode of Payment", UPI_MOP_NAME):
		mop = frappe.new_doc("Mode of Payment")
		mop.mode_of_payment = UPI_MOP_NAME
		mop.enabled = 1
		mop.type = "Bank"
		mop.insert(ignore_permissions=True)
		frappe.logger().info("ch_pos: created Mode of Payment 'UPI'")

	mop = frappe.get_doc("Mode of Payment", UPI_MOP_NAME)
	existing_companies = {row.company for row in (mop.accounts or [])}

	# Wire any company that has a matching "Payment Gateway Clearing — UPI - <abbr>"
	# account but no MOP mapping yet. This is the same convention used for the
	# pre-existing Card / Wallet / EMI MOPs (see fixtures audit on 2026-06-19).
	companies = frappe.get_all(
		"Company",
		fields=["name", "abbr"],
		filters={"is_group": 0},
	)
	added = 0
	for c in companies:
		if c["name"] in existing_companies:
			continue
		candidate = f"Payment Gateway Clearing — UPI - {c['abbr']}"
		if not frappe.db.exists("Account", candidate):
			# No UPI clearing account provisioned for this company — skip silently.
			continue
		mop.append("accounts", {
			"company": c["name"],
			"default_account": candidate,
		})
		added += 1
	if added:
		mop.save(ignore_permissions=True)
		frappe.logger().info(
			f"ch_pos: wired UPI MOP default_account for {added} compan{'y' if added == 1 else 'ies'}"
		)


# ── B. POS Profile payments table ─────────────────────────────────────────

def _ensure_pos_profile_payment_methods() -> None:
	"""Append UPI + Credit Card rows to every enabled POS Profile that is
	missing them. Preserves the existing Cash default."""
	profiles = frappe.get_all(
		"POS Profile",
		filters={"disabled": 0},
		fields=["name", "company"],
	)
	for prof in profiles:
		doc = frappe.get_doc("POS Profile", prof["name"])
		existing = {row.mode_of_payment for row in (doc.payments or [])}
		changed = False
		for mop_name in DEFAULT_POS_MOPS:
			if mop_name in existing:
				continue
			# Only append if a per-company default account exists on the MOP,
			# otherwise ERPNext will fail at save time with "Default account
			# missing" for the company. This keeps the patch safe across
			# half-configured environments.
			if not _mop_has_account_for_company(mop_name, prof["company"]):
				frappe.logger().warning(
					f"ch_pos: skipping {mop_name} on POS Profile {prof['name']} "
					f"— no default_account configured for company {prof['company']}"
				)
				continue
			doc.append("payments", {
				"mode_of_payment": mop_name,
				"default": 0,
				"allow_in_returns": 1 if mop_name == CARD_MOP_NAME else 0,
			})
			changed = True
		if changed:
			doc.save(ignore_permissions=True)
			frappe.logger().info(
				f"ch_pos: added UPI / Card MOP rows to POS Profile {prof['name']}"
			)


def _mop_has_account_for_company(mop_name: str, company: str) -> bool:
	return bool(
		frappe.db.get_value(
			"Mode of Payment Account",
			{"parent": mop_name, "company": company},
			"default_account",
		)
	)
