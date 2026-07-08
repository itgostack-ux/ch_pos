frappe.ui.form.on("Sales Invoice", {
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

        // POS return: check for missing free items from the original invoice.
        // Fires once per form load in draft state — cashier sees the prompt
        // before they hit Submit / Pay.
        ch_pos_check_missing_free_items(frm);
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

    // Re-check whenever the cashier picks / changes the return_against link.
    return_against(frm) {
        ch_pos_check_missing_free_items(frm);
    },
});

/**
 * POS return reminder — free-gift parity with the original sale.
 *
 * When the cashier creates a return (`is_return=1`) against a Sales Invoice
 * that had free items (Pricing Rule "Same" / "Other Item" product discount),
 * the linked free rows are NOT auto-copied by ERPNext. This helper asks the
 * server which free items are still missing from the return cart and shows
 * a dialog with a one-click "Add all" primary_action.
 *
 * Guard rails:
 *  - Only runs for POS returns in draft state (docstatus=0).
 *  - De-dupes: sets `frm.__ch_free_item_reminder_shown` so we don't spam the
 *    cashier on every field-refresh tick.
 *  - Non-blocking: the dialog has a "Skip" secondary — customer may
 *    legitimately keep the free item, so we never force.
 */
function ch_pos_check_missing_free_items(frm) {
    if (!frm || !frm.doc) return;
    if (frm.doc.docstatus !== 0) return;
    if (!frm.doc.is_return) return;
    if (!frm.doc.return_against) return;
    // Only relevant to POS returns.
    if (!frm.doc.is_pos) return;

    // De-dupe within a single form session. `return_against` change resets it.
    const dedupe_key = `${frm.doc.name}::${frm.doc.return_against}`;
    if (frm.__ch_free_item_reminder_shown === dedupe_key) return;

    const current_items = (frm.doc.items || []).map((r) => ({ item_code: r.item_code }));
    frappe.call({
        method: "ch_pos.overrides.free_item_return_guard.get_missing_free_items",
        args: {
            return_against: frm.doc.return_against,
            current_items: JSON.stringify(current_items),
        },
        callback(r) {
            const missing = r.message || [];
            if (!missing.length) return;

            frm.__ch_free_item_reminder_shown = dedupe_key;

            const rows_html = missing
                .map(
                    (m, i) =>
                        `<tr>
                            <td>${i + 1}</td>
                            <td><b>${frappe.utils.escape_html(m.item_name || m.item_code)}</b>
                                <br><small class="text-muted">${frappe.utils.escape_html(m.item_code)}</small></td>
                            <td class="text-right">${m.qty}</td>
                            <td>${frappe.utils.escape_html(m.uom || "")}</td>
                        </tr>`
                )
                .join("");

            const d = new frappe.ui.Dialog({
                title: __("Free items on original sale"),
                fields: [
                    {
                        fieldtype: "HTML",
                        fieldname: "body",
                        options: `
                            <div class="alert alert-warning" style="margin-bottom:12px;">
                                ${__("The original sale <b>{0}</b> included the following free / gift item(s). If the customer is returning them along with the phone, click <b>Add to Return</b>. If the customer is keeping the gifts, click <b>Skip</b>.", [frappe.utils.escape_html(frm.doc.return_against)])}
                            </div>
                            <table class="table table-bordered" style="margin-bottom:0;">
                                <thead>
                                    <tr>
                                        <th style="width:40px;">#</th>
                                        <th>${__("Item")}</th>
                                        <th class="text-right" style="width:80px;">${__("Qty")}</th>
                                        <th style="width:80px;">${__("UOM")}</th>
                                    </tr>
                                </thead>
                                <tbody>${rows_html}</tbody>
                            </table>
                        `,
                    },
                ],
                primary_action_label: __("Add to Return"),
                primary_action() {
                    missing.forEach((m) => {
                        const child = frm.add_child("items", {
                            item_code: m.item_code,
                            // Returns use negative qty. Rate stays 0 (free).
                            qty: -1 * Math.abs(m.qty || 1),
                            uom: m.uom || undefined,
                            warehouse: m.warehouse || undefined,
                            rate: 0,
                            is_free_item: 1,
                        });
                    });
                    frm.refresh_field("items");
                    frappe.show_alert({
                        message: __("Added {0} free item(s) to the return", [missing.length]),
                        indicator: "green",
                    });
                    d.hide();
                },
                secondary_action_label: __("Skip"),
                secondary_action() {
                    d.hide();
                },
            });
            d.show();
        },
    });
}

