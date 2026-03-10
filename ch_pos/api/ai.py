import json

import frappe
from frappe.utils import cint, flt, now_datetime


@frappe.whitelist()
def compare_items(item_codes, customer_preferences=None):
    """Generate AI or static comparison for 2-3 items."""
    if isinstance(item_codes, str):
        item_codes = frappe.parse_json(item_codes)
    if isinstance(customer_preferences, str):
        customer_preferences = frappe.parse_json(customer_preferences)

    if not item_codes or len(item_codes) < 2:
        frappe.throw("At least 2 items are required for comparison.")
    item_codes = item_codes[:3]

    # Check cache
    cached = _find_cached_comparison(item_codes)
    if cached:
        return cached

    settings = _get_ai_settings()

    # Try AI comparison
    if settings and settings.enable_ai:
        try:
            result = _ai_compare(item_codes, customer_preferences, settings)
            _cache_comparison(item_codes, customer_preferences, result, "AI", settings.comparison_model)
            return result
        except Exception:
            frappe.log_error("POS AI Comparison failed")
            if not settings.fallback_to_static:
                frappe.throw("AI comparison unavailable. Please try again.")

    # Static fallback
    result = _static_compare(item_codes, customer_preferences)
    _cache_comparison(item_codes, customer_preferences, result, "Static Fallback")
    return result


@frappe.whitelist()
def get_upsell_suggestions(item_code, cart_items=None):
    """AI upsell suggestions for an item."""
    if isinstance(cart_items, str):
        cart_items = frappe.parse_json(cart_items)

    item = frappe.get_cached_doc("Item", item_code)

    # Accessories in same item group
    accessories = frappe.db.get_all(
        "Item",
        filters={
            "item_group": ["like", "%Accessor%"],
            "brand": item.brand,
            "disabled": 0,
            "is_sales_item": 1,
        },
        fields=["name as item_code", "item_name", "image"],
        limit=5,
    )

    # Warranty plans
    plans = frappe.db.get_all(
        "CH Warranty Plan",
        filters={"status": "Active", "brand": item.brand},
        fields=["name", "plan_name", "price", "duration_months", "coverage_description"],
    )

    suggestions = []
    for acc in accessories:
        price = flt(
            frappe.db.get_value(
                "CH Item Price",
                {"item_code": acc.item_code, "channel": "POS", "status": "Active"},
                "selling_price",
            )
        )
        suggestions.append(
            {
                "item_code": acc.item_code,
                "item_name": acc.item_name,
                "type": "Accessory",
                "reason": f"Popular accessory for {item.brand}",
                "price": price,
            }
        )

    for plan in plans:
        suggestions.append(
            {
                "item_code": plan.name,
                "item_name": plan.plan_name,
                "type": "Warranty",
                "reason": plan.coverage_description or f"{plan.duration_months} months protection",
                "price": flt(plan.price),
            }
        )

    return suggestions


@frappe.whitelist()
def explain_offers(cart):
    """Plain-language explanation of applied offers."""
    if isinstance(cart, str):
        cart = frappe.parse_json(cart)

    items = cart.get("items", [])
    if not items:
        return "No items in cart."

    explanations = []
    for item in items:
        offers = frappe.db.get_all(
            "CH Item Offer",
            filters={
                "item_code": item.get("item_code"),
                "channel": "POS",
                "status": "Active",
                "start_date": ["<=", frappe.utils.today()],
                "end_date": [">=", frappe.utils.today()],
            },
            fields=["offer_name", "offer_type", "value_type", "value"],
            order_by="priority asc",
        )
        for offer in offers:
            desc = _describe_offer(offer, item)
            if desc:
                explanations.append(desc)

    return " ".join(explanations) if explanations else "No special offers apply to this cart."


# ── internal helpers ──────────────────────────────────────────────


def _get_ai_settings():
    try:
        return frappe.get_cached_doc("POS AI Settings")
    except frappe.DoesNotExistError:
        return None


def _find_cached_comparison(item_codes):
    """Look for a recent cached comparison with the same items."""
    settings = _get_ai_settings()
    ttl = cint(settings.cache_ttl_hours) if settings else 24

    cutoff = frappe.utils.add_to_date(now_datetime(), hours=-ttl)
    sorted_codes = sorted(item_codes)

    existing = frappe.db.get_all(
        "POS Comparison Request",
        filters={"creation": [">=", cutoff]},
        fields=["name", "comparison_result", "recommendation"],
        order_by="creation desc",
        limit=20,
    )

    for row in existing:
        cached_items = frappe.db.get_all(
            "POS Comparison Item",
            filters={"parent": row.name},
            pluck="item_code",
        )
        if sorted(cached_items) == sorted_codes:
            return {
                "comparison_result": frappe.parse_json(row.comparison_result) if row.comparison_result else {},
                "recommendation": row.recommendation,
                "source": "cache",
            }

    return None


def _cache_comparison(item_codes, preferences, result, source, model=None):
    doc = frappe.new_doc("POS Comparison Request")
    doc.source = source
    doc.ai_model = model
    doc.customer_preferences = json.dumps(preferences) if preferences else None
    doc.comparison_result = json.dumps(result.get("comparison_result", {}))
    doc.recommendation = result.get("recommendation", "")
    for code in item_codes:
        item_name = frappe.db.get_value("Item", code, "item_name")
        doc.append("items", {"item_code": code, "item_name": item_name})
    doc.insert(ignore_permissions=True)


def _ai_compare(item_codes, preferences, settings):
    """Call external AI API for comparison. Returns dict."""
    import requests

    items_data = []
    for code in item_codes:
        item = frappe.get_cached_doc("Item", code)
        specs = {}
        model_name = frappe.db.get_value("Item", code, "custom_ch_model")
        if model_name:
            model_doc = frappe.get_cached_doc("CH Model", model_name)
            specs = {sv.specification: sv.value for sv in (model_doc.spec_values or [])}

        price = flt(
            frappe.db.get_value(
                "CH Item Price",
                {"item_code": code, "channel": "POS", "status": "Active"},
                "selling_price",
            )
        )
        items_data.append(
            {"item_code": code, "item_name": item.item_name, "brand": item.brand, "price": price, "specs": specs}
        )

    system_prompt = settings.comparison_system_prompt or "You are a helpful product comparison assistant."
    user_prompt = (
        f"Compare these products for a customer:\n{json.dumps(items_data, indent=2)}"
        f"\nCustomer preferences: {json.dumps(preferences or {})}"
        "\nReturn JSON with keys: comparison_table (list of dicts), recommendation (string)"
    )

    api_key = settings.get_password("api_key")
    endpoint = settings.api_endpoint or "https://api.openai.com/v1/chat/completions"

    start = now_datetime()
    resp = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": settings.comparison_model or "gpt-4o",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": cint(settings.max_tokens) or 2000,
            "response_format": {"type": "json_object"},
        },
        timeout=cint(settings.timeout_sec) or 10,
    )
    resp.raise_for_status()
    latency = (now_datetime() - start).total_seconds() * 1000

    content = resp.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    parsed["source"] = "AI"
    parsed["ai_latency_ms"] = int(latency)
    return parsed


def _static_compare(item_codes, preferences):
    """Specs-based static comparison fallback."""
    comparison_table = []
    for code in item_codes:
        item = frappe.get_cached_doc("Item", code)
        specs = {}
        model_name = frappe.db.get_value("Item", code, "custom_ch_model")
        if model_name:
            model_doc = frappe.get_cached_doc("CH Model", model_name)
            specs = {sv.specification: sv.value for sv in (model_doc.spec_values or [])}

        price = flt(
            frappe.db.get_value(
                "CH Item Price",
                {"item_code": code, "channel": "POS", "status": "Active"},
                "selling_price",
            )
        )
        comparison_table.append(
            {
                "item_code": code,
                "item_name": item.item_name,
                "brand": item.brand,
                "price": price,
                "specs": specs,
            }
        )

    return {
        "comparison_result": comparison_table,
        "recommendation": "Compare the specifications above to find the best match for your needs.",
        "source": "Static Fallback",
    }


def _describe_offer(offer, item):
    if offer.value_type == "Percentage":
        return f"{offer.offer_name}: {flt(offer.value)}% off on {item.get('item_name', '')}."
    elif offer.value_type == "Amount":
        return f"{offer.offer_name}: ₹{flt(offer.value)} off on {item.get('item_name', '')}."
    return ""
