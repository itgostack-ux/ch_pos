frappe.ui.form.on("POS Invoice", {
    refresh(frm) {
        // Show linked documents in sidebar
        if (frm.doc.custom_kiosk_token) {
            frm.sidebar.add_user_action(__("View Kiosk Token"), () => {
                frappe.set_route("Form", "POS Kiosk Token", frm.doc.custom_kiosk_token);
            });
        }
        if (frm.doc.custom_guided_session) {
            frm.sidebar.add_user_action(__("View Guided Session"), () => {
                frappe.set_route("Form", "POS Guided Session", frm.doc.custom_guided_session);
            });
        }
        if (frm.doc.custom_repair_intake) {
            frm.sidebar.add_user_action(__("View Repair Intake"), () => {
                frappe.set_route("Form", "POS Repair Intake", frm.doc.custom_repair_intake);
            });
        }

        // Margin scheme indicator
        if (frm.doc.custom_is_margin_scheme) {
            frm.dashboard.add_indicator(
                __("Margin Scheme Applied"),
                "orange"
            );
        }
    },

    // Load kiosk token into cart
    custom_kiosk_token(frm) {
        if (!frm.doc.custom_kiosk_token) return;
        frappe.call({
            method: "ch_pos.api.search.load_kiosk_token",
            args: { token: frm.doc.custom_kiosk_token },
            callback(r) {
                if (!r.message) return;
                const data = r.message;
                (data.items || []).forEach((item) => {
                    let row = frm.add_child("items", {
                        item_code: item.item_code,
                        qty: item.qty,
                        rate: item.rate,
                    });
                });
                frm.refresh_field("items");
                frappe.show_alert({
                    message: __("Loaded {0} items from kiosk token", [data.items.length]),
                    indicator: "green",
                });
            },
        });
    },
});
