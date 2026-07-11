"""
CH POS — Gift Redemption API (spin-wheel freebie).

Complete flow:
    1. Customer buys goods at POS → Sales Invoice submitted.
    2. `issue_gift_for_invoice_hook` fires on submit. If any cart line
       matches an active gamified `CH Item Offer` (offer_type=Freebie,
       is_gamified=1), one CH Gift Redemption is issued (max 1 per
       invoice — user rule).
    3. Customer receives an email + WhatsApp with a link
       `/spin?token=<spin_token>` (long, opaque, unguessable).
    4. Customer opens link on their phone → sees wheel animation →
       calls `spin_wheel(token)` which reveals a short human-friendly
       `redemption_code` (e.g. `GIFT-A7X2`) and moves status
       Issued → Revealed.
    5. Customer visits any store within the TTL (default 7 days).
       Cashier clicks "Redeem Gift Code" in POS, enters the code,
       calls `redeem_gift_code(code, pos_profile)`. Server creates a
       new Sales Invoice with the reward item at ₹0, `is_free_item=1`,
       `custom_original_invoice` = parent, `custom_original_invoice_reason`
       = "Late Free Gift". Gift status moves Revealed → Redeemed.

Reuse notes:
    * WhatsApp send: `ch_item_master.ch_core.whatsapp.send_template_message`
    * Encryption/HMAC: not needed — spin_token is a random URL-safe secret
      generated with `secrets.token_urlsafe`.
    * Invoice creation: goes directly via `frappe.get_doc` instead of
      `pos_api.create_pos_invoice` because:
        - the free invoice has grand_total = 0, no payments needed
        - no CH Free Sale Approval required (offer is pre-approved)
        - keeps this module decoupled from the huge create_pos_invoice
          function surface

All state transitions take row-level locks (SELECT ... FOR UPDATE) so
concurrent spins/redemptions cannot double-issue.
"""

from __future__ import annotations

import secrets

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Crockford-like alphabet (no 0/O/1/I/L/U for readability on receipts).
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_PREFIX = "GIFT-"
_CODE_LENGTH = 4  # -> 30^4 = 810k codes; expand if collisions arise
_DEFAULT_TTL_HOURS = 168  # 7 days — user default

_ORIGINAL_INVOICE_REASON = "Late Free Gift"  # matches existing Select option
_FREE_SALE_REASON = "Spin Wheel Gift Redemption"

# WhatsApp event key (looked up in per-company CH WhatsApp Template library)
_WA_EVENT_GIFT_ISSUED = "spin_wheel_gift_issued"


# ---------------------------------------------------------------------------
# Code / token generation
# ---------------------------------------------------------------------------

def _generate_short_code() -> str:
	"""Return a short human-friendly redemption code, unique in the table."""
	for _ in range(20):
		body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
		candidate = f"{_CODE_PREFIX}{body}"
		if not frappe.db.exists("CH Gift Redemption", {"redemption_code": candidate}):
			return candidate
	# Extremely unlikely — fall back to a longer code.
	suffix = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
	return f"{_CODE_PREFIX}{suffix}"


# ---------------------------------------------------------------------------
# Issuance (on_submit hook)
# ---------------------------------------------------------------------------

def issue_gift_for_invoice_hook(doc, method=None):
	"""``doc_events['Sales Invoice']['on_submit']`` hook.

	Wire in ``ch_pos/hooks.py``. Non-blocking: any failure is logged but
	will never fail the invoice submit (the sale is already finalised).
	"""
	if not doc or doc.doctype != "Sales Invoice":
		return
	if getattr(doc, "is_return", 0) or getattr(doc, "docstatus", 0) != 1:
		return
	# Only issue for POS sales — the wheel/redemption flow assumes an
	# in-person retail visit for both the trigger sale and the eventual
	# redemption. Non-POS invoices (e.g. B2B, back-office) are skipped.
	if not getattr(doc, "is_pos", 0):
		return
	if frappe.flags.get("in_import") or frappe.flags.get("in_migrate"):
		return
	try:
		issue_gift_for_invoice(doc)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"CH Gift Redemption: issuance failed for {doc.name}",
		)


def issue_gift_for_invoice(sales_invoice) -> str | None:
	"""Issue a CH Gift Redemption for the invoice if a gamified offer matches.

	Returns the CH Gift Redemption name (or None if no offer matched or a
	redemption was already issued).
	"""
	# Enforce the "max 1 spin per invoice" rule — user requirement.
	existing = frappe.db.exists(
		"CH Gift Redemption",
		{"parent_sales_invoice": sales_invoice.name, "status": ("not in", ("Cancelled",))},
	)
	if existing:
		return existing

	offer = _find_matching_gamified_offer(sales_invoice)
	if not offer:
		return None

	ttl_hours = cint(offer.get("redemption_ttl_hours")) or _DEFAULT_TTL_HOURS

	gift = frappe.get_doc({
		"doctype": "CH Gift Redemption",
		"parent_sales_invoice": sales_invoice.name,
		"offer": offer.name,
		"reward_item": offer.reward_item,
		"reward_qty": cint(offer.reward_qty) or 1,
		"wheel_style": offer.wheel_style or "Prize Wheel",
		"customer": sales_invoice.customer,
		"customer_email": _resolve_customer_email(sales_invoice),
		"customer_mobile": _resolve_customer_mobile(sales_invoice),
		"company": sales_invoice.company,
		"store": _resolve_store(sales_invoice),
		"status": "Issued",
		"issued_at": now_datetime(),
		"expires_at": add_to_date(now_datetime(), hours=ttl_hours),
		"redemption_code": _generate_short_code(),
		"spin_token": secrets.token_urlsafe(32),
	})
	gift.flags.ignore_permissions = True
	gift.insert()

	# Fire notifications asynchronously so an SMTP/WhatsApp hiccup never
	# blocks the POS submit path.
	frappe.enqueue(
		_send_gift_notifications,
		queue="short",
		gift_name=gift.name,
		enqueue_after_commit=True,
	)
	return gift.name


def _find_matching_gamified_offer(sales_invoice):
	"""Return the highest-priority active gamified Freebie offer matching any
	line item on the invoice; else None.
	"""
	item_codes = [i.item_code for i in (sales_invoice.items or []) if i.item_code]
	if not item_codes:
		return None

	rows = frappe.get_all(
		"CH Item Offer",
		filters={
			"offer_type": "Freebie",
			"is_gamified": 1,
			"status": "Active",
			"approval_status": "Approved",
			"trigger_item": ("in", item_codes),
			"start_date": ("<=", sales_invoice.posting_date),
			"end_date": (">=", sales_invoice.posting_date),
		},
		or_filters={"company": sales_invoice.company},
		fields=[
			"name", "company", "reward_item", "reward_qty",
			"wheel_style", "redemption_ttl_hours", "priority",
		],
		order_by="priority desc, modified desc",
		limit=1,
	)
	return rows[0] if rows else None


def _resolve_customer_email(sales_invoice) -> str | None:
	return (
		getattr(sales_invoice, "contact_email", None)
		or frappe.db.get_value("Customer", sales_invoice.customer, "email_id")
	)


def _resolve_customer_mobile(sales_invoice) -> str | None:
	return (
		getattr(sales_invoice, "contact_mobile", None)
		or frappe.db.get_value("Customer", sales_invoice.customer, "mobile_no")
	)


def _resolve_store(sales_invoice) -> str | None:
	"""Best-effort CH Store lookup — invoice may or may not carry one."""
	for fld in ("custom_ch_store", "custom_store", "ch_store"):
		val = getattr(sales_invoice, fld, None)
		if val:
			return val
	return None


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _send_gift_notifications(gift_name: str) -> None:
	"""Fire email + WhatsApp (best-effort). Stores per-channel timestamps or
	the error message on the CH Gift Redemption row.
	"""
	gift = frappe.get_doc("CH Gift Redemption", gift_name)
	spin_url = frappe.utils.get_url(f"/spin?token={gift.spin_token}")

	# --- Email ---
	if gift.customer_email:
		try:
			_send_gift_email(gift, spin_url)
			gift.db_set("email_sent_at", now_datetime(), update_modified=False)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Gift email failed for {gift.name}")
			gift.db_set(
				"notification_error",
				(gift.notification_error or "") + f"\nemail: {frappe.get_traceback()[:400]}",
				update_modified=False,
			)

	# --- WhatsApp ---
	if gift.customer_mobile:
		try:
			from ch_item_master.ch_core.whatsapp import send_template_message

			send_template_message(
				phone=gift.customer_mobile,
				event=_WA_EVENT_GIFT_ISSUED,
				body_values={
					"1": gift.customer_name or "Customer",
					"2": spin_url,
					"3": frappe.utils.format_datetime(gift.expires_at, "dd MMM"),
				},
				customer_name=gift.customer_name,
				ref_doctype="CH Gift Redemption",
				ref_name=gift.name,
				company=gift.company,
			)
			gift.db_set("whatsapp_sent_at", now_datetime(), update_modified=False)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Gift WhatsApp failed for {gift.name}")
			gift.db_set(
				"notification_error",
				(gift.notification_error or "") + f"\nwhatsapp: {frappe.get_traceback()[:400]}",
				update_modified=False,
			)


def _send_gift_email(gift, spin_url: str) -> None:
	"""Send the spin-wheel invite email. Kept simple + inline-HTML."""
	subject = _("You have a surprise gift! Spin the wheel to reveal it")
	body = f"""
	<div style="font-family:Segoe UI,Arial,sans-serif;max-width:640px;margin:auto;
	            border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">
	  <div style="background:#4f46e5;color:#fff;padding:18px 24px;font-size:20px;font-weight:600">
	    🎁 A surprise gift is waiting for you!
	  </div>
	  <div style="padding:24px;color:#111827;line-height:1.55">
	    <p>Hi {frappe.utils.escape_html(gift.customer_name or 'there')},</p>
	    <p>Thank you for your recent purchase.
	       As a token of appreciation, we've prepared a surprise gift for you —
	       but you'll have to spin the wheel to find out what it is!</p>
	    <div style="text-align:center;margin:28px 0">
	      <a href="{spin_url}" style="display:inline-block;padding:14px 36px;
	         background:#16a34a;color:#fff;text-decoration:none;border-radius:8px;
	         font-weight:600;font-size:16px">🎡 Spin the Wheel</a>
	    </div>
	    <p style="color:#6b7280;font-size:13px">
	      This link is valid until
	      <b>{frappe.utils.format_datetime(gift.expires_at, "dd MMM yyyy, hh:mm a")}</b>.
	      After spinning, take the revealed code to any of our stores to collect
	      your gift alongside invoice
	      <b>{frappe.utils.escape_html(gift.parent_sales_invoice)}</b>.
	    </p>
	  </div>
	</div>
	"""
	frappe.sendmail(
		recipients=[gift.customer_email],
		subject=subject,
		message=body,
		reference_doctype="CH Gift Redemption",
		reference_name=gift.name,
		delayed=False,
	)


# ---------------------------------------------------------------------------
# Public (customer-facing) endpoints — used by /spin page
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60, ip_based=True)
def get_gift_details(token: str) -> dict:
	"""Return safe metadata for the /spin page. Does NOT reveal the code."""
	if not token or len(token) < 20:
		frappe.throw(_("Invalid link."))

	name = frappe.db.get_value("CH Gift Redemption", {"spin_token": token}, "name")
	if not name:
		frappe.throw(_("This spin link is invalid or has been revoked."))

	gift = frappe.get_doc("CH Gift Redemption", name)
	_mark_expired_if_due(gift)

	return {
		"reward_item_name": gift.reward_item_name or gift.reward_item,
		"wheel_style": gift.wheel_style or "Prize Wheel",
		"customer_name": gift.customer_name,
		"status": gift.status,
		"expires_at": str(gift.expires_at) if gift.expires_at else None,
		"already_revealed": gift.status in ("Revealed", "Redeemed", "Expired"),
		"parent_sales_invoice": gift.parent_sales_invoice,
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60, ip_based=True)
def spin_wheel(token: str) -> dict:
	"""Reveal the redemption code. Idempotent: repeated calls return the
	same code as long as the gift has not been redeemed / expired.
	"""
	if not token or len(token) < 20:
		frappe.throw(_("Invalid link."))

	name = frappe.db.get_value("CH Gift Redemption", {"spin_token": token}, "name")
	if not name:
		frappe.throw(_("This spin link is invalid or has been revoked."))

	# Row-level lock so two simultaneous spins from two tabs converge on
	# the same code + single Revealed transition.
	frappe.db.get_value("CH Gift Redemption", name, "name", for_update=True)
	gift = frappe.get_doc("CH Gift Redemption", name)

	_mark_expired_if_due(gift)
	if gift.status == "Expired":
		frappe.throw(_("This gift has expired."))
	if gift.status == "Cancelled":
		frappe.throw(_("This gift has been cancelled."))
	if gift.status == "Redeemed":
		frappe.throw(_("This gift has already been redeemed."))

	if gift.status == "Issued":
		gift.db_set("status", "Revealed", update_modified=False)
		gift.db_set("revealed_at", now_datetime(), update_modified=False)

	return {
		"redemption_code": gift.redemption_code,
		"reward_item_name": gift.reward_item_name or gift.reward_item,
		"reward_qty": cint(gift.reward_qty) or 1,
		"expires_at": str(gift.expires_at),
	}


# ---------------------------------------------------------------------------
# Cashier endpoint — redemption
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def redeem_gift_code(code: str, pos_profile: str) -> dict:
	"""Cashier flow. Validates the code and creates a linked ₹0 Sales Invoice
	containing only the reward item.

	Both parameters are required — the invoice must be booked in a real
	POS session so it appears in the daily closing.
	"""
	if not code:
		frappe.throw(_("Redemption code is required."))
	if not pos_profile:
		frappe.throw(_("POS Profile is required."))

	code = str(code).strip().upper()
	frappe.has_permission("Sales Invoice", "create", throw=True)

	# Row-level lock on the redemption to prevent two cashiers grabbing
	# the same code at the same time.
	name = frappe.db.get_value(
		"CH Gift Redemption", {"redemption_code": code}, "name", for_update=True
	)
	if not name:
		frappe.throw(_("No gift found for this code."))

	gift = frappe.get_doc("CH Gift Redemption", name)
	_mark_expired_if_due(gift)

	if gift.status == "Issued":
		frappe.throw(_("The customer has not yet spun the wheel for this gift."))
	if gift.status == "Redeemed":
		frappe.throw(
			_("Gift already redeemed on invoice {0} at {1}.").format(
				gift.redeemed_invoice or "-",
				frappe.utils.format_datetime(gift.redeemed_at) if gift.redeemed_at else "-",
			)
		)
	if gift.status == "Expired":
		frappe.throw(_("This gift has expired and cannot be redeemed."))
	if gift.status == "Cancelled":
		frappe.throw(_("This gift has been cancelled."))
	if gift.status != "Revealed":
		frappe.throw(_("Gift is in state {0}; cannot redeem.").format(gift.status))

	# Enforce parent-invoice linkage — user requirement.
	if not gift.parent_sales_invoice or not frappe.db.exists(
		"Sales Invoice", {"name": gift.parent_sales_invoice, "docstatus": 1}
	):
		frappe.throw(_("Parent invoice for this gift is missing or not submitted."))

	inv_name = _create_gift_invoice(gift, pos_profile)

	gift.db_set("status", "Redeemed", update_modified=False)
	gift.db_set("redeemed_at", now_datetime(), update_modified=False)
	gift.db_set("redeemed_invoice", inv_name, update_modified=False)

	return {
		"redeemed_invoice": inv_name,
		"parent_sales_invoice": gift.parent_sales_invoice,
		"reward_item": gift.reward_item,
		"reward_qty": cint(gift.reward_qty) or 1,
	}


def _create_gift_invoice(gift, pos_profile: str) -> str:
	"""Create + submit the ₹0 free-gift Sales Invoice, linked to the parent."""
	profile = frappe.get_doc("POS Profile", pos_profile)
	if profile.company != gift.company:
		frappe.throw(
			_("POS Profile company ({0}) does not match gift company ({1}).").format(
				profile.company, gift.company
			)
		)

	warehouse = _pick_warehouse(profile, gift.reward_item)

	inv = frappe.new_doc("Sales Invoice")
	inv.is_pos = 1
	inv.pos_profile = profile.name
	inv.customer = gift.customer
	inv.company = gift.company
	inv.posting_date = frappe.utils.nowdate()
	inv.set_posting_time = 1
	inv.due_date = frappe.utils.nowdate()
	inv.update_stock = 1

	inv.append("items", {
		"item_code": gift.reward_item,
		"qty": flt(gift.reward_qty) or 1,
		"rate": 0,
		"price_list_rate": 0,
		"discount_percentage": 100,
		"is_free_item": 1,
		"warehouse": warehouse,
	})

	# Zero-value POS payment row so ERPNext's POS validation is satisfied.
	default_mop = _default_mode_of_payment(profile)
	if default_mop:
		inv.append("payments", {"mode_of_payment": default_mop, "amount": 0})

	# Parent-invoice linkage + free-sale flags (all pre-existing custom fields
	# on Sales Invoice — see ch_pos/setup.py).
	if inv.meta.has_field("custom_original_invoice"):
		inv.custom_original_invoice = gift.parent_sales_invoice
	if inv.meta.has_field("custom_original_invoice_reason"):
		inv.custom_original_invoice_reason = _ORIGINAL_INVOICE_REASON
	if inv.meta.has_field("custom_is_free_sale"):
		inv.custom_is_free_sale = 1
	if inv.meta.has_field("custom_free_sale_reason"):
		inv.custom_free_sale_reason = f"{_FREE_SALE_REASON}: {gift.name}"
	if inv.meta.has_field("custom_free_sale_approved_by"):
		inv.custom_free_sale_approved_by = f"Gamified Offer: {gift.offer}"

	inv.flags.ignore_permissions = True
	inv.flags.ignore_pricing_rule = True
	inv.insert()
	inv.submit()
	return inv.name


def _pick_warehouse(profile, item_code: str) -> str | None:
	"""Prefer the POS Profile's default warehouse; fall back to the item's."""
	if profile.warehouse:
		return profile.warehouse
	return frappe.db.get_value(
		"Item Default", {"parent": item_code, "company": profile.company}, "default_warehouse"
	)


def _default_mode_of_payment(profile) -> str | None:
	for pm in profile.payments or []:
		if cint(pm.default):
			return pm.mode_of_payment
	return (profile.payments[0].mode_of_payment if profile.payments else None)


# ---------------------------------------------------------------------------
# Lookup helper — used by the cashier UI before actually redeeming.
# ---------------------------------------------------------------------------

@frappe.whitelist()
def lookup_gift_code(code: str) -> dict:
	"""Cashier-side pre-check. Returns display metadata; does NOT redeem."""
	if not code:
		frappe.throw(_("Redemption code is required."))
	code = str(code).strip().upper()
	frappe.has_permission("CH Gift Redemption", "read", throw=True)

	name = frappe.db.get_value("CH Gift Redemption", {"redemption_code": code}, "name")
	if not name:
		frappe.throw(_("No gift found for this code."))

	gift = frappe.get_doc("CH Gift Redemption", name)
	_mark_expired_if_due(gift)
	return {
		"name": gift.name,
		"redemption_code": gift.redemption_code,
		"status": gift.status,
		"parent_sales_invoice": gift.parent_sales_invoice,
		"customer": gift.customer,
		"customer_name": gift.customer_name,
		"reward_item": gift.reward_item,
		"reward_item_name": gift.reward_item_name,
		"reward_qty": cint(gift.reward_qty) or 1,
		"expires_at": str(gift.expires_at) if gift.expires_at else None,
		"redeemed_invoice": gift.redeemed_invoice,
	}


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

def _mark_expired_if_due(gift) -> None:
	if gift.status in ("Redeemed", "Expired", "Cancelled"):
		return
	if not gift.expires_at:
		return
	if get_datetime(gift.expires_at) < now_datetime():
		gift.db_set("status", "Expired", update_modified=False)
		gift.reload()


def expire_stale_gift_redemptions():
	"""Scheduler hook — bulk-mark expired gifts. Wired hourly in hooks.py."""
	frappe.db.sql(
		"""
		UPDATE `tabCH Gift Redemption`
		   SET status = 'Expired', modified = NOW()
		 WHERE status IN ('Issued', 'Revealed')
		   AND expires_at IS NOT NULL
		   AND expires_at < NOW()
		"""
	)
	frappe.db.commit()
