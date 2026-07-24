import frappe
from frappe import _
from frappe.utils import flt, cint

from ch_pos.api.scope_guard import assert_pos_profile_scope
from ch_pos.config import get_control_setting


# Universal questions shown for every category. Anything category-specific
# (Capacity, Material, Use Case, Storage, Colour, ...) MUST come from the
# Sub Category's Specifications table so the form always matches the product.
UNIVERSAL_QUESTIONS = [
    {
        "question": "What is your budget range?",
        "type": "range",
        "key": "budget",
        "options": {"min": 0, "max": 200000, "step": 1000},
    },
    {
        "question": "Do you have a brand preference?",
        "type": "choice",
        "key": "brand",
        "options": [],  # filled dynamically from items in this sub-category
    },
]


# Sub Category Spec.spec_type → question type for the guided form.
_SPEC_TYPE_TO_QUESTION_TYPE = {
    "Variant": "choice",
    "Property": "choice",
}

# A spec is only usable as a guided question when it offers a real, bounded
# choice. Below 2 distinct values there is nothing to choose; above this cap the
# data is unnormalised free text (e.g. Colour with 1000+ values) and a dropdown
# of it is pure noise.
MIN_SPEC_OPTIONS = 2


def _positive_setting(fieldname, default, maximum):
    value = cint(get_control_setting(fieldname, default)) or default
    return max(1, min(value, maximum))


def _ensure_within_limit(rows, limit, label):
    if len(rows) > limit:
        frappe.throw(
            _("{0} exceeds the configured limit of {1} rows. Narrow the selection or raise the limit.").format(
                label, limit
            )
        )
    return rows


@frappe.whitelist()
def get_guided_questions(sub_category) -> list:
    """Return guided selling questions for a sub-category.

    Always:
      • Budget (universal)
      • Brand (auto-populated from items in this sub-category)
      • Condition (only when refurbished/exchange items exist)
    Plus a question per Sub Category specification, sourced from real
    `CH Model Spec Value` data so options never include junk like
    "Photography" for bags.
    """
    if not sub_category:
        return []

    questions = [dict(q) for q in UNIVERSAL_QUESTIONS]

    # ── Brand options from items in this sub-category ────────────────────
    catalog_limit = _positive_setting("guided_catalog_result_limit", 1000, 10000)
    brands = frappe.db.get_all(
        "Item",
        filters={"ch_sub_category": sub_category, "disabled": 0},
        fields=["brand"],
        distinct=True,
        pluck="brand",
        limit_page_length=catalog_limit + 1,
    )
    _ensure_within_limit(brands, catalog_limit, _("Guided brand options"))
    brand_opts = sorted({b for b in brands if b})
    for q in questions:
        if q["key"] == "brand":
            q["options"] = brand_opts
            break
    # Drop the brand question entirely if no brands exist for this sub-category
    if not brand_opts:
        questions = [q for q in questions if q["key"] != "brand"]

    # ── Condition (only if the catalogue actually has refurbished stock) ─
    if _has_refurbished_items(sub_category):
        questions.append({
            "question": "Condition preference?",
            "type": "choice",
            "key": "condition",
            "options": ["New", "Refurbished", "Any"],
        })

    # ── Spec-driven category-specific questions ──────────────────────────
    sub_cat_doc = frappe.get_cached_doc("CH Sub Category", sub_category)
    for spec in sub_cat_doc.specifications or []:
        spec_name = spec.spec
        if not spec_name:
            continue
        options = _get_spec_options(sub_category, spec_name)
        # Skip specs that are unusable as a guided question: fewer than 2
        # distinct values (nothing to choose) or an unbounded set of free-text
        # values (e.g. Colour with 1000+ entries) that renders a junk dropdown.
        max_spec_options = _positive_setting("guided_spec_option_limit", 40, 500)
        if not options or not (MIN_SPEC_OPTIONS <= len(options) <= max_spec_options):
            continue
        questions.append({
            "question": f"Preferred {spec_name}?",
            "type": _SPEC_TYPE_TO_QUESTION_TYPE.get(spec.spec_type, "choice"),
            "key": f"spec_{spec_name}",
            "options": options,
            "spec_name": spec_name,
        })

    return questions


def _has_refurbished_items(sub_category):
    """True when at least one non-disabled item in the sub-category is refurbished."""
    if not frappe.db.has_column("Item", "ch_item_condition"):
        return False
    return bool(frappe.db.exists("Item", {
        "ch_sub_category": sub_category,
        "disabled": 0,
        "ch_item_condition": ("in", ["Refurbished", "Used", "Pre-Owned"]),
    }))


def _get_spec_options(sub_category, spec_name):
    """Get distinct values of a spec across models in a sub-category."""
    limit = _positive_setting("guided_spec_option_limit", 40, 500)
    return frappe.db.sql(
        """SELECT DISTINCT sv.spec_value
           FROM `tabCH Model Spec Value` sv
           JOIN `tabCH Model` m ON m.name = sv.parent
           WHERE m.sub_category = %s AND sv.spec = %s AND IFNULL(sv.spec_value, '') != ''
           ORDER BY sv.spec_value
           LIMIT %s""",
        (sub_category, spec_name, limit + 1),
        pluck="spec_value",
    )


@frappe.whitelist()
def get_guided_recommendations(
    sub_category, responses, warehouse=None, pos_profile=None, limit=8
) -> list:
    """Given guided session responses, return ranked item recommendations."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    anchors = assert_pos_profile_scope(pos_profile)
    if warehouse and warehouse != anchors.get("warehouse"):
        frappe.throw(_("Warehouse does not match the active POS Profile."), frappe.PermissionError)
    warehouse = anchors.get("warehouse")
    if isinstance(responses, str):
        responses = frappe.parse_json(responses)
    limit = min(cint(limit) or 8, 20)
    candidate_limit = _positive_setting("guided_candidate_limit", 1000, 5000)
    related_limit = _positive_setting("guided_related_row_limit", 10000, 50000)

    # Base query — items in this specific sub-category (e.g. Backpacks only,
    # not the entire Accessories item_group which includes earbuds, cables...)
    items = frappe.db.sql(
        """SELECT i.name as item_code, i.item_name, i.image, i.brand, i.item_group,
                  i.has_serial_no, i.stock_uom, i.ch_model,
                  b.actual_qty as stock_qty
           FROM `tabItem` i
           LEFT JOIN `tabBin` b ON b.item_code = i.name AND b.warehouse = %(wh)s
           WHERE i.ch_sub_category = %(sub_cat)s
             AND i.disabled = 0 AND i.is_sales_item = 1 AND i.has_variants = 0
             AND IFNULL(i.ch_lifecycle_status, '') IN ('Active', 'Obsolete')
           ORDER BY i.item_name
           LIMIT %(candidate_limit)s""",
        {"sub_cat": sub_category, "wh": warehouse, "candidate_limit": candidate_limit + 1},
        as_dict=True,
    )
    _ensure_within_limit(items, candidate_limit, _("Guided item candidates"))

    item_codes = [item.item_code for item in items]
    price_map = {}
    if item_codes:
        price_rows = frappe.get_all(
            "CH Item Price",
            filters={
                "item_code": ("in", item_codes),
                "channel": "POS",
                "status": "Active",
            },
            fields=["item_code", "selling_price"],
            limit_page_length=related_limit + 1,
        )
        _ensure_within_limit(price_rows, related_limit, _("Guided price rows"))
        for row in price_rows:
            price_map[row.item_code] = max(
                flt(row.selling_price), flt(price_map.get(row.item_code))
            )

    uom_names = list({item.stock_uom for item in items if item.stock_uom})
    uom_map = {}
    if uom_names:
        all_uoms = frappe.get_all(
            "UOM",
            filters={"name": ("in", uom_names)},
            fields=["name", "must_be_whole_number"],
            limit_page_length=related_limit + 1,
        )
        _ensure_within_limit(all_uoms, related_limit, _("Guided UOM rows"))
        uom_map = {u.name: cint(u.must_be_whole_number) for u in all_uoms}

    prefs = {r.get("key"): r.get("answer") for r in responses}

    # Pre-fetch specs for every model in scope so we can score against
    # category-specific spec_* preferences (Capacity, Material, Colour, ...).
    all_model_names = list({item.ch_model for item in items if item.ch_model})
    model_specs = {}
    if all_model_names:
        spec_rows = frappe.db.get_all(
            "CH Model Spec Value",
            filters={"parent": ["in", all_model_names]},
            fields=["parent", "spec", "spec_value"],
            limit_page_length=related_limit + 1,
        )
        _ensure_within_limit(spec_rows, related_limit, _("Guided model specification rows"))
        for s in spec_rows:
            model_specs.setdefault(s.parent, {})[s.spec] = s.spec_value

    scored = []
    for item in items:
        item_specs = model_specs.get(item.ch_model, {}) if item.ch_model else {}
        price = flt(price_map.get(item.item_code))
        score = _score_item(item, prefs, item_specs, price)
        if score > 0:
            scored.append(
                {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "image": item.image,
                    "brand": item.brand,
                    "ch_model": item.ch_model or None,
                    "price": price,
                    "stock_qty": flt(item.stock_qty),
                    "has_serial_no": cint(item.has_serial_no),
                    "stock_uom": item.stock_uom or "Nos",
                    "must_be_whole_number": cint(uom_map.get(item.stock_uom, 0)),
                    "match_score": round(score, 1),
                    "reason": _build_reason(item, prefs, item_specs),
                    "specs": item_specs,
                }
            )

    # Best match first; among equal scores, in-stock items rank ahead so a store
    # surfaces what it can sell now without ever returning an empty list.
    scored.sort(
        key=lambda x: (x["match_score"], 1 if flt(x["stock_qty"]) > 0 else 0),
        reverse=True,
    )
    top = scored[:limit]

    return top


@frappe.whitelist()
def get_guided_catalog() -> dict:
    """Return active categories and sub-categories for guided POS flow."""
    result_limit = _positive_setting("guided_catalog_result_limit", 1000, 10000)
    categories = frappe.get_all(
        "CH Category",
        filters={"disabled": 0},
        fields=["name", "category_name"],
        order_by="category_name asc",
        limit_page_length=result_limit + 1,
    )
    sub_categories = frappe.get_all(
        "CH Sub Category",
        filters={"disabled": 0},
        fields=["name", "sub_category_name", "category"],
        order_by="sub_category_name asc",
        limit_page_length=result_limit + 1,
    )
    _ensure_within_limit(categories, result_limit, _("Guided categories"))
    _ensure_within_limit(sub_categories, result_limit, _("Guided sub-categories"))
    return {
        "categories": categories,
        "sub_categories": sub_categories,
    }


@frappe.whitelist(methods=["POST"])
def save_guided_session(
    session_name=None,
    pos_profile=None,
    category=None,
    sub_category=None,
    kiosk_token=None,
    responses=None,
    recommendations=None,
    status="Completed",
) -> dict:
    """Create or update POS Guided Session from POS UI."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    anchors = assert_pos_profile_scope(pos_profile)
    if isinstance(responses, str):
        responses = frappe.parse_json(responses)
    if isinstance(recommendations, str):
        recommendations = frappe.parse_json(recommendations)

    responses = responses or []
    recommendations = recommendations or []

    if not sub_category:
        frappe.throw("Sub Category is required", title=_("Validation Error"))

    if session_name and frappe.db.exists("POS Guided Session", session_name):
        doc = frappe.get_doc("POS Guided Session", session_name)
        if doc.pos_profile != pos_profile:
            frappe.throw(_("Guided session belongs to another POS Profile."), frappe.PermissionError)
    else:
        doc = frappe.new_doc("POS Guided Session")

    if kiosk_token:
        token_profile = frappe.db.get_value("POS Kiosk Token", kiosk_token, "pos_profile")
        if token_profile != pos_profile:
            frappe.throw(_("Queue token belongs to another POS Profile."), frappe.PermissionError)

    warehouse = anchors.get("warehouse")

    doc.store = warehouse
    doc.pos_profile = pos_profile
    doc.category = category
    doc.sub_category = sub_category
    doc.kiosk_token = kiosk_token
    doc.status = status if status in ("In Progress", "Completed", "Abandoned") else "In Progress"

    doc.set("responses", [])
    for r in responses:
        answer = r.get("answer")
        if isinstance(answer, (list, tuple)):
            answer = ", ".join([str(v) for v in answer if v is not None])
        doc.append("responses", {
            "question": r.get("question") or r.get("key") or "",
            "answer": str(answer or "")[:140],
        })

    doc.set("recommended_items", [])
    for idx, rec in enumerate(recommendations, 1):
        if not rec.get("item_code"):
            continue
        doc.append("recommended_items", {
            "item_code": rec.get("item_code"),
            "rank": idx,
            "match_score": flt(rec.get("match_score") or 0),
            "reason": (rec.get("reason") or "")[:1000],
        })

    doc.save()

    return {
        "name": doc.name,
        "status": doc.status,
    }


def _score_item(item, prefs, item_specs=None, price=0):
    """Score an item (0-100) based on customer preferences.

    item_specs: optional dict {spec_name: spec_value} for the item's model,
    used to score category-specific spec_* preferences.
    """
    score = 50  # base score
    item_specs = item_specs or {}

    # Brand match
    if prefs.get("brand") and prefs["brand"] != "Any":
        if item.brand == prefs["brand"]:
            score += 20
        else:
            score -= 10

    # Budget match (needs price lookup)
    budget = prefs.get("budget")
    if budget:
        price = flt(price)
        if price:
            budget_val = flt(budget)
            if price <= budget_val:
                score += 15
            elif price <= budget_val * 1.1:
                score += 5
            else:
                score -= 20

    # Spec preference match — category-driven, e.g. Capacity, Material, Colour
    for key, answer in prefs.items():
        if not key.startswith("spec_") or not answer or answer == "Any":
            continue
        spec_name = key[len("spec_"):]
        actual = item_specs.get(spec_name)
        if actual is None:
            continue
        # Multi-select answers come as comma-separated string from the form
        wanted = [a.strip() for a in str(answer).split(",") if a.strip()]
        if not wanted:
            continue
        if str(actual) in wanted:
            score += 12
        else:
            score -= 6

    return max(0, min(100, score))


def _build_reason(item, prefs, item_specs=None):
    parts = []
    item_specs = item_specs or {}
    if prefs.get("brand") and item.brand == prefs.get("brand"):
        parts.append(f"Matches preferred brand ({item.brand})")
    if prefs.get("budget"):
        parts.append("Within budget range")
    for key, answer in prefs.items():
        if not key.startswith("spec_") or not answer or answer == "Any":
            continue
        spec_name = key[len("spec_"):]
        actual = item_specs.get(spec_name)
        if actual is None:
            continue
        wanted = [a.strip() for a in str(answer).split(",") if a.strip()]
        if str(actual) in wanted:
            parts.append(f"{spec_name}: {actual}")
    return "; ".join(parts) if parts else "Good match for your requirements"
