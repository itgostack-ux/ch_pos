# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

def boot_session(bootinfo):
	"""Push POS settings to client at login."""
	import frappe
	if frappe.db.exists("DocType", "CH POS Settings"):
		settings = frappe.get_cached_doc("CH POS Settings")
		bootinfo["ch_pos_settings"] = {
			"enable_guided_selling": getattr(settings, "enable_guided_selling", 0),
			"enable_walkin_tokens": getattr(settings, "enable_walkin_tokens", 0),
		}
