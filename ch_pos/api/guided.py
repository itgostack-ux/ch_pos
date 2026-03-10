import frappe
from frappe.utils import flt, cint


DISCOVERY_QUESTIONS = [
    {
        "question": "What is your budget range?",
        "type": "range",
        "key": "budget",
        "options": {"min": 0, "max": 200000, "step": 1000},
    },
    {
        "question": "What will you primarily use this for?",
        "type": "multi",
        "key": "usage",
        "options": ["Gaming", "Photography", "Business", "Social Media", "Basic Use"],
    },
    {
        "question": "Do you have a brand preference?",
        "type": "choice",
        "key": "brand",
        "options": [],  # filled dynamically
    },
    {
        "question": "Condition preference?",
        "type": "choice",
        "key": "condition",
        "options": ["New", "Refurbished", "Any"],
    },
]


@frappe.whitelist()
def get_guided_questions(sub_category):
    """Return guided selling questions for a sub-category."""
    questions = list(DISCOVERY_QUESTIONS)

    # Fill brand options from items in this sub-category's item group
    item_group = frappe.db.get_value("CH Sub Category", sub_category, "item_group")
    if item_group:
        brands = frappe.db.get_all(
            "Item",
            filters={"item_group": item_group, "disabled": 0},
            fields=["brand"],
            distinct=True,
            pluck="brand",
        )
        for q in questions:
            if q["key"] == "brand":
                q["options"] = sorted(set(b for b in brands if b))
                break

    # Add spec-based questions from CH Sub Category specifications
    sub_cat_doc = frappe.get_cached_doc("CH Sub Category", sub_category)
    for spec in sub_cat_doc.specifications or []:
        questions.append(
            {
                "question": f"Preferred {spec.specification}?",
                "type": "choice",
                "key": f"spec_{spec.specification}",
                "options": _get_spec_options(item_group, spec.specification),
            }
        )

    return questions


def _get_spec_options(item_group, spec_name):
    """Get distinct values of a spec across models in an item group."""
    return frappe.db.sql(
        """SELECT DISTINCT sv.value
           FROM `tabCH Spec Value` sv
           JOIN `tabCH Model` m ON m.name = sv.parent
           JOIN `tabCH Sub Category` sc ON sc.name = m.sub_category
           WHERE sc.item_group = %s AND sv.specification = %s
           ORDER BY sv.value""",
        (item_group, spec_name),
        pluck="value",
    )


@frappe.whitelist()
def get_guided_recommendations(sub_category, responses, warehouse=None, limit=8):
    """Given guided session responses, return ranked item recommendations."""
    if isinstance(responses, str):
        responses = frappe.parse_json(responses)
    limit = min(cint(limit) or 8, 20)

    item_group = frappe.db.get_value("CH Sub Category", sub_category, "item_group")
    if not item_group:
        return []

    # Base query — items in stock at this warehouse
    items = frappe.db.sql(
        """SELECT i.name as item_code, i.item_name, i.image, i.brand, i.item_group,
                  b.actual_qty as stock_qty
           FROM `tabItem` i
           LEFT JOIN `tabBin` b ON b.item_code = i.name AND b.warehouse = %(wh)s
           WHERE i.item_group = %(ig)s
             AND i.disabled = 0 AND i.is_sales_item = 1 AND i.has_variants = 0
             AND (b.actual_qty > 0 OR %(wh)s IS NULL)
           ORDER BY i.item_name""",
        {"ig": item_group, "wh": warehouse},
        as_dict=True,
    )

    prefs = {r.get("key"): r.get("answer") for r in responses}
    scored = []
    for item in items:
        score = _score_item(item, prefs)
        if score > 0:
            # get selling price
            price = flt(
                frappe.db.get_value(
                    "CH Item Price",
                    {"item_code": item.item_code, "channel": "POS", "status": "Active"},
                    "selling_price",
                )
            )
            scored.append(
                {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "image": item.image,
                    "brand": item.brand,
                    "price": price,
                    "stock_qty": flt(item.stock_qty),
                    "match_score": round(score, 1),
                    "reason": _build_reason(item, prefs),
                }
            )

    scored.sort(key=lambda x: x["match_score"], reverse=True)
    return scored[:limit]


def _score_item(item, prefs):
    """Score an item (0-100) based on customer preferences."""
    score = 50  # base score

    # Brand match
    if prefs.get("brand") and prefs["brand"] != "Any":
        if item.brand == prefs["brand"]:
            score += 20
        else:
            score -= 10

    # Budget match (needs price lookup)
    budget = prefs.get("budget")
    if budget:
        price = flt(
            frappe.db.get_value(
                "CH Item Price",
                {"item_code": item.item_code, "channel": "POS", "status": "Active"},
                "selling_price",
            )
        )
        if price:
            budget_val = flt(budget)
            if price <= budget_val:
                score += 15
            elif price <= budget_val * 1.1:
                score += 5
            else:
                score -= 20

    return max(0, min(100, score))


def _build_reason(item, prefs):
    parts = []
    if prefs.get("brand") and item.brand == prefs.get("brand"):
        parts.append(f"Matches preferred brand ({item.brand})")
    if prefs.get("budget"):
        parts.append("Within budget range")
    return "; ".join(parts) if parts else "Good match for your requirements"
