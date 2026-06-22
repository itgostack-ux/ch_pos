app_name = "ch_pos"
app_title = "CH POS"
app_publisher = "GoFix"
app_description = "POS solution for GoGizmo & GoFix retail stores"
app_email = "admin@gofix.in"
app_license = "MIT"

boot_session = "ch_pos.boot.boot_session"

required_apps = ["frappe", "erpnext", "ch_item_master"]

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "ch_pos",
		"logo": "/assets/ch_pos/icon.svg",
		"title": "CH POS",
		"route": "/POS",
	}
]

after_install = "ch_pos.setup.after_install"
before_migrate = "ch_pos.setup.before_migrate"
after_migrate = "ch_pos.setup.after_migrate"
before_uninstall = "ch_pos.setup.before_uninstall"

# Override Sales Invoice for margin scheme calculation
override_doctype_class = {
    "Sales Invoice": "ch_pos.overrides.pos_invoice.CustomPOSInvoice",
}

# Override whitelisted ERPNext functions with fixed versions.
# This avoids patching upstream repos (frappe/erpnext) which would be
# overwritten on bench update.
override_whitelisted_methods = {
    # Fixed: counts draft invoices + submitted-but-not-yet-stocked invoices
    "erpnext.accounts.doctype.pos_invoice.pos_invoice.get_stock_availability":
        "ch_pos.overrides.pos_reserved_qty.get_stock_availability",
}

# Client scripts
doctype_js = {
    "Sales Invoice": "custom/pos_invoice.js",
    "CH POS Password": "pos_core/doctype/ch_manager_pin/ch_manager_pin.js",
}

# App-level JS (extends POS UI)
app_include_js = [
    "/assets/ch_pos/js/pos_extensions.js",
    "/assets/ch_pos/js/ch_customer_dialog.js",
    "/assets/ch_pos/js/bin_transfer_dialog.js",
    "/assets/ch_pos/js/ch_pin_ux.js",  # TC_018 — Manager PIN field UX
]

# App-level CSS
app_include_css = [
    "/assets/ch_pos/css/pos_variables.css",
    "/assets/ch_pos/css/pos_layout.css",
    "/assets/ch_pos/css/pos_components.css",
    "/assets/ch_pos/css/stock_transfer.css",
]

# Doc events
doc_events = {
    "Sales Invoice": {
        "validate": [
            "ch_pos.overrides.pos_invoice.validate_margin_scheme",
            "ch_pos.overrides.discount_control.validate_pos_commercial_policy",
            "ch_pos.overrides.return_policy.validate_return_policy",
            "ch_pos.overrides.pos_invoice.validate_eod_lock",
            "ch_pos.api.isolation_api.validate_pos_invoice_isolation",
            "ch_pos.api.isolation_api.validate_no_post_close_transaction",
            "ch_pos.overrides.cash_receipt_limit.validate_section_269st_cash_limit",
            "ch_pos.overrides.cutoff_time_guard.validate_pos_cutoff_time",
        ],
        "on_submit": [
            "ch_pos.overrides.pos_invoice.create_customer_device_records",
            "ch_pos.overrides.pos_invoice.update_serial_lifecycle",
            "ch_pos.overrides.pos_invoice.update_kiosk_token_status",
            "ch_pos.overrides.pos_invoice.increment_coupon_usage",
        ],
        "on_cancel": [
            "ch_pos.overrides.pos_invoice.reverse_serial_lifecycle",
            "ch_pos.overrides.pos_invoice.deactivate_customer_devices",
            "ch_pos.overrides.pos_invoice.revert_kiosk_token_status",
            "ch_pos.overrides.pos_invoice.decrement_coupon_usage",
        ],
    },
}

# Scheduler
scheduler_events = {
    "hourly": [
        "ch_pos.pos_kiosk.doctype.pos_kiosk_token.pos_kiosk_token.expire_old_tokens",
        "ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session.auto_close_stale_sessions",
    ],
    "daily": [
        # Release IMEIs whose pre-booking validity has lapsed (back to sellable).
        "ch_pos.api.pos_api.release_expired_prebook_reservations",
    ],
    "cron": {
        "*/10 * * * *": [
            "ch_pos.api.token_api.recover_stale_pos_billing",
        ],
        # Close all open POS sessions at 6:00 AM every day.
        # Stores open at 10:00 AM, so cashiers are forced to start a fresh session.
        "0 6 * * *": [
            "ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session.auto_close_overnight_sessions",
        ],
        # Calculate attach rate bonuses on the 1st of each month for the previous month
        "0 2 1 * *": [
            "ch_pos.api.pos_api.calculate_attach_rate_bonus",
        ],
    },
}

# Fixtures (install custom fields and workspaces)
# NOTE: Consider migrating fixtures to programmatic setup (after_migrate hook)
# Reference: India Compliance pattern — toggle_custom_fields(), setup_roles()
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["module", "=", "POS Core"]],
    },
    {
        "dt": "Workspace",
        "filters": [["name", "in", ["CH Store Operations", "CH Finance & Compliance"]]],
    },
]
