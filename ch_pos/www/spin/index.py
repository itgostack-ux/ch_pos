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

	# Guests skip CSRF validation, but a browser with a live Desk session
	# (store staff testing the link, kiosk machines) gets validated — the
	# reveal POST must carry that session's real token or it 400s.
	if frappe.session.user and frappe.session.user != "Guest":
		context.csrf_token = frappe.sessions.get_csrf_token()
	else:
		context.csrf_token = "guest"

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
