"""Free Gift Bundle Report -- did bills for bundle devices carry their free gift?

A bill counts here when it sells a device whose model has an active Product
Bundle (the bundle can sit on the variant or on its template, as in the POS).
For each such bill and bundle:

  * devices  = units of the bundle's devices on the bill
  * expected = devices x the bundle's gift quantity
  * given    = the bundle's gift items on the same bill at ₹0 (or flagged
               free) -- accessories and free plans alike
  * status   = Given (all expected gifts), Partial (some), Skipped (none)

Every other bill in the period is listed as Not Applicable: it sold no device
that comes with a free gift.

A gift item billed at a price is a sale, not a gift, and is not counted.
Bills are counted on their own posting date; returns are left out. Gift
contents are read from the bundles as they are today.
"""

import frappe
from frappe import _
from frappe.utils import flt

from ch_erp15.ch_erp15.report_scope import scope_where_clause
from ch_pos.api.pos_api import _stamp_billed_by


def execute(filters=None):
	filters = frappe._dict(filters or {})
	bills = _get_bills(filters)
	bills += _not_applicable(filters, {b.invoice for b in bills})
	bills.sort(key=lambda b: (b.posting_date, b.invoice), reverse=True)
	if filters.status:
		bills = [b for b in bills if b.gift_status == filters.status]
	summary = _report_summary(bills)
	chart = _chart(bills)
	if filters.view == "Invoice Detail":
		return _detail_columns(), bills, None, chart, summary
	return _summary_columns(), _summarise(bills), None, chart, summary


def _get_bills(filters):
	bundle_filters = {"disabled": 0}
	if filters.product_bundle:
		bundle_filters["name"] = filters.product_bundle
	parents = frappe.get_all("Product Bundle", filters=bundle_filters, pluck="new_item_code")
	if not parents:
		return []

	gifts = {}  # bundle -> {gift item: qty per device}
	for row in frappe.get_all(
		"Product Bundle Item",
		filters={"parent": ("in", parents), "parenttype": "Product Bundle"},
		fields=["parent", "item_code", "qty"],
	):
		gifts.setdefault(row.parent, {})[row.item_code] = flt(row.qty) or 1
	parents = [p for p in parents if gifts.get(p)]
	if not parents:
		return []

	# Devices of each bundle: the parent item itself, or its variants.
	device_bundle = {}
	for item in frappe.db.sql(
		"""SELECT name, variant_of FROM `tabItem`
		WHERE name IN %(p)s OR variant_of IN %(p)s""",
		{"p": parents},
		as_dict=True,
	):
		device_bundle[item.name] = item.name if item.name in gifts else item.variant_of

	gift_codes = {code for g in gifts.values() for code in g}
	conditions, values = _invoice_conditions(filters)
	conditions.append("sii.item_code IN %(codes)s")
	values["codes"] = list(set(device_bundle) | gift_codes)

	lines = frappe.db.sql(
		f"""
		SELECT si.name AS invoice, si.posting_date, si.customer, si.customer_name,
			si.pos_profile, COALESCE(pp.warehouse, si.set_warehouse, '') AS store,
			sii.item_code, sii.item_name, sii.qty, sii.rate, sii.is_free_item, sii.serial_no
		FROM `tabSales Invoice` si
		JOIN `tabSales Invoice Item` sii ON sii.parent = si.name AND sii.parenttype = 'Sales Invoice'
		LEFT JOIN `tabPOS Profile` pp ON pp.name = si.pos_profile
		WHERE {" AND ".join(conditions)}
		ORDER BY si.posting_date DESC, si.name DESC, sii.idx
		""",
		values,
		as_dict=True,
	)

	by_invoice = {}
	for line in lines:
		by_invoice.setdefault(line.invoice, []).append(line)

	gift_value = _gift_prices(gift_codes)
	bills = []
	for invoice, inv_lines in by_invoice.items():
		head = inv_lines[0]
		# Free gift lines on this bill, as a pool each bundle draws from.
		free_pool = {}
		for line in inv_lines:
			if line.item_code in gift_codes and (flt(line.rate) == 0 or line.is_free_item):
				free_pool[line.item_code] = free_pool.get(line.item_code, 0) + flt(line.qty)

		devices = {}
		for line in inv_lines:
			bundle = device_bundle.get(line.item_code)
			if not bundle or line.is_free_item or flt(line.rate) <= 0:
				continue
			d = devices.setdefault(bundle, {"qty": 0, "names": [], "imeis": []})
			d["qty"] += flt(line.qty)
			if line.item_name not in d["names"]:
				d["names"].append(line.item_name)
			d["imeis"] += [s.strip() for s in (line.serial_no or "").split("\n") if s.strip()]

		for bundle, d in devices.items():
			expected = given = value = 0
			given_names = []
			for code, per_device in gifts[bundle].items():
				need = d["qty"] * per_device
				take = min(need, free_pool.get(code, 0))
				free_pool[code] = free_pool.get(code, 0) - take
				expected += need
				given += take
				value += take * gift_value.get(code, 0)
				if take:
					given_names.append(frappe.db.get_value("Item", code, "item_name") or code)
			bills.append(frappe._dict(
				name=invoice,
				invoice=invoice,
				posting_date=head.posting_date,
				store=head.store,
				pos_profile=head.pos_profile,
				customer=head.customer,
				customer_name=head.customer_name,
				product_bundle=bundle,
				devices=", ".join(d["names"]),
				imei=", ".join(d["imeis"]),
				device_qty=d["qty"],
				gifts_expected=expected,
				gifts_given=given,
				gift_status="Given" if given >= expected else ("Partial" if given else "Skipped"),
				gifts=", ".join(given_names),
				gift_value=value,
			))
	_stamp_billed_by(bills)
	return bills


def _invoice_conditions(filters):
	"""Bills in the report: submitted sales (no returns) of the company and
	period, the chosen store, and the user's CH User Scope."""
	conditions = [
		"si.docstatus = 1",
		"si.is_return = 0",
		"si.company = %(company)s",
		"si.posting_date BETWEEN %(from_date)s AND %(to_date)s",
	]
	values = {"company": filters.company, "from_date": filters.from_date, "to_date": filters.to_date}
	if filters.pos_profile:
		conditions.append("si.pos_profile = %(pos_profile)s")
		values["pos_profile"] = filters.pos_profile
	scope_clause = scope_where_clause(pos_profile_field="si.pos_profile")
	if scope_clause is not None:
		conditions.append(scope_clause)
	return conditions, values


def _not_applicable(filters, with_bundle):
	"""Bills that sold no device with a free gift (no gift was due)."""
	conditions, values = _invoice_conditions(filters)
	rows = frappe.db.sql(
		f"""
		SELECT si.name AS invoice, si.posting_date, si.customer, si.customer_name,
			si.pos_profile, COALESCE(pp.warehouse, si.set_warehouse, '') AS store,
			(SELECT GROUP_CONCAT(sii.item_name ORDER BY sii.idx SEPARATOR ', ')
			 FROM `tabSales Invoice Item` sii
			 WHERE sii.parent = si.name AND sii.parenttype = 'Sales Invoice') AS item_names
		FROM `tabSales Invoice` si
		LEFT JOIN `tabPOS Profile` pp ON pp.name = si.pos_profile
		WHERE {" AND ".join(conditions)}
		""",
		values,
		as_dict=True,
	)
	out = []
	for r in rows:
		if r.invoice in with_bundle:
			continue
		out.append(frappe._dict(
			r, name=r.invoice, product_bundle="", devices=r.item_names or "", imei="",
			device_qty=0, gifts_expected=0, gifts_given=0,
			gift_status="Not Applicable", gifts="", gift_value=0))
	_stamp_billed_by(out)
	return out


def _gift_prices(codes):
	"""Today's POS selling price of each gift -- the value given away."""
	if not codes or not frappe.db.table_exists("CH Item Price"):
		return {}
	return {
		r.item_code: flt(r.selling_price)
		for r in frappe.get_all(
			"CH Item Price",
			filters={"item_code": ("in", list(codes)), "channel": "POS", "status": "Active"},
			fields=["item_code", "selling_price"],
		)
	}


def _summarise(bills):
	groups = {}
	for b in bills:
		g = groups.setdefault((b.product_bundle, b.store), frappe._dict(
			product_bundle=b.product_bundle, store=b.store, invoices=0, given=0, partial=0,
			skipped=0, not_applicable=0, device_qty=0, gifts_given=0, gift_value=0))
		g.invoices += 1
		g[{"Given": "given", "Partial": "partial", "Skipped": "skipped",
		   "Not Applicable": "not_applicable"}[b.gift_status]] += 1
		g.device_qty += b.device_qty
		g.gifts_given += b.gifts_given
		g.gift_value += b.gift_value
	# Bundles first; the "no gift due" rows of each store last.
	rows = sorted(groups.values(), key=lambda g: (not g.product_bundle, g.product_bundle, g.store))
	for g in rows:
		due = g.invoices - g.not_applicable
		g.attach_rate = round(g.given * 100.0 / due, 2) if due else None
		if not g.product_bundle:
			g.product_bundle_label = _("Not Applicable (no gift due)")
	return rows


def _report_summary(bills):
	given = sum(1 for b in bills if b.gift_status == "Given")
	partial = sum(1 for b in bills if b.gift_status == "Partial")
	skipped = sum(1 for b in bills if b.gift_status == "Skipped")
	not_applicable = sum(1 for b in bills if b.gift_status == "Not Applicable")
	total = given + partial + skipped  # bills where a gift was due
	return [
		{"label": _("Total Invoices"), "value": len(bills), "datatype": "Int", "indicator": "Blue"},
		{"label": _("Not Applicable"), "value": not_applicable, "datatype": "Int", "indicator": "Grey"},
		{"label": _("Invoices with Bundle Device"), "value": total, "datatype": "Int", "indicator": "Blue"},
		{"label": _("Free Gift Given"), "value": given, "datatype": "Int", "indicator": "Green"},
		{"label": _("Partly Given"), "value": partial, "datatype": "Int", "indicator": "Orange"},
		{"label": _("Free Gift Skipped"), "value": skipped, "datatype": "Int", "indicator": "Red"},
		{"label": _("Gift Given %"), "value": round(given * 100.0 / total, 2) if total else 0,
		 "datatype": "Percent", "indicator": "Green" if total and given == total else "Orange"},
	]


def _chart(bills):
	"""One bar per bundle (Given / Partial / Skipped), plus one bar for the
	bills where no gift was due."""
	if not bills:
		return None
	labels = sorted({b.product_bundle for b in bills if b.product_bundle})
	na_label = _("Not Applicable")
	has_na = any(b.gift_status == "Not Applicable" for b in bills)
	if has_na:
		labels.append(na_label)
	count = lambda label, status: sum(
		1 for b in bills
		if b.gift_status == status and (b.product_bundle or na_label) == label)
	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Given"), "values": [count(l, "Given") for l in labels]},
				{"name": _("Partial"), "values": [count(l, "Partial") for l in labels]},
				{"name": _("Skipped"), "values": [count(l, "Skipped") for l in labels]},
				{"name": na_label, "values": [count(l, "Not Applicable") for l in labels]},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"colors": ["#16a34a", "#f59e0b", "#dc2626", "#9ca3af"],
	}


def _summary_columns():
	return [
		{"label": _("Product Bundle"), "fieldname": "product_bundle", "fieldtype": "Link", "options": "Product Bundle", "width": 190},
		{"label": _("Store"), "fieldname": "store", "fieldtype": "Link", "options": "Warehouse", "width": 200},
		{"label": _("Invoices"), "fieldname": "invoices", "fieldtype": "Int", "width": 90},
		{"label": _("Not Applicable"), "fieldname": "not_applicable", "fieldtype": "Int", "width": 110},
		{"label": _("Gift Given"), "fieldname": "given", "fieldtype": "Int", "width": 100},
		{"label": _("Partly Given"), "fieldname": "partial", "fieldtype": "Int", "width": 105},
		{"label": _("Gift Skipped"), "fieldname": "skipped", "fieldtype": "Int", "width": 105},
		{"label": _("Gift Given %"), "fieldname": "attach_rate", "fieldtype": "Percent", "width": 110},
		{"label": _("Devices Sold"), "fieldname": "device_qty", "fieldtype": "Float", "precision": 0, "width": 105},
		{"label": _("Gifts Given (Qty)"), "fieldname": "gifts_given", "fieldtype": "Float", "precision": 0, "width": 120},
		{"label": _("Gift Value (₹)"), "fieldname": "gift_value", "fieldtype": "Currency", "width": 120},
	]


def _detail_columns():
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 160},
		{"label": _("Store"), "fieldname": "store", "fieldtype": "Link", "options": "Warehouse", "width": 180},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 150},
		{"label": _("Sales By"), "fieldname": "billed_by", "fieldtype": "Data", "width": 130},
		{"label": _("Product Bundle"), "fieldname": "product_bundle", "fieldtype": "Link", "options": "Product Bundle", "width": 160},
		{"label": _("Device / Items"), "fieldname": "devices", "fieldtype": "Data", "width": 230},
		{"label": _("IMEI / Serial"), "fieldname": "imei", "fieldtype": "Data", "width": 150},
		{"label": _("Devices"), "fieldname": "device_qty", "fieldtype": "Float", "precision": 0, "width": 75},
		{"label": _("Gifts Due"), "fieldname": "gifts_expected", "fieldtype": "Float", "precision": 0, "width": 85},
		{"label": _("Gifts Given"), "fieldname": "gifts_given", "fieldtype": "Float", "precision": 0, "width": 90},
		{"label": _("Gift Status"), "fieldname": "gift_status", "fieldtype": "Data", "width": 105},
		{"label": _("Gifts"), "fieldname": "gifts", "fieldtype": "Data", "width": 220},
		{"label": _("Gift Value (₹)"), "fieldname": "gift_value", "fieldtype": "Currency", "width": 110},
	]
