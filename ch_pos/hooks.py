app_name = "ch_pos"
app_title = "CH POS"
app_publisher = "GoFix"
app_description = "POS solution for GoGizmo & GoFix retail stores"
app_email = "admin@gofix.in"
app_license = "MIT"

required_apps = ["frappe", "erpnext"]

after_install = "ch_pos.setup.after_install"
before_uninstall = "ch_pos.setup.before_uninstall"

# Override Sales Invoice for margin scheme calculation
override_doctype_class = {
    "Sales Invoice": "ch_pos.overrides.pos_invoice.CustomPOSInvoice",
}

# Client scripts
doctype_js = {
    "Sales Invoice": "custom/pos_invoice.js",
}

# App-level JS (extends POS UI)
app_include_js = [
    "/assets/ch_pos/js/pos_extensions.js",
]

# App-level CSS
app_include_css = [
    "/assets/ch_pos/css/pos_variables.css",
    "/assets/ch_pos/css/pos_layout.css",
    "/assets/ch_pos/css/pos_components.css",
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
        ],
        "on_submit": [
            "ch_pos.overrides.pos_invoice.create_customer_device_records",
            "ch_pos.overrides.pos_invoice.update_serial_lifecycle",
            "ch_pos.overrides.pos_invoice.update_kiosk_token_status",
        ],
        "on_cancel": [
            "ch_pos.overrides.pos_invoice.reverse_serial_lifecycle",
            "ch_pos.overrides.pos_invoice.deactivate_customer_devices",
            "ch_pos.overrides.pos_invoice.revert_kiosk_token_status",
        ],
    },
}

# Scheduler
scheduler_events = {
    "hourly": [
        "ch_pos.pos_kiosk.doctype.pos_kiosk_token.pos_kiosk_token.expire_old_tokens",
        "ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session.auto_close_stale_sessions",
    ],
}

# Fixtures (install custom fields and workspaces)
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
