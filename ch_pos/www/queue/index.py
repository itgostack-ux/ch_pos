"""
CH Queue — Customer Kiosk page context.
Accessible at /queue?store=<pos_profile_name>
No login required.
"""

no_cache = 1

def get_context(context):
    import frappe
    context.no_cache = 1
    context.no_breadcrumbs = 1
    context.title = "CH Queue — Check-In"
    # Inject CSRF token so JS can make authenticated POST requests
    try:
        from frappe.sessions import get_csrf_token
        context.csrf_token = get_csrf_token()
    except Exception:
        context.csrf_token = "fetch"
