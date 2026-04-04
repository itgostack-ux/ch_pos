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
	"""AI-powered upsell suggestions for an item.

	Flow: gather item context + catalog data → call AI for smart suggestions
	→ fall back to rule-based suggestions on any failure.
	Resilience: returns empty list on any failure instead of raising.
	"""
	try:
		if isinstance(cart_items, str):
			cart_items = frappe.parse_json(cart_items)

		item = frappe.get_cached_doc("Item", item_code)
		settings = _get_ai_settings()

		# Gather catalog context for AI / fallback
		catalog_context = _build_upsell_context(item, cart_items)

		if settings and settings.enable_ai:
			try:
				result = _ai_upsell(item, cart_items, catalog_context, settings)
				if result:
					return result
			except Exception:
				frappe.log_error(frappe.get_traceback(), "AI upsell failed - using rule fallback")

		# Rule-based fallback
		return _rule_based_upsell(item, catalog_context)

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


# -- AI upsell helpers -------------------------------------------------------


def _build_upsell_context(item, cart_items):
	"""Gather catalog data for upsell: accessories, warranty plans, related items."""
	context = {"accessories": [], "plans": [], "related": []}

	# Accessories matching brand or general
	acc_filters = {"item_group": "Accessories", "disabled": 0, "is_sales_item": 1}
	if item.brand:
		acc_filters["brand"] = item.brand
	accessories = frappe.db.get_all(
		"Item",
		filters=acc_filters,
		fields=["name as item_code", "item_name", "brand", "image"],
		limit=10,
	)
	for acc in accessories:
		price = flt(frappe.db.get_value(
			"CH Item Price",
			{"item_code": acc.item_code, "channel": "POS", "status": "Active"},
			"selling_price",
		))
		if not price:
			price = flt(frappe.db.get_value("Item Price",
				{"item_code": acc.item_code, "selling": 1}, "price_list_rate"))
		acc["price"] = price
		context["accessories"].append(acc)

	# Active warranty / protection plans
	plans = frappe.db.get_all(
		"CH Warranty Plan",
		filters={"status": "Active"},
		fields=["name", "plan_name", "price", "duration_months", "coverage_description", "plan_type", "brand"],
	)
	for plan in plans:
		context["plans"].append({
			"plan_code": plan.name,
			"plan_name": plan.plan_name,
			"price": flt(plan.price),
			"duration_months": plan.duration_months,
			"coverage": plan.coverage_description or "",
			"plan_type": plan.plan_type or "",
			"brand": plan.brand or "",
		})

	# Higher-spec items in same category (for upgrades)
	if item.item_group:
		related = frappe.db.sql("""
			SELECT i.name as item_code, i.item_name, i.brand,
				COALESCE(cp.selling_price, ip.price_list_rate, 0) as price
			FROM tabItem i
			LEFT JOIN `tabCH Item Price` cp ON cp.item_code = i.name AND cp.channel = 'POS' AND cp.status = 'Active'
			LEFT JOIN `tabItem Price` ip ON ip.item_code = i.name AND ip.selling = 1
			WHERE i.item_group = %(group)s AND i.disabled = 0 AND i.is_sales_item = 1
				AND i.name != %(item)s
				AND COALESCE(cp.selling_price, ip.price_list_rate, 0) > 0
			ORDER BY COALESCE(cp.selling_price, ip.price_list_rate, 0) DESC
			LIMIT 5
		""", {"group": item.item_group, "item": item.name}, as_dict=1)
		context["related"] = related

	return context


def _ai_upsell(item, cart_items, catalog_context, settings):
	"""Call AI for smart upsell suggestions."""
	import requests

	# Build item details
	item_data = {
		"item_code": item.name,
		"item_name": item.item_name,
		"brand": item.brand or "",
		"item_group": item.item_group or "",
		"description": (item.description or "")[:300],
	}
	price = flt(frappe.db.get_value(
		"CH Item Price",
		{"item_code": item.name, "channel": "POS", "status": "Active"},
		"selling_price",
	))
	if not price:
		price = flt(frappe.db.get_value("Item Price",
			{"item_code": item.name, "selling": 1}, "price_list_rate"))
	item_data["price"] = price

	# Model specs
	if hasattr(item, "ch_model") and item.ch_model:
		try:
			model_doc = frappe.get_cached_doc("CH Model", item.ch_model)
			item_data["specs"] = {sv.specification: sv.value for sv in (model_doc.spec_values or [])}
		except Exception:
			pass

	cart_summary = []
	if cart_items:
		for ci in cart_items:
			ci_code = ci.get("item_code", "") if isinstance(ci, dict) else ci
			ci_name = frappe.db.get_value("Item", ci_code, "item_name") or ci_code
			cart_summary.append(ci_name)

	system_prompt = settings.upsell_system_prompt or (
		"You are a helpful retail assistant at an electronics store. "
		"Suggest relevant accessories, protection plans, or upgrades. "
		"Be helpful and concise. Return JSON only."
	)

	user_prompt = (
		f"Customer is buying: {json.dumps(item_data)}\n"
		f"Already in cart: {json.dumps(cart_summary) if cart_summary else 'nothing else'}\n\n"
		f"Available accessories: {json.dumps(catalog_context['accessories'][:5])}\n"
		f"Available protection plans: {json.dumps(catalog_context['plans'][:5])}\n"
		f"Available upgrades: {json.dumps([{'item_code':r['item_code'],'item_name':r['item_name'],'price':r['price']} for r in catalog_context.get('related',[])])}\n\n"
		"Pick the top 3-4 most relevant suggestions. For each:\n"
		"- item_code: exact code from the lists above\n"
		"- item_name: exact name\n"
		"- type: 'Accessory', 'Protection Plan', or 'Upgrade'\n"
		"- reason: one compelling sentence why the customer needs this\n"
		"- price: the price\n"
		"- priority: 1 (must-have) to 3 (nice-to-have)\n\n"
		"Return JSON: {\"suggestions\": [...], \"sales_tip\": \"one sentence sales coaching tip\"}"
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
			"max_tokens": cint(settings.max_tokens) or 2000,
			"response_format": {"type": "json_object"},
		},
		timeout=cint(settings.timeout_sec) or 10,
	)
	resp.raise_for_status()

	content = resp.json()["choices"][0]["message"]["content"]
	parsed = json.loads(content)

	suggestions = parsed.get("suggestions", [])
	sales_tip = parsed.get("sales_tip", "")

	# Validate and enrich suggestions — only return items that actually exist
	valid = []
	for s in suggestions:
		code = s.get("item_code", "")
		stype = s.get("type", "")
		if stype == "Protection Plan":
			if frappe.db.exists("CH Warranty Plan", code):
				valid.append({
					"item_code": code,
					"item_name": s.get("item_name", ""),
					"type": "Protection Plan",
					"reason": s.get("reason", ""),
					"price": flt(s.get("price", 0)),
					"priority": cint(s.get("priority", 2)),
					"source": "AI",
				})
		elif frappe.db.exists("Item", code):
			valid.append({
				"item_code": code,
				"item_name": s.get("item_name", ""),
				"type": stype or "Accessory",
				"reason": s.get("reason", ""),
				"price": flt(s.get("price", 0)),
				"priority": cint(s.get("priority", 2)),
				"source": "AI",
			})

	if sales_tip and valid:
		valid[0]["sales_tip"] = sales_tip

	return valid if valid else None


def _rule_based_upsell(item, catalog_context):
	"""Fallback: rule-based upsell from catalog context."""
	suggestions = []

	for acc in catalog_context.get("accessories", [])[:3]:
		suggestions.append({
			"item_code": acc["item_code"],
			"item_name": acc["item_name"],
			"type": "Accessory",
			"reason": f"Popular accessory for {item.brand or item.item_group}",
			"price": flt(acc.get("price", 0)),
			"priority": 2,
			"source": "Rule",
		})

	for plan in catalog_context.get("plans", [])[:2]:
		suggestions.append({
			"item_code": plan["plan_code"],
			"item_name": plan["plan_name"],
			"type": "Protection Plan",
			"reason": plan.get("coverage") or f"{plan['duration_months']} months protection",
			"price": flt(plan.get("price", 0)),
			"priority": 1,
			"source": "Rule",
		})

	return suggestions


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
