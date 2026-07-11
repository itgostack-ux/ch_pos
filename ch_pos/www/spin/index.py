"""
CH Spin Wheel — Customer-facing gift reveal page.

Accessible at /spin?token=<spin_token>
No login required (guest access via a random, opaque token).
"""

no_cache = 1


def get_context(context):
	import frappe

	context.no_cache = 1
	context.no_breadcrumbs = 1
	context.title = "Spin & Win — Your Gift Awaits"

	token = (frappe.form_dict.get("token") or "").strip()
	context.token = token
	context.gift = None
	context.error = None

	if not token:
		context.error = "Missing spin token."
		return

	try:
		from ch_pos.api.gift_redemption import get_gift_details

		context.gift = get_gift_details(token)
	except frappe.ValidationError as exc:
		context.error = str(exc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Spin page load failed")
		context.error = "This link is invalid or has expired."
