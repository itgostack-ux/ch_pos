/**
 * CH POS — Stock Transfer Workspace
 *
 * "To Warehouse" is populated from get_transfer_target_warehouses,
 * which returns every sellable warehouse in the SAME COMPANY as the
 * source, sorted by kilometre-distance (ascending) using
 * Warehouse.custom_latitude / custom_longitude.
 *
 * Fail-safe: if the API returns nothing, falls back to a plain Link
 * picker so the user is never locked out.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number, wh_label } from "../../shared/helpers.js";

export class StockTransferWorkspace {
    constructor() {
        // Cache non-empty responses per source warehouse
        this._target_cache = {};

        EventBus.on("workspace:render", (ctx) => {
            if (ctx.mode !== "stock_transfer") return;
            this.render(ctx.panel);
        });
    }

    // ════════════════════════════════════════════════════════════════════════
    // Public — render workspace shell
    // ════════════════════════════════════════════════════════════════════════

    render(panel) {
        this.panel = panel;
        panel.html(`
            <div class="ch-pos-mode-panel">
                <div class="ch-mode-header">
                    <h4>
                        <span class="mode-icon"
                              style="background:#fef3c7;color:#d97706">
                            <i class="fa fa-truck"></i>
                        </span>
                        ${__("Stock Transfers")}
                    </h4>
                    <span class="ch-mode-hint">
                        ${__("Track incoming & outgoing stock movements")}
                    </span>
                </div>

                <div class="ch-st-tabs"
                     style="display:flex;gap:8px;
                            margin-bottom:var(--pos-space-md)">
                    <button class="ch-pos-category-chip active"
                            data-tab="incoming">
                        <i class="fa fa-arrow-down"></i> ${__("Incoming")}
                    </button>
                    <button class="ch-pos-category-chip" data-tab="outgoing">
                        <i class="fa fa-arrow-up"></i> ${__("Outgoing")}
                    </button>
                    <button class="ch-pos-category-chip" data-tab="new">
                        <i class="fa fa-plus"></i> ${__("New Transfer")}
                    </button>
                </div>

                <div class="ch-st-tab-content">
                    <div class="ch-st-loading"
                         style="padding:40px;text-align:center">
                        <i class="fa fa-spinner fa-spin fa-2x"
                           style="opacity:0.3"></i>
                    </div>
                    <div class="ch-st-body"></div>
                </div>
            </div>
        `);

        this._bind(panel);
        this._load_tab(panel, "incoming");
    }

    // ════════════════════════════════════════════════════════════════════════
    // Event binding (delegated, namespaced)
    // ════════════════════════════════════════════════════════════════════════

    _bind(panel) {
        panel.off(".chStockTransfer");

        panel.on(
            "click.chStockTransfer",
            ".ch-st-tabs .ch-pos-category-chip",
            (e) => {
                const tab = $(e.currentTarget).data("tab");
                panel.find(".ch-st-tabs .ch-pos-category-chip")
                     .removeClass("active");
                $(e.currentTarget).addClass("active");
                tab === "new"
                    ? this._render_new_transfer(panel)
                    : this._load_tab(panel, tab);
            }
        );

        panel.on("click.chStockTransfer",
            ".ch-st-view-detail", function () {
                frappe.set_route(
                    "Form", "Stock Entry", $(this).data("name")
                );
            });

        panel.on("click.chStockTransfer",
            ".ch-st-id-link", (e) => {
                this._show_item_list_popup($(e.currentTarget).data("name"));
            });

        panel.on("click.chStockTransfer",
            ".ch-st-manifest-btn", function () {
                frappe.set_route(
                    "Form", "CH Transfer Manifest",
                    $(this).data("manifest")
                );
            });

        panel.on("click.chStockTransfer", ".ch-st-eway-btn", (e) => {
            const name = $(e.currentTarget).data("name");
            frappe.call({
                method: "ch_pos.api.pos_api.generate_packed_transfer_ewaybill",
                args:   { stock_entry: name },
                freeze: true,
                freeze_message: __("Generating e-Way Bill..."),
                callback: (r) => {
                    if (r.message)
                        frappe.show_alert({
                            message:   __("e-Way Bill generation queued"),
                            indicator: "green",
                        });
                },
            });
        });

        panel.on("click.chStockTransfer",
            ".ch-st-accept-btn", function () {
                panel.trigger("st:accept", [$(this).data("name")]);
            });
        panel.on("st:accept.chStockTransfer", (e, name) =>
            this._accept_transfer(panel, name)
        );

        panel.on("click.chStockTransfer", ".ch-st-handover-btn", (e) => {
            const name = $(e.currentTarget).data("name");
            frappe.confirm(
                __(
                    "Confirm handover of {0}? This will move stock to the transit warehouse.",
                    [name]
                ),
                () => frappe.call({
                    method: "ch_erp15.ch_erp15.custom.stock_entry.set_pending_qty",
                    args:   { StockEntry: name },
                    freeze: true,
                    freeze_message: __("Moving stock to transit..."),
                    callback: (r) => {
                        if (!r.message) return;
                        frappe.show_alert({
                            message: __(
                                "Handover complete — {0} is now Pending With Goods",
                                [name]
                            ),
                            indicator: "orange",
                        });
                        this._load_tab(panel, "outgoing");
                    },
                })
            );
        });

        panel.on("click.chStockTransfer", ".ch-st-cancel-btn", (e) => {
            const name = $(e.currentTarget).data("name");
            frappe.confirm(
                __(
                    "Cancel transfer {0}? This will delete the draft Stock Entry.",
                    [name]
                ),
                () => frappe.call({
                    method: "frappe.client.delete",
                    args:   { doctype: "Stock Entry", name },
                    freeze: true,
                    callback: () => {
                        frappe.show_alert({
                            message:   __("Transfer {0} cancelled", [name]),
                            indicator: "red",
                        });
                        this._load_tab(panel, "outgoing");
                    },
                })
            );
        });
    }

    // ════════════════════════════════════════════════════════════════════════
    // Tab loader (incoming / outgoing)
    // ════════════════════════════════════════════════════════════════════════

    _load_tab(panel, tab) {
        const loading = panel.find(".ch-st-loading");
        const body    = panel.find(".ch-st-body");
        loading.show();
        body.empty();

        frappe.call({
            method: "ch_pos.api.pos_api.get_stock_transfers",
            args: {
                pos_profile: PosState.pos_profile,
                direction:   tab,
            },
            callback: (r) => {
                loading.hide();
                const entries = r.message || [];
                if (!entries.length) {
                    body.html(this._empty_state_html(tab));
                    return;
                }
                body.html(`
                    <div class="ch-st-list">
                        ${entries.map(se => this._transfer_row(se, tab))
                                 .join("")}
                    </div>
                `);
            },
        });
    }

    _empty_state_html(tab) {
        const icon = tab === "incoming" ? "arrow-down" : "arrow-up";
        return `
            <div class="ch-pos-empty-state" style="padding:40px">
                <div class="empty-icon">
                    <i class="fa fa-${icon}"></i>
                </div>
                <div class="empty-title">
                    ${__("No {0} transfers", [tab])}
                </div>
                <div class="empty-subtitle">
                    ${__("No recent stock movements found")}
                </div>
            </div>`;
    }

    // ════════════════════════════════════════════════════════════════════════
    // Transfer row card
    // ════════════════════════════════════════════════════════════════════════

    _transfer_row(se, tab) {
        const esc = s => frappe.utils.escape_html(s || "");
        const cs  = se.custom_status || "";
        const ls  = se.custom_logistics_status || "";

        const STATUS_COLOR = {
            "Draft":                 "ch-pos-badge-muted",
            "Pending With Goods":    "ch-pos-badge-warning",
            "Ready For Pickup":      "ch-pos-badge-info",
            "In Transit":            "ch-pos-badge-info",
            "Ready For Receive":     "ch-pos-badge-warning",
            "Receive At Transit":    "ch-pos-badge-warning",
            "Partially Transferred": "ch-pos-badge-warning",
            "Transferred":           "ch-pos-badge-success",
            "Force Closed":          "ch-pos-badge-muted",
        };

        const status_label = cs
            || (se.docstatus === 0 ? __("Draft")
                : se.docstatus === 1 ? __("Submitted")
                : __("Cancelled"));
        const status_cls = STATUS_COLOR[cs]
            || (se.docstatus === 1
                ? "ch-pos-badge-success" : "ch-pos-badge-muted");

        const arrow_color = tab === "incoming"
            ? "var(--pos-success)" : "var(--pos-danger)";

        const from_wh = esc(se.from_warehouse || "—");
        const to_wh   = esc(se.to_warehouse   || "—");

        const primary    = esc(
            se.primary_item_name || se.primary_item_code || ""
        );
        const more       = parseInt(se.additional_item_count || 0, 10) || 0;
        const item_label = primary
            ? (more > 0 ? `${primary} +${more} ${__("more")}` : primary)
            : __("{0} items", [se.item_count || 0]);

        const can_receive = tab === "incoming" && [
            "Ready For Receive",
            "Receive At Transit",
            "Partially Transferred",
        ].includes(cs);
        const accept_btn = can_receive ? `
            <button class="btn btn-xs btn-success ch-st-accept-btn"
                    data-name="${esc(se.name)}"
                    style="border-radius:var(--pos-radius-sm)">
                <i class="fa fa-barcode"></i> ${__("Scan & Receive")}
            </button>` : "";

        const can_handover = tab === "outgoing"
            && se.docstatus === 0 && !cs;
        const handover_btn = can_handover ? `
            <button class="btn btn-xs btn-warning ch-st-handover-btn"
                    data-name="${esc(se.name)}"
                    style="border-radius:var(--pos-radius-sm)">
                <i class="fa fa-sign-out"></i> ${__("Handover")}
            </button>` : "";
        const cancel_btn = can_handover ? `
            <button class="btn btn-xs btn-danger ch-st-cancel-btn"
                    data-name="${esc(se.name)}"
                    style="border-radius:var(--pos-radius-sm)">
                <i class="fa fa-times"></i> ${__("Cancel")}
            </button>` : "";

        // Manifest / E-Way Bill buttons hidden on the Incoming tab per
        // request — kept here commented (unchanged) so they can be restored
        // by deleting the tab-guarded versions below and uncommenting these.
        // const manifest_btn = se.custom_transfer_manifest ? `
        //     <button class="btn btn-xs btn-outline-primary ch-st-manifest-btn"
        //             data-manifest="${esc(se.custom_transfer_manifest)}"
        //             style="border-radius:var(--pos-radius-sm)">
        //         <i class="fa fa-file-text-o"></i>
        //         ${__("Manifest / E-Way Bill")}
        //     </button>` : "";
        // const eway_btn = se.custom_transfer_manifest
        //     && ["Pending With Goods", "Ready For Pickup"].includes(cs)
        //     ? `<button class="btn btn-xs btn-outline-success ch-st-eway-btn"
        //                 data-name="${esc(se.name)}"
        //                 style="border-radius:var(--pos-radius-sm)">
        //            <i class="fa fa-road"></i> ${__("Generate E-Way Bill")}
        //        </button>`
        //     : "";
        const manifest_btn = (tab !== "incoming" && se.custom_transfer_manifest) ? `
            <button class="btn btn-xs btn-outline-primary ch-st-manifest-btn"
                    data-manifest="${esc(se.custom_transfer_manifest)}"
                    style="border-radius:var(--pos-radius-sm)">
                <i class="fa fa-file-text-o"></i>
                ${__("Manifest / E-Way Bill")}
            </button>` : "";
        const eway_btn = (tab !== "incoming" && se.custom_transfer_manifest
            && ["Pending With Goods", "Ready For Pickup"].includes(cs))
            ? `<button class="btn btn-xs btn-outline-success ch-st-eway-btn"
                        data-name="${esc(se.name)}"
                        style="border-radius:var(--pos-radius-sm)">
                   <i class="fa fa-road"></i> ${__("Generate E-Way Bill")}
               </button>`
            : "";

        const LC = {
            "Pending Pickup":   "#fbbf24",
            "Picked Up":        "#60a5fa",
            "In Transit":       "#3b82f6",
            "Delivered":        "#34d399",
            "Revert Requested": "#f87171",
            "Reverted":         "#9ca3af",
        };
        const logistics_badge = ls ? `
            <span style="display:inline-flex;align-items:center;gap:4px;
                         padding:2px 8px;border-radius:10px;
                         font-size:10px;font-weight:600;
                         background:${(LC[ls] || "#e5e7eb")}20;
                         color:${LC[ls] || "#6b7280"}">
                <i class="fa fa-truck" style="font-size:9px"></i> ${esc(ls)}
            </span>` : "";

        let courier_html = "";
        if (se.remarks) {
            const parts = [];
            const mc = se.remarks.match(/Courier:\s*([^|]+)/);
            if (mc) parts.push(
                `<i class="fa fa-truck"></i> ${esc(mc[1].trim())}`
            );
            const mt = se.remarks.match(/Tracking:\s*([^|]+)/);
            if (mt) parts.push(
                `<i class="fa fa-barcode"></i> ${esc(mt[1].trim())}`
            );
            if (parts.length) {
                courier_html = `
                    <div style="display:flex;gap:10px;margin-top:6px;
                                font-size:11px;color:var(--pos-text-muted)">
                        ${parts.join(" <span style='opacity:0.3'>·</span> ")}
                    </div>`;
            }
        }

        const lp = se.custom_logistics_person_name
            || se.custom_logistics_person;
        const logistics_person = lp ? `
            <div style="font-size:11px;color:var(--pos-text-muted);
                        margin-top:4px">
                <i class="fa fa-user"></i> ${esc(lp)}
            </div>` : "";

        return `
            <div class="ch-pos-section-card"
                 style="margin-bottom:var(--pos-space-sm)">
                <div class="section-body" style="padding:12px 16px">

                    <div style="display:flex;justify-content:space-between;
                                align-items:flex-start;margin-bottom:8px">
                        <div>
                            <div class="ch-st-id-link"
                                 data-name="${esc(se.name)}"
                                 title="${__("View items in this transfer")}"
                                 style="font-weight:700;
                                        font-size:var(--pos-fs-sm);
                                        cursor:pointer;
                                        color:var(--pos-primary,inherit)">
                                ${esc(se.name)}
                            </div>
                            <div style="font-size:var(--pos-fs-2xs);
                                        color:var(--pos-text-muted)">
                                ${frappe.datetime.str_to_user(se.posting_date)}
                                · ${item_label}
                            </div>
                        </div>
                        <div style="display:flex;gap:6px;
                                    align-items:center;flex-wrap:wrap;
                                    justify-content:flex-end">
                            <span class="ch-pos-badge ${status_cls}">
                                ${esc(status_label)}
                            </span>
                            ${logistics_badge}
                            ${accept_btn}
                            ${handover_btn}
                            ${cancel_btn}
                            ${manifest_btn}
                            ${eway_btn}
                            <button class="btn btn-xs btn-outline-secondary
                                           ch-st-view-detail"
                                    data-name="${esc(se.name)}"
                                    style="border-radius:var(--pos-radius-sm)">
                                <i class="fa fa-external-link"></i>
                            </button>
                        </div>
                    </div>

                    <div style="display:flex;align-items:center;gap:6px;
                                font-size:var(--pos-fs-xs);
                                color:var(--pos-text-secondary);
                                flex-wrap:wrap">
                        <span style="font-size:11px;
                                     color:var(--pos-text-muted)">
                            ${__("From")}
                        </span>
                        <span style="padding:3px 8px;
                                     background:var(--pos-surface-sunken);
                                     border-radius:var(--pos-radius-sm)">
                            ${from_wh}
                        </span>
                        <i class="fa fa-arrow-right"
                           style="color:${arrow_color};margin:0 4px"></i>
                        <span style="font-size:11px;
                                     color:var(--pos-text-muted)">
                            ${__("To")}
                        </span>
                        <span style="padding:3px 8px;
                                     background:var(--pos-surface-sunken);
                                     border-radius:var(--pos-radius-sm)">
                            ${to_wh}
                        </span>
                    </div>

                    ${courier_html}
                    ${logistics_person}
                </div>
            </div>`;
    }

    // ════════════════════════════════════════════════════════════════════════
    // Item list popup — click the transfer ID to see every item + qty
    // (the card itself only ever shows the first item name + "+N more",
    // and the server never sends those extra names/quantities to the
    // browser at all, so this fetches the full line list on demand).
    // ════════════════════════════════════════════════════════════════════════

    _show_item_list_popup(se_name) {
        frappe.call({
            method: "ch_pos.api.pos_api.get_stock_transfer_items",
            args:   { stock_entry: se_name },
            freeze: true,
            callback: (r) => {
                if (!r.message) return;
                const items = r.message.items || [];
                const esc   = s => frappe.utils.escape_html(s || "");

                const rows = items.length
                    ? items.map(it => `
                        <tr>
                            <td>
                                <div style="font-weight:600">
                                    ${esc(it.item_name || it.item_code)}
                                </div>
                                <div style="font-size:11px;
                                            color:var(--pos-text-muted)">
                                    ${esc(it.item_code)}
                                </div>
                            </td>
                            <td class="text-center" style="font-weight:600">
                                ${flt(it.qty)} ${esc(it.uom || "")}
                            </td>
                        </tr>`).join("")
                    : `<tr><td colspan="2" style="text-align:center;
                           color:var(--pos-text-muted)">
                           ${__("No items found")}</td></tr>`;

                const dialog = new frappe.ui.Dialog({
                    title: __("Items in {0}", [se_name]),
                    fields: [{
                        fieldtype: "HTML",
                        fieldname: "items_html",
                        options: `
                            <table class="table table-bordered"
                                   style="margin-bottom:0">
                                <thead>
                                    <tr>
                                        <th>${__("Item")}</th>
                                        <th class="text-center"
                                            style="width:120px">
                                            ${__("Qty")}
                                        </th>
                                    </tr>
                                </thead>
                                <tbody>${rows}</tbody>
                            </table>`,
                    }],
                });
                dialog.show();
            },
        });
    }

    // ════════════════════════════════════════════════════════════════════════
    // New Transfer form
    //
    // FIX: _init_from_wh_field returns source synchronously so we pass
    // it explicitly to _load_target_warehouses (works around Frappe
    // Link.set_value() being async on first render).
    // ════════════════════════════════════════════════════════════════════════

    _render_new_transfer(panel) {
        const body = panel.find(".ch-st-body");
        panel.find(".ch-st-loading").hide();

        body.off(".chStockTransferNew");
        panel.off(".chStockTransferNew");
        this.transfer_items            = [];
        this._transfer_scans_in_flight = new Set();
        this.from_wh_field             = null;
        this.to_wh_field               = null;

        body.html(`
            <div class="ch-pos-section-card ch-st-card">
                <div class="section-header">
                    <i class="fa fa-plus-circle"></i>
                    ${__("Create Stock Transfer")}
                </div>
                <div class="section-body ch-st-form">

                    <div class="ch-st-scope-banner"
                         style="display:flex;align-items:center;gap:10px;
                                padding:10px 14px;
                                border-radius:var(--pos-radius-sm);
                                background:var(--pos-surface-sunken);
                                margin-bottom:14px;font-size:12px;
                                color:var(--pos-text-secondary)">
                        <i class="ch-st-scope-icon
                                  fa fa-circle-o-notch fa-spin"
                           style="color:var(--pos-primary);
                                  flex-shrink:0"></i>
                        <div style="flex:1;min-width:0">
                            <div class="ch-st-scope-msg">
                                ${__("Loading warehouses…")}
                            </div>
                            <div class="ch-st-scope-sub"
                                 style="font-size:11px;
                                        color:var(--pos-text-muted);
                                        margin-top:2px;display:none"></div>
                        </div>
                    </div>

                    <div class="ch-st-grid-2">
                        <div class="ch-st-field">
                            <label class="ch-st-label">
                                ${__("From Warehouse")}
                                <span class="ch-st-req">*</span>
                            </label>
                            <div class="ch-st-from-wh ch-st-link"></div>
                        </div>
                        <div class="ch-st-field">
                            <label class="ch-st-label">
                                ${__("To Warehouse")}
                                <span class="ch-st-req">*</span>
                                <span class="ch-st-scope-tag"
                                      style="display:none;font-size:10px;
                                             font-weight:500;
                                             color:var(--pos-primary);
                                             margin-left:4px">
                                    <i class="fa fa-sort-numeric-asc"></i>
                                    ${__("Nearest first")}
                                </span>
                            </label>
                            <div class="ch-st-to-wh-skeleton"
                                 style="height:36px;
                                        border-radius:var(--pos-radius-sm);
                                        background:var(--pos-surface-sunken);
                                        display:flex;align-items:center;
                                        padding:0 12px;
                                        color:var(--pos-text-muted);
                                        font-size:12px">
                                <i class="fa fa-spinner fa-spin"
                                   style="margin-right:8px"></i>
                                ${__("Sorting by distance…")}
                            </div>
                            <div class="ch-st-to-wh ch-st-link"
                                 style="display:none"></div>
                        </div>
                    </div>

                    <div class="ch-st-nearby-chips"
                         style="display:none;flex-wrap:wrap;gap:6px;
                                margin-bottom:12px"></div>

                    <div class="ch-st-wh-alert" style="display:none">
                        <i class="fa fa-exclamation-triangle"></i>
                        <span class="ch-st-wh-alert-msg">
                            ${__("Pick source and destination warehouses to begin scanning")}
                        </span>
                    </div>

                    <div class="ch-st-scan-row">
                        <i class="fa fa-barcode ch-st-scan-icon"></i>
                        <input type="text"
                               class="form-control ch-st-scan-input"
                               placeholder="${__("Scan or type IMEI / Serial and press Enter")}"
                               autocomplete="off"
                               inputmode="text"
                               spellcheck="false">
                        <div class="ch-st-scan-feedback"
                             aria-live="polite"></div>
                        <div class="ch-st-scan-hint">
                            <i class="fa fa-info-circle"></i>
                            ${__("Each scan is tracked end-to-end in IMEI Tracker")}
                        </div>
                    </div>

                    <div class="ch-st-counter-strip">
                        <div class="ch-st-counter">
                            <span class="ch-st-counter-label">${__("Lines")}</span>
                            <span class="ch-st-counter-value ch-st-c-lines">0</span>
                        </div>
                        <div class="ch-st-counter">
                            <span class="ch-st-counter-label">${__("Total Qty")}</span>
                            <span class="ch-st-counter-value ch-st-c-qty">0</span>
                        </div>
                        <div class="ch-st-counter">
                            <span class="ch-st-counter-label">${__("Scanned Serials")}</span>
                            <span class="ch-st-counter-value ch-st-c-serials">0</span>
                        </div>
                    </div>

                    <div class="ch-st-items-table"></div>

                    <div class="ch-st-courier-section"
                         style="display:none;margin-top:16px;padding-top:14px;
                                border-top:1px solid var(--pos-border-light)">
                        <div style="font-weight:700;font-size:var(--pos-fs-sm);
                                    margin-bottom:10px;
                                    color:var(--pos-text-secondary)">
                            <i class="fa fa-truck"></i>
                            ${__("Courier Hand-over")}
                        </div>
                        <div style="display:grid;grid-template-columns:1fr 1fr;
                                    gap:10px;margin-bottom:10px">
                            <div>
                                <label style="font-size:var(--pos-fs-2xs);
                                              font-weight:600;
                                              color:var(--pos-text-muted)">
                                    ${__("Courier / Agent Name")}
                                </label>
                                <input type="text"
                                       class="form-control ch-st-courier-name"
                                       placeholder="${__("e.g. BlueDart, Delhivery")}"
                                       style="border-radius:var(--pos-radius-sm);
                                              height:36px">
                            </div>
                            <div>
                                <label style="font-size:var(--pos-fs-2xs);
                                              font-weight:600;
                                              color:var(--pos-text-muted)">
                                    ${__("Tracking / AWB No")}
                                </label>
                                <input type="text"
                                       class="form-control ch-st-courier-tracking"
                                       placeholder="${__("Tracking number")}"
                                       style="border-radius:var(--pos-radius-sm);
                                              height:36px">
                            </div>
                        </div>
                        <div style="display:grid;grid-template-columns:1fr 1fr;
                                    gap:10px">
                            <div>
                                <label style="font-size:var(--pos-fs-2xs);
                                              font-weight:600;
                                              color:var(--pos-text-muted)">
                                    ${__("Expected Delivery Date")}
                                </label>
                                <input type="date"
                                       class="form-control ch-st-delivery-date"
                                       style="border-radius:var(--pos-radius-sm);
                                              height:36px">
                            </div>
                            <div>
                                <label style="font-size:var(--pos-fs-2xs);
                                              font-weight:600;
                                              color:var(--pos-text-muted)">
                                    ${__("Handover Notes")}
                                </label>
                                <input type="text"
                                       class="form-control ch-st-handover-notes"
                                       placeholder="${__("Special instructions…")}"
                                       style="border-radius:var(--pos-radius-sm);
                                              height:36px">
                            </div>
                        </div>
                    </div>

                    <div class="ch-st-new-actions"
                         style="display:none;text-align:right;padding-top:12px;
                                border-top:1px solid var(--pos-border-light);
                                margin-top:12px">
                        <button class="btn btn-primary ch-st-submit-transfer"
                                style="border-radius:var(--pos-radius-sm)">
                            <i class="fa fa-paper-plane"></i>
                            ${__("Submit for Approval")}
                        </button>
                    </div>
                </div>
            </div>
        `);

        // ── Wire up controls ────────────────────────────────────────────────
        const source_wh = this._init_from_wh_field(body);
        this._load_target_warehouses(panel, body, source_wh);
        this._bind_scanner(panel, body);
        this._bind_item_actions(panel, body);

        // Reload target list if source changes (rare — usually locked)
        this.from_wh_field.$input.on("change", () => {
            const new_source = this.from_wh_field.get_value();
            this._transfer_source_wh = new_source;
            this._load_target_warehouses(panel, body, new_source);
        });

        body.on(
            "click.chStockTransferNew",
            ".ch-st-submit-transfer",
            () => this._submit_transfer(panel, body)
        );

        this._render_transfer_items(body);
        setTimeout(
            () => body.find(".ch-st-scan-input").trigger("focus"),
            150
        );
    }

    // ════════════════════════════════════════════════════════════════════════
    // From-Warehouse field (locked to POS Profile warehouse)
    //
    // Returns the source warehouse name so the caller can use it
    // immediately, without waiting for Frappe's async set_value cycle.
    // ════════════════════════════════════════════════════════════════════════

    _init_from_wh_field(body) {
        this._transfer_source_wh = (PosState.warehouse || "").trim();

        this.from_wh_field = frappe.ui.form.make_control({
            df: {
                fieldname:   "from_wh",
                fieldtype:   "Link",
                options:     "Warehouse",
                placeholder: __("Source warehouse"),
                get_query: () => ({
                    filters: [
                        ["Warehouse", "disabled", "=", 0],
                        ["Warehouse", "is_group", "=", 0],
                        ["Warehouse", "name",     "=",
                            this._transfer_source_wh || "__none__"],
                    ],
                }),
            },
            parent:       body.find(".ch-st-from-wh"),
            render_input: true,
        });

        // Set both the control AND raw input so get_value works synchronously
        this.from_wh_field.set_value(this._transfer_source_wh);
        this.from_wh_field.$input.val(this._transfer_source_wh);
        this.from_wh_field.$input.prop("disabled", true);

        // Return the locked source — caller uses this directly
        return this._transfer_source_wh;
    }

    // ════════════════════════════════════════════════════════════════════════
    // Fetch target warehouses from server (COMPANY-SCOPED)
    //
    // Passes `company` explicitly so the picker never leaks warehouses
    // across companies, even when a Warehouse row has no company set.
    // ════════════════════════════════════════════════════════════════════════

    _fetch_target_warehouses(from_warehouse, search = "") {
        return new Promise((resolve) => {
            if (!from_warehouse) {
                resolve([]);
                return;
            }
            frappe.call({
                method: "ch_pos.api.pos_api.get_transfer_target_warehouses",
                args: {
                    from_warehouse,
                    search,
                    // Explicit company scope — never leak across companies
                    company: PosState.company || undefined,
                },
                callback: (r) => resolve(r.message || []),
                error:    ()  => resolve([]),
            });
        });
    }

    // ════════════════════════════════════════════════════════════════════════
    // Load & render distance-sorted To-WH picker
    //
    // The source warehouse is passed in EXPLICITLY (not read from field)
    // because Frappe's Link.set_value() is asynchronous.
    // ════════════════════════════════════════════════════════════════════════

    async _load_target_warehouses(panel, body, explicit_from_wh = null) {
        // Resolve source in priority order
        const from_wh = (explicit_from_wh
                        || this._transfer_source_wh
                        || (this.from_wh_field
                            && this.from_wh_field.get_value())
                        || "").trim();

        console.log("[StockTransfer] Loading targets for:", from_wh);

        const skeleton  = body.find(".ch-st-to-wh-skeleton");
        const wrapper   = body.find(".ch-st-to-wh");
        const chips_div = body.find(".ch-st-nearby-chips");
        const banner_i  = body.find(".ch-st-scope-icon");
        const banner_m  = body.find(".ch-st-scope-msg");
        const banner_s  = body.find(".ch-st-scope-sub");
        const tag_el    = body.find(".ch-st-scope-tag");

        const set_banner = (icon_cls, color, msg, sub = "") => {
            banner_i.attr("class", `ch-st-scope-icon fa ${icon_cls}`)
                    .css("color", color);
            banner_m.text(msg);
            if (sub) banner_s.text(sub).show();
            else     banner_s.hide();
        };

        // Guard: no source at all
        if (!from_wh) {
            skeleton.hide();
            wrapper.show();
            set_banner(
                "fa-info-circle",
                "var(--pos-text-muted)",
                __("Select a source warehouse first")
            );
            this._build_fallback_link_field(body);
            return;
        }

        set_banner(
            "fa-circle-o-notch fa-spin",
            "var(--pos-primary)",
            __("Finding warehouses near {0}…", [from_wh])
        );

        // Fetch — bypass cache if empty so retry works
        let rows;
        if (this._target_cache[from_wh]
            && this._target_cache[from_wh].length > 0) {
            rows = this._target_cache[from_wh];
            console.log("[StockTransfer] Using cached rows:", rows.length);
        } else {
            rows = await this._fetch_target_warehouses(from_wh);
            console.log("[StockTransfer] API returned rows:", rows.length);
            if (rows.length > 0) {
                this._target_cache[from_wh] = rows;
            }
        }

        skeleton.hide();
        wrapper.show().empty();

        // ── Empty result → fallback plain Link picker ────────────────────
        if (!rows || rows.length === 0) {
            set_banner(
                "fa-exclamation-triangle",
                "#d97706",
                __("No target warehouses available"),
                __("Check bench logs — API returned an empty list")
            );
            tag_el.hide();
            chips_div.hide();
            this._build_fallback_link_field(body);
            return;
        }

        // ── Update banner (with company + distance status) ───────────────
        const with_coords    = rows.filter(r => r.has_coords).length;
        const without_coords = rows.length - with_coords;

        // Extract company from response (all rows share the same company
        // because of the server-side filter). Fallback to PosState.
        const scope_company = (rows[0] && rows[0].company)
                            || PosState.company
                            || "";
        const scope_suffix  = scope_company
                            ? __(" · {0}", [scope_company])
                            : "";

        if (with_coords > 0) {
            set_banner(
                "fa-map-marker",
                "var(--pos-success)",
                __("{0} warehouses within {1}{2} — sorted by distance",
                   [rows.length, from_wh, scope_suffix]),
                without_coords > 0
                    ? __("{0} warehouse(s) have no coordinates and appear last",
                         [without_coords])
                    : ""
            );
            tag_el.show();
        } else {
            set_banner(
                "fa-exclamation-triangle",
                "#d97706",
                __("{0} warehouses in {1}{2} — GPS distance not computed",
                   [rows.length, from_wh, scope_suffix]),
                __("Run: bench execute ch_pos.patches.import_warehouse_coords.run")
            );
            tag_el.hide();
        }

        // ── Build Autocomplete field ─────────────────────────────────────
        this.to_wh_field = frappe.ui.form.make_control({
            df: {
                fieldname:   "to_wh",
                fieldtype:   "Autocomplete",
                options:     rows.map(r => r.warehouse),
                placeholder: __("Target warehouse (nearest first)"),
            },
            parent:       wrapper,
            render_input: true,
        });

        this._decorate_autocomplete(this.to_wh_field, rows);

        this.to_wh_field.$input.on("change", () => {
            body.find(".ch-st-nearby-chips .ch-st-wh-chip")
                .removeClass("active");
            this._validate_warehouses(body);
        });

        // ── Quick-pick chips: 5 nearest ──────────────────────────────────
        const chip_rows = rows.filter(r => r.has_coords).slice(0, 5);
        if (chip_rows.length) {
            chips_div.html(
                chip_rows.map(r => `
                    <button class="ch-pos-category-chip ch-st-wh-chip"
                            data-wh="${frappe.utils.escape_html(r.warehouse)}"
                            style="font-size:11px;padding:4px 10px;
                                   display:inline-flex;align-items:center;
                                   gap:5px">
                        <i class="fa fa-map-marker"
                           style="color:var(--pos-primary);font-size:10px"></i>
                        <span style="font-weight:600">
                            ${frappe.utils.escape_html(
                                r.store_name || r.warehouse_name
                            )}
                        </span>
                        <span style="opacity:0.65;font-size:10px">
                            · ${r.distance_km} km
                        </span>
                    </button>
                `).join("")
            ).css("display", "flex");

            chips_div
                .off("click.chStChip")
                .on("click.chStChip", ".ch-st-wh-chip", (e) => {
                    const wh = $(e.currentTarget).data("wh");
                    if (!this.to_wh_field) return;
                    this.to_wh_field.set_value(wh);
                    chips_div.find(".ch-st-wh-chip").removeClass("active");
                    $(e.currentTarget).addClass("active");
                    this._validate_warehouses(body);
                });
        } else {
            chips_div.hide();
        }

        this._validate_warehouses(body);
    }

    /**
     * Fallback plain Link field — company-scoped so user cannot pick
     * a warehouse from a different company. Used when the distance-
     * sorted API returns empty for any reason.
     */
    _build_fallback_link_field(body) {
        const wrapper = body.find(".ch-st-to-wh");
        wrapper.empty();

        const src_company = PosState.company || "";

        this.to_wh_field = frappe.ui.form.make_control({
            df: {
                fieldname:   "to_wh",
                fieldtype:   "Link",
                options:     "Warehouse",
                placeholder: __("Select target warehouse"),
                get_query: () => {
                    const filters = [
                        ["Warehouse", "disabled", "=", 0],
                        ["Warehouse", "is_group", "=", 0],
                        ["Warehouse", "name", "!=",
                            (this.from_wh_field
                             && this.from_wh_field.get_value()) || ""],
                    ];
                    if (src_company) {
                        filters.push(
                            ["Warehouse", "company", "=", src_company]
                        );
                    }
                    return { filters, page_length: 50 };
                },
            },
            parent:       wrapper,
            render_input: true,
        });

        this.to_wh_field.$input.on("change", () => {
            this._validate_warehouses(body);
        });
    }

    /**
     * Enhance Autocomplete dropdown items with rich distance labels.
     */
    _decorate_autocomplete(field, rows) {
        if (!field || !field.awesomplete) return;

        const lookup = {};
        rows.forEach(r => { lookup[r.warehouse] = r; });

        field.awesomplete.item = (text) => {
            const r     = lookup[text.value] || {};
            const label = r.store_name || r.warehouse_name || text.value;
            const dist  = (r.distance_km !== null
                           && r.distance_km !== undefined)
                ? `  <span style="opacity:0.6;font-size:11px">
                        · ${r.distance_km} km
                     </span>`
                : `  <span style="opacity:0.4;font-size:11px">
                        · ${__("distance unknown")}
                     </span>`;
            const sub = [r.warehouse, r.company, r.city]
                .filter(Boolean).join(" · ");

            const html = `
                <div>
                    <div style="font-weight:600">
                        ${frappe.utils.escape_html(label)}${dist}
                    </div>
                    <div style="font-size:11px;opacity:0.65">
                        ${frappe.utils.escape_html(sub)}
                    </div>
                </div>`;
            return $(`<li>${html}</li>`).get(0);
        };
    }

    // ════════════════════════════════════════════════════════════════════════
    // Warehouse pair validation
    // ════════════════════════════════════════════════════════════════════════

    _validate_warehouses(body) {
        const f = this.from_wh_field && this.from_wh_field.get_value();
        const t = this.to_wh_field   && this.to_wh_field.get_value();

        const alert_el = body.find(".ch-st-wh-alert");
        const msg_el   = body.find(".ch-st-wh-alert-msg");

        if (f && t && f !== t) {
            alert_el.hide();
        } else {
            msg_el.text(
                f && t && f === t
                    ? __("Source and destination warehouse must be different")
                    : __("Pick source and destination warehouses to begin scanning")
            );
            alert_el.show();
        }
    }

    // ════════════════════════════════════════════════════════════════════════
    // Scanner
    // ════════════════════════════════════════════════════════════════════════

    _bind_scanner(panel, body) {
        body.on(
            "keydown.chStockTransferNew",
            ".ch-st-scan-input",
            (e) => {
                if (e.key !== "Enter") return;
                e.preventDefault();

                const $inp    = $(e.currentTarget);
                const barcode = ($inp.val() || "").trim();
                if (!barcode) return;

                const from_wh = this.from_wh_field
                    && this.from_wh_field.get_value();
                const to_wh   = this.to_wh_field
                    && this.to_wh_field.get_value();

                if (!from_wh || !to_wh || from_wh === to_wh) {
                    this._scan_feedback(
                        body, "error",
                        __("Pick valid source and destination warehouses first")
                    );
                    return;
                }

                const scan_key = barcode.toLowerCase();
                const already  = (this.transfer_items || []).some(r =>
                    (r.serial_nos || []).some(
                        s => String(s).trim().toLowerCase() === scan_key
                    )
                );
                if (already
                    || this._transfer_scans_in_flight.has(scan_key)) {
                    $inp.val("");
                    this._scan_feedback(
                        body, "warn",
                        __("{0} already scanned", [barcode])
                    );
                    return;
                }

                this._transfer_scans_in_flight.add(scan_key);
                $inp.prop("disabled", true);

                frappe.call({
                    method: "ch_pos.api.pos_api.scan_for_stock_transfer",
                    args:   { barcode, from_warehouse: from_wh },
                    callback: (r) => {
                        this._transfer_scans_in_flight.delete(scan_key);
                        $inp.prop("disabled", false)
                            .val("")
                            .trigger("focus");

                        const res = r && r.message;
                        if (!res || !res.ok) {
                            this._scan_feedback(
                                body, "error",
                                (res && res.message) || __("Scan failed")
                            );
                            return;
                        }

                        const ret_sn  = String(res.serial_no || "").trim();
                        const ret_key = ret_sn.toLowerCase();
                        const exists  = (this.transfer_items || []).some(
                            item => (item.serial_nos || []).some(
                                s => String(s).trim().toLowerCase() === ret_key
                            )
                        );
                        if (!ret_sn || exists) {
                            this._scan_feedback(
                                body, "warn",
                                __("{0} already scanned",
                                   [ret_sn || barcode])
                            );
                            return;
                        }

                        const line = this.transfer_items.find(
                            x => x.item_code === res.item_code
                        );
                        if (line) {
                            line.serial_nos.push(res.serial_no);
                            line.qty = line.serial_nos.length;
                        } else {
                            this.transfer_items.push({
                                item_code:  res.item_code,
                                item_name:  res.item_name,
                                uom:        res.uom || "Nos",
                                qty:        1,
                                serial_nos: [res.serial_no],
                            });
                        }
                        this._scan_feedback(
                            body, "ok",
                            __("{0} added", [res.serial_no])
                        );
                        this._render_transfer_items(body);
                    },
                    error: () => {
                        this._transfer_scans_in_flight.delete(scan_key);
                        $inp.prop("disabled", false).trigger("focus");
                        this._scan_feedback(
                            body, "error",
                            __("Scan failed — try again")
                        );
                    },
                });
            }
        );
    }

    // ════════════════════════════════════════════════════════════════════════
    // Item action bindings
    // ════════════════════════════════════════════════════════════════════════

    _bind_item_actions(panel, body) {
        body.on(
            "click.chStockTransferNew",
            ".ch-st-remove-row",
            function () {
                panel.trigger(
                    "st:removeline",
                    [parseInt($(this).data("idx"), 10)]
                );
            }
        );
        panel.on("st:removeline.chStockTransferNew", (e, idx) => {
            this.transfer_items.splice(idx, 1);
            this._render_transfer_items(body);
        });

        body.on(
            "click.chStockTransferNew",
            ".ch-st-remove-serial",
            (e) => {
                const $btn   = $(e.currentTarget);
                const idx    = parseInt($btn.data("idx"), 10);
                const serial = String($btn.data("serial"));
                const line   = this.transfer_items[idx];
                if (!line) return;
                line.serial_nos = (line.serial_nos || [])
                    .filter(s => s !== serial);
                line.qty = line.serial_nos.length;
                if (line.qty === 0) this.transfer_items.splice(idx, 1);
                this._render_transfer_items(body);
            }
        );
    }

    // ════════════════════════════════════════════════════════════════════════
    // Scan feedback toast
    // ════════════════════════════════════════════════════════════════════════

    _scan_feedback(container, kind, message) {
        const cls  = {
            ok:    "ch-st-fb-ok",
            warn:  "ch-st-fb-warn",
            error: "ch-st-fb-err",
        };
        const icon = {
            ok:    "check-circle",
            warn:  "exclamation-circle",
            error: "times-circle",
        };

        container.find(".ch-st-scan-feedback")
            .removeClass("ch-st-fb-ok ch-st-fb-warn ch-st-fb-err")
            .addClass(cls[kind] || "ch-st-fb-err")
            .html(`
                <i class="fa fa-${icon[kind] || "info-circle"}"></i>
                ${frappe.utils.escape_html(message)}
            `)
            .stop(true, true)
            .fadeIn(80);

        clearTimeout(this._fb_t);
        this._fb_t = setTimeout(
            () => container.find(".ch-st-scan-feedback").fadeOut(200),
            kind === "ok" ? 1200 : 2600
        );
    }

    // ════════════════════════════════════════════════════════════════════════
    // Items table
    // ════════════════════════════════════════════════════════════════════════

    _render_transfer_items(container) {
        const table          = container.find(".ch-st-items-table");
        const actions        = container.find(".ch-st-new-actions");
        const courier_section = container.find(".ch-st-courier-section");
        const esc             = s => frappe.utils.escape_html(s || "");

        const lines   = (this.transfer_items || []).length;
        const tot_qty = (this.transfer_items || [])
            .reduce((a, r) => a + (parseFloat(r.qty) || 0), 0);
        const tot_sn  = (this.transfer_items || [])
            .reduce((a, r) => a + ((r.serial_nos || []).length), 0);

        container.find(".ch-st-c-lines").text(lines);
        container.find(".ch-st-c-qty").text(tot_qty);
        container.find(".ch-st-c-serials").text(tot_sn);

        if (!lines) {
            table.html(`
                <div class="ch-st-empty">
                    <i class="fa fa-inbox"></i>
                    <div class="ch-st-empty-title">
                        ${__("No items added yet")}
                    </div>
                    <div class="ch-st-empty-hint">
                        ${__("Scan an IMEI / Serial above")}
                    </div>
                </div>
            `);
            actions.hide();
            courier_section.hide();
            return;
        }

        actions.show();
        courier_section.show();

        const row_html = (r, idx) => {
            const has_sn   = (r.serial_nos || []).length > 0;
            const qty_cell = has_sn
                ? `<div class="ch-st-qty-tracked">
                       <i class="fa fa-link"></i> ${r.qty}
                   </div>`
                : `<strong>${r.qty}</strong>`;

            const chips = (r.serial_nos || []).map(sn => `
                <span class="ch-st-chip" title="${esc(sn)}">
                    <i class="fa fa-barcode"></i>
                    <span class="ch-st-chip-text">${esc(sn)}</span>
                    <button class="ch-st-remove-serial"
                            data-idx="${idx}"
                            data-serial="${esc(sn)}"
                            title="${__("Remove this serial")}">
                        <i class="fa fa-times"></i>
                    </button>
                </span>
            `).join("");

            const chips_row = has_sn ? `
                <tr class="ch-st-chips-row">
                    <td colspan="4">
                        <div class="ch-st-chips">${chips}</div>
                    </td>
                </tr>` : "";

            return `
                <tr class="ch-st-item-row
                    ${has_sn ? " ch-st-item-row--tracked" : ""}">
                    <td>
                        <div class="ch-st-item-name">${esc(r.item_name)}</div>
                        <div class="ch-st-item-code">
                            ${esc(r.item_code)}
                            ${has_sn
                                ? `· <span class="ch-st-track-tag">
                                       ${__("IMEI-tracked")}
                                   </span>`
                                : ""}
                        </div>
                    </td>
                    <td class="text-center">${qty_cell}</td>
                    <td class="text-center ch-st-uom">${esc(r.uom)}</td>
                    <td class="text-center">
                        <button class="btn btn-link text-danger
                                       ch-st-remove-row"
                                data-idx="${idx}"
                                title="${__("Remove line")}">
                            <i class="fa fa-trash-o"></i>
                        </button>
                    </td>
                </tr>
                ${chips_row}`;
        };

        table.html(`
            <table class="ch-st-table">
                <thead><tr>
                    <th>${__("Item")}</th>
                    <th class="text-center"
                        style="width:90px">${__("Qty")}</th>
                    <th class="text-center"
                        style="width:80px">${__("UOM")}</th>
                    <th style="width:48px"></th>
                </tr></thead>
                <tbody>
                    ${this.transfer_items.map(row_html).join("")}
                </tbody>
            </table>
        `);
    }

    // ════════════════════════════════════════════════════════════════════════
    // Submit transfer
    // ════════════════════════════════════════════════════════════════════════

    _submit_transfer(panel, body) {
        const from_wh = this.from_wh_field
            && this.from_wh_field.get_value();
        const to_wh   = this.to_wh_field
            && this.to_wh_field.get_value();

        if (!from_wh || !to_wh) {
            frappe.show_alert({
                message: __(
                    "Both source and destination warehouses are required"
                ),
                indicator: "red",
            });
            return;
        }
        if (from_wh === to_wh) {
            frappe.show_alert({
                message: __(
                    "Source and destination warehouse must be different"
                ),
                indicator: "orange",
            });
            return;
        }
        if (!(this.transfer_items || []).length) return;

        const courier_name     = body.find(".ch-st-courier-name").val()     || "";
        const courier_tracking = body.find(".ch-st-courier-tracking").val() || "";
        const delivery_date    = body.find(".ch-st-delivery-date").val()    || "";
        const handover_notes   = body.find(".ch-st-handover-notes").val()   || "";

        const notes = [
            handover_notes,
            courier_name     ? `Courier: ${courier_name}`      : "",
            courier_tracking ? `Tracking: ${courier_tracking}` : "",
        ].filter(Boolean).join(" | ") || undefined;

        frappe.call({
            method: "ch_pos.api.pos_api.create_store_transfer_request",
            args: {
                from_warehouse:         from_wh,
                to_warehouse:           to_wh,
                items: this.transfer_items.map(r => ({
                    item_code: r.item_code,
                    qty:       r.qty,
                    uom:       r.uom,
                    serial_no: (r.serial_nos || []).join("\n"),
                })),
                notes,
                expected_delivery_date: delivery_date || undefined,
            },
            freeze:         true,
            freeze_message: __("Submitting transfer request for approval…"),
            callback: (r) => {
                if (!r.message) return;
                frappe.show_alert({
                    message: __(
                        "Transfer {0} submitted for approval",
                        [r.message.name]
                    ),
                    indicator: "orange",
                });
                this.transfer_items = [];
                delete this._target_cache[from_wh];

                panel.find(".ch-st-tabs .ch-pos-category-chip")
                     .removeClass("active");
                panel.find(
                    '.ch-st-tabs [data-tab="outgoing"]'
                ).addClass("active");
                this._load_tab(panel, "outgoing");
            },
        });
    }

    // ════════════════════════════════════════════════════════════════════════
    // Accept / Scan-Receive dialog
    // ════════════════════════════════════════════════════════════════════════

    _accept_transfer(panel, name) {
        frappe.call({
            method: "ch_pos.api.pos_api.get_stock_transfer_items",
            args:   { stock_entry: name },
            freeze: true,
            callback: (r) => {
                if (!r.message) return;
                this._show_scan_receive_dialog(panel, r.message);
            },
        });
    }

    _show_scan_receive_dialog(panel, data) {
        const items   = data.items || [];
        const se_name = data.name;

        const recv_state = items.map(item => ({
            item_code: item.item_code,
            item_name: item.item_name || item.item_code,
            qty:       item.qty,
            received:  flt(item.received_qty),
        }));

        const render_rows = () =>
            recv_state.map(s => {
                const done  = s.received >= s.qty;
                const style = done
                    ? "color:var(--pos-success);font-weight:700" : "";
                return `<tr>
                    <td>
                        <div style="font-weight:600">
                            ${frappe.utils.escape_html(s.item_name)}
                        </div>
                        <div style="font-size:11px;
                                    color:var(--pos-text-muted)">
                            ${frappe.utils.escape_html(s.item_code)}
                        </div>
                    </td>
                    <td class="text-center"
                        style="font-weight:600">${s.qty}</td>
                    <td class="text-center"
                        style="${style}">${s.received}</td>
                    <td class="text-center">
                        ${s.qty - s.received}
                    </td>
                </tr>`;
            }).join("");

        const all_done = () =>
            recv_state.every(s => s.received >= s.qty);

        const dialog = new frappe.ui.Dialog({
            title:  __("Scan & Receive — {0}", [se_name]),
            size:   "large",
            fields: [
                {
                    fieldtype: "HTML",
                    fieldname: "header_html",
                    options: `
                        <div style="margin-bottom:12px;
                                    color:var(--pos-text-secondary);
                                    font-size:13px">
                            <strong>${wh_label(data.from_warehouse)}</strong>
                            <i class="fa fa-arrow-right"
                               style="margin:0 8px"></i>
                            <strong>${wh_label(data.to_warehouse)}</strong>
                        </div>`,
                },
                {
                    fieldtype:   "Data",
                    fieldname:   "barcode_input",
                    label:       __("Scan IMEI / Barcode"),
                    placeholder: __(
                        "Scan or type barcode then press Enter"
                    ),
                },
                {
                    fieldtype: "HTML",
                    fieldname: "scan_status",
                    options:   "",
                },
                {
                    fieldtype: "HTML",
                    fieldname: "items_table",
                    options: `
                        <table class="table table-bordered
                                      ch-scan-recv-table"
                               style="margin:0">
                            <thead><tr>
                                <th>${__("Item")}</th>
                                <th class="text-center"
                                    style="width:70px">
                                    ${__("Sent")}
                                </th>
                                <th class="text-center"
                                    style="width:80px">
                                    ${__("Received")}
                                </th>
                                <th class="text-center"
                                    style="width:80px">
                                    ${__("Pending")}
                                </th>
                            </tr></thead>
                            <tbody>${render_rows()}</tbody>
                        </table>`,
                },
            ],
            primary_action_label: __("Confirm Received"),
            primary_action: () => {
                dialog.hide();
                frappe.call({
                    method: "ch_pos.api.pos_api.pos_confirm_receive",
                    args:   { stock_entry: se_name },
                    freeze: true,
                    freeze_message: __("Finalizing receive…"),
                    callback: (r) => {
                        if (!r.message) return;
                        frappe.show_alert({
                            message: r.message.partial
                                ? __("Transfer {0} partially received",
                                     [se_name])
                                : __("Transfer {0} received in full",
                                     [se_name]),
                            indicator: "green",
                        });
                        this._load_tab(panel, "incoming");
                    },
                });
            },
        });

        const bf = dialog.fields_dict.barcode_input;
        bf.$input.on("keydown", (e) => {
            if (e.key !== "Enter") return;
            e.preventDefault();
            const barcode = bf.get_value().trim();
            if (!barcode) return;
            bf.set_value("");

            frappe.call({
                method: "ch_pos.api.pos_api.pos_scan_receive",
                args:   { stock_entry: se_name, barcode },
                callback: (r) => {
                    if (!r.message) return;
                    (r.message.items || []).forEach(si => {
                        const local = recv_state.find(
                            s => s.item_code === si.item_code
                        );
                        if (local) local.received = si.received_qty;
                    });
                    dialog.$wrapper
                          .find(".ch-scan-recv-table tbody")
                          .html(render_rows());
                    dialog.fields_dict.scan_status.$wrapper.html(`
                        <div class="alert alert-success"
                             style="padding:6px 10px;font-size:12px;
                                    margin:4px 0">
                            <i class="fa fa-check-circle"></i>
                            ${frappe.utils.escape_html(
                                r.message.item_code
                            )} scanned
                        </div>
                    `);
                    setTimeout(() => bf.$input.focus(), 100);
                    if (all_done()) {
                        dialog.fields_dict.scan_status.$wrapper.html(`
                            <div class="alert alert-info"
                                 style="padding:8px 12px;font-size:13px;
                                        margin:4px 0">
                                <i class="fa fa-check-circle"></i>
                                <strong>${__("All items scanned!")}</strong>
                                ${__("Click Confirm Received to complete.")}
                            </div>
                        `);
                    }
                },
                error: () => {
                    dialog.fields_dict.scan_status.$wrapper.html(`
                        <div class="alert alert-danger"
                             style="padding:6px 10px;font-size:12px;
                                    margin:4px 0">
                            <i class="fa fa-exclamation-triangle"></i>
                            ${__("Barcode not found or already fully received")}
                        </div>
                    `);
                },
            });
        });

        dialog.show();
        setTimeout(() => bf.$input.focus(), 200);
    }
}