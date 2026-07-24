import frappe
from frappe import _
from frappe.utils import flt, cint

from ch_pos.api.scope_guard import assert_pos_profile_scope, assert_store_scope
from ch_pos.config import is_privileged_user, require_authenticated_user


def _resolve_company_scope(pos_profile=None, company=None):
    require_authenticated_user()
    frappe.has_permission("Item", "read", throw=True)
    if pos_profile:
        anchors = assert_pos_profile_scope(pos_profile)
        if company and company != anchors.get("company"):
            frappe.throw(_("POS Profile belongs to another company."), frappe.PermissionError)
        return anchors.get("company")
    if company:
        assert_store_scope(company=company)
        return company
    if not is_privileged_user():
        frappe.throw(_("POS Profile is required to view offers."), frappe.PermissionError)
    return None


def _filter_company_offers(offers, company):
    if not company or not offers:
        return offers
    primary = {offer.name for offer in offers if offer.company == company}
    additional = frappe.get_all(
        "CH Offer Company",
        filters={
            "parent": ["in", [offer.name for offer in offers]],
            "parenttype": "CH Item Offer",
            "company": company,
        },
        pluck="parent",
        limit_page_length=500,
    )
    allowed = primary | set(additional)
    return [offer for offer in offers if offer.name in allowed]


@frappe.whitelist()
def get_applicable_offers(
    item_code=None,
    item_group=None,
    cart_total=0,
    payment_mode=None,
    pos_profile=None,
    company=None,
) -> list:
    """Return all CH Item Offers applicable to an item or cart via POS channel."""
    company = _resolve_company_scope(pos_profile, company)
    today = frappe.utils.today()
    filters = {
        "channel": "POS",
        "status": "Active",
        "approval_status": "Approved",
        "start_date": ["<=", today],
        "end_date": [">=", today],
        "offer_type": ["not in", ["Combo", "Attachment", "Freebie"]],
    }
    if item_code:
        filters["item_code"] = item_code

    offers = frappe.db.get_all(
        "CH Item Offer",
        filters=filters,
        fields=[
            "name", "company", "offer_name", "offer_type", "value_type", "value",
            "priority", "stackable",
            "min_bill_amount", "payment_mode", "bank_name", "card_type",
        ],
        order_by="priority asc",
        limit_page_length=500,
    )
    offers = _filter_company_offers(offers, company)

    cart_total = flt(cart_total)
    result = []
    for offer in offers:
        # Check minimum bill amount
        if flt(offer.min_bill_amount) and cart_total < flt(offer.min_bill_amount):
            continue
        # Check payment mode condition
        if (
            offer.payment_mode
            and payment_mode
            and offer.payment_mode.strip().casefold() != str(payment_mode).strip().casefold()
        ):
            continue

        result.append(
            {
                "name": offer.name,
                "offer_name": offer.offer_name,
                "offer_type": offer.offer_type,
                "value_type": offer.value_type,
                "value": offer.value,
                "priority": offer.priority,
                "stackable": offer.stackable,
                "conditions_text": _build_conditions_text(offer),
            }
        )

    return result


@frappe.whitelist()
def get_best_offer_combination(cart_items, pos_profile=None, company=None) -> dict:
    """Find the best combination of non-conflicting offers for the cart."""
    if isinstance(cart_items, str):
        cart_items = frappe.parse_json(cart_items)

    all_offers = []
    for item in cart_items:
        item_code = item.get("item_code")
        offers = get_applicable_offers(
            item_code=item_code,
            pos_profile=pos_profile,
            company=company,
        )
        for offer in offers:
            offer["for_item"] = item_code
            offer["for_item_name"] = item.get("item_name", "")
            offer["for_amount"] = flt(item.get("amount", 0))
            all_offers.append(offer)

    # Sort by priority (lower = higher priority)
    all_offers.sort(key=lambda x: (x.get("priority", 99)))

    selected = []
    used_items = set()
    total_savings = 0

    for offer in all_offers:
        item_key = offer["for_item"]

        # Non-stackable: only one offer per item
        if not offer.get("stackable") and item_key in used_items:
            continue

        savings = _calculate_savings(offer)
        selected.append({**offer, "savings": savings})
        total_savings += savings
        used_items.add(item_key)

    return {
        "offers": selected,
        "total_savings": total_savings,
        "explanation": f"Applied {len(selected)} offer(s) saving ₹{total_savings:,.0f}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Combo Offer Detection (#3)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def check_combo_offers(cart_items, company=None, pos_profile=None) -> list:
    """Detect active combo offers satisfied by the current cart items.

    Args:
        cart_items: list of dicts with item_code, qty, rate, amount
        company: optional company filter

    Returns:
        list of matching combos with savings info
    """
    company = _resolve_company_scope(pos_profile, company)
    if isinstance(cart_items, str):
        cart_items = frappe.parse_json(cart_items)

    today = frappe.utils.today()
    filters = {
        "offer_type": "Combo",
        "status": "Active",
        "approval_status": "Approved",
        "start_date": ["<=", today],
        "end_date": [">=", today],
    }
    combo_offers = frappe.get_all(
        "CH Item Offer",
        filters=filters,
        fields=[
            "name", "company", "offer_name", "value_type", "value", "combo_price", "priority"
        ],
        order_by="priority asc",
        limit_page_length=500,
    )
    combo_offers = _filter_company_offers(combo_offers, company)

    if not combo_offers:
        return []

    # Build cart inventory: {item_code: total_qty}
    cart_inventory = {}
    cart_prices = {}
    for item in cart_items:
        ic = item.get("item_code")
        cart_inventory[ic] = cart_inventory.get(ic, 0) + flt(item.get("qty", 1))
        cart_prices[ic] = flt(item.get("rate", 0))

    matched = []
    for offer in combo_offers:
        combo_items = frappe.get_all(
            "CH Offer Combo Item",
            filters={"parent": offer.name, "parenttype": "CH Item Offer"},
            fields=["item_code", "qty"],
        )
        if not combo_items:
            continue

        # Check if all required items are in cart with sufficient qty
        satisfied = True
        combo_original_total = 0
        for ci in combo_items:
            available = cart_inventory.get(ci.item_code, 0)
            if available < cint(ci.qty):
                satisfied = False
                break
            combo_original_total += cart_prices.get(ci.item_code, 0) * cint(ci.qty)

        if not satisfied:
            continue

        # Calculate savings
        combo_price = flt(offer.combo_price)
        if combo_price > 0:
            savings = combo_original_total - combo_price
        elif offer.value_type == "Percentage":
            savings = combo_original_total * flt(offer.value) / 100
        elif offer.value_type == "Amount":
            savings = flt(offer.value)
        else:
            savings = 0

        matched.append({
            "offer_name": offer.name,
            "offer_title": offer.offer_name,
            "combo_items": [{"item_code": ci.item_code, "qty": ci.qty} for ci in combo_items],
            "combo_price": combo_price,
            "original_total": combo_original_total,
            "savings": max(savings, 0),
            "discount_amount": max(savings, 0),
        })

    return matched


# ─────────────────────────────────────────────────────────────────────────────
# Attachment / Freebie Offer Detection (#11)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def check_attachment_offers(cart_items, company=None, pos_profile=None) -> list:
    """Detect attachment/freebie offers triggered by items in the cart.

    Returns list of reward items that should be added or discounted.
    """
    company = _resolve_company_scope(pos_profile, company)
    if isinstance(cart_items, str):
        cart_items = frappe.parse_json(cart_items)

    today = frappe.utils.today()
    cart_item_codes = {item.get("item_code") for item in cart_items if item.get("item_code")}

    # An offer may target either the exact variant sold or its template
    # (= all variants). Variant-specific offers override template-level
    # ones for that cart line — SAP free-goods "most specific wins".
    template_of = {
        code: frappe.get_cached_value("Item", code, "variant_of")
        for code in cart_item_codes
    }
    trigger_candidates = cart_item_codes | {t for t in template_of.values() if t}
    if not trigger_candidates:
        return []

    filters = {
        "offer_type": ["in", ["Attachment", "Freebie"]],
        # Spin Wheel freebies (is_gamified=1) are issued post-sale as a
        # CH Gift Redemption — surfacing them here would add the gift to
        # the cart too and the customer would receive it twice.
        "is_gamified": 0,
        "status": "Active",
        "approval_status": "Approved",
        "start_date": ["<=", today],
        "end_date": [">=", today],
        "trigger_item": ["in", list(trigger_candidates)],
    }
    all_offers = frappe.get_all(
        "CH Item Offer",
        filters=filters,
        fields=[
            "name", "company", "offer_name", "offer_type",
            "trigger_item", "trigger_item_name",
            "reward_item", "reward_item_name",
            "reward_price", "reward_qty",
        ],
        order_by="priority desc, modified desc",
        limit_page_length=500,
    )
    all_offers = _filter_company_offers(all_offers, company)

    offers, seen = [], set()
    for code in cart_item_codes:
        variant_hits = [o for o in all_offers if o.trigger_item == code]
        template_hits = [
            o for o in all_offers
            if template_of.get(code) and o.trigger_item == template_of[code]
        ]
        for offer in variant_hits or template_hits:
            if offer.name not in seen:
                seen.add(offer.name)
                offers.append(offer)

    result = []
    for offer in offers:
        reward_item_price = flt(frappe.db.get_value("Item Price", {
            "item_code": offer.reward_item,
            "selling": 1,
        }, "price_list_rate")) or 0

        result.append({
            "offer_name": offer.name,
            "offer_title": offer.offer_name,
            "offer_type": offer.offer_type,
            "trigger_item": offer.trigger_item,
            "trigger_item_name": offer.trigger_item_name,
            "reward_item": offer.reward_item,
            "reward_item_name": offer.reward_item_name,
            "reward_qty": cint(offer.reward_qty) or 1,
            "reward_price": flt(offer.reward_price) if offer.offer_type == "Attachment" else 0,
            "original_price": reward_item_price,
            "savings": max(reward_item_price - flt(offer.reward_price if offer.offer_type == "Attachment" else 0), 0),
            "is_free": offer.offer_type == "Freebie",
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Coupon Code Integration (#10)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def validate_coupon_code(
    coupon_code,
    customer=None,
    pos_profile=None,
    company=None,
) -> dict:
    """Validate a coupon code and return its details.

    Returns dict with coupon info, or raises an error if invalid.
    """
    company = _resolve_company_scope(pos_profile, company)
    frappe.has_permission("Sales Invoice", "create", throw=True)
    if not coupon_code:
        frappe.throw(_("Please enter a coupon code"), title=_("Validation Error"))

    coupon = frappe.db.get_value(
        "Coupon Code",
        {"coupon_code": coupon_code},
        ["name", "coupon_code", "coupon_type", "pricing_rule", "customer",
         "valid_from", "valid_upto", "maximum_use", "used"],
        as_dict=True,
    )

    if not coupon:
        frappe.throw(_("Invalid coupon code: {0}").format(frappe.bold(coupon_code)), title=_("Validation Error"))

    today = frappe.utils.today()
    if coupon.valid_from and str(coupon.valid_from) > today:
        frappe.throw(_("Coupon {0} is not yet active").format(frappe.bold(coupon_code)), title=_("Validation Error"))
    if coupon.valid_upto and str(coupon.valid_upto) < today:
        frappe.throw(_("Coupon {0} has expired").format(frappe.bold(coupon_code)), title=_("Validation Error"))
    if coupon.maximum_use and cint(coupon.used) >= cint(coupon.maximum_use):
        frappe.throw(_("Coupon {0} usage limit reached").format(frappe.bold(coupon_code)), title=_("Validation Error"))
    if coupon.customer and coupon.customer != customer:
        frappe.throw(_("Coupon {0} is not valid for this customer").format(frappe.bold(coupon_code)), title=_("Validation Error"))

    # Get linked pricing rule details
    pr = frappe.db.get_value(
        "Pricing Rule",
        coupon.pricing_rule,
        ["title", "company", "rate_or_discount", "discount_percentage", "discount_amount",
         "rate", "disable", "valid_from", "valid_upto"],
        as_dict=True,
    )

    if not pr or pr.disable:
        frappe.throw(_("The pricing rule linked to coupon {0} is disabled").format(
            frappe.bold(coupon_code)))
    if company and pr.company and pr.company != company:
        frappe.throw(_("Coupon belongs to another company."), frappe.PermissionError)

    return {
        "valid": True,
        "coupon_name": coupon.name,
        "coupon_code": coupon.coupon_code,
        "coupon_type": coupon.coupon_type,
        "pricing_rule": coupon.pricing_rule,
        "pricing_rule_title": pr.title,
        "discount_type": pr.rate_or_discount,
        "discount_percentage": flt(pr.discount_percentage),
        "discount_amount": flt(pr.discount_amount),
        "rate": flt(pr.rate),
        "remaining_uses": (cint(coupon.maximum_use) - cint(coupon.used)) if coupon.maximum_use else "Unlimited",
    }


@frappe.whitelist(methods=["POST"])
def apply_coupon_code(
    coupon_code,
    customer=None,
    pos_profile=None,
    company=None,
) -> dict:
    """Validate and return coupon details for POS cart application.

    The actual application happens when the Sales Invoice is created —
    we just pass coupon_code to create_pos_invoice().
    This API is for pre-validation + UI feedback.
    """
    return validate_coupon_code(
        coupon_code,
        customer,
        pos_profile=pos_profile,
        company=company,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_savings(offer):
    if offer.get("value_type") == "Amount":
        return flt(offer.get("value"))
    elif offer.get("value_type") == "Percentage":
        return flt(offer.get("for_amount")) * flt(offer.get("value")) / 100
    return 0


def _build_conditions_text(offer):
    parts = []
    if flt(offer.get("min_bill_amount")):
        parts.append(f"Min bill ₹{flt(offer.min_bill_amount):,.0f}")
    if offer.get("payment_mode"):
        parts.append(f"Payment: {offer.payment_mode}")
    if offer.get("bank_name"):
        parts.append(f"Bank: {offer.bank_name}")
    if offer.get("card_type"):
        parts.append(f"Card: {offer.card_type}")
    return " | ".join(parts) if parts else "No conditions"
