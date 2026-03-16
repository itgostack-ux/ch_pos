"""
CH Queue — Customer Kiosk page context.
Accessible at /queue?store=<pos_profile_name>
No login required.
"""

no_cache = 1

def get_context(context):
    context.no_cache = 1
    context.no_breadcrumbs = 1
    context.title = "CH Queue — Check-In"
