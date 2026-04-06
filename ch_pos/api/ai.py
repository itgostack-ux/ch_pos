import json

import frappe
from frappe.utils import cint, flt, now_datetime


@frappe.whitelist()
def compare_items(item_codes, customer_preferences=None):
	"""Generate AI or static comparison for 2-3 items.

	Resilience: AI timeout/failure always falls back to static comparison.
	Never raises an exception to the caller -- degraded mode is returned instead.
	"""
	if isinstance(item_codes, str):
		item_codes = frappe.parse_json(item_codes)
	if isinstance(customer_preferences, str):
		customer_preferences = frappe.parse_json(customer_preferences)

	if not item_codes or len(item_codes) < 2:
		frappe.throw("At least 2 items are required for comparison.")
	item_codes = item_codes[:3]

	cached = _find_cached_comparison(item_codes)
	if cached:
		return cached

	settings = _get_ai_settings()

	if settings and settings.enable_ai:
		try:
			result = _ai_compare(item_codes, customer_preferences, settings)
			_cache_comparison(item_codes, customer_preferences, result, "AI", settings.comparison_model)
			return result
		except Exception:
			frappe.log_error(frappe.get_traceback(), "POS AI Comparison failed - using static fallback")

	result = _static_compare(item_codes, customer_preferences)
	_cache_comparison(item_codes, customer_preferences, result, "Static Fallback")
	return result


@frappe.whitelist()
def get_upsell_suggestions(item_code, cart_items=None):
	"""Hybrid upsell suggestions: smart rules (instant) + optional AI coaching tip.

	Flow: smart rule engine picks best plans/accessories/upgrades from catalog
	→ optionally calls AI for a one-sentence sales coaching tip
	→ returns instantly even if AI is slow/unavailable.
	Resilience: returns empty list on any failure instead of raising.
	"""
	try:
		if isinstance(cart_items, str):
			cart_items = frappe.parse_json(cart_items)

		item = frappe.get_cached_doc("Item", item_code)

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
				pass  # AI tip is optional — rule suggestions are already good

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
def explain_offers(cart):
	"""AI-powered plain-language explanation of applied offers.

	Flow: gather offer data → call AI for friendly explanation
	→ fall back to template-based explanation on failure.
	Resilience: returns a safe message on any failure.
	"""
	try:
		if isinstance(cart, str):
			cart = frappe.parse_json(cart)

		items = cart.get("items", [])
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
	try:
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
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Comparison cache write failed")


def _ai_compare(item_codes, preferences, settings):
	"""Call external AI API for comparison. Returns dict."""
	import requests

	items_data = []
	for code in item_codes:
		item = frappe.get_cached_doc("Item", code)
		specs = {}
		model_name = frappe.db.get_value("Item", code, "ch_model")
		if model_name:
			model_doc = frappe.get_cached_doc("CH Model", model_name)
			specs = {sv.specification: sv.value for sv in (model_doc.spec_values or [])}
		price = flt(frappe.db.get_value(
			"CH Item Price",
			{"item_code": code, "channel": "POS", "status": "Active"},
			"selling_price",
		))
		items_data.append({
			"item_code": code,
			"item_name": item.item_name,
			"brand": item.brand,
			"price": price,
			"specs": specs,
		})

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
		model_name = frappe.db.get_value("Item", code, "ch_model")
		if model_name:
			model_doc = frappe.get_cached_doc("CH Model", model_name)
			specs = {sv.specification: sv.value for sv in (model_doc.spec_values or [])}
		price = flt(frappe.db.get_value(
			"CH Item Price",
			{"item_code": code, "channel": "POS", "status": "Active"},
			"selling_price",
		))
		comparison_table.append({
			"item_code": code,
			"item_name": item.item_name,
			"brand": item.brand,
			"price": price,
			"specs": specs,
		})

	return {
		"comparison_result": comparison_table,
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
		"selling_price",
	))
	if not price:
		price = flt(frappe.db.get_value(
			"Item Price",
			{"item_code": item_code, "selling": 1},
			"price_list_rate",
		))
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
	plans = frappe.db.get_all(
		"CH Warranty Plan",
		filters={"status": "Active"},
		fields=["name", "plan_name", "price", "duration_months", "plan_type",
				"brand", "coverage_description", "service_item"],
	)

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
			FROM `tabCH Sold Plan` sp
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
				plan_price=f"{flt(plan.price):,.0f}",
			)

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
				"type": "Protection Plan",
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
		limit=20,
	)

	suggestions = []
	for acc in accessories:
		if acc.item_code in cart_codes:
			continue
		# Filter junk: skip items with only numeric/very short names
		if len(acc.item_name or "") < 4 or (acc.item_name or "").strip().isdigit():
			continue

		price = _get_item_pos_price(acc.item_code)

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

	api_key = settings.get_password("api_key")
	endpoint = settings.api_endpoint or "https://api.openai.com/v1/chat/completions"

	resp = requests.post(
		endpoint,
		headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
		json={
			"model": settings.upsell_model or "gpt-4o-mini",
			"messages": [
				{"role": "system", "content": "You are a retail sales coach. Be concise."},
				{"role": "user", "content": prompt},
			],
			"max_tokens": 60,
		},
		timeout=5,
	)
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

	for item in items:
		item_code = item.get("item_code")
		if not item_code:
			continue
		# Item-specific offers
		offers = frappe.db.get_all(
			"CH Item Offer",
			filters={
				"item_code": item_code,
				"channel": "POS",
				"status": "Active",
				"start_date": ["<=", today],
				"end_date": [">=", today],
			},
			fields=["name", "offer_name", "offer_type", "value_type", "value", "notes"],
			order_by="priority asc",
		)
		for offer in offers:
			if offer.name not in seen_offers:
				seen_offers.add(offer.name)
				offer_data.append({"offer": offer, "item": item})

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
	)
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

	api_key = settings.get_password("api_key")
	endpoint = settings.api_endpoint or "https://api.openai.com/v1/chat/completions"

	resp = requests.post(
		endpoint,
		headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
		json={
			"model": settings.upsell_model or "gpt-4o-mini",
			"messages": [
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_prompt},
			],
			"max_tokens": 300,
		},
		timeout=cint(settings.timeout_sec) or 10,
	)
	resp.raise_for_status()
	return resp.json()["choices"][0]["message"]["content"].strip()
