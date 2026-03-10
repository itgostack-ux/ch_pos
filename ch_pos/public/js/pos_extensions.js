/**
 * CH POS Extensions — loaded on every page via app_include_js.
 * Extends the ERPNext POS UI with kiosk token loading, guided selling,
 * AI comparison, and offer display.
 */

(function () {
    "use strict";

    // Only run on POS page
    var route = frappe.get_route && frappe.get_route();
    if (!route || (route[0] !== "point-of-sale" && route[0] !== "pos")) {
        return;
    }

    // ── Utility: call ch_pos API ──────────────────────────
    function ch_pos_call(method, args) {
        return frappe.call({
            method: `ch_pos.api.${method}`,
            args: args,
            async: true,
        });
    }

    // ── Kiosk Token Loader ────────────────────────────────
    $(document).on("click", ".ch-pos-load-token", function () {
        frappe.prompt(
            { fieldname: "token", fieldtype: "Link", options: "POS Kiosk Token", label: "Kiosk Token" },
            (values) => {
                ch_pos_call("search.load_kiosk_token", { token: values.token }).then((r) => {
                    if (r && r.message) {
                        frappe.show_alert({
                            message: __("Token loaded with {0} items", [r.message.items.length]),
                            indicator: "green",
                        });
                    }
                });
            },
            __("Load Kiosk Token"),
            __("Load")
        );
    });

    // ── Item Comparison ───────────────────────────────────
    $(document).on("click", ".ch-pos-compare", function () {
        let selected = $(this).data("items");
        if (!selected || selected.length < 2) {
            frappe.msgprint(__("Select at least 2 items to compare."));
            return;
        }
        ch_pos_call("ai.compare_items", { item_codes: selected }).then((r) => {
            if (r && r.message) {
                let html = "<table class='table table-bordered'>";
                const items = r.message.comparison_result || [];
                if (Array.isArray(items)) {
                    items.forEach((item) => {
                        html += `<tr><td><b>${item.item_name || item.item_code}</b></td>`;
                        html += `<td>₹${item.price || 0}</td>`;
                        const specs = item.specs || {};
                        html += `<td>${Object.entries(specs).map(([k, v]) => `${k}: ${v}`).join("<br>")}</td></tr>`;
                    });
                }
                html += "</table>";
                if (r.message.recommendation) {
                    html += `<p class='mt-2'><b>Recommendation:</b> ${r.message.recommendation}</p>`;
                }
                frappe.msgprint({ title: __("Product Comparison"), message: html, wide: true });
            }
        });
    });

    // ── Offer Display ─────────────────────────────────────
    $(document).on("click", ".ch-pos-show-offers", function () {
        let item_code = $(this).data("item-code");
        ch_pos_call("offers.get_applicable_offers", { item_code: item_code }).then((r) => {
            if (r && r.message && r.message.length) {
                let html = "<ul>";
                r.message.forEach((o) => {
                    html += `<li><b>${o.offer_name}</b>: ${o.value_type === "Percentage" ? o.value + "% off" : "₹" + o.value + " off"}`;
                    if (o.conditions_text && o.conditions_text !== "No conditions") {
                        html += ` <small class='text-muted'>(${o.conditions_text})</small>`;
                    }
                    html += `</li>`;
                });
                html += "</ul>";
                frappe.msgprint({ title: __("Available Offers"), message: html });
            } else {
                frappe.msgprint(__("No offers available for this item."));
            }
        });
    });
})();
