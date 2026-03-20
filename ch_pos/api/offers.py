import frappe
from frappe import _
from frappe.utils import flt, cint


@frappe.whitelist()
def get_applicable_offers(item_code=None, item_group=None, cart_total=0, payment_mode=None):
    """Return all CH Item Offers applicable to an item or cart via POS channel."""
    today = frappe.utils.today()
    filters = {
        "channel": "POS",
        "status": "Active",
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
            "name", "offer_name", "offer_type", "value_type", "value",
            "priority", "stackable",
            "min_bill_amount", "payment_mode", "bank_name", "card_type",
        ],
        order_by="priority asc",
    )

    cart_total = flt(cart_total)
    result = []
    for offer in offers:
        # Check minimum bill amount
        if flt(offer.min_bill_amount) and cart_total < flt(offer.min_bill_amount):
            continue
        # Check payment mode condition
        if offer.payment_mode and payment_mode and offer.payment_mode != payment_mode:
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
def get_best_offer_combination(cart_items):
    """Find the best combination of non-conflicting offers for the cart."""
    if isinstance(cart_items, str):
        cart_items = frappe.parse_json(cart_items)

    all_offers = []
    for item in cart_items:
        item_code = item.get("item_code")
        offers = get_applicable_offers(item_code=item_code)
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
def check_combo_offers(cart_items, company=None):
    """Detect active combo offers satisfied by the current cart items.

    Args:
        cart_items: list of dicts with item_code, qty, rate, amount
        company: optional company filter

    Returns:
        list of matching combos with savings info
    """
    if isinstance(cart_items, str):
        cart_items = frappe.parse_json(cart_items)

    today = frappe.utils.today()
    filters = {
        "offer_type": "Combo",
        "status": "Active",
        "start_date": ["<=", today],
        "end_date": [">=", today],
    }
    if company:
        filters["company"] = company

    combo_offers = frappe.get_all(
        "CH Item Offer",
        filters=filters,
        fields=["name", "offer_name", "value_type", "value", "combo_price", "priority"],
        order_by="priority asc",
    )

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
def check_attachment_offers(cart_items, company=None):
    """Detect attachment/freebie offers triggered by items in the cart.

    Returns list of reward items that should be added or discounted.
    """
    if isinstance(cart_items, str):
        cart_items = frappe.parse_json(cart_items)

    today = frappe.utils.today()
    cart_item_codes = {item.get("item_code") for item in cart_items}

    filters = {
        "offer_type": ["in", ["Attachment", "Freebie"]],
        "status": "Active",
        "start_date": ["<=", today],
        "end_date": [">=", today],
        "trigger_item": ["in", list(cart_item_codes)],
    }
    if company:
        filters["company"] = company

    offers = frappe.get_all(
        "CH Item Offer",
        filters=filters,
        fields=[
            "name", "offer_name", "offer_type",
            "trigger_item", "trigger_item_name",
            "reward_item", "reward_item_name",
            "reward_price", "reward_qty",
        ],
    )

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
def validate_coupon_code(coupon_code, customer=None):
    """Validate a coupon code and return its details.

    Returns dict with coupon info, or raises an error if invalid.
    """
    if not coupon_code:
        frappe.throw(_("Please enter a coupon code"))

    coupon = frappe.db.get_value(
        "Coupon Code",
        {"coupon_code": coupon_code},
        ["name", "coupon_code", "coupon_type", "pricing_rule", "customer",
         "valid_from", "valid_upto", "maximum_use", "used"],
        as_dict=True,
    )

    if not coupon:
        frappe.throw(_("Invalid coupon code: {0}").format(frappe.bold(coupon_code)))

    today = frappe.utils.today()
    if coupon.valid_from and str(coupon.valid_from) > today:
        frappe.throw(_("Coupon {0} is not yet active").format(frappe.bold(coupon_code)))
    if coupon.valid_upto and str(coupon.valid_upto) < today:
        frappe.throw(_("Coupon {0} has expired").format(frappe.bold(coupon_code)))
    if coupon.maximum_use and cint(coupon.used) >= cint(coupon.maximum_use):
        frappe.throw(_("Coupon {0} usage limit reached").format(frappe.bold(coupon_code)))
    if coupon.customer and customer and coupon.customer != customer:
        frappe.throw(_("Coupon {0} is not valid for this customer").format(frappe.bold(coupon_code)))

    # Get linked pricing rule details
    pr = frappe.db.get_value(
        "Pricing Rule",
        coupon.pricing_rule,
        ["title", "rate_or_discount", "discount_percentage", "discount_amount",
         "rate", "disable", "valid_from", "valid_upto"],
        as_dict=True,
    )

    if not pr or pr.disable:
        frappe.throw(_("The pricing rule linked to coupon {0} is disabled").format(
            frappe.bold(coupon_code)))

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


@frappe.whitelist()
def apply_coupon_code(coupon_code, customer=None):
    """Validate and return coupon details for POS cart application.

    The actual application happens when the Sales Invoice is created —
    we just pass coupon_code to create_pos_invoice().
    This API is for pre-validation + UI feedback.
    """
    return validate_coupon_code(coupon_code, customer)


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
