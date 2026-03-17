"""
CH Queue — Management Dashboard page context.
Accessible at /queue-mgmt
Requires login.
"""

no_cache = 1

def get_context(context):
    import frappe
    if frappe.session.user == "Guest":
        frappe.throw("You must be logged in to access this page.", frappe.PermissionError)
    context.no_cache = 1
    context.no_breadcrumbs = 1
    context.title = "CH Queue — Management"
    context.user = frappe.session.user
    context.user_name = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
    # Inject CSRF token so JS can make authenticated POST requests
    try:
        from frappe.sessions import get_csrf_token
        context.csrf_token = get_csrf_token()
    except Exception:
        context.csrf_token = ""
