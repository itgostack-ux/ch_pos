import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from ch_pos.api.scope_guard import assert_pos_profile_scope
from ch_pos.api.outbound_security import parse_exact_host_allowlist, post_json_with_bearer
from ch_pos.config import get_control_setting, is_privileged_user, require_configured_roles
from ch_pos.rate_limits import increment_fixed_window


def _positive_setting(fieldname, default, maximum):
	value = cint(get_control_setting(fieldname, default)) or default
	return max(1, min(value, maximum))


def _consume_rate_limit(endpoint, identity, limit, window):
	if increment_fixed_window(f"ai:{endpoint}", identity, window) > limit:
		frappe.throw(
			_("POS AI request limit exceeded. Please try again later."),
			frappe.RateLimitExceededError)


def _enforce_ai_rate_limit(endpoint):
	window = _positive_setting("pos_ai_rate_window_seconds", 3600, 86400)
	user_limit = _positive_setting("pos_ai_requests_per_user", 60, 10000)
	ip_limit = _positive_setting("pos_ai_requests_per_ip", 180, 30000)
	_consume_rate_limit(endpoint, f"user:{frappe.session.user}", user_limit, window)
	ip = getattr(frappe.local, "request_ip", None)
	if ip:
		_consume_rate_limit(endpoint, f"ip:{ip}", ip_limit, window)


def _authorize_ai(pos_profile, feature_field, endpoint):
	frappe.has_permission("Item", ptype="read", throw=True)
	frappe.has_permission("POS Profile", ptype="read", throw=True)
	if not pos_profile:
		if not is_privileged_user():
			frappe.throw(_("POS Profile is required for POS AI."), frappe.PermissionError)
		anchors = {"pos_profile": None, "company": None, "warehouse": None, "store": None}
		extension = None
	else:
		anchors = assert_pos_profile_scope(pos_profile)
		extension = frappe.db.get_value(
			"POS Profile Extension",
			{"pos_profile": pos_profile},
			["disabled", feature_field, "max_comparison_items"],
			as_dict=True)
		if not is_privileged_user() and (
			not extension or cint(extension.disabled) or not cint(extension.get(feature_field))
		):
			frappe.throw(_("This POS AI feature is disabled for the selected profile."), frappe.PermissionError)
	_enforce_ai_rate_limit(endpoint)
	return anchors, extension


def _validate_payload(value):
	limit = _positive_setting("pos_ai_max_payload_chars", 8000, 100000)
	if len(json.dumps(value or {}, default=str)) > limit:
		frappe.throw(_("POS AI request payload exceeds the configured limit."))


@frappe.whitelist()
def compare_items(item_codes, customer_preferences=None, pos_profile=None) -> dict:
	"""Generate AI or static comparison for 2-3 items.

	Resilience: AI timeout/failure always falls back to static comparison.
	Never raises an exception to the caller -- degraded mode is returned instead.
	"""
	anchors, extension = _authorize_ai(pos_profile, "enable_ai_comparison", "compare")
	if isinstance(item_codes, str):
		item_codes = frappe.parse_json(item_codes)
	if isinstance(customer_preferences, str):
		customer_preferences = frappe.parse_json(customer_preferences)

	if not isinstance(item_codes, (list, tuple)) or len(item_codes) < 2:
		frappe.throw("At least 2 items are required for comparison.", title=_("Validation Error"))
	configured_max = cint(extension.max_comparison_items) if extension else 3
	max_items = max(2, min(configured_max or 3, 3))
	item_codes = list(dict.fromkeys(str(code).strip() for code in item_codes if str(code).strip()))
	if len(item_codes) < 2:
		frappe.throw("At least 2 distinct items are required for comparison.", title=_("Validation Error"))
	item_codes = item_codes[:max_items]
	_validate_payload(customer_preferences)
	item_data = _load_comparison_item_data(item_codes)

	cached = _find_cached_comparison(item_codes, customer_preferences, anchors.get("warehouse"))
	if cached:
		return cached

	settings = _get_ai_settings()

	if settings and settings.enable_ai:
		try:
			result = _ai_compare(item_data, customer_preferences, settings)
			_cache_comparison(item_codes, customer_preferences, result, "AI", settings.comparison_model, anchors.get("warehouse"))
			return result
		except Exception:
			frappe.log_error(frappe.get_traceback(), "POS AI Comparison failed - using static fallback")

	result = _static_compare(item_data, customer_preferences)
	_cache_comparison(item_codes, customer_preferences, result, "Static Fallback", warehouse=anchors.get("warehouse"))
	return result


@frappe.whitelist()
def get_upsell_suggestions(item_code, cart_items=None, pos_profile=None) -> list:
	"""Hybrid upsell suggestions: smart rules (instant) + optional AI coaching tip.

	Flow: smart rule engine picks best plans/accessories/upgrades from catalog
	→ optionally calls AI for a one-sentence sales coaching tip
	→ returns instantly even if AI is slow/unavailable.
	Resilience: returns empty list on any failure instead of raising.
	"""
	_authorize_ai(pos_profile, "enable_ai_upsell", "upsell")
	if isinstance(cart_items, str):
		cart_items = frappe.parse_json(cart_items)
	if not isinstance(cart_items or [], list):
		frappe.throw(_("Cart items must be a list."))
	max_cart_items = _positive_setting("pos_ai_max_cart_items", 50, 500)
	if len(cart_items or []) > max_cart_items:
		frappe.throw(_("Cart exceeds the configured POS AI item limit."))
	_validate_payload(cart_items)
	item = frappe.get_cached_doc("Item", item_code)
	item.check_permission("read")
	try:
		# Get device price
		device_price = _get_item_pos_price(item.name)

		# Cart item codes already added (to avoid duplicate suggestions)
		cart_codes = set()
		if cart_items:
			for ci in cart_items:
				cart_codes.add(ci.get("item_code", ci) if isinstance(ci, dict) else ci)

		# ------ Primary: Smart Rule Engine (instant, free) ------
		suggestions = _smart_rule_upsell(item, device_price, cart_codes)

		if not suggestions:
			return []

		# ------ Secondary: Optional AI coaching tip ------
		settings = _get_ai_settings()
		if settings and settings.enable_ai:
			try:
				tip = _ai_coaching_tip(item, device_price, suggestions, settings)
				if tip:
					suggestions[0]["sales_tip"] = tip
			except Exception:
				frappe.log_error(frappe.get_traceback(), "POS optional AI tip generation failed")

		# Template tip fallback if no AI tip
		if not suggestions[0].get("sales_tip"):
			suggestions[0]["sales_tip"] = _template_sales_tip(item, device_price)

		# Strip internal fields before returning
		for s in suggestions:
			s.pop("_sold_count", None)

		return suggestions

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"get_upsell_suggestions failed for {item_code}")
		return []


@frappe.whitelist()
def explain_offers(cart, pos_profile=None) -> dict:
	"""AI-powered plain-language explanation of applied offers.

	Flow: gather offer data → call AI for friendly explanation
	→ fall back to template-based explanation on failure.
	Resilience: returns a safe message on any failure.
	"""
	_authorize_ai(pos_profile, "enable_ai_upsell", "offers")
	if isinstance(cart, str):
		cart = frappe.parse_json(cart)
	if not isinstance(cart, dict):
		frappe.throw(_("Cart must be an object."))
	_validate_payload(cart)
	items = cart.get("items", [])
	if not isinstance(items, list):
		frappe.throw(_("Cart items must be a list."))
	if len(items) > _positive_setting("pos_ai_max_cart_items", 50, 500):
		frappe.throw(_("Cart exceeds the configured POS AI item limit."))
	try:
		if not items:
			return "No items in cart."

		offer_data = _gather_offer_data(items)
		if not offer_data:
			return "No special offers apply to this cart."

		settings = _get_ai_settings()
		if settings and settings.enable_ai:
			try:
				result = _ai_explain_offers(offer_data, cart, settings)
				if result:
					return result
			except Exception:
				frappe.log_error(frappe.get_traceback(), "AI offer explain failed - using template")

		# Template-based fallback
		explanations = []
		for od in offer_data:
			desc = _describe_offer(od["offer"], od["item"])
			if desc:
				explanations.append(desc)
		return " ".join(explanations) if explanations else "No special offers apply to this cart."

	except Exception:
		frappe.log_error(frappe.get_traceback(), "explain_offers failed")
		return "Offer information temporarily unavailable."


# -- internal helpers ---------------------------------------------------------


def _get_ai_settings():
	try:
		return frappe.get_cached_doc("POS AI Settings")
	except frappe.DoesNotExistError:
		return None


def _ai_allowed_hosts(settings):
	return parse_exact_host_allowlist(
		settings.get("allowed_api_hosts") or "api.openai.com",
		label=_("POS AI API"))


def _post_ai_request(settings, payload, *, timeout):
	return post_json_with_bearer(
		settings.api_endpoint or "https://api.openai.com/v1/chat/completions",
		allowed_hosts=_ai_allowed_hosts(settings),
		label=_("POS AI API"),
		api_key=settings.get_password("api_key"),
		payload=payload,
		timeout=timeout)


def _ensure_ai_rows(rows, label):
	limit = _positive_setting("pos_ai_related_row_limit", 1000, 10000)
	if len(rows) > limit:
		frappe.throw(
			_("{0} exceeds the configured POS AI limit of {1} rows.").format(label, limit)
		)
	return rows


def _get_pos_prices(item_codes):
	item_codes = list(dict.fromkeys(item_codes or []))
	if not item_codes:
		return {}
	limit = _positive_setting("pos_ai_related_row_limit", 1000, 10000)
	ch_rows = frappe.get_all(
		"CH Item Price",
		filters={"item_code": ("in", item_codes), "channel": "POS", "status": "Active"},
		fields=["item_code", "selling_price"],
		order_by="modified desc, name desc",
		limit_page_length=limit + 1)
	_ensure_ai_rows(ch_rows, _("POS item prices"))
	prices = {}
	for row in ch_rows:
		prices.setdefault(row.item_code, flt(row.selling_price))
	missing = [code for code in item_codes if not prices.get(code)]
	if missing:
		fallback_rows = frappe.get_all(
			"Item Price",
			filters={"item_code": ("in", missing), "selling": 1},
			fields=["item_code", "price_list_rate"],
			order_by="modified desc, name desc",
			limit_page_length=limit + 1)
		_ensure_ai_rows(fallback_rows, _("Fallback item prices"))
		for row in fallback_rows:
			prices.setdefault(row.item_code, flt(row.price_list_rate))
	return prices


def _load_comparison_item_data(item_codes):
	rows = frappe.get_list(
		"Item",
		filters={"name": ("in", item_codes)},
		fields=["name", "item_name", "brand", "ch_model"],
		limit_page_length=len(item_codes) + 1)
	by_name = {row.name: row for row in rows}
	if set(by_name) != set(item_codes):
		frappe.throw(_("One or more comparison items do not exist or are not readable."), frappe.PermissionError)
	prices = _get_pos_prices(item_codes)
	model_names = sorted({row.ch_model for row in rows if row.ch_model})
	specs_by_model = {}
	if model_names:
		limit = _positive_setting("pos_ai_related_row_limit", 1000, 10000)
		spec_rows = frappe.get_all(
			"CH Model Spec Value",
			filters={"parent": ("in", model_names)},
			fields=["parent", "spec", "spec_value"],
			limit_page_length=limit + 1)
		_ensure_ai_rows(spec_rows, _("Comparison specifications"))
		for row in spec_rows:
			specs_by_model.setdefault(row.parent, {})[row.spec] = row.spec_value
	return [
		{
			"item_code": code,
			"item_name": by_name[code].item_name,
			"brand": by_name[code].brand,
			"price": flt(prices.get(code)),
			"specs": specs_by_model.get(by_name[code].ch_model, {}),
		}
		for code in item_codes
	]


def _find_cached_comparison(item_codes, preferences=None, warehouse=None):
	"""Look for a recent cached comparison with the same items."""
	settings = _get_ai_settings()
	ttl = cint(settings.cache_ttl_hours) if settings else 24
	cutoff = frappe.utils.add_to_date(now_datetime(), hours=-ttl)
	sorted_codes = sorted(item_codes)
	preference_json = json.dumps(preferences or {}, sort_keys=True, separators=(",", ":"))
	filters = {"creation": [">=", cutoff], "owner": frappe.session.user}
	if warehouse:
		filters["created_at_store"] = warehouse

	existing = frappe.db.get_all(
		"POS Comparison Request",
		filters=filters,
		fields=["name", "comparison_result", "recommendation", "customer_preferences"],
		order_by="creation desc",
		limit=50)
	parent_names = [row.name for row in existing]
	items_by_parent = {}
	if parent_names:
		for child in frappe.db.get_all(
			"POS Comparison Item",
			filters={"parent": ("in", parent_names)},
			fields=["parent", "item_code"]):
			items_by_parent.setdefault(child.parent, []).append(child.item_code)

	for row in existing:
		cached_preferences = frappe.parse_json(row.customer_preferences) if row.customer_preferences else {}
		cached_json = json.dumps(cached_preferences, sort_keys=True, separators=(",", ":"))
		if cached_json == preference_json and sorted(items_by_parent.get(row.name, [])) == sorted_codes:
			return {
				"comparison_result": frappe.parse_json(row.comparison_result) if row.comparison_result else {},
				"recommendation": row.recommendation,
				"source": "cache",
			}

	return None


def _cache_comparison(item_codes, preferences, result, source, model=None, warehouse=None):
	try:
		doc = frappe.new_doc("POS Comparison Request")
		doc.source = source
		doc.ai_model = model
		doc.created_at_store = warehouse
		doc.customer_preferences = json.dumps(preferences, sort_keys=True) if preferences else None
		doc.comparison_result = json.dumps(result.get("comparison_result", {}))
		doc.recommendation = result.get("recommendation", "")
		item_names = {
			row.name: row.item_name for row in frappe.db.get_all(
				"Item", filters={"name": ("in", item_codes)}, fields=["name", "item_name"]
			)
		}
		for code in item_codes:
			doc.append("items", {"item_code": code, "item_name": item_names.get(code)})
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Comparison cache write failed")


def _ai_compare(items_data, preferences, settings):
	"""Call external AI API for comparison. Returns dict."""
	import requests

	system_prompt = settings.comparison_system_prompt or "You are a helpful product comparison assistant."
	user_prompt = (
		f"Compare these products for a customer:\n{json.dumps(items_data, indent=2)}"
		f"\nCustomer preferences: {json.dumps(preferences or {})}"
		"\nReturn JSON with keys: comparison_table (list of dicts), recommendation (string)"
	)

	start = now_datetime()
	resp = _post_ai_request(
		settings,
		{
			"model": settings.comparison_model or "gpt-4o",
			"messages": [
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_prompt},
			],
			"max_tokens": cint(settings.max_tokens) or 2000,
			"response_format": {"type": "json_object"},
		},
		timeout=cint(settings.timeout_sec) or 10)
	resp.raise_for_status()
	latency = (now_datetime() - start).total_seconds() * 1000

	content = resp.json()["choices"][0]["message"]["content"]
	parsed = json.loads(content)
	parsed["source"] = "AI"
	parsed["ai_latency_ms"] = int(latency)
	return parsed


def _static_compare(items_data, preferences):
	"""Specs-based static comparison fallback."""
	return {
		"comparison_result": items_data,
		"recommendation": "Compare the specifications above to find the best match for your needs.",
		"source": "Static Fallback",
	}


def _describe_offer(offer, item):
	if isinstance(offer, dict):
		vtype = offer.get("value_type", "")
		val = flt(offer.get("value", 0))
		name = offer.get("offer_name", "")
	else:
		vtype = offer.value_type
		val = flt(offer.value)
		name = offer.offer_name

	item_name = item.get("item_name", "") if isinstance(item, dict) else getattr(item, "item_name", "")

	if vtype == "Percentage":
		return f"{name}: {val}% off on {item_name}."
	elif vtype == "Amount":
		return f"{name}: Rs.{val} off on {item_name}."
	return ""


# -- Smart Hybrid Upsell Engine -----------------------------------------------

# Price tiers for warranty plan matching
TIER_PREMIUM = 50000   # ₹50K+
TIER_MID = 15000       # ₹15K–50K
# Below ₹15K = budget

# Plan recommendation matrix: {tier: [(plan_type_keyword, priority, reason_template), ...]}
PLAN_TIERS = {
	"premium": [
		("Gold", 1, "Complete protection for your ₹{price} {brand} — covers everything for 24 months"),
		("Theft", 1, "Theft & loss cover is a must-have for premium devices"),
		("ADLD", 2, "Accidental damage, liquid & dust protection — peace of mind for {duration}"),
		("Screen", 2, "Screen repairs cost ₹5,000+ — this covers it for just ₹{plan_price}"),
	],
	"mid": [
		("Extended Warranty 24", 1, "Extend your warranty to 24 months — the #1 plan for {group}"),
		("Screen", 1, "Screen protection at just ₹{plan_price} — most popular for {group}"),
		("ADLD", 2, "Covers accidental damage & liquid spills for {duration}"),
		("Extended Warranty 12", 2, "Basic 12-month extended warranty — affordable peace of mind"),
	],
	"budget": [
		("Extended Warranty 12", 1, "Affordable protection — extend your warranty for just ₹{plan_price}"),
		("Screen", 2, "Protect your screen for just ₹{plan_price}"),
	],
}


def _get_item_pos_price(item_code):
	"""Get POS selling price for an item, falling back to Item Price."""
	price = flt(frappe.db.get_value(
		"CH Item Price",
		{"item_code": item_code, "channel": "POS", "status": "Active"},
		"selling_price"))
	if not price:
		price = flt(frappe.db.get_value(
			"Item Price",
			{"item_code": item_code, "selling": 1},
			"price_list_rate"))
	return price


def _smart_rule_upsell(item, device_price, cart_codes):
	"""Smart rule-based upsell: picks best warranty plans, accessories, upgrades."""
	suggestions = []

	# 1. Warranty plan matching by price tier
	plan_suggestions = _match_warranty_plans(item, device_price, cart_codes)
	suggestions.extend(plan_suggestions)

	# 2. Accessory matching (brand + group)
	acc_suggestions = _match_accessories(item, cart_codes)
	suggestions.extend(acc_suggestions)

	# 3. Upgrade suggestions (same group, slightly higher price)
	if device_price > 0:
		upgrade_suggestions = _match_upgrades(item, device_price, cart_codes)
		suggestions.extend(upgrade_suggestions)

	# Sort: priority 1 first, then by sold-history popularity
	suggestions.sort(key=lambda s: (s["priority"], -s.get("_sold_count", 0)))

	# Limit to top 4
	return suggestions[:4]


def _match_warranty_plans(item, device_price, cart_codes):
	"""Match warranty plans based on device price tier and sold history."""
	# Get all active plans
	limit = _positive_setting("pos_ai_related_row_limit", 1000, 10000)
	plans = frappe.db.get_all(
		"CH Warranty Plan",
		filters={"status": "Active"},
		fields=["name", "plan_name", "price", "duration_months", "plan_type",
				"brand", "coverage_description", "service_item"],
		limit_page_length=limit + 1)
	_ensure_ai_rows(plans, _("Warranty plan candidates"))

	# Never recommend a plan whose service_item is not a Live (Active-lifecycle)
	# Item — it would be blocked at Sales Invoice ("Activate the item first").
	from ch_item_master.ch_item_master.governance import filter_sellable_items
	_live = filter_sellable_items([p.service_item for p in plans])
	plans = [p for p in plans if not p.service_item or p.service_item in _live]

	if not plans:
		return []

	# Determine price tier
	if device_price >= TIER_PREMIUM:
		tier = "premium"
	elif device_price >= TIER_MID:
		tier = "mid"
	else:
		tier = "budget"

	tier_rules = PLAN_TIERS.get(tier, PLAN_TIERS["budget"])

	# Get sold-history counts for this item_group (for popularity boost)
	sold_counts = {}
	if item.item_group:
		sold_data = frappe.db.sql("""
			SELECT sp.warranty_plan, COUNT(*) as cnt
			FROM `tabActive VAS Plans` sp
			JOIN tabItem i ON i.name = sp.item_code
			WHERE i.item_group = %(group)s
			GROUP BY sp.warranty_plan
		""", {"group": item.item_group}, as_dict=1)
		sold_counts = {s.warranty_plan: s.cnt for s in sold_data}

	matched = []
	used_plans = set()

	for keyword, priority, reason_tpl in tier_rules:
		for plan in plans:
			if plan.name in used_plans or plan.name in cart_codes:
				continue
			# Brand filter: if plan has brand, must match device brand
			if plan.brand and plan.brand != item.brand:
				continue
			# Match by keyword in plan_name
			if keyword.lower() not in plan.plan_name.lower():
				continue

			# Build compelling reason
			reason = reason_tpl.format(
				price=f"{device_price:,.0f}" if device_price else "your device",
				brand=item.brand or item.item_group or "device",
				group=item.item_group or "devices",
				duration=f"{plan.duration_months} months" if plan.duration_months else "extended period",
				plan_price=f"{flt(plan.price):,.0f}")

			# Boost reason with sold history
			sold_count = sold_counts.get(plan.name, 0)
			if sold_count >= 3:
				reason += f" — {sold_count} customers chose this!"
			elif sold_count >= 1:
				reason += " — popular choice"

			matched.append({
				"item_code": plan.service_item or plan.name,
				"warranty_plan": plan.name,
				"item_name": plan.plan_name,
				# Preserve the master classification. Treating every plan as a
				# Protection Plan makes Extended/Own Warranty cart rows claim
				# is_vas=1, which the invoice integrity gate correctly rejects.
				"plan_type": plan.plan_type,
				"type": (
					"Protection Plan"
					if plan.plan_type in ("Value Added Service", "Protection Plan")
					else "Warranty"
				),
				"reason": reason,
				"price": flt(plan.price),
				"priority": priority,
				"source": "Smart",
				"_sold_count": sold_count,
			})
			used_plans.add(plan.name)
			break  # One plan per tier rule

	return matched


def _match_accessories(item, cart_codes):
	"""Match accessories by brand or item group. Filters junk items."""
	filters = {"item_group": "Accessories", "disabled": 0, "is_sales_item": 1}
	accessories = frappe.db.get_all(
		"Item",
		filters=filters,
		fields=["name as item_code", "item_name", "brand"],
		limit=20)
	accessory_prices = _get_pos_prices([row.item_code for row in accessories])

	suggestions = []
	for acc in accessories:
		if acc.item_code in cart_codes:
			continue
		# Filter junk: skip items with only numeric/very short names
		if len(acc.item_name or "") < 4 or (acc.item_name or "").strip().isdigit():
			continue

		price = flt(accessory_prices.get(acc.item_code))

		# Prefer brand match
		brand_match = item.brand and acc.brand and acc.brand == item.brand
		reason = (
			f"Made for your {item.brand}" if brand_match
			else f"Popular accessory for {item.item_group or 'this device'}"
		)

		suggestions.append({
			"item_code": acc.item_code,
			"item_name": acc.item_name,
			"type": "Accessory",
			"reason": reason,
			"price": price,
			"priority": 2 if brand_match else 3,
			"source": "Smart",
			"_sold_count": 0,
		})

	return suggestions[:2]  # Max 2 accessories


def _match_upgrades(item, device_price, cart_codes):
	"""Suggest upgrades: same item_group, 10-30% more expensive."""
	if not item.item_group or device_price <= 0:
		return []

	min_price = device_price * 1.10
	max_price = device_price * 1.35

	upgrades = frappe.db.sql("""
		SELECT i.name as item_code, i.item_name, i.brand,
			COALESCE(cp.selling_price, ip.price_list_rate, 0) as price
		FROM tabItem i
		LEFT JOIN `tabCH Item Price` cp
			ON cp.item_code = i.name AND cp.channel = 'POS' AND cp.status = 'Active'
		LEFT JOIN `tabItem Price` ip
			ON ip.item_code = i.name AND ip.selling = 1
		WHERE i.item_group = %(group)s AND i.disabled = 0 AND i.is_sales_item = 1
			AND i.name != %(item)s
			AND COALESCE(cp.selling_price, ip.price_list_rate, 0) BETWEEN %(min)s AND %(max)s
		ORDER BY COALESCE(cp.selling_price, ip.price_list_rate, 0) ASC
		LIMIT 2
	""", {"group": item.item_group, "item": item.name, "min": min_price, "max": max_price}, as_dict=1)

	suggestions = []
	for u in upgrades:
		if u.item_code in cart_codes:
			continue
		extra = flt(u.price) - device_price
		suggestions.append({
			"item_code": u.item_code,
			"item_name": u.item_name,
			"type": "Upgrade",
			"reason": f"For just ₹{extra:,.0f} more, get the {u.item_name}",
			"price": flt(u.price),
			"priority": 3,
			"source": "Smart",
			"_sold_count": 0,
		})

	return suggestions[:1]  # Max 1 upgrade suggestion


def _ai_coaching_tip(item, device_price, suggestions, settings):
	"""Optional: call AI for a one-sentence sales coaching tip (not for picking items)."""
	import requests

	plan_names = [s["item_name"] for s in suggestions if s["type"] == "Protection Plan"]
	tier_label = "premium" if device_price >= TIER_PREMIUM else ("mid-range" if device_price >= TIER_MID else "budget")

	prompt = (
		f"Customer is buying: {item.item_name} ({item.brand or ''}, ₹{device_price:,.0f}, {tier_label}).\n"
		f"We're suggesting: {', '.join(s['item_name'] for s in suggestions)}.\n"
		"Give ONE short sales coaching tip (max 15 words) for the salesperson. "
		"Focus on how to pitch the protection plans naturally."
	)

	resp = _post_ai_request(
		settings,
		{
			"model": settings.upsell_model or "gpt-4o-mini",
			"messages": [
				{"role": "system", "content": "You are a retail sales coach. Be concise."},
				{"role": "user", "content": prompt},
			],
			"max_tokens": 60,
		},
		timeout=5)
	resp.raise_for_status()
	tip = resp.json()["choices"][0]["message"]["content"].strip().strip('"')
	return tip if len(tip) < 200 else tip[:200]


def _template_sales_tip(item, device_price):
	"""Generate a template-based sales tip when AI is unavailable."""
	brand = item.brand or "this device"
	if device_price >= TIER_PREMIUM:
		return f"Premium {brand} purchase — emphasize Gold Bundle as investment protection."
	elif device_price >= TIER_MID:
		return f"Mention that extended warranty is the #1 add-on for {item.item_group or 'smartphones'}."
	else:
		return "Highlight the affordable price of our protection plans — great value!"


# -- AI offer explain helpers ------------------------------------------------


def _gather_offer_data(items):
	"""Gather all applicable offers for cart items, including global offers."""
	offer_data = []
	seen_offers = set()
	today = frappe.utils.today()

	item_by_code = {
		item.get("item_code"): item for item in items if isinstance(item, dict) and item.get("item_code")
	}
	limit = _positive_setting("pos_ai_related_row_limit", 1000, 10000)
	if item_by_code:
		offers = frappe.db.get_all(
			"CH Item Offer",
			filters={
				"item_code": ("in", list(item_by_code)),
				"channel": "POS",
				"status": "Active",
				"start_date": ["<=", today],
				"end_date": [">=", today],
			},
			fields=["name", "item_code", "offer_name", "offer_type", "value_type", "value", "notes"],
			order_by="priority asc",
			limit_page_length=limit + 1)
		_ensure_ai_rows(offers, _("Item offer rows"))
		for offer in offers:
			if offer.name not in seen_offers:
				seen_offers.add(offer.name)
				offer_data.append({"offer": offer, "item": item_by_code[offer.item_code]})

	# Global offers (item_code is null or empty)
	global_offers = frappe.db.get_all(
		"CH Item Offer",
		filters={
			"item_code": ["in", [None, ""]],
			"channel": "POS",
			"status": "Active",
			"start_date": ["<=", today],
			"end_date": [">=", today],
		},
		fields=["name", "offer_name", "offer_type", "value_type", "value", "notes"],
		order_by="priority asc",
		limit_page_length=limit + 1)
	_ensure_ai_rows(global_offers, _("Global offer rows"))
	for offer in global_offers:
		if offer.name not in seen_offers:
			seen_offers.add(offer.name)
			offer_data.append({"offer": offer, "item": {"item_name": "your cart"}})

	return offer_data


def _ai_explain_offers(offer_data, cart, settings):
	"""Call AI to explain offers in plain language."""
	import requests

	offers_summary = []
	for od in offer_data:
		offers_summary.append({
			"item": od["item"].get("item_name", od["item"].get("item_code", "")),
			"offer": od["offer"].get("offer_name", ""),
			"type": od["offer"].get("value_type", ""),
			"value": flt(od["offer"].get("value", 0)),
			"notes": od["offer"].get("notes", ""),
		})

	system_prompt = settings.offer_explain_prompt or (
		"You are a friendly retail assistant. Explain discounts simply. "
		"Use Indian Rupee. Max 3 sentences."
	)

	user_prompt = (
		f"The customer's cart has these offers applied:\n{json.dumps(offers_summary, indent=2)}\n\n"
		"Explain the savings in a friendly, clear way. "
		"Mention total approximate savings. Max 3 sentences."
	)

	resp = _post_ai_request(
		settings,
		{
			"model": settings.upsell_model or "gpt-4o-mini",
			"messages": [
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_prompt},
			],
			"max_tokens": 300,
		},
		timeout=cint(settings.timeout_sec) or 10)
	resp.raise_for_status()
	return resp.json()["choices"][0]["message"]["content"].strip()
