import { PosState, EventBus } from "../../state.js";
import { wh_label } from "../../shared/helpers.js";

const TABS = [
    { key: "stock",    icon: "fa-cubes",          label: __("Stock Report") },
    { key: "count",    icon: "fa-check-square-o", label: __("Cycle Count") },
    { key: "history",  icon: "fa-history",         label: __("Count History") },
    { key: "variance", icon: "fa-balance-scale",   label: __("Variance Requests") },
];

// ─── Item doctype field names (CH Item Master section) ───────────────────────
const ITEM_FIELD_CATEGORY     = "ch_category";
const ITEM_FIELD_SUB_CATEGORY = "ch_sub_category";
const ITEM_FIELD_MODEL        = "ch_model";

// ─── In-Transit status tokens ─────────────────────────────────────────────────
const IN_TRANSIT_TOKENS = new Set(["in transit", "intransit", "in-transit"]);
const _norm = (s) => (s || "").toLowerCase().replace(/[\s_-]+/g, " ").trim();
const _is_in_transit = (status) => IN_TRANSIT_TOKENS.has(_norm(status));

// ─── Warehouse short-name formatter ──────────────────────────────────────────
const CATEGORY_TOKENS = /-(Sellable|Damaged|Demo|Buyback)$/i;

const _short_wh = (name) => {
    if (!name) return "";
    let s = String(name).trim();

    const dash_idx = s.lastIndexOf(" - ");
    if (dash_idx !== -1) s = s.slice(0, dash_idx).trim();

    if (s.indexOf("-") !== -1) {
        const first_dash = s.indexOf("-");
        const tail       = s.slice(first_dash + 1);
        if (CATEGORY_TOKENS.test(tail)) {
            s = tail;
        }
    }
    return s;
};

export class StockAuditWorkspace {

    constructor() {
        this._panel        = null;
        this._active_tab   = "stock";
        this._current_data = null;
        this._xlsx_loaded  = false;

        EventBus.on("workspace:render", (ctx) => {
            if (ctx.mode !== "stock_audit") return;
            this.render(ctx.panel);
        });
    }

    // ─────────────────────────── RENDER ────────────────────────────────────────

    render(panel) {
        this._panel = panel;

        const tabs_html = TABS.map((t) => `
            <div class="ch-sa-tab" data-tab="${t.key}"
                 style="padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;">
                <i class="fa ${t.icon}"></i> ${t.label}
            </div>`).join("");

        panel.html(`
            <div class="ch-pos-mode-panel">

                <div class="ch-mode-header"
                     style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
                    <div>
                        <h4>
                            <span class="mode-icon" style="background:#ecfeff;color:#0e7490">
                                <i class="fa fa-balance-scale"></i>
                            </span>
                            ${__("Stock Audit")}
                        </h4>
                        <span class="ch-mode-hint">
                            ${__("On-hand visibility, cycle counts, and variance approvals for this store.")}
                        </span>
                    </div>
                    <div style="display:flex;gap:8px;">
                        <button class="btn btn-default btn-sm ch-sa-refresh">
                            <i class="fa fa-refresh"></i> ${__("Refresh")}
                        </button>
                    </div>
                </div>

                <div class="ch-sa-kpi-strip"
                     style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:var(--pos-space-md);">
                </div>

                <div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);">
                    <div class="section-body" style="padding:0;">
                        <div class="ch-sa-tabs"
                             style="display:flex;border-bottom:1px solid var(--pos-border);overflow-x:auto;">
                            ${tabs_html}
                        </div>
                    </div>
                </div>

                <div class="ch-sa-tab-body"></div>
            </div>
        `);

        this._bind(panel);
        this._load_xlsx_library();
        this._refresh_kpis();
        this._switch_tab(this._active_tab);
    }


    _load_xlsx_library() {
        if (typeof XLSX !== "undefined") { this._xlsx_loaded = true; return; }
        const url = "https://cdn.jsdelivr.net/npm/xlsx-js-style@1.2.0/dist/xlsx.bundle.js";
        frappe.require(url, () => {
            this._xlsx_loaded = (typeof XLSX !== "undefined");
        });
    }


    _bind(panel) {
        panel.on("click", ".ch-sa-tab", (e) => {
            const tab = $(e.currentTarget).data("tab");
            if (tab) this._switch_tab(tab);
        });

        panel.on("click", ".ch-sa-refresh", () => {
            this._refresh_kpis();
            this._switch_tab(this._active_tab);
        });

        panel.on("click", ".ch-sa-download-excel", () => {
            this._download_excel_report();
        });

        panel.on("click", ".ch-sa-open-doc", (e) => {
            const dt = $(e.currentTarget).data("dt");
            const dn = $(e.currentTarget).data("dn");
            if (dt && dn) frappe.set_route("Form", dt, dn);
        });

        panel.on("click", ".ch-sa-print-doc", (e) => {
            const dt = $(e.currentTarget).data("dt");
            const dn = $(e.currentTarget).data("dn");
            if (!dt || !dn) return;
            const url = `/printview?doctype=${encodeURIComponent(dt)}&name=${encodeURIComponent(dn)}&format=Standard&no_letterhead=0`;
            window.open(url, "_blank");
        });

        panel.on("click", ".ch-sa-print-stock", (e) => {
            const payload = $(e.currentTarget).data("payload") || "";
            if (!payload) return;
            try {
                this._print_stock_snapshot(JSON.parse(payload));
            } catch (_) {
                frappe.msgprint(__("Could not prepare stock print snapshot."));
            }
        });

        panel.on("click", ".ch-sa-start-count", () => this._start_count());
        panel.on("click", ".ch-sa-open-stock",  () => this._switch_tab("stock"));
    }


    _switch_tab(tab) {
        this._active_tab = tab;
        const panel = this._panel;
        if (!panel) return;

        panel.find(".ch-sa-tab").each(function () {
            const active = $(this).data("tab") === tab;
            $(this).css({
                "border-bottom-color": active ? "var(--primary)" : "transparent",
                "color":               active ? "var(--primary)" : "",
                "font-weight":         active ? 600 : 400,
            });
        });

        const body = panel.find(".ch-sa-tab-body");
        body.html(`<div class="text-muted text-center" style="padding:24px">
            <i class="fa fa-spinner fa-spin"></i> ${__("Loading…")}
        </div>`);

        if      (tab === "stock")    this._render_stock(body);
        else if (tab === "count")    this._render_count(body);
        else if (tab === "history")  this._render_history(body);
        else if (tab === "variance") this._render_variance(body);
    }

    // ═══════════════════════════ KPI STRIP ═════════════════════════════════════

    _refresh_kpis() {
        const strip = this._panel && this._panel.find(".ch-sa-kpi-strip");
        if (!strip || !strip.length) return;
        strip.html("");
        if (!PosState.pos_profile) return;

        frappe.xcall("ch_pos.api.stock_report.get_store_stock_report", {
            pos_profile: PosState.pos_profile,
        }).then((d) => {
            const s = d.summary || {};
            const kpis = [
                { label: __("Items on Hand"), value: s.items || 0,
                  color: "#2563eb", bg: "#dbeafe", icon: "fa-cubes" },
                { label: __("Stock Value"),
                  value: format_currency(s.total_stock_value || 0),
                  color: "#0d9488", bg: "#ccfbf1", icon: "fa-inr" },
                { label: __("Due for Count"), value: s.due_for_count || 0,
                  color: s.due_for_count ? "#dc2626" : "#16a34a",
                  bg:    s.due_for_count ? "#fef2f2" : "#dcfce7",
                  icon: "fa-check-square-o" },
            ];
            strip.html(kpis.map((k) => this._kpi(k)).join(""));
        }).catch(() => {});

        frappe.xcall("ch_pos.api.stock_report.list_variance_requests", {
            pos_profile: PosState.pos_profile,
            limit: 200,
        }).then((d) => {
            const pending = (d.rows || []).filter(
                (r) => ["Pending", "Escalated", "Awaiting Approval"].includes(r.status)
            ).length;
            strip.append(this._kpi({
                label: __("Pending Variance Approvals"),
                value: pending,
                color: pending ? "#d97706" : "#16a34a",
                bg:    pending ? "#fef3c7" : "#dcfce7",
                icon: "fa-balance-scale",
            }));
        }).catch(() => {});
    }

    _kpi(k) {
        return `
            <div style="flex:1 1 200px;min-width:180px;display:flex;align-items:center;
                        gap:12px;padding:14px 16px;background:#fff;
                        border:1px solid var(--pos-border);border-radius:var(--pos-radius-sm);">
                <div style="width:44px;height:44px;border-radius:50%;display:flex;
                            align-items:center;justify-content:center;
                            background:${k.bg};color:${k.color};font-size:18px;
                            flex-shrink:0;">
                    <i class="fa ${k.icon}"></i>
                </div>
                <div style="flex:1;min-width:0">
                    <div style="font-size:18px;font-weight:700;line-height:1.1;color:${k.color};">
                        ${k.value}
                    </div>
                    <div style="font-size:12px;color:#6b7280;line-height:1.4;margin-top:2px;">
                        ${k.label}
                    </div>
                </div>
            </div>`;
    }

    // ═══════════════════════════ STOCK REPORT TAB ══════════════════════════════

    _render_stock(body) {
        if (!PosState.pos_profile) {
            body.html(this._empty(__("No POS profile selected.")));
            return;
        }

        frappe.xcall("ch_pos.api.stock_report.get_store_stock_report", {
            pos_profile: PosState.pos_profile,
        }).then((d) => {
            this._current_data = d;

            const payload = frappe.utils.escape_html(JSON.stringify({
                warehouse:  d.warehouse,
                summary:    d.summary || {},
                rows:       d.rows || [],
                printed_on: frappe.datetime.now_datetime(),
            }));

            const rows = (d.rows || []).map((r) => {
                const due_badge = r.due
                    ? `<span class="badge" style="background:#dc2626;color:#fff;
                              padding:3px 10px;border-radius:12px;font-size:11px;">
                              ${__("Due")}
                       </span>`
                    : `<span class="text-muted" style="font-size:12px">—</span>`;

                const last = r.last_verified
                    ? frappe.datetime.str_to_user(r.last_verified)
                    : `<span class="text-muted">${__("Never")}</span>`;

                const since = (r.days_since_count != null)
                    ? `${r.days_since_count}d`
                    : `<span class="text-muted">—</span>`;

                const item_cell = `
                    <div style="line-height:1.35">
                        <div style="font-weight:500;color:#111827;font-size:13px;">
                            ${frappe.utils.escape_html(r.item_name || r.item_code || "—")}
                        </div>
                        <div style="font-size:11px;color:#9ca3af;margin-top:1px">
                            ${frappe.utils.escape_html(r.item_code || "")}
                        </div>
                    </div>`;

                const cls_badge = r.cycle_count_class
                    ? `<span style="display:inline-block;min-width:22px;padding:2px 8px;
                              background:#f3f4f6;color:#374151;border-radius:10px;
                              font-size:11px;font-weight:600;text-align:center;">
                              ${frappe.utils.escape_html(r.cycle_count_class)}
                       </span>`
                    : `<span class="text-muted">—</span>`;

                return `<tr>
                    <td>${item_cell}</td>
                    <td class="text-right" style="font-weight:500">${flt(r.on_hand_qty)}</td>
                    <td class="text-right">${format_currency(r.stock_value || 0)}</td>
                    <td class="text-center">${cls_badge}</td>
                    <td>${last}</td>
                    <td class="text-center">${since}</td>
                    <td class="text-center">${due_badge}</td>
                </tr>`;
            }).join("");

            const summary    = d.summary    || {};
            const pagination = d.pagination || {};
            let wh_lbl = wh_label(d.warehouse);
            if (pagination.has_more) {
                wh_lbl += ` <span style="font-size:12px;color:#9ca3af;font-weight:400;">
                    (${__("Showing {0} of {1} items", [summary.items_on_page || 0, summary.items || 0])})
                </span>`;
            }

            body.html(`
                <div class="ch-pos-section-card">
                    <div class="section-header"
                         style="display:flex;justify-content:space-between;align-items:center;
                                padding:14px 18px;">
                        <span style="font-weight:600;text-transform:uppercase;letter-spacing:.5px;
                                     font-size:12px;color:#111827;">
                            <i class="fa fa-cubes" style="color:#0e7490"></i>
                            ${__("Store Stock — {0}", [wh_lbl])}
                        </span>
                        <div style="display:flex;gap:8px;align-items:center">
                            <button class="btn btn-sm btn-default ch-sa-print-stock"
                                    data-payload='${payload}'
                                    style="padding:5px 12px;">
                            </button>
                            <button class="btn btn-sm btn-success ch-sa-download-excel"
                                    style="padding:5px 12px;">
                                <i class="fa fa-file-excel-o"></i> ${__("Download Report")}
                            </button>
                            <button class="btn btn-sm btn-primary ch-sa-start-count"
                                    style="padding:5px 12px;">
                                <i class="fa fa-check-square-o"></i> ${__("Start Cycle Count")}
                            </button>
                        </div>
                    </div>
                    <div class="section-body" style="padding:0;max-height:560px;overflow:auto">
                        <table class="table table-hover" style="font-size:13px;margin:0">
                            <thead style="background:#f9fafb;position:sticky;top:0;z-index:2;">
                                <tr>
                                    <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Item")}</th>
                                    <th class="text-right" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("On Hand")}</th>
                                    <th class="text-right" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Value")}</th>
                                    <th class="text-center" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Class")}</th>
                                    <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Last Verified")}</th>
                                    <th class="text-center" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Since")}</th>
                                    <th class="text-center" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Due?")}</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${rows || `<tr><td colspan="7" class="text-center text-muted"
                                    style="padding:32px">
                                    <i class="fa fa-inbox fa-2x" style="color:#d1d5db;margin-bottom:8px"></i>
                                    <div>${__("No stock on hand")}</div>
                                </td></tr>`}
                            </tbody>
                        </table>
                    </div>
                </div>`);
        }).catch((e) =>
            body.html(this._empty(__("Could not load store stock: {0}", [e.message || e])))
        );
    }

    // ═══════════════════════════ CYCLE COUNT TAB ═══════════════════════════════

    _render_count(body) {
        body.html(`
            <div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);">
                <div class="section-header" style="padding:14px 18px;">
                    <span style="font-weight:600;text-transform:uppercase;letter-spacing:.5px;
                                 font-size:12px;color:#111827;">
                        <i class="fa fa-check-square-o" style="color:#2563eb"></i>
                        ${__("Start a New Cycle Count")}
                    </span>
                </div>
                <div class="section-body" style="padding:20px;">
                    <p class="text-muted" style="margin:0 0 16px;font-size:13px;line-height:1.5;">
                        ${__("Pick a class (A/B/C) and choose whether to count only items that are due, or every item in scope.")}
                    </p>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        <button class="btn btn-primary btn-sm ch-sa-start-count" style="padding:6px 14px;">
                            <i class="fa fa-play"></i> ${__("Start Count")}
                        </button>
                        <button class="btn btn-default btn-sm ch-sa-open-stock" style="padding:6px 14px;">
                            <i class="fa fa-cubes"></i> ${__("View Store Stock")}
                        </button>
                        <button class="btn btn-success btn-sm ch-sa-download-excel" style="padding:6px 14px;">
                            <i class="fa fa-file-excel-o"></i> ${__("Download Report")}
                        </button>
                    </div>
                </div>
            </div>
            <div class="ch-pos-section-card">
                <div class="section-header" style="padding:14px 18px;">
                    <span style="font-weight:600;text-transform:uppercase;letter-spacing:.5px;
                                 font-size:12px;color:#111827;">
                        <i class="fa fa-clock-o" style="color:#f59e0b"></i>
                        ${__("Open / Recent Counts")}
                    </span>
                </div>
                <div class="section-body ch-sa-recent" style="padding:0">
                    <div class="text-muted text-center" style="padding:24px">
                        <i class="fa fa-spinner fa-spin"></i>
                    </div>
                </div>
            </div>`);
        this._load_history(body.find(".ch-sa-recent"), 8);
    }

    // ═══════════════════════════ COUNT HISTORY TAB ═════════════════════════════

    _render_history(body) {
        body.html(`
            <div class="ch-pos-section-card">
                <div class="section-header" style="padding:14px 18px;">
                    <span style="font-weight:600;text-transform:uppercase;letter-spacing:.5px;
                                 font-size:12px;color:#111827;">
                        <i class="fa fa-history" style="color:#8b5cf6"></i>
                        ${__("Cycle Count History")}
                    </span>
                </div>
                <div class="section-body ch-sa-history" style="padding:0">
                    <div class="text-muted text-center" style="padding:24px">
                        <i class="fa fa-spinner fa-spin"></i>
                    </div>
                </div>
            </div>`);
        this._load_history(body.find(".ch-sa-history"), 50);
    }

    _load_history(container, limit) {
        if (!PosState.pos_profile) {
            container.html(this._empty(__("No POS profile selected.")));
            return;
        }
        frappe.xcall("ch_pos.api.stock_report.list_cycle_counts", {
            pos_profile: PosState.pos_profile,
            limit,
        }).then((d) => {
            const rows = (d.rows || []).map((r) => {
                const status_cls = {
                    "Counting":                    "default",
                    "Draft":                       "default",
                    "Completed - Verified":        "success",
                    "Variance - Pending Approval": "warning",
                    "Variance - Approved":         "success",
                    "Variance - Rejected":         "danger",
                }[r.status] || "default";

                const vexc = r.variance_exception
                    ? `<a class="ch-sa-open-doc"
                           data-dt="CH Exception Request"
                           data-dn="${frappe.utils.escape_html(r.variance_exception)}"
                           style="cursor:pointer">
                           ${frappe.utils.escape_html(r.variance_exception)}
                       </a>`
                    : `<span class="text-muted">—</span>`;

                const sr = r.stock_reconciliation
                    ? `<a class="ch-sa-open-doc"
                           data-dt="Stock Reconciliation"
                           data-dn="${frappe.utils.escape_html(r.stock_reconciliation)}"
                           style="cursor:pointer">
                           ${frappe.utils.escape_html(r.stock_reconciliation)}
                       </a>`
                    : `<span class="text-muted">—</span>`;

                return `<tr>
                    <td>
                        <a class="ch-sa-open-doc"
                           data-dt="CH Cycle Count"
                           data-dn="${frappe.utils.escape_html(r.name)}"
                           style="cursor:pointer;font-weight:500;">
                           ${frappe.utils.escape_html(r.name)}
                        </a>
                    </td>
                    <td>${r.count_date ? frappe.datetime.str_to_user(r.count_date) : `<span class="text-muted">—</span>`}</td>
                    <td>${frappe.utils.escape_html(r.counted_by || "—")}</td>
                    <td><span class="badge badge-${status_cls}">
                        ${frappe.utils.escape_html(r.status || "")}
                    </span></td>
                    <td class="text-right">${flt(r.total_variance_qty)}</td>
                    <td class="text-right">
                        ${format_currency(r.total_variance_value || 0)}
                    </td>
                    <td>${vexc}</td>
                    <td>${sr}</td>
                    <td class="text-center">
                        <button class="btn btn-xs btn-default ch-sa-print-doc"
                                data-dt="CH Cycle Count"
                                data-dn="${frappe.utils.escape_html(r.name)}"
                                title="${__("Print")}">
                            <i class="fa fa-print"></i>
                        </button>
                    </td>
                </tr>`;
            }).join("");

            container.html(`
                <div style="max-height:520px;overflow:auto">
                    <table class="table table-hover" style="font-size:13px;margin:0">
                        <thead style="background:#f9fafb;position:sticky;top:0;z-index:2;">
                            <tr>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Count")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Date")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Counted By")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Status")}</th>
                                <th class="text-right" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Var Qty")}</th>
                                <th class="text-right" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Var Value")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Variance Req.")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Stock Recon.")}</th>
                                <th class="text-center" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Print")}</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${rows || `<tr><td colspan="9" class="text-center text-muted"
                                style="padding:32px">
                                <i class="fa fa-inbox fa-2x" style="color:#d1d5db;margin-bottom:8px"></i>
                                <div>${__("No cycle counts yet for this warehouse.")}</div>
                            </td></tr>`}
                        </tbody>
                    </table>
                </div>`);
        }).catch((e) =>
            container.html(this._empty(__("Could not load cycle counts: {0}", [e.message || e])))
        );
    }

    // ═══════════════════════════ VARIANCE TAB ══════════════════════════════════

    _render_variance(body) {
        body.html(`
            <div class="ch-pos-section-card">
                <div class="section-header" style="padding:14px 18px;">
                    <span style="font-weight:600;text-transform:uppercase;letter-spacing:.5px;
                                 font-size:12px;color:#111827;">
                        <i class="fa fa-balance-scale" style="color:#0e7490"></i>
                        ${__("Stock Count Variance — Approval Log")}
                    </span>
                </div>
                <div class="section-body ch-sa-variance" style="padding:0">
                    <div class="text-muted text-center" style="padding:24px">
                        <i class="fa fa-spinner fa-spin"></i>
                    </div>
                </div>
            </div>`);

        if (!PosState.pos_profile) {
            body.find(".ch-sa-variance").html(this._empty(__("No POS profile selected.")));
            return;
        }

        frappe.xcall("ch_pos.api.stock_report.list_variance_requests", {
            pos_profile: PosState.pos_profile,
            limit: 100,
        }).then((d) => {
            const rows = (d.rows || []).map((r) => {
                const status_cls = {
                    "Pending":          "warning",
                    "Escalated":        "warning",
                    "Awaiting Approval":"warning",
                    "Approved":         "success",
                    "Auto-Approved":    "success",
                    "Rejected":         "danger",
                    "Expired":          "secondary",
                }[r.status] || "default";

                const ref = (r.reference_doctype && r.reference_name)
                    ? `<a class="ch-sa-open-doc"
                           data-dt="${frappe.utils.escape_html(r.reference_doctype)}"
                           data-dn="${frappe.utils.escape_html(r.reference_name)}"
                           style="cursor:pointer">
                           ${frappe.utils.escape_html(r.reference_name)}
                       </a>`
                    : `<span class="text-muted">—</span>`;

                return `<tr>
                    <td>
                        <a class="ch-sa-open-doc"
                           data-dt="CH Exception Request"
                           data-dn="${frappe.utils.escape_html(r.name)}"
                           style="cursor:pointer;font-weight:500;">
                           ${frappe.utils.escape_html(r.name)}
                        </a>
                    </td>
                    <td>${ref}</td>
                    <td>${frappe.utils.escape_html(r.requested_by_name || r.requested_by || "")}</td>
                    <td class="text-right">
                        ${format_currency(r.requested_value || 0)}
                    </td>
                    <td class="text-right">
                        ${r.resolution_value
                            ? format_currency(r.resolution_value)
                            : `<span class="text-muted">—</span>`}
                    </td>
                    <td><span class="badge badge-${status_cls}">
                        ${frappe.utils.escape_html(r.status || "")}
                    </span></td>
                    <td>${r.raised_at   ? frappe.datetime.prettyDate(r.raised_at)   : `<span class="text-muted">—</span>`}</td>
                    <td>${r.resolved_at ? frappe.datetime.prettyDate(r.resolved_at) : `<span class="text-muted">—</span>`}</td>
                    <td class="text-center">
                        <button class="btn btn-xs btn-default ch-sa-print-doc"
                                data-dt="CH Exception Request"
                                data-dn="${frappe.utils.escape_html(r.name)}"
                                title="${__("Print")}">
                            <i class="fa fa-print"></i>
                        </button>
                    </td>
                </tr>`;
            }).join("");

            body.find(".ch-sa-variance").html(`
                <div style="max-height:520px;overflow:auto">
                    <table class="table table-hover" style="font-size:13px;margin:0">
                        <thead style="background:#f9fafb;position:sticky;top:0;z-index:2;">
                            <tr>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Request")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Cycle Count")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Raised By")}</th>
                                <th class="text-right" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Variance ₹")}</th>
                                <th class="text-right" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Resolved ₹")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Status")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Raised")}</th>
                                <th style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Resolved")}</th>
                                <th class="text-center" style="padding:10px 14px;font-weight:600;color:#374151;font-size:12px;">${__("Print")}</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${rows || `<tr><td colspan="9" class="text-center text-muted"
                                style="padding:32px">
                                <i class="fa fa-inbox fa-2x" style="color:#d1d5db;margin-bottom:8px"></i>
                                <div>${__("No variance approvals for this warehouse.")}</div>
                            </td></tr>`}
                        </tbody>
                    </table>
                </div>`);
        }).catch((e) =>
            body.find(".ch-sa-variance").html(
                this._empty(__("Could not load variance log: {0}", [e.message || e]))
            )
        );
    }

    // ═══════════════════════════ EXCEL DOWNLOAD ════════════════════════════════

    _download_excel_report() {
        if (!PosState.pos_profile) {
            frappe.msgprint(__("No POS profile selected."));
            return;
        }

        if (typeof XLSX === "undefined") {
            frappe.dom.freeze(__("Loading Excel library…"));
            const url = "https://cdn.jsdelivr.net/npm/xlsx-js-style@1.2.0/dist/xlsx.bundle.js";
            frappe.require(url, () => {
                frappe.dom.unfreeze();
                if (typeof XLSX === "undefined") {
                    frappe.msgprint(__(
                        "Could not load Excel library. Please check your internet connection and try again."
                    ));
                    return;
                }
                this._xlsx_loaded = true;
                this._fetch_and_generate_excel();
            });
        } else {
            this._fetch_and_generate_excel();
        }
    }

    // ──────────────────────────────────────────────────────────────────────────
    // SILENT xcall — swallows Frappe's auto-msgprint on field-permission errors
    // ──────────────────────────────────────────────────────────────────────────

    _silent_xcall(method, args) {
        return new Promise((resolve) => {
            const original_msgprint = frappe.msgprint;
            frappe.msgprint = function (opts) {
                const msg = typeof opts === "string" ? opts : (opts && opts.message) || "";
                if (msg && /Field not permitted in query/i.test(msg)) {
                    return;
                }
                return original_msgprint.apply(this, arguments);
            };

            frappe.xcall(method, args)
                .then((res) => {
                    frappe.msgprint = original_msgprint;
                    resolve(res);
                })
                .catch(() => {
                    frappe.msgprint = original_msgprint;
                    resolve(null);
                })
                .finally(() => {
                    setTimeout(() => { frappe.msgprint = original_msgprint; }, 0);
                });
        });
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Resolve sibling warehouse names for a given main (Sellable) warehouse.
    // ──────────────────────────────────────────────────────────────────────────

    _resolve_sibling_warehouses(main_wh) {
        if (!main_wh) {
            return Promise.resolve({ damaged: "", demo: "", buyback: "" });
        }

        const dash_idx = main_wh.lastIndexOf(" - ");
        const prefix   = dash_idx !== -1 ? main_wh.slice(0, dash_idx) : main_wh;
        const suffix   = dash_idx !== -1 ? main_wh.slice(dash_idx)    : "";

        const sellable_regex = /-Sellable$/i;
        if (sellable_regex.test(prefix)) {
            const base = prefix.replace(sellable_regex, "");
            const candidates = {
                damaged: `${base}-Damaged${suffix}`,
                demo:    `${base}-Demo${suffix}`,
                buyback: `${base}-Buyback${suffix}`,
            };
            return this._verify_warehouses(candidates, base, suffix);
        }

        if (/^Sellable$/i.test(prefix)) {
            const candidates = {
                damaged: `Damaged${suffix}`,
                demo:    `Demo${suffix}`,
                buyback: `Buyback${suffix}`,
            };
            return this._verify_warehouses(candidates, "", suffix);
        }

        const candidates = {
            damaged: `Damaged${suffix}`,
            demo:    `Demo${suffix}`,
            buyback: `Buyback${suffix}`,
        };
        return this._verify_warehouses(candidates, "", suffix);
    }

    _verify_warehouses(candidates, base, suffix) {
        const names = [candidates.damaged, candidates.demo, candidates.buyback];

        return frappe.xcall("frappe.client.get_list", {
            doctype: "Warehouse",
            filters: { name: ["in", names] },
            fields:  ["name"],
            limit_page_length: 0,
        }).then((found) => {
            const found_set = new Set((found || []).map((f) => f.name));
            const resolved  = { ...candidates };

            const categories = [
                { key: "damaged", token: "Damaged" },
                { key: "demo",    token: "Demo"    },
                { key: "buyback", token: "Buyback" },
            ];

            const wildcard_lookups = [];

            categories.forEach((cat) => {
                if (found_set.has(candidates[cat.key])) return;

                const wildcard_filter = `%${cat.token}${suffix || ""}`;

                wildcard_lookups.push(
                    frappe.xcall("frappe.client.get_list", {
                        doctype: "Warehouse",
                        filters: { name: ["like", wildcard_filter] },
                        fields:  ["name"],
                        limit_page_length: 10,
                    }).then((rows) => {
                        if (rows && rows.length) {
                            const preferred = base
                                ? rows.find((r) => r.name.indexOf(base) !== -1)
                                : null;
                            resolved[cat.key] = (preferred || rows[0]).name;
                        } else {
                            resolved[cat.key] = "";
                        }
                    }).catch(() => { resolved[cat.key] = ""; })
                );
            });

            if (!wildcard_lookups.length) return resolved;
            return Promise.all(wildcard_lookups).then(() => resolved);
        }).catch(() => candidates);
    }


    _fetch_warehouse_stock(warehouse) {
        if (!warehouse) return Promise.resolve([]);
        return this._silent_xcall("frappe.client.get_list", {
            doctype: "Bin",
            filters: {
                warehouse:   warehouse,
                actual_qty: [">", 0],
            },
            fields: [
                "item_code", "warehouse",
                "actual_qty", "stock_value", "valuation_rate",
            ],
            limit_page_length: 0,
            order_by: "item_code",
        }).then((bins) =>
            (bins || []).map((b) => ({
                item_code:   b.item_code || "",
                item_name:   b.item_code || "",
                warehouse:   b.warehouse || warehouse,
                on_hand_qty: flt(b.actual_qty),
                stock_value: flt(b.stock_value),
            }))
        );
    }

   
    _fetch_warehouse_serials(warehouse) {
        if (!warehouse) return Promise.resolve([]);
        return this._silent_xcall("frappe.client.get_list", {
            doctype: "Serial No",
            filters: {
                warehouse: warehouse,
                status:    ["!=", "In Transit"],
            },
            fields: ["name", "item_code", "warehouse", "status"],
            limit_page_length: 0,
            order_by: "item_code, name",
        }).then((rows) => rows || []);
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Fetch company name from POS Profile
    // ──────────────────────────────────────────────────────────────────────────

    _fetch_company_name() {
        if (!PosState.pos_profile) return Promise.resolve("");
        return this._silent_xcall("frappe.client.get_value", {
            doctype:   "POS Profile",
            filters:   { name: PosState.pos_profile },
            fieldname: "company",
        }).then((res) => (res && res.company) || "").catch(() => "");
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Sheet-name sanitiser
    // ──────────────────────────────────────────────────────────────────────────

    _safe_sheet_name(name) {
        let s = String(name || "Sheet").replace(/[\\\/\?\*\[\]:]/g, "-");
        if (s.length > 31) s = s.slice(0, 31);
        return s || "Sheet";
    }

    // ──────────────────────────────────────────────────────────────────────────
    // Build ITEM CODE → item details map.
    // ──────────────────────────────────────────────────────────────────────────

    _build_item_details_map(sources, all_codes) {
        const map = {};

        (all_codes || []).forEach((c) => {
            if (c && !map[c]) {
                map[c] = {
                    item_name:     "",
                    item_category: "",
                    sub_category:  "",
                    model:         "",
                    __resolved:    false,
                };
            }
        });

        (sources || []).forEach((rows) => {
            (rows || []).forEach((r) => {
                if (r && r.item_code && r.item_name && r.item_name !== r.item_code) {
                    if (!map[r.item_code]) {
                        map[r.item_code] = {
                            item_name: "", item_category: "",
                            sub_category: "", model: "", __resolved: false,
                        };
                    }
                    if (!map[r.item_code].item_name) {
                        map[r.item_code].item_name = r.item_name;
                    }
                }
            });
        });

        const codes = Object.keys(map);
        if (!codes.length) return Promise.resolve(map);

        const ITEM_FIELDS = [
            "name",
            "item_name",
            ITEM_FIELD_CATEGORY,
            ITEM_FIELD_SUB_CATEGORY,
            ITEM_FIELD_MODEL,
        ];

        const CHUNK = 200;
        const chunks = [];
        for (let i = 0; i < codes.length; i += CHUNK) {
            chunks.push(codes.slice(i, i + CHUNK));
        }

        const bulk_calls = chunks.map((chunk) =>
            this._silent_xcall("frappe.client.get_list", {
                doctype: "Item",
                filters: { name: ["in", chunk] },
                fields:  ITEM_FIELDS,
                limit_page_length: 0,
            }).then((rows) => {
                (rows || []).forEach((it) => {
                    const code = it.name;
                    if (!code || !map[code]) return;
                    if (it.item_name) map[code].item_name = it.item_name;
                    map[code].item_category = it[ITEM_FIELD_CATEGORY]     || "";
                    map[code].sub_category  = it[ITEM_FIELD_SUB_CATEGORY] || "";
                    map[code].model         = it[ITEM_FIELD_MODEL]        || "";
                    map[code].__resolved    = true;
                });
            })
        );

        return Promise.all(bulk_calls).then(() => {
            const unresolved = codes.filter((c) => !map[c].__resolved);
            if (!unresolved.length) return map;

            const lookups = unresolved.map((code) =>
                this._silent_xcall("frappe.client.get_value", {
                    doctype:   "Item",
                    filters:   { name: code },
                    fieldname: [
                        "item_name",
                        ITEM_FIELD_CATEGORY,
                        ITEM_FIELD_SUB_CATEGORY,
                        ITEM_FIELD_MODEL,
                    ],
                }).then((res) => {
                    if (res) {
                        if (res.item_name) map[code].item_name = res.item_name;
                        map[code].item_category = res[ITEM_FIELD_CATEGORY]     || "";
                        map[code].sub_category  = res[ITEM_FIELD_SUB_CATEGORY] || "";
                        map[code].model         = res[ITEM_FIELD_MODEL]        || "";
                    }
                    map[code].__resolved = true;
                })
            );

            return Promise.all(lookups).then(() => map);

        }).then((m) => {
            Object.keys(m).forEach((code) => {
                if (!m[code].item_name) m[code].item_name = code;
            });
            return m;
        }).catch(() => {
            Object.keys(map).forEach((code) => {
                if (!map[code].item_name) map[code].item_name = code;
            });
            return map;
        });
    }

    _fetch_and_generate_excel() {
        frappe.dom.freeze(__("Preparing Excel report…"));

        Promise.all([
            frappe.xcall("ch_pos.api.stock_report.get_store_stock_report", {
                pos_profile: PosState.pos_profile,
            }),
            this._fetch_company_name(),
        ]).then(([stock_data, company_name]) => {

            const warehouse = stock_data.warehouse || "";

            this._resolve_sibling_warehouses(warehouse).then((siblings) => {

                const damaged_wh = siblings.damaged;
                const demo_wh    = siblings.demo;
                const buyback_wh = siblings.buyback;

                console.log("[Stock Audit] Resolved warehouses:", {
                    main:    warehouse,
                    damaged: damaged_wh,
                    demo:    demo_wh,
                    buyback: buyback_wh,
                    company: company_name,
                });

                Promise.all([
                    this._fetch_warehouse_serials(warehouse),
                    this._fetch_warehouse_stock(damaged_wh),
                    this._fetch_warehouse_stock(demo_wh),
                    this._fetch_warehouse_stock(buyback_wh),
                    this._fetch_warehouse_serials(damaged_wh),
                    this._fetch_warehouse_serials(demo_wh),
                    this._fetch_warehouse_serials(buyback_wh),
                ]).then(([
                    serial_list,
                    damaged_rows,  demo_rows,  buyback_rows,
                    damaged_serial, demo_serial, buyback_serial,
                ]) => {

                    const codes_from_serials = []
                        .concat((serial_list    || []).map((s) => s.item_code))
                        .concat((damaged_serial || []).map((s) => s.item_code))
                        .concat((demo_serial    || []).map((s) => s.item_code))
                        .concat((buyback_serial || []).map((s) => s.item_code));

                    const codes_from_bins = []
                        .concat((stock_data.rows || []).map((r) => r.item_code))
                        .concat((damaged_rows    || []).map((r) => r.item_code))
                        .concat((demo_rows       || []).map((r) => r.item_code))
                        .concat((buyback_rows    || []).map((r) => r.item_code));

                    const all_codes = Array.from(new Set(
                        [...codes_from_serials, ...codes_from_bins].filter(Boolean)
                    ));

                    this._build_item_details_map(
                        [
                            stock_data.rows || [],
                            damaged_rows    || [],
                            demo_rows       || [],
                            buyback_rows    || [],
                        ],
                        all_codes
                    ).then((details_map) => {

                        const enrich_serials = (arr) => (arr || []).map((s) => {
                            const d = details_map[s.item_code] || {};
                            return {
                                ...s,
                                item_name:     d.item_name     || s.item_code || "",
                                item_category: d.item_category || "",
                                sub_category:  d.sub_category  || "",
                                model:         d.model         || "",
                            };
                        });

                        const enrich_rows = (arr) => (arr || []).map((r) => {
                            const d = details_map[r.item_code] || {};
                            const real_name =
                                (r.item_name && r.item_name !== r.item_code)
                                    ? r.item_name
                                    : (d.item_name || r.item_code || "");
                            return {
                                ...r,
                                item_name:     real_name,
                                item_category: d.item_category || "",
                                sub_category:  d.sub_category  || "",
                                model:         d.model         || "",
                            };
                        });

                        frappe.dom.unfreeze();
                        this._generate_workbook(
                            stock_data,
                            enrich_serials(serial_list),
                            { warehouse: damaged_wh, rows: enrich_rows(damaged_rows), serials: enrich_serials(damaged_serial) },
                            { warehouse: demo_wh,    rows: enrich_rows(demo_rows),    serials: enrich_serials(demo_serial)    },
                            { warehouse: buyback_wh, rows: enrich_rows(buyback_rows), serials: enrich_serials(buyback_serial) },
                            details_map,
                            company_name
                        );
                    });

                }).catch((e) => {
                    frappe.dom.unfreeze();
                    frappe.msgprint(__("Could not generate report: {0}", [e.message || e]));
                });
            });

        }).catch((e) => {
            frappe.dom.unfreeze();
            frappe.msgprint(__("Could not load store stock: {0}", [e.message || e]));
        });
    }

   
    _generate_workbook(stock_data, serial_list, damaged, demo, buyback, details_map, company_name) {

        const dmap      = details_map || {};
        const warehouse = stock_data.warehouse || "";
        const all_rows  = stock_data.rows      || [];
        const main_rows = all_rows.filter((r) => !_is_in_transit(r.status));
        const company   = company_name || "";

        const detail_of = (r, field) => {
            if (r && r[field]) return r[field];
            const d = dmap[r && r.item_code];
            return (d && d[field]) || "";
        };

        const wb = XLSX.utils.book_new();

        // ── Shared styles ───────────────────────────────────────────────
        const TITLE_STYLE = {
            font:      { bold: true, sz: 13, color: { rgb: "FFFFFF" } },
            fill:      { patternType: "solid", fgColor: { rgb: "0E7490" } },
            alignment: { horizontal: "center", vertical: "center" },
        };
        const HDR_STYLE = {
            font:      { bold: true, sz: 11, color: { rgb: "FFFFFF" } },
            fill:      { patternType: "solid", fgColor: { rgb: "1F4E79" } },
            alignment: { horizontal: "center", vertical: "center", wrapText: true },
            border: {
                top:    { style: "thin", color: { rgb: "9CA3AF" } },
                bottom: { style: "thin", color: { rgb: "9CA3AF" } },
                left:   { style: "thin", color: { rgb: "9CA3AF" } },
                right:  { style: "thin", color: { rgb: "9CA3AF" } },
            },
        };

        const apply_row_style = (ws, row_idx, ncols, style) => {
            for (let c = 0; c < ncols; c++) {
                const ref = XLSX.utils.encode_cell({ r: row_idx, c });
                if (!ws[ref]) ws[ref] = { t: "s", v: "" };
                ws[ref].s = style;
            }
        };

        // ══════════════════════════════════════════════════════════════
        // Combine ALL warehouses (Main + Damaged + Demo + Buyback)
        // ══════════════════════════════════════════════════════════════

        const tag_rows = (rows, category, fallback_wh) =>
            (rows || []).map((r) => ({
                ...r,
                __category: category,
                warehouse:  r.warehouse || fallback_wh || "",
            }));

        const combined_qty_rows = []
            .concat(tag_rows(main_rows,       "Main",    warehouse))
            .concat(tag_rows(damaged.rows,    "Damaged", damaged.warehouse))
            .concat(tag_rows(demo.rows,       "Demo",    demo.warehouse))
            .concat(tag_rows(buyback.rows,    "Buyback", buyback.warehouse));

        const combined_serials = []
            .concat(tag_rows(serial_list,     "Main",    warehouse))
            .concat(tag_rows(damaged.serials, "Damaged", damaged.warehouse))
            .concat(tag_rows(demo.serials,    "Demo",    demo.warehouse))
            .concat(tag_rows(buyback.serials, "Buyback", buyback.warehouse))
            .filter((s) => !_is_in_transit(s.status));

        const cat_order = { "Main": 0, "Damaged": 1, "Demo": 2, "Buyback": 3 };

        // ══════════════════════════════════════════════════════════════
        // Sheet 1 — Qty Sheet (ALL warehouses)
        // ══════════════════════════════════════════════════════════════
        const qty_map = {};
        combined_qty_rows.forEach((r) => {
            const wh_short = _short_wh(r.warehouse) || __("Unknown");
            const code     = r.item_code || "";
            const key      = `${wh_short}||${code}`;
            if (!qty_map[key]) {
                qty_map[key] = {
                    wh:            wh_short,
                    code,
                    category:      r.__category,
                    item_name:     detail_of(r, "item_name")     || code,
                    item_category: detail_of(r, "item_category"),
                    sub_category:  detail_of(r, "sub_category"),
                    model:         detail_of(r, "model"),
                    qty:           0,
                    class:         r.cycle_count_class || "—",
                    last_verified: r.last_verified     || null,
                    due:           false,
                };
            }
            qty_map[key].qty += flt(r.on_hand_qty);
            if (r.due) qty_map[key].due = true;
        });

        const qty_rows_all = Object.values(qty_map).sort((a, b) => {
            const co = (cat_order[a.category] ?? 9) - (cat_order[b.category] ?? 9);
            if (co !== 0) return co;
            const wc = (a.wh || "").localeCompare(b.wh || "");
            if (wc !== 0) return wc;
            return (a.code || "").localeCompare(b.code || "");
        });

        // Title: "<Company> — Stock Report — Qty Wise (All Warehouses)"
        const qty_title_txt = [
            company,
            __("Stock Report — Qty Wise (All Warehouses)"),
        ].filter(Boolean).join(" — ");

        const qty_title   = [qty_title_txt];
        const qty_headers = [
            __("S.No"), __("Category"), __("Warehouse"),
            __("Item Category"), __("Sub Category"), __("Model"),
            __("Item Code"), __("Item Name"),
            __("On Hand Qty"), __("ABC Class"),
            __("Last Verified"), __("Due for Count"),
        ];
        const qty_data_rows = qty_rows_all.map((it, i) => [
            i + 1,
            it.category,
            it.wh,
            it.item_category,
            it.sub_category,
            it.model,
            it.code,
            it.item_name,
            it.qty,
            it.class,
            it.last_verified
                ? frappe.datetime.str_to_user(it.last_verified) : __("Never"),
            it.due ? __("Yes") : __("No"),
        ]);

        // NO empty row: title (row 0), headers (row 1), data (row 2+)
        const ws1 = XLSX.utils.aoa_to_sheet([qty_title, qty_headers, ...qty_data_rows]);

        ws1["!merges"] = [{ s: { r: 0, c: 0 }, e: { r: 0, c: qty_headers.length - 1 } }];
        apply_row_style(ws1, 0, qty_headers.length, TITLE_STYLE);
        apply_row_style(ws1, 1, qty_headers.length, HDR_STYLE);
        ws1["!rows"] = [{ hpt: 30 }, { hpt: 32 }];

        // Auto-filter on header row (row 2 in Excel, index 1)
        const qty_last_col = XLSX.utils.encode_col(qty_headers.length - 1);
        const qty_last_row = 2 + qty_data_rows.length;   // header at row 2 + data rows
        ws1["!autofilter"] = { ref: `A2:${qty_last_col}${qty_last_row}` };
        ws1["!freeze"]     = { xSplit: 0, ySplit: 2 };

        ws1["!cols"] = [
            { wch: 6  }, { wch: 12 }, { wch: 30 },
            { wch: 20 }, { wch: 20 }, { wch: 20 },
            { wch: 18 }, { wch: 36 },
            { wch: 14 }, { wch: 10 }, { wch: 16 }, { wch: 14 },
        ];

        // ══════════════════════════════════════════════════════════════
        // Sheet 2 — IMEI Sheet (ALL warehouses)
        // ══════════════════════════════════════════════════════════════
        const serials_all = combined_serials.slice().sort((a, b) => {
            const co = (cat_order[a.__category] ?? 9) - (cat_order[b.__category] ?? 9);
            if (co !== 0) return co;
            const wc = (_short_wh(a.warehouse) || "").localeCompare(_short_wh(b.warehouse) || "");
            if (wc !== 0) return wc;
            const ic = (a.item_code || "").localeCompare(b.item_code || "");
            if (ic !== 0) return ic;
            return (a.name || "").localeCompare(b.name || "");
        });

        const imei_title_txt = [
            company,
            __("Stock Report — IMEI Wise (All Warehouses)"),
        ].filter(Boolean).join(" — ");

        const imei_title   = [imei_title_txt];
        const imei_headers = [
            __("S.No"), __("Category"), __("Warehouse"),
            __("Item Category"), __("Sub Category"), __("Model"),
            __("Item Code"), __("Item Name"),
            __("IMEI / Serial No"), __("Status"),
        ];
        const imei_data_rows = serials_all.map((s, i) => [
            i + 1,
            s.__category || "Main",
            _short_wh(s.warehouse) || __("Unknown"),
            detail_of(s, "item_category"),
            detail_of(s, "sub_category"),
            detail_of(s, "model"),
            s.item_code || "",
            detail_of(s, "item_name") || s.item_code || "",
            s.name   || "",
            s.status || __("Active"),
        ]);

        const ws2 = XLSX.utils.aoa_to_sheet([imei_title, imei_headers, ...imei_data_rows]);
        ws2["!merges"] = [{ s: { r: 0, c: 0 }, e: { r: 0, c: imei_headers.length - 1 } }];
        apply_row_style(ws2, 0, imei_headers.length, TITLE_STYLE);
        apply_row_style(ws2, 1, imei_headers.length, HDR_STYLE);
        ws2["!rows"] = [{ hpt: 30 }, { hpt: 32 }];

        const imei_last_col = XLSX.utils.encode_col(imei_headers.length - 1);
        const imei_last_row = 2 + imei_data_rows.length;
        ws2["!autofilter"] = { ref: `A2:${imei_last_col}${imei_last_row}` };
        ws2["!freeze"]     = { xSplit: 0, ySplit: 2 };

        ws2["!cols"] = [
            { wch: 6 }, { wch: 12 }, { wch: 30 },
            { wch: 20 }, { wch: 20 }, { wch: 20 },
            { wch: 18 }, { wch: 36 },
            { wch: 26 }, { wch: 14 },
        ];

        // ── Register sheets ─────────────────────────────────────────────
        XLSX.utils.book_append_sheet(wb, ws1, this._safe_sheet_name(__("Qty Sheet")));
        XLSX.utils.book_append_sheet(wb, ws2, this._safe_sheet_name(__("IMEI Sheet")));

        const safe_wh  = (warehouse || "all").replace(/[^a-zA-Z0-9]/g, "_");
        const filename = `stock_audit_${safe_wh}_${frappe.datetime.now_date()}.xlsx`;
        XLSX.writeFile(wb, filename);

        frappe.show_alert({
            message: __("Downloaded: {0}", [filename]),
            indicator: "green",
        });
    }


    _print_stock_snapshot(payload) {
        const rows       = payload.rows      || [];
        const warehouse  = payload.warehouse || "";
        const summary    = payload.summary   || {};
        const printed_on = payload.printed_on || frappe.datetime.now_datetime();

        const table_rows = rows.map((r) => `
            <tr>
                <td>
                    <div style="font-weight:500">${frappe.utils.escape_html(r.item_name || r.item_code || "")}</div>
                    <div style="font-size:10px;color:#9ca3af">${frappe.utils.escape_html(r.item_code || "")}</div>
                </td>
                <td style="text-align:right">${flt(r.on_hand_qty)}</td>
                <td style="text-align:right">
                    ${format_currency(r.stock_value || 0)}
                </td>
                <td style="text-align:center">${frappe.utils.escape_html(r.cycle_count_class || "-")}</td>
                <td style="text-align:center">${r.due ? "Yes" : "No"}</td>
            </tr>`).join("");

        const html = `<!DOCTYPE html><html><head>
            <title>${__("Stock Audit Snapshot")}</title>
            <style>
                body  { font-family: Arial, sans-serif; padding: 18px }
                h2    { margin: 0 0 10px }
                .meta { margin-bottom: 12px; color: #4b5563 }
                table { width: 100%; border-collapse: collapse; font-size: 12px }
                th, td{ border: 1px solid #d1d5db; padding: 6px 8px }
                th    { background: #f3f4f6; text-align: left }
                .summary { margin-top: 12px; font-weight: bold }
            </style></head><body>
            <h2>${__("Stock Audit Snapshot")}</h2>
            <div class="meta">
                <div><b>${__("Warehouse")}:</b> ${wh_label(warehouse)}</div>
                <div><b>${__("Printed On")}:</b> ${frappe.utils.escape_html(printed_on)}</div>
                <div>
                    <b>${__("Items")}:</b> ${flt(summary.items || rows.length)} ·
                    <b>${__("Stock Value")}:</b>
                    ${format_currency(summary.total_stock_value || 0)}
                </div>
            </div>
            <table>
                <thead><tr>
                    <th>${__("Item")}</th>
                    <th>${__("On Hand")}</th><th>${__("Value")}</th>
                    <th>${__("Class")}</th><th>${__("Due")}</th>
                </tr></thead>
                <tbody>
                    ${table_rows || `<tr><td colspan="5" style="text-align:center">
                        ${__("No rows")}
                    </td></tr>`}
                </tbody>
            </table>
            <div class="summary">
                ${__("Total Items")}: ${summary.items || rows.length} |
                ${__("Total Value")}:
                ${format_currency(summary.total_stock_value || 0)}
            </div>
            <script>window.print();<\/script>
            </body></html>`;

        const win = window.open("", "_blank");
        if (!win) {
            frappe.msgprint(__("Popup blocked. Please allow popups for print."));
            return;
        }
        frappe.xcall("ch_pos.api.print_api.log_client_print", {
            pos_profile: PosState.pos_profile,
            report_name: "Stock Audit Snapshot",
            filters: {
                warehouse,
                items: flt(summary.items || rows.length),
                total_stock_value: flt(summary.total_stock_value || 0),
            },
        }).catch((err) => console.warn("Stock Audit print audit log failed", err));
        win.document.open();
        win.document.write(html);
        win.document.close();
    }

    // ═══════════════════════════ CYCLE COUNT DIALOGS ═══════════════════════════

    _start_count() {
        if (!PosState.pos_profile) {
            frappe.msgprint(__("No POS profile — cannot resolve this store's warehouse."));
            return;
        }
        const d = new frappe.ui.Dialog({
            title: __("Start Cycle Count"),
            fields: [
                {
                    fieldname: "class_filter", label: __("Count Class"),
                    fieldtype: "Select", options: "\nA\nB\nC",
                    description: __("Leave blank to count all classes."),
                },
                {
                    fieldname: "only_due", label: __("Only items due for count"),
                    fieldtype: "Check", default: 0,
                },
            ],
            primary_action_label: __("Start"),
            primary_action: (values) => {
                d.hide();
                frappe.xcall("ch_pos.api.stock_report.start_store_cycle_count", {
                    pos_profile:  PosState.pos_profile,
                    class_filter: values.class_filter || null,
                    only_due:     values.only_due ? 1 : 0,
                }).then((res) => {
                    if (!res || !res.cycle_count) {
                        frappe.msgprint(__("Could not start the count."));
                        return;
                    }
                    if (!res.items) {
                        frappe.msgprint(__("No items to count for the chosen filters."));
                        return;
                    }
                    this._open_count_sheet(res);
                }).catch((e) =>
                    frappe.msgprint(__("Could not start count: {0}", [e.message || e]))
                );
            },
        });
        d.show();
    }

    _open_count_sheet(res) {
        const lines   = res.lines || [];
        const blind   = !!res.blind_count;
        const scanned = {};

        const body = lines.map((l) => {
            const name = frappe.utils.escape_html(l.item_name || l.item_code);
            if (l.is_serialized) {
                return `<tr>
                    <td>${name}
                        <div class="text-muted" style="font-size:.75rem">
                            ${__("Serialized — scan each IMEI")}
                        </div>
                    </td>
                    <td>
                        <input type="text" class="form-control input-sm cc-scan"
                               data-item="${frappe.utils.escape_html(l.item_code)}"
                               placeholder="${__("Scan IMEI, press Enter")}">
                        <div class="cc-serials"
                             data-item="${frappe.utils.escape_html(l.item_code)}"
                             style="margin-top:4px"></div>
                    </td></tr>`;
            }
            const hint = blind
                ? ""
                : `<div class="text-muted" style="font-size:.75rem">
                       ${__("system")}: ${l.system_qty}
                   </div>`;
            return `<tr>
                <td>${name}${hint}</td>
                <td>
                    <input type="number" min="0" class="form-control input-sm cc-qty"
                           data-item="${frappe.utils.escape_html(l.item_code)}"
                           placeholder="0">
                </td></tr>`;
        }).join("");

        const d = new frappe.ui.Dialog({
            title: __("Count Sheet — {0}", [res.cycle_count]),
            size: "large",
            fields: [{ fieldtype: "HTML", fieldname: "sheet" }],
            primary_action_label: __("Submit Count"),
            primary_action: () => {
                const qty_map = {};
                d.$wrapper.find(".cc-qty").each(function () {
                    qty_map[$(this).data("item")] = flt($(this).val());
                });
                const counts = lines.map((l) =>
                    l.is_serialized
                        ? { item_code: l.item_code,
                            scanned_serials: (scanned[l.item_code] || []).join("\n") }
                        : { item_code: l.item_code,
                            counted_qty: qty_map[l.item_code] || 0 }
                );
                frappe.xcall("ch_pos.api.stock_report.submit_pos_count", {
                    cycle_count: res.cycle_count,
                    counts:      JSON.stringify(counts),
                }).then((r) => {
                    d.hide();
                    const verified = r.status === "Completed - Verified";
                    frappe.msgprint({
                        title:     verified ? __("Count Verified ✔") : __("Variance Sent for Approval"),
                        indicator: verified ? "green" : "orange",
                        message:   verified
                            ? __("All counts match — {0} verified, last-verified updated.", [r.name])
                            : __(
                                "Variance of {0} on {1} routed for approval (exception {2}). Reconciliation posts after approval.",
                                [
                                    format_currency(r.total_variance_value),
                                    r.name,
                                    r.variance_exception || "—",
                                ]
                            ),
                    });
                    this._refresh_kpis();
                    this._switch_tab("history");
                }).catch((e) =>
                    frappe.msgprint(__("Submit failed: {0}", [e.message || e]))
                );
            },
        });

        d.fields_dict.sheet.$wrapper.html(`
            <div class="text-muted" style="margin-bottom:8px">
                ${__("Warehouse")}: <b>${wh_label(res.warehouse)}</b>
                · ${res.items} ${__("item(s)")}
                ${blind ? `· <span style="color:#d97706">${__("Blind count")}</span>` : ""}
            </div>
            <div style="max-height:420px;overflow:auto">
            <table class="table table-bordered" style="font-size:.85rem">
                <thead><tr>
                    <th>${__("Item")}</th>
                    <th style="width:45%">${__("Counted")}</th>
                </tr></thead>
                <tbody>${body}</tbody>
            </table></div>`);

        const render_chips = () => {
            d.$wrapper.find(".cc-serials").each(function () {
                const code = $(this).data("item");
                const list = scanned[code] || [];
                $(this).html(list.map((s, i) =>
                    `<span class="badge"
                           style="background:#e0e7ff;color:#3730a3;margin:2px;cursor:pointer"
                           data-code="${frappe.utils.escape_html(code)}"
                           data-idx="${i}">
                        ${frappe.utils.escape_html(s)} ✕
                    </span>`
                ).join(""));
            });
        };

        d.$wrapper.find(".cc-scan").on("keydown", function (e) {
            if (e.key !== "Enter") return;
            e.preventDefault();
            const code = $(this).data("item");
            const val  = ($(this).val() || "").trim();
            if (!val) return;
            scanned[code] = scanned[code] || [];
            if (!scanned[code].includes(val)) scanned[code].push(val);
            $(this).val("");
            render_chips();
        });

        d.$wrapper.on("click", ".cc-serials .badge", function () {
            const code = $(this).data("code");
            scanned[code].splice($(this).data("idx"), 1);
            render_chips();
        });

        d.show();
    }


    _empty(msg) {
        return `<div class="text-muted text-center" style="padding:32px">
            <i class="fa fa-inbox fa-2x" style="color:#d1d5db;margin-bottom:8px"></i>
            <div>${msg}</div>
        </div>`;
    }
}
