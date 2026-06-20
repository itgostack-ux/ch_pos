/**
 * CH POS — Stock Audit Workspace
 *
 * Dedicated audit / inventory-control surface for store executives.
 * Cleanly separated from the cashier "Bill Exceptions" workspace
 * (market-standard split — SAP Retail / Oracle Xstore / GoFrugal):
 *
 *   Bill Exceptions  → cashier overrides at billing time
 *                      (Discount Override / Free Accessory / Below Margin / …)
 *   Stock Audit      → on-hand visibility, cycle counting, variance approvals
 *
 * Tabs:
 *   • Stock Report      — on-hand snapshot with last-verified status
 *   • Cycle Count       — kick off a count (ABC / due-only filter)
 *   • Count History     — recent CH Cycle Count rows for this warehouse
 *   • Variance Requests — Stock Count Variance approval audit log
 */
import { PosState, EventBus } from "../../state.js";

const TABS = [
	{ key: "stock",    icon: "fa-cubes",          label: __("Stock Report") },
	{ key: "count",    icon: "fa-check-square-o", label: __("Cycle Count") },
	{ key: "history",  icon: "fa-history",        label: __("Count History") },
	{ key: "variance", icon: "fa-balance-scale",  label: __("Variance Requests") },
];

export class StockAuditWorkspace {
	constructor() {
		this._panel = null;
		this._active_tab = "stock";
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "stock_audit") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		this._panel = panel;
		const tabs_html = TABS.map((t) => `
			<div class="ch-sa-tab" data-tab="${t.key}"
				style="padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;">
				<i class="fa ${t.icon}"></i> ${t.label}
			</div>`).join("");

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header" style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
					<div>
						<h4>
							<span class="mode-icon" style="background:#ecfeff;color:#0e7490">
								<i class="fa fa-balance-scale"></i>
							</span>
							${__("Stock Audit")}
						</h4>
						<span class="ch-mode-hint">${__("On-hand visibility, cycle counts, and variance approvals for this store.")}</span>
					</div>
					<button class="btn btn-default btn-sm ch-sa-refresh">
						<i class="fa fa-refresh"></i> ${__("Refresh")}
					</button>
				</div>

				<div class="ch-sa-kpi-strip" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:var(--pos-space-md);"></div>

				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);">
					<div class="section-body" style="padding:0;">
						<div class="ch-sa-tabs" style="display:flex;border-bottom:1px solid var(--pos-border);overflow-x:auto;">
							${tabs_html}
						</div>
					</div>
				</div>

				<div class="ch-sa-tab-body"></div>
			</div>
		`);

		this._bind(panel);
		this._refresh_kpis();
		this._switch_tab(this._active_tab);
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
		panel.on("click", ".ch-sa-open-doc", (e) => {
			const dt = $(e.currentTarget).data("dt");
			const dn = $(e.currentTarget).data("dn");
			if (dt && dn) frappe.set_route("Form", dt, dn);
		});
		panel.on("click", ".ch-sa-start-count", () => this._start_count());
		panel.on("click", ".ch-sa-open-stock", () => this._switch_tab("stock"));
	}

	_switch_tab(tab) {
		this._active_tab = tab;
		const panel = this._panel;
		if (!panel) return;
		panel.find(".ch-sa-tab").each(function () {
			const active = $(this).data("tab") === tab;
			$(this).css({
				"border-bottom-color": active ? "var(--primary)" : "transparent",
				"color":                active ? "var(--primary)" : "",
				"font-weight":          active ? 600 : 400,
			});
		});
		const body = panel.find(".ch-sa-tab-body");
		body.html(`<div class="text-muted text-center" style="padding:24px"><i class="fa fa-spinner fa-spin"></i> ${__("Loading…")}</div>`);

		if (tab === "stock")    this._render_stock(body);
		else if (tab === "count")    this._render_count(body);
		else if (tab === "history")  this._render_history(body);
		else if (tab === "variance") this._render_variance(body);
	}

	// ── KPI strip ───────────────────────────────────────────────

	_refresh_kpis() {
		const strip = this._panel?.find(".ch-sa-kpi-strip");
		if (!strip || !strip.length) return;
		strip.html("");
		if (!PosState.pos_profile) return;

		// Reuse the existing store-stock endpoint for the headline numbers.
		frappe.xcall("ch_pos.api.stock_report.get_store_stock_report", {
			pos_profile: PosState.pos_profile,
		}).then((d) => {
			const s = d.summary || {};
			const kpis = [
				{ label: __("Items on Hand"), value: s.items || 0, color: "#2563eb", bg: "#dbeafe", icon: "fa-cubes" },
				{ label: __("Stock Value"),   value: frappe.format(s.total_stock_value || 0, { fieldtype: "Currency" }),
				  color: "#0d9488", bg: "#ccfbf1", icon: "fa-inr" },
				{ label: __("Due for Count"), value: s.due_for_count || 0,
				  color: (s.due_for_count ? "#dc2626" : "#16a34a"),
				  bg:    (s.due_for_count ? "#fef2f2" : "#dcfce7"),
				  icon: "fa-check-square-o" },
			];
			strip.html(kpis.map((k) => this._kpi(k)).join(""));
		}).catch(() => { /* silent — stock report failures show in tab */ });

		// Variance approvals waiting on someone
		frappe.xcall("ch_pos.api.stock_report.list_variance_requests", {
			pos_profile: PosState.pos_profile,
			limit: 200,
		}).then((d) => {
			const rows = d.rows || [];
			const pending = rows.filter((r) => ["Pending", "Escalated", "Awaiting Approval"].includes(r.status)).length;
			strip.append(this._kpi({
				label: __("Pending Variance Approvals"),
				value: pending,
				color: pending ? "#d97706" : "#16a34a",
				bg:    pending ? "#fef3c7" : "#dcfce7",
				icon:  "fa-balance-scale",
			}));
		}).catch(() => {});
	}

	_kpi(k) {
		return `
			<div style="flex:1 1 180px;min-width:160px;display:flex;align-items:center;gap:10px;padding:10px 12px;background:#fff;border:1px solid var(--pos-border);border-radius:var(--pos-radius-sm);">
				<div style="width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:${k.bg};color:${k.color};font-size:16px;">
					<i class="fa ${k.icon}"></i>
				</div>
				<div style="flex:1;min-width:0">
					<div style="font-size:13px;font-weight:600;line-height:1.1;color:${k.color};">${k.value}</div>
					<div style="font-size:11px;color:#6b7280;line-height:1.2;">${k.label}</div>
				</div>
			</div>`;
	}

	// ── Tab: Stock Report ───────────────────────────────────────

	_render_stock(body) {
		if (!PosState.pos_profile) {
			body.html(this._empty(__("No POS profile selected.")));
			return;
		}
		frappe.xcall("ch_pos.api.stock_report.get_store_stock_report", {
			pos_profile: PosState.pos_profile,
		}).then((d) => {
			const rows = (d.rows || []).map((r) => {
				const due_badge = r.due
					? `<span class="badge" style="background:#dc2626;color:#fff">${__("Due")}</span>` : "";
				const last = r.last_verified
					? frappe.datetime.str_to_user(r.last_verified)
					: `<span class="text-muted">${__("Never")}</span>`;
				const since = (r.days_since_count || r.days_since_count === 0) ? `${r.days_since_count}d` : "—";
				return `<tr>
					<td>${frappe.utils.escape_html(r.item_name || r.item_code)}</td>
					<td class="text-right">${flt(r.on_hand_qty)}</td>
					<td class="text-right">${frappe.format(r.stock_value || 0, { fieldtype: "Currency" })}</td>
					<td class="text-center">${frappe.utils.escape_html(r.cycle_count_class || "—")}</td>
					<td>${last}</td>
					<td class="text-center">${since}</td>
					<td class="text-center">${due_badge}</td>
				</tr>`;
			}).join("");
			body.html(`
				<div class="ch-pos-section-card">
					<div class="section-header" style="display:flex;justify-content:space-between;align-items:center">
						<span><i class="fa fa-cubes"></i> ${__("Store Stock — {0}", [frappe.utils.escape_html(d.warehouse || "")])}</span>
						<button class="btn btn-xs btn-primary ch-sa-start-count">
							<i class="fa fa-check-square-o"></i> ${__("Start Cycle Count")}
						</button>
					</div>
					<div class="section-body" style="padding:0;max-height:520px;overflow:auto">
						<table class="table table-condensed table-hover" style="font-size:13px;margin:0">
							<thead><tr>
								<th>${__("Item")}</th>
								<th class="text-right">${__("On Hand")}</th>
								<th class="text-right">${__("Value")}</th>
								<th class="text-center">${__("Class")}</th>
								<th>${__("Last Verified")}</th>
								<th class="text-center">${__("Since")}</th>
								<th class="text-center">${__("Due?")}</th>
							</tr></thead>
							<tbody>${rows || `<tr><td colspan="7" class="text-center text-muted" style="padding:20px">${__("No stock on hand")}</td></tr>`}</tbody>
						</table>
					</div>
				</div>`);
		}).catch((e) => body.html(this._empty(__("Could not load store stock: {0}", [e.message || e]))));
	}

	// ── Tab: Cycle Count ────────────────────────────────────────

	_render_count(body) {
		body.html(`
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);">
				<div class="section-header"><i class="fa fa-check-square-o"></i> ${__("Start a New Cycle Count")}</div>
				<div class="section-body">
					<p class="text-muted" style="margin:0 0 12px;font-size:13px">
						${__("Pick a class (A/B/C) and choose whether to count only items that are <i>due</i>, or every item in scope. ABC class &amp; due-status come from the rolling consumption-value model.")}
					</p>
					<button class="btn btn-primary btn-sm ch-sa-start-count">
						<i class="fa fa-play"></i> ${__("Start Count")}
					</button>
					<button class="btn btn-default btn-sm ch-sa-open-stock" style="margin-left:8px">
						<i class="fa fa-cubes"></i> ${__("View Store Stock")}
					</button>
				</div>
			</div>

			<div class="ch-pos-section-card">
				<div class="section-header"><i class="fa fa-clock-o"></i> ${__("Open / Recent Counts")}</div>
				<div class="section-body ch-sa-recent" style="padding:0">
					<div class="text-muted text-center" style="padding:16px"><i class="fa fa-spinner fa-spin"></i></div>
				</div>
			</div>
		`);
		this._load_history(body.find(".ch-sa-recent"), 8);
	}

	// ── Tab: Count History ──────────────────────────────────────

	_render_history(body) {
		body.html(`
			<div class="ch-pos-section-card">
				<div class="section-header"><i class="fa fa-history"></i> ${__("Cycle Count History")}</div>
				<div class="section-body ch-sa-history" style="padding:0">
					<div class="text-muted text-center" style="padding:16px"><i class="fa fa-spinner fa-spin"></i></div>
				</div>
			</div>
		`);
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
					? `<a class="ch-sa-open-doc" data-dt="CH Exception Request" data-dn="${frappe.utils.escape_html(r.variance_exception)}" style="cursor:pointer">${frappe.utils.escape_html(r.variance_exception)}</a>`
					: "—";
				const sr = r.stock_reconciliation
					? `<a class="ch-sa-open-doc" data-dt="Stock Reconciliation" data-dn="${frappe.utils.escape_html(r.stock_reconciliation)}" style="cursor:pointer">${frappe.utils.escape_html(r.stock_reconciliation)}</a>`
					: "—";
				return `<tr>
					<td><a class="ch-sa-open-doc" data-dt="CH Cycle Count" data-dn="${frappe.utils.escape_html(r.name)}" style="cursor:pointer">${frappe.utils.escape_html(r.name)}</a></td>
					<td>${r.count_date ? frappe.datetime.str_to_user(r.count_date) : "—"}</td>
					<td>${frappe.utils.escape_html(r.counted_by || "—")}</td>
					<td><span class="badge badge-${status_cls}">${frappe.utils.escape_html(r.status || "")}</span></td>
					<td class="text-right">${flt(r.total_variance_qty)}</td>
					<td class="text-right">${frappe.format(r.total_variance_value || 0, { fieldtype: "Currency" })}</td>
					<td>${vexc}</td>
					<td>${sr}</td>
				</tr>`;
			}).join("");
			container.html(`
				<div style="max-height:520px;overflow:auto">
					<table class="table table-condensed table-hover" style="font-size:13px;margin:0">
						<thead><tr>
							<th>${__("Count")}</th>
							<th>${__("Date")}</th>
							<th>${__("Counted By")}</th>
							<th>${__("Status")}</th>
							<th class="text-right">${__("Var Qty")}</th>
							<th class="text-right">${__("Var Value")}</th>
							<th>${__("Variance Req.")}</th>
							<th>${__("Stock Recon.")}</th>
						</tr></thead>
						<tbody>${rows || `<tr><td colspan="8" class="text-center text-muted" style="padding:20px">${__("No cycle counts yet for this warehouse.")}</td></tr>`}</tbody>
					</table>
				</div>`);
		}).catch((e) => container.html(this._empty(__("Could not load cycle counts: {0}", [e.message || e]))));
	}

	// ── Tab: Variance Requests ──────────────────────────────────

	_render_variance(body) {
		body.html(`
			<div class="ch-pos-section-card">
				<div class="section-header">
					<i class="fa fa-balance-scale"></i> ${__("Stock Count Variance — Approval Log")}
				</div>
				<div class="section-body ch-sa-variance" style="padding:0">
					<div class="text-muted text-center" style="padding:16px"><i class="fa fa-spinner fa-spin"></i></div>
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
					"Pending":            "warning",
					"Escalated":          "warning",
					"Awaiting Approval":  "warning",
					"Approved":           "success",
					"Auto-Approved":      "success",
					"Rejected":           "danger",
					"Expired":            "secondary",
				}[r.status] || "default";
				const ref = (r.reference_doctype && r.reference_name)
					? `<a class="ch-sa-open-doc" data-dt="${frappe.utils.escape_html(r.reference_doctype)}" data-dn="${frappe.utils.escape_html(r.reference_name)}" style="cursor:pointer">${frappe.utils.escape_html(r.reference_name)}</a>`
					: "—";
				return `<tr>
					<td><a class="ch-sa-open-doc" data-dt="CH Exception Request" data-dn="${frappe.utils.escape_html(r.name)}" style="cursor:pointer">${frappe.utils.escape_html(r.name)}</a></td>
					<td>${ref}</td>
					<td>${frappe.utils.escape_html(r.requested_by_name || r.requested_by || "")}</td>
					<td class="text-right">${frappe.format(r.requested_value || 0, { fieldtype: "Currency" })}</td>
					<td class="text-right">${r.resolution_value ? frappe.format(r.resolution_value, { fieldtype: "Currency" }) : "—"}</td>
					<td><span class="badge badge-${status_cls}">${frappe.utils.escape_html(r.status || "")}</span></td>
					<td>${r.raised_at ? frappe.datetime.prettyDate(r.raised_at) : "—"}</td>
					<td>${r.resolved_at ? frappe.datetime.prettyDate(r.resolved_at) : "—"}</td>
				</tr>`;
			}).join("");
			body.find(".ch-sa-variance").html(`
				<div style="max-height:520px;overflow:auto">
					<table class="table table-condensed table-hover" style="font-size:13px;margin:0">
						<thead><tr>
							<th>${__("Request")}</th>
							<th>${__("Cycle Count")}</th>
							<th>${__("Raised By")}</th>
							<th class="text-right">${__("Variance ₹")}</th>
							<th class="text-right">${__("Resolved ₹")}</th>
							<th>${__("Status")}</th>
							<th>${__("Raised")}</th>
							<th>${__("Resolved")}</th>
						</tr></thead>
						<tbody>${rows || `<tr><td colspan="8" class="text-center text-muted" style="padding:20px">${__("No variance approvals for this warehouse.")}</td></tr>`}</tbody>
					</table>
				</div>`);
		}).catch((e) => body.find(".ch-sa-variance").html(this._empty(__("Could not load variance log: {0}", [e.message || e]))));
	}

	// ── Cycle-count dialogs (lifted from Reports for module separation) ──

	_start_count() {
		if (!PosState.pos_profile) {
			frappe.msgprint(__("No POS profile — cannot resolve this store's warehouse."));
			return;
		}
		const d = new frappe.ui.Dialog({
			title: __("Start Cycle Count"),
			fields: [
				{ fieldname: "class_filter", label: __("Count Class"), fieldtype: "Select",
				  options: "\nA\nB\nC", description: __("Leave blank to count all classes.") },
				{ fieldname: "only_due", label: __("Only items due for count"), fieldtype: "Check", default: 0 },
			],
			primary_action_label: __("Start"),
			primary_action: (values) => {
				d.hide();
				frappe.xcall("ch_pos.api.stock_report.start_store_cycle_count", {
					pos_profile: PosState.pos_profile,
					class_filter: values.class_filter || null,
					only_due: values.only_due ? 1 : 0,
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
				}).catch((e) => frappe.msgprint(__("Could not start count: {0}", [e.message || e])));
			},
		});
		d.show();
	}

	_open_count_sheet(res) {
		const lines = res.lines || [];
		const blind = !!res.blind_count;
		const scanned = {}; // item_code -> [serials]

		const body = lines.map((l) => {
			const name = frappe.utils.escape_html(l.item_name || l.item_code);
			if (l.is_serialized) {
				return `<tr>
					<td>${name}<div class="text-muted" style="font-size:0.75rem">${__("Serialized — scan each IMEI")}</div></td>
					<td>
						<input type="text" class="form-control input-sm cc-scan" data-item="${frappe.utils.escape_html(l.item_code)}"
							placeholder="${__("Scan IMEI, press Enter")}">
						<div class="cc-serials" data-item="${frappe.utils.escape_html(l.item_code)}" style="margin-top:4px"></div>
					</td></tr>`;
			}
			const hint = blind ? "" : `<div class="text-muted" style="font-size:0.75rem">${__("system")}: ${l.system_qty}</div>`;
			return `<tr>
				<td>${name}${hint}</td>
				<td><input type="number" min="0" class="form-control input-sm cc-qty"
					data-item="${frappe.utils.escape_html(l.item_code)}" placeholder="0"></td></tr>`;
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
						? { item_code: l.item_code, scanned_serials: (scanned[l.item_code] || []).join("\n") }
						: { item_code: l.item_code, counted_qty: qty_map[l.item_code] || 0 }
				);
				frappe.xcall("ch_pos.api.stock_report.submit_pos_count", {
					cycle_count: res.cycle_count,
					counts: JSON.stringify(counts),
				}).then((r) => {
					d.hide();
					const verified = r.status === "Completed - Verified";
					frappe.msgprint({
						title: verified ? __("Count Verified ✔") : __("Variance Sent for Approval"),
						indicator: verified ? "green" : "orange",
						message: verified
							? __("All counts match — {0} verified, last-verified updated.", [r.name])
							: __("Variance of {0} on {1} routed for approval (exception {2}). Reconciliation posts after approval.",
								[frappe.format(r.total_variance_value, { fieldtype: "Currency" }), r.name, r.variance_exception || "—"]),
					});
					this._refresh_kpis();
					this._switch_tab("history");
				}).catch((e) => frappe.msgprint(__("Submit failed: {0}", [e.message || e])));
			},
		});

		d.fields_dict.sheet.$wrapper.html(`
			<div class="text-muted" style="margin-bottom:8px">
				${__("Warehouse")}: <b>${frappe.utils.escape_html(res.warehouse)}</b> · ${res.items} ${__("item(s)")}
				${blind ? `· <span style="color:#d97706">${__("Blind count")}</span>` : ""}
			</div>
			<div style="max-height:420px;overflow:auto">
			<table class="table table-bordered" style="font-size:0.85rem">
				<thead><tr><th>${__("Item")}</th><th style="width:45%">${__("Counted")}</th></tr></thead>
				<tbody>${body}</tbody>
			</table></div>`);

		const render_chips = () => {
			d.$wrapper.find(".cc-serials").each(function () {
				const code = $(this).data("item");
				const list = scanned[code] || [];
				$(this).html(list.map((s, i) =>
					`<span class="badge" style="background:#e0e7ff;color:#3730a3;margin:2px;cursor:pointer"
						data-code="${frappe.utils.escape_html(code)}" data-idx="${i}">${frappe.utils.escape_html(s)} ✕</span>`
				).join(""));
			});
		};
		d.$wrapper.find(".cc-scan").on("keydown", function (e) {
			if (e.key !== "Enter") return;
			e.preventDefault();
			const code = $(this).data("item");
			const val = ($(this).val() || "").trim();
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

	// ── Helpers ─────────────────────────────────────────────────

	_empty(msg) {
		return `<div class="text-muted text-center" style="padding:24px">${msg}</div>`;
	}
}
