import math

import frappe
from frappe import _

from ch_pos.setup_test_data import run as setup_test_master_data
from ch_pos.test_pos_scenarios import get_context


def _ensure_finance_partner(partner_name="UI Test Finance", tenure_options="3,6,9,12"):
	if frappe.db.exists("CH Finance Partner", partner_name):
		partner = frappe.get_doc("CH Finance Partner", partner_name)
		updates = {}
		if not partner.enabled:
			updates["enabled"] = 1
		if not partner.tenure_options:
			updates["tenure_options"] = tenure_options
		if updates:
			partner.update(updates)
			partner.flags.ignore_permissions = True
			partner.save()
			frappe.db.commit()
		return partner

	partner = frappe.new_doc("CH Finance Partner")
	partner.partner_name = partner_name
	partner.short_code = "UITF"
	partner.enabled = 1
	partner.tenure_options = tenure_options
	partner.remarks = "Created for POS Cypress UI tests"
	partner.flags.ignore_permissions = True
	partner.insert()
	frappe.db.commit()
	return partner


def _find_or_create_finance_sale_type(partner_name):
	sale_types = frappe.get_all(
		"CH Sale Type",
		filters={"enabled": 1},
		fields=["name", "sale_type_name", "code", "requires_customer", "requires_payment"],
		order_by="modified desc",
	)

	for st in sale_types:
		name = (st.get("sale_type_name") or st.get("name") or "").strip()
		code = (st.get("code") or "").strip().upper()
		if code == "FS" or "finance" in name.lower() or "emi" in name.lower():
			doc = frappe.get_doc("CH Sale Type", name)
			if not any((row.sale_sub_type or "").strip() == partner_name for row in doc.sub_types or []):
				doc.append("sub_types", {
					"sale_sub_type": partner_name,
					"description": "UI finance partner for Cypress payment tests",
					"requires_reference": 1,
				})
				doc.flags.ignore_permissions = True
				doc.save()
				frappe.db.commit()
			return doc

	name = "UI Test Finance Sale"
	if frappe.db.exists("CH Sale Type", name):
		doc = frappe.get_doc("CH Sale Type", name)
	else:
		doc = frappe.new_doc("CH Sale Type")
		doc.sale_type_name = name
		doc.code = "UIFS"
		doc.enabled = 1
		doc.is_default = 0
		doc.requires_customer = 1
		doc.requires_payment = 1
		doc.description = "Created for POS Cypress UI tests"

	if not any((row.sale_sub_type or "").strip() == partner_name for row in doc.sub_types or []):
		doc.append("sub_types", {
			"sale_sub_type": partner_name,
			"description": "UI finance partner for Cypress payment tests",
			"requires_reference": 1,
		})

	doc.flags.ignore_permissions = True
	if doc.is_new():
		doc.insert()
	else:
		doc.save()
	frappe.db.commit()
	return doc


def _pick_test_item(ctx):
	preferred_code = "CSV000001-BLA-Lightning"
	warehouse = ctx["pos_profile"].warehouse
	rate = frappe.db.get_value(
		"Item Price",
		{"item_code": preferred_code, "selling": 1},
		"price_list_rate",
	)
	stock_qty = frappe.db.get_value("Bin", {"item_code": preferred_code, "warehouse": warehouse}, "actual_qty") or 0
	if frappe.db.exists("Item", preferred_code) and rate and stock_qty > 0:
		item_name = frappe.db.get_value("Item", preferred_code, "item_name") or preferred_code
		return {"item_code": preferred_code, "item_name": item_name, "rate": float(rate)}

	if not ctx.get("simple_item"):
		frappe.throw(_("No in-stock non-serial test item available for POS UI testing."), title=_("API Error"))

	return {
		"item_code": ctx["simple_item"].name,
		"item_name": ctx["simple_item"].item_name,
		"rate": float(ctx["simple_item"].rate or 0),
	}


@frappe.whitelist()
def prepare_pos_payment_ui_test() -> dict:
	frappe.only_for(("System Manager", "Sales Manager", "Sales User"))
	setup_test_master_data()
	ctx = get_context()

	if not ctx.get("pos_profile"):
		frappe.throw(_("No usable POS Profile found for CH POS UI testing."), title=_("API Error"))
	if not ctx.get("customer"):
		frappe.throw(_("No customer found for CH POS UI testing."), title=_("API Error"))
	if not ctx.get("session_name"):
		frappe.throw(_("No active POS session available for CH POS UI testing."), title=_("API Error"))

	partner = _ensure_finance_partner()
	finance_sale_type = _find_or_create_finance_sale_type(partner.name)
	item = _pick_test_item(ctx)
	rate = float(item["rate"] or 0)
	if rate <= 0:
		frappe.throw(_("Selected UI test item does not have a valid selling price."), title=_("API Error"))

	qty = max(1, int(math.ceil(2000 / rate)))

	return {
		"route": "/desk/ch-pos-app",
		"pos_profile": ctx["pos_profile"].name,
		"session_name": ctx["session_name"],
		"customer": ctx["customer"],
		"item_code": item["item_code"],
		"item_name": item["item_name"],
		"item_qty": qty,
		"item_rate": rate,
		"store_offer_name": "Store Flat ₹200 Off",
		"store_offer_discount": 200,
		"finance_partner": partner.name,
		"finance_tenure": "6",
		"finance_sale_type": finance_sale_type.name,
		"finance_sale_code": finance_sale_type.code,
		"approval_id": "UITEST-LOAN-001",
		"down_payment": 500,
	}