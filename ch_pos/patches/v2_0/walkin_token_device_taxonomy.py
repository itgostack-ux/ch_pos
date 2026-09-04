"""Walk-in token device fields become item-master links.

``POS Kiosk Token.device_type`` / ``device_brand`` / ``device_model`` used to
be free text (the kiosk tiles, the counter dialog and the retired GoFix
tablet masters each spelled devices their own way). They are now Links to
CH Category, Brand and CH Model, so every token joins catalogue analytics
and hands a clean taxonomy to the Service Request.

This patch normalises existing rows: values that match the master (or the
retired kiosk labels: Mobile -> Smart Phones, Smartwatch -> Watches, ...)
become links; anything else moves into ``other_device_hint`` so no
customer-typed detail is lost. The symptom child rows are re-keyed from the
old device label to the CH Category. Post-model-sync: needs the new columns.
"""

import frappe


def execute():
	if not frappe.db.has_column("POS Kiosk Token", "other_device_hint"):
		return
	from ch_pos.api.token_api import _LEGACY_DEVICE_TYPE_TO_CATEGORY, _normalise_device

	mapping = dict(_LEGACY_DEVICE_TYPE_TO_CATEGORY)
	if frappe.db.table_exists("GoFix Device Type") and frappe.db.has_column("GoFix Device Type", "ch_category"):
		for row in frappe.get_all("GoFix Device Type", fields=["name", "ch_category"]):
			if row.ch_category:
				mapping[row.name] = row.ch_category

	rows = frappe.db.sql(
		"""
		SELECT name, device_type, device_brand, device_model, other_device_hint
		FROM `tabPOS Kiosk Token`
		WHERE IFNULL(device_type, '') <> '' OR IFNULL(device_brand, '') <> '' OR IFNULL(device_model, '') <> ''
		""",
		as_dict=True,
	)
	changed = 0
	for r in rows:
		device_type = mapping.get(r.device_type, r.device_type)
		fixed = _normalise_device(device_type, r.device_brand, r.device_model, r.other_device_hint)
		fixed["device_model_name"] = (
			frappe.db.get_value("CH Model", fixed["device_model"], "model_name") if fixed["device_model"] else None
		)
		if (
			fixed["device_type"] != (r.device_type or "")
			or fixed["device_brand"] != (r.device_brand or "")
			or fixed["device_model"] != (r.device_model or "")
			or fixed["other_device_hint"] != (r.other_device_hint or "")
		):
			frappe.db.set_value("POS Kiosk Token", r.name, fixed, update_modified=False)
			changed += 1

	_rekey_symptoms(mapping)
	frappe.logger("ch_pos").info(f"walk-in token taxonomy: normalised {changed} of {len(rows)} rows")


def _rekey_symptoms(mapping: dict) -> None:
	if not frappe.db.table_exists("POS Kiosk Token Symptom"):
		return
	if frappe.db.has_column("POS Kiosk Token Symptom", "device_type"):
		from frappe.model.utils.rename_field import rename_field

		rename_field("POS Kiosk Token Symptom", "device_type", "device_category")
	if not frappe.db.has_column("POS Kiosk Token Symptom", "device_category"):
		return
	for old, new in mapping.items():
		frappe.db.sql(
			"UPDATE `tabPOS Kiosk Token Symptom` SET device_category = %s WHERE device_category = %s",
			(new, old),
		)
	# Anything that is still not a CH Category (e.g. "Other") is a generic symptom.
	frappe.db.sql(
		"""
		UPDATE `tabPOS Kiosk Token Symptom` s
		SET s.device_category = NULL
		WHERE IFNULL(s.device_category, '') <> ''
		  AND NOT EXISTS (SELECT 1 FROM `tabCH Category` c WHERE c.name = s.device_category)
		"""
	)
