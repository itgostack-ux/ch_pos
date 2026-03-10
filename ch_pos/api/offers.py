import frappe
from frappe.utils import flt


@frappe.whitelist()
def get_applicable_offers(item_code=None, item_group=None, cart_total=0, payment_mode=None):
    """Return all CH Item Offers applicable to an item or cart via POS channel."""
    today = frappe.utils.today()
    filters = {
        "channel": "POS",
        "status": "Active",
        "start_date": ["<=", today],
        "end_date": [">=", today],
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
