/**
 * CH POS — Reports / Store Dashboard Workspace
 *
 * Today's performance dashboard: KPIs, hourly sales chart,
 * top sellers, staff performance, inventory alerts.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class ReportsWorkspace {
	constructor() {
		const today = frappe.datetime.get_today();
		this._date = today;
		this._from_date = today;
		this._to_date = today;
		this._salesman = "";
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "reports") return;
			this.render(ctx.panel);
		});
	}

	_get_filters() {
		const today = frappe.datetime.get_today();
		let from_date = this._from_date || this._date || today;
		let to_date = this._to_date || this._date || from_date;
		if (from_date > to_date) {
			const tmp = from_date;
			from_date = to_date;
			to_date = tmp;
		}
		return {
			from_date,
			to_date,
			salesman: this._salesman || "",
		};
	}

	_range_label(filters = this._get_filters()) {
		const from_user = frappe.datetime.str_to_user(filters.from_date);
		const to_user = frappe.datetime.str_to_user(filters.to_date);
		return filters.from_date === filters.to_date ? from_user : `${from_user} - ${to_user}`;
	}

	_salesman_options() {
		const esc = frappe.utils.escape_html;
		const access = PosState.executive_access || {};
		const by_company = access.store_executives || {};
		const company = PosState.active_company;
		const pools = company && by_company[company] ? [by_company[company]] : Object.values(by_company);
		const seen = new Set();
		const execs = [];

		pools.forEach((pool) => {
			(pool || []).forEach((ex) => {
				if (!ex || !ex.name || seen.has(ex.name)) return;
				seen.add(ex.name);
				execs.push(ex);
			});
		});

		const selected = this._salesman || "";
		let options = `<option value="">${__("All Salesmen")}</option>`;
		execs.forEach((ex) => {
			const sel = ex.name === selected ? " selected" : "";
			const role = ex.role && ex.role !== "Executive" ? ` (${ex.role})` : "";
			options += `<option value="${esc(ex.name)}"${sel}>${esc(ex.executive_name || ex.name)}${esc(role)}</option>`;
		});
		if (selected && !seen.has(selected)) {
			options += `<option value="${esc(selected)}" selected>${esc(selected)}</option>`;
		}
		return options;
	}

	_salesman_label() {
		if (!this._salesman) return __("All Salesmen");
		const access = PosState.executive_access || {};
		const pools = Object.values(access.store_executives || {});
		for (const pool of pools) {
			const match = (pool || []).find((ex) => ex.name === this._salesman);
			if (match) return match.executive_name || match.name;
		}
		return this._salesman;
	}

	_download_csv() {
		const d = this._dashboard_data || {};
		const filters = this._get_filters();
		const range = this._range_label(filters);
		const avg = d.total_invoices ? (d.total_revenue / d.total_invoices) : 0;
		const rows = [
			[__("Store Dashboard"), range],
			[__("POS Profile"), PosState.pos_profile || ""],
			[__("Salesman"), this._salesman_label()],
			[],
			[__("Metric"), __("Value")],
			[__("Revenue"), d.total_revenue || 0],
			[__("Invoices"), d.total_invoices || 0],
			[__("Items Sold"), d.total_items_sold || 0],
			[__("Avg Basket"), Math.round(avg * 100) / 100],
			[__("Returns"), d.total_returns || 0],
			[__("Stock Value"), d.stock_value || 0],
			[__("Aging Stock (>90d)"), d.aging_stock_value || 0],
		];
		const csv = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
		const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		const suffix = this._salesman ? `-${this._salesman.replace(/[^a-z0-9_-]/gi, "_")}` : "";
		a.download = `store-dashboard-${filters.from_date}-${filters.to_date}${suffix}.csv`;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(url);
		frappe.show_alert({ message: __("Dashboard downloaded for {0}", [range]), indicator: "green" });
	}

	render(panel) {
		this._panel = panel;
		const today = frappe.datetime.get_today();
		const filters = this._get_filters();
		const from_date = filters.from_date;
		const to_date = filters.to_date;
		const salesman = filters.salesman;
		const pp = encodeURIComponent(PosState.pos_profile || "");
		const wh = encodeURIComponent(PosState.warehouse || "");
		const enc = (v) => encodeURIComponent(JSON.stringify(v));
		const posting_date_filter = enc(["between", [from_date, to_date]]);
		const creation_filter = enc(["between", [`${from_date} 00:00:00`, `${to_date} 23:59:59`]]);
		const salesman_invoice_filter = salesman ? `&custom_sales_executive=${encodeURIComponent(salesman)}` : "";
		const salesman_token_filter = salesman ? `&sales_executive=${encodeURIComponent(salesman)}` : "";

		/* KPI definitions — { cls, icon, color, bg, label, link? } */
		const footfall = [
{ cls: "walkins",   icon: "fa-sign-in",    color: "#4f46e5", bg: "#e0e7ff", label: __("Walk-ins"),   link: `/desk/pos-kiosk-token?pos_profile=${pp}&visit_source=Counter&creation=${creation_filter}&status=${enc(["!=", "Cancelled"])}${salesman_token_filter}` },
{ cls: "kiosk",     icon: "fa-tablet",     color: "#7c3aed", bg: "#f3e8ff", label: __("Kiosk"),      link: `/desk/pos-kiosk-token?pos_profile=${pp}&visit_source=Kiosk&creation=${creation_filter}&status=${enc(["!=", "Cancelled"])}${salesman_token_filter}` },
			{ cls: "conversion",icon: "fa-percent",    color: "#16a34a", bg: "#dcfce7", label: __("Conversion") },
{ cls: "repairs",   icon: "fa-wrench",     color: "#d97706", bg: "#fef3c7", label: __("Repairs"),    link: `/desk/pos-kiosk-token?pos_profile=${pp}&visit_purpose=Repair&creation=${creation_filter}${salesman_token_filter}` },
{ cls: "buybacks",  icon: "fa-exchange",   color: "#dc2626", bg: "#fef2f2", label: __("Buybacks"),   link: `/desk/pos-kiosk-token?pos_profile=${pp}&visit_purpose=Buyback&creation=${creation_filter}${salesman_token_filter}` },
{ cls: "cancelled", icon: "fa-ban",        color: "#ef4444", bg: "#fee2e2", label: __("Cancelled"),  link: `/desk/pos-kiosk-token?pos_profile=${pp}&status=Cancelled&creation=${creation_filter}${salesman_token_filter}` },
{ cls: "dropped",   icon: "fa-user-times", color: "#f97316", bg: "#fff7ed", label: __("Dropped"),    link: `/desk/pos-kiosk-token?pos_profile=${pp}&status=Dropped&creation=${creation_filter}${salesman_token_filter}` },
		];
		const sales = [
			{ cls: "revenue",    icon: "fa-inr",         color: "#2563eb", bg: "#dbeafe", label: __("Revenue"),    link: `/desk/sales-invoice?pos_profile=${pp}&posting_date=${posting_date_filter}&docstatus=1&is_return=0${salesman_invoice_filter}` },
			{ cls: "invoices",   icon: "fa-file-text-o", color: "#16a34a", bg: "#dcfce7", label: __("Invoices"),   link: `/desk/sales-invoice?pos_profile=${pp}&posting_date=${posting_date_filter}&docstatus=1&is_return=0${salesman_invoice_filter}` },
			{ cls: "items-sold", icon: "fa-shopping-bag", color: "#d97706", bg: "#fef3c7", label: __("Items Sold") },
			{ cls: "avg-basket", icon: "fa-calculator",  color: "#4f46e5", bg: "#e0e7ff", label: __("Avg Basket") },
			{ cls: "returns",    icon: "fa-undo",        color: "#dc2626", bg: "#fef2f2", label: __("Returns"),    link: `/desk/sales-invoice?pos_profile=${pp}&posting_date=${posting_date_filter}&docstatus=1&is_return=1${salesman_invoice_filter}` },
		];
		// Stock-value KPIs — populated from store_dashboard response (warehouse-level totals).
		const stock = [
			{ cls: "stock-value",       icon: "fa-cubes",   color: "#0d9488", bg: "#ccfbf1", label: __("Stock Value") },
			{ cls: "aging-stock-value", icon: "fa-hourglass-half", color: "#b45309", bg: "#fef3c7", label: __("Aging Stock (>90d)") },
		];

		const kpiCard = (k, def) => {
			const clickable = k.link ? " ch-rpt-kpi--link" : "";
			const href = k.link ? ` data-href="${frappe.utils.escape_html(k.link)}"` : "";
			const labelEsc = frappe.utils.escape_html(k.label);
			return `<div class="ch-rpt-kpi${clickable}"${href} title="${labelEsc}">
				<div class="ch-rpt-kpi-accent" style="background:${k.color}"></div>
				<div class="ch-rpt-kpi-body">
					<div class="ch-rpt-kpi-icon" style="background:${k.bg};color:${k.color}"><i class="fa ${k.icon}"></i></div>
					<div class="ch-rpt-kpi-info">
						<div class="ch-rpt-kpi-value ch-rpt-${k.cls}">${def || "0"}</div>
						<div class="ch-rpt-kpi-label" title="${labelEsc}">${k.label}</div>
					</div>
				</div>
			</div>`;
		};

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-rpt-header">
					<div class="ch-mode-header" style="margin-bottom:0">
						<h4>
							<span class="mode-icon" style="background:#e0e7ff;color:#4f46e5">
								<i class="fa fa-bar-chart"></i>
							</span>
							${__("Store Dashboard")}
						</h4>
						<span class="ch-mode-hint">${__("Performance for selected period")}</span>
					</div>
					<div class="ch-rpt-header-actions">
						<button class="btn btn-sm btn-default ch-rpt-z-report"
							title="${__("End-of-day store summary across all sessions")}">
							<i class="fa fa-file-text"></i> ${__("Z Report")}
						</button>
						<button class="btn btn-sm btn-default ch-rpt-stock-audit"
							title="${__("Open the Stock Audit workspace \u2014 stock report, cycle count, count history, variance approvals.")}">
							<i class="fa fa-balance-scale"></i> ${__("Stock Audit")}
						</button>
						<span class="ch-rpt-filter-group" title="${__("From Date")}">
							<i class="fa fa-calendar-o"></i>
							<input type="date" class="form-control input-sm ch-rpt-from-date"
								value="${from_date}" max="${today}">
						</span>
						<span class="ch-rpt-filter-group" title="${__("To Date")}">
							<i class="fa fa-calendar-check-o"></i>
							<input type="date" class="form-control input-sm ch-rpt-to-date"
								value="${to_date}" max="${today}">
						</span>
						<select class="form-control input-sm ch-rpt-salesman"
							title="${__("Filter by Salesman")}">
							${this._salesman_options()}
						</select>
						<button class="btn btn-sm btn-default ch-rpt-download"
							title="${__("Download this summary as CSV")}">
							<i class="fa fa-download"></i> ${__("Download")}
						</button>
						<button class="btn btn-sm btn-default ch-rpt-refresh">
							<i class="fa fa-refresh"></i> ${__("Refresh")}
						</button>
					</div>
				</div>

				<div class="ch-rpt-loading">
					<i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.3"></i>
				</div>

				<div class="ch-rpt-content" style="display:none;">
					<div class="ch-rpt-kpi-row">
						${footfall.map(k => kpiCard(k)).join("")}
					</div>
					<div class="ch-rpt-kpi-row">
						${sales.map(k => kpiCard(k, k.cls === "revenue" || k.cls === "avg-basket" ? "₹0" : "0")).join("")}
					</div>
					<div class="ch-rpt-kpi-row">
						${stock.map(k => kpiCard(k, "₹0")).join("")}
					</div>

					<div class="ch-rpt-section ch-rpt-ai">
						<div class="ch-rpt-section-head">
							<span>
								<span class="ch-rpt-ai-badge">AI</span>
								${__("Insights & Recommendations")}
								<span class="ch-rpt-ai-sub">${__("Live from your data")}</span>
							</span>
							<button class="btn btn-xs btn-default ch-rpt-ai-refresh" title="${__("Re-generate insights")}">
								<i class="fa fa-refresh"></i> ${__("Refresh")}
							</button>
						</div>
						<div class="ch-rpt-section-body ch-rpt-ai-body">
							<div class="ch-rpt-ai-loading">
								<i class="fa fa-spinner fa-spin"></i> ${__("Generating insights…")}
							</div>
							<div class="ch-rpt-ai-list" style="display:none"></div>
						</div>
					</div>

					<div class="ch-rpt-section">
						<div class="ch-rpt-section-head"><i class="fa fa-area-chart"></i> ${__("Hourly Sales")}</div>
						<div class="ch-rpt-section-body">
							<div class="ch-rpt-chart" data-chart="hourly"></div>
							<div class="ch-rpt-chart-empty" style="display:none;">
								<div class="ch-rpt-empty">
									<i class="fa fa-bar-chart"></i>
									<span>${__("No sales recorded for this period")}</span>
								</div>
							</div>
						</div>
					</div>

					<div class="ch-rpt-two-col">
						<div class="ch-rpt-section">
							<div class="ch-rpt-section-head"><i class="fa fa-trophy"></i> ${__("Top Sellers")}</div>
							<div class="ch-rpt-section-body ch-rpt-section-body--flush">
								<table class="ch-rpt-table">
									<thead><tr>
										<th style="width:32px">#</th>
										<th>${__("Item")}</th>
										<th class="text-right">${__("Qty")}</th>
										<th class="text-right">${__("Revenue")}</th>
									</tr></thead>
									<tbody class="ch-rpt-top-items"></tbody>
								</table>
							</div>
						</div>
						<div class="ch-rpt-section">
							<div class="ch-rpt-section-head"><i class="fa fa-users"></i> ${__("Staff")}</div>
							<div class="ch-rpt-section-body ch-rpt-section-body--flush">
								<table class="ch-rpt-table">
									<thead><tr>
										<th>${__("Salesman")}</th>
										<th class="text-right">${__("Bills")}</th>
										<th class="text-right">${__("Revenue")}</th>
									</tr></thead>
									<tbody class="ch-rpt-staff"></tbody>
								</table>
							</div>
						</div>
					</div>

					<div class="ch-rpt-section">
						<div class="ch-rpt-section-head"><i class="fa fa-exclamation-triangle"></i> ${__("Low Stock Alerts")}</div>
						<div class="ch-rpt-section-body">
							<div class="ch-rpt-inventory-grid"></div>
						</div>
					</div>

					<div class="ch-rpt-two-col">
						<div class="ch-rpt-section">
							<div class="ch-rpt-section-head">
								<span><i class="fa fa-clipboard"></i> ${__("Material Requests")}</span>
								<a class="ch-rpt-view-all" href="/desk/material-request?set_warehouse=${wh}" target="_blank">${__("View All")} →</a>
							</div>
							<div class="ch-rpt-section-body ch-rpt-section-body--flush">
								<div class="ch-rpt-mr-list"></div>
							</div>
						</div>
						<div class="ch-rpt-section">
							<div class="ch-rpt-section-head">
								<span><i class="fa fa-truck"></i> ${__("Stock Transfers")}</span>
								<a class="ch-rpt-view-all" href="/app/stock-entry?stock_entry_type=Material+Transfer&from_warehouse=${wh}" target="_blank">${__("View All")} →</a>
							</div>
							<div class="ch-rpt-section-body ch-rpt-section-body--flush">
								<div class="ch-rpt-st-list"></div>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);

		this._bind(panel);
		this._load_data(panel);
	}

	_bind(panel) {
		panel.off(".chReports");
		panel.on("click.chReports", ".ch-rpt-refresh", () => {
			panel.find(".ch-rpt-content").hide();
			panel.find(".ch-rpt-loading").show();
			this._load_data(panel);
		});
		panel.on("change.chReports", ".ch-rpt-from-date, .ch-rpt-to-date", (e) => {
			const today = frappe.datetime.get_today();
			let from_date = panel.find(".ch-rpt-from-date").val() || today;
			let to_date = panel.find(".ch-rpt-to-date").val() || from_date;
			if (from_date > to_date) {
				if ($(e.currentTarget).hasClass("ch-rpt-from-date")) {
					to_date = from_date;
				} else {
					from_date = to_date;
				}
			}
			this._from_date = from_date;
			this._to_date = to_date;
			this._date = to_date;
			this.render(panel);
		});
		panel.on("change.chReports", ".ch-rpt-salesman", (e) => {
			this._salesman = $(e.currentTarget).val() || "";
			this.render(panel);
		});
		panel.on("click.chReports", ".ch-rpt-download", () => this._download_csv());
		panel.on("click.chReports", ".ch-rpt-z-report", () => this._show_z_report());
		panel.on("click.chReports", ".ch-rpt-stock-audit", () => EventBus.emit("mode:switch", "stock_audit"));

		// Store Insights — refresh + open referenced document
		panel.on("click.chReports", ".ch-rpt-ai-refresh", () => this._load_ai_insights(panel, true));
		panel.on("click.chReports", ".ch-rpt-ai-card[data-href]", function (e) {
			if ($(e.target).closest("a,button").length) return;
			window.open($(this).attr("data-href"), "_blank");
		});
		panel.on("click.chReports", ".ch-rpt-ai-card [data-action='open-ref']", function (e) {
			e.stopPropagation();
			const dt = $(this).attr("data-dt"), dn = $(this).attr("data-dn");
			if (dt && dn) window.open(`/desk/${frappe.router.slug(dt)}/${encodeURIComponent(dn)}`, "_blank");
		});

		// KPI cards → open filtered list in new tab
		panel.on("click.chReports", ".ch-rpt-kpi--link", function () {
			const href = $(this).attr("data-href");
			if (href) window.open(href, "_blank");
		});
		// MR / STE / inventory pill clicks
		panel.on("click.chReports", ".ch-rpt-doc-row[data-href]", function () {
			window.open($(this).attr("data-href"), "_blank");
		});
		panel.on("click.chReports", ".ch-rpt-inv-pill[data-item]", function () {
			window.open(`/desk/item/${encodeURIComponent($(this).attr("data-item"))}`, "_blank");
		});

		if (!this._walkin_bound) {
			this._walkin_bound = true;
			EventBus.on("walkin:logged", (d) => {
				const current_panel = this._panel;
				const filters = this._get_filters();
				const today = frappe.datetime.get_today();
				if (!current_panel || filters.from_date !== today || filters.to_date !== today || filters.salesman) return;
				current_panel.find(".ch-rpt-walkins").text(d.walkin_count || 0);
				current_panel.find(".ch-rpt-kiosk").text(d.kiosk_count || 0);
			});
		}
	}

	_load_data(panel) {
		const filters = this._get_filters();
		this._load_ai_insights(panel, false);

		frappe.call({
			method: "ch_pos.api.pos_api.get_today_footfall",
			args: {
				pos_profile: PosState.pos_profile,
				from_date: filters.from_date,
				to_date: filters.to_date,
				salesman: filters.salesman,
			},
			callback: (r) => {
				const f = r.message || {};
				// Hide kiosk KPI tile entirely when this profile is not kiosk-enabled.
				if (!parseInt(f.kiosk_enabled, 10)) {
					panel.find(".ch-rpt-kpi:has(.ch-rpt-kiosk)").hide();
				} else {
					panel.find(".ch-rpt-kpi:has(.ch-rpt-kiosk)").show();
				}
				panel.find(".ch-rpt-walkins").text(f.walkin_count || 0);
				panel.find(".ch-rpt-kiosk").text(f.kiosk_count || 0);
				panel.find(".ch-rpt-conversion").text((f.conversion_pct || 0) + "%");
				panel.find(".ch-rpt-repairs").text(f.repair_intake_count || 0);
				panel.find(".ch-rpt-buybacks").text(f.buyback_count || 0);
				panel.find(".ch-rpt-cancelled").text(f.cancelled_count || 0);
				panel.find(".ch-rpt-dropped").text(f.dropped_count || 0);
			},
		});

		frappe.call({
			method: "ch_pos.api.pos_api.store_dashboard",
			args: {
				pos_profile: PosState.pos_profile,
				from_date: filters.from_date,
				to_date: filters.to_date,
				salesman: filters.salesman,
			},
			callback: (r) => {
				panel.find(".ch-rpt-loading").hide();
				const content = panel.find(".ch-rpt-content");
				content.show();
				const d = r.message || {};
				this._dashboard_data = d;  // cached for CSV download

				// KPIs
				content.find(".ch-rpt-revenue").text(`₹${format_number(d.total_revenue || 0)}`);
				content.find(".ch-rpt-invoices").text(d.total_invoices || 0);
				content.find(".ch-rpt-items-sold").text(d.total_items_sold || 0);
				const avg = d.total_invoices ? (d.total_revenue / d.total_invoices) : 0;
				content.find(".ch-rpt-avg-basket").text(`₹${format_number(avg)}`);
				content.find(".ch-rpt-returns").text(d.total_returns || 0);

				// Stock-value KPIs (warehouse total + aged > 90 days)
				content.find(".ch-rpt-stock-value").text(`₹${format_number(d.stock_value || 0)}`);
				content.find(".ch-rpt-aging-stock-value").text(`₹${format_number(d.aging_stock_value || 0)}`);
				if (d.warehouse) {
					const stock_href = `/desk/bin?warehouse=${encodeURIComponent(d.warehouse)}&actual_qty=${enc([">", 0])}`;
					content.find(".ch-rpt-kpi:has(.ch-rpt-stock-value)").attr("data-href", stock_href).addClass("ch-rpt-kpi--link");
					content.find(".ch-rpt-kpi:has(.ch-rpt-aging-stock-value)").attr("data-href", stock_href).addClass("ch-rpt-kpi--link");
				}

				// Hourly chart
				const hourly = d.hourly_sales || [];
				const chart_el = content.find('.ch-rpt-chart[data-chart="hourly"]');
				const chart_empty = content.find(".ch-rpt-chart-empty");
				if (hourly.length) {
					const max_rev = Math.max(...hourly.map((h) => h.revenue));
					let bars = "";
					for (let hr = 9; hr <= 21; hr++) {
						const match = hourly.find((h) => h.hour === hr);
						const rev = match ? match.revenue : 0;
						const cnt = match ? match.count : 0;
						const pct = max_rev > 0 ? Math.max((rev / max_rev) * 100, 2) : 2;
						const label = hr > 12 ? `${hr - 12}p` : hr === 12 ? "12p" : `${hr}a`;
						bars += `<div class="ch-rpt-bar-col" title="${label}: ₹${format_number(rev)} (${cnt} bills)">
							<div class="ch-rpt-bar" style="height:${pct}%"></div>
							<span class="ch-rpt-bar-label">${label}</span>
						</div>`;
					}
					chart_el.html(bars).show();
					chart_empty.hide();
				} else {
					chart_el.hide();
					chart_empty.show();
				}

				// Top items
				const top_body = content.find(".ch-rpt-top-items").empty();
				(d.top_items || []).forEach((item, idx) => {
					const medal = idx === 0 ? "🥇" : idx === 1 ? "🥈" : idx === 2 ? "🥉" : (idx + 1);
					top_body.append(`<tr>
						<td style="width:32px;text-align:center">${medal}</td>
						<td>${frappe.utils.escape_html(item.item_name)}</td>
						<td class="text-right">${item.qty}</td>
						<td class="text-right" style="font-weight:600">₹${format_number(item.revenue)}</td>
					</tr>`);
				});
				if (!(d.top_items || []).length) {
					top_body.append(`<tr><td colspan="4" class="text-muted text-center" style="padding:20px">${__("No sales for this period")}</td></tr>`);
				}

				// Staff
				const staff_body = content.find(".ch-rpt-staff").empty();
				(d.staff_performance || []).forEach((s) => {
					staff_body.append(`<tr>
						<td>${frappe.utils.escape_html(s.cashier)}</td>
						<td class="text-right">${s.invoices}</td>
						<td class="text-right" style="font-weight:600">₹${format_number(s.revenue)}</td>
					</tr>`);
				});
				if (!(d.staff_performance || []).length) {
					staff_body.append(`<tr><td colspan="3" class="text-muted text-center" style="padding:20px">${__("No staff data")}</td></tr>`);
				}

				// Inventory alerts — clickable pills
				const inv_grid = content.find(".ch-rpt-inventory-grid").empty();
				(d.inventory_alerts || []).forEach((item) => {
					const is_oos = item.qty <= 0;
					const cls = is_oos ? "ch-rpt-inv-pill--oos" : "ch-rpt-inv-pill--low";
					const item_code = item.item_code || item.item_name;
					inv_grid.append(`<span class="ch-rpt-inv-pill ${cls}" data-item="${frappe.utils.escape_html(item_code)}">
						${frappe.utils.escape_html(item.item_name)} <b>${Math.floor(item.qty)}</b>
					</span>`);
				});
				if (!(d.inventory_alerts || []).length) {
					inv_grid.html(`<div class="ch-rpt-empty">
						<i class="fa fa-check-circle" style="color:#16a34a"></i>
						<span>${__("All stock levels healthy")}</span>
					</div>`);
				}

				// Material Requests — clickable rows
				const mr_list = content.find(".ch-rpt-mr-list").empty();
				(d.material_requests || []).forEach((mr) => {
					const cls = mr.status === "Pending" ? "ch-rpt-badge--warning"
						: mr.status === "Partially Ordered" ? "ch-rpt-badge--info"
						: mr.status === "Ordered" || mr.status === "Transferred" ? "ch-rpt-badge--success"
						: "ch-rpt-badge--muted";
					mr_list.append(`
						<div class="ch-rpt-doc-row" data-href="/desk/material-request/${encodeURIComponent(mr.name)}">
							<div>
								<div class="ch-rpt-doc-id">${frappe.utils.escape_html(mr.name)}</div>
								<div class="ch-rpt-doc-meta">
									${frappe.datetime.str_to_user(mr.transaction_date)} · ${mr.item_count} ${__("items")}
								</div>
							</div>
							<span class="ch-rpt-badge ${cls}">${frappe.utils.escape_html(mr.status)}</span>
						</div>`);
				});
				if (!(d.material_requests || []).length) {
					mr_list.html(`<div class="ch-rpt-empty" style="padding:20px">${__("No pending requests")}</div>`);
				}

				// Stock Transfers — clickable rows
				const st_list = content.find(".ch-rpt-st-list").empty();
				(d.stock_transfers || []).forEach((se) => {
					const status_label = se.docstatus === 0 ? __("Draft") : __("Completed");
					const cls = se.docstatus === 0 ? "ch-rpt-badge--warning" : "ch-rpt-badge--success";
					// TC_042 / TC_043 — show From → To warehouse pair and the
					// primary item name (or "+N more" when a transfer has
					// multiple lines) instead of the bare item count.
					const from_wh = frappe.utils.escape_html(se.from_warehouse || "—");
					const to_wh = frappe.utils.escape_html(se.to_warehouse || "—");
					const item_count = se.item_count || 0;
					const primary = se.primary_item_name || se.primary_item_code || "";
					const item_label = primary
						? (item_count > 1
							? `${frappe.utils.escape_html(primary)} <span class="text-muted">+${item_count - 1} ${__("more")}</span>`
							: frappe.utils.escape_html(primary))
						: `${item_count} ${__("items")}`;
					st_list.append(`
						<div class="ch-rpt-doc-row" data-href="/app/stock-entry/${encodeURIComponent(se.name)}">
							<div>
								<div class="ch-rpt-doc-id">${frappe.utils.escape_html(se.name)}</div>
								<div class="ch-rpt-doc-meta">
									${frappe.datetime.str_to_user(se.posting_date)} · ${item_label}
								</div>
								<div class="ch-rpt-doc-meta" style="margin-top:2px;font-size:11px">
									<i class="fa fa-arrow-right" style="opacity:0.5"></i>
									${from_wh} → ${to_wh}
								</div>
							</div>
							<span class="ch-rpt-badge ${cls}">${status_label}</span>
						</div>`);
				});
				if (!(d.stock_transfers || []).length) {
					st_list.html(`<div class="ch-rpt-empty" style="padding:20px">${__("No recent transfers")}</div>`);
				}
			},
		});
	}

	_load_ai_insights(panel, force) {
		const section = panel.find(".ch-rpt-ai");
		if (!section.length) return;
		const filters = this._get_filters();
		const loading = section.find(".ch-rpt-ai-loading");
		const list = section.find(".ch-rpt-ai-list");
		loading.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Analysing your store…")}`).show();
		list.hide();

		frappe.call({
			method: "ch_pos.api.store_insights.store_insights",
			args: {
				pos_profile: PosState.pos_profile,
				from_date: filters.from_date,
				to_date: filters.to_date,
				salesman: filters.salesman,
			},
			callback: (r) => {
				const data = (r && r.message) || {};
				const cards = data.insights || [];
				loading.hide();
				list.show().empty();

				// Stamp "as of HH:MM" next to the section sub-heading.
				if (data.generated_on) {
					section.find(".ch-rpt-ai-sub").text(
						__("Live from your data · as of {0}", [data.generated_on])
					);
				}

				if (!cards.length) {
					list.html(`<div class="ch-rpt-ai-allgood">
						<i class="fa fa-check-circle"></i>
						<div>
							<strong>${__("Everything looks healthy")}</strong>
							<span>${__("No issues need your attention right now.")}</span>
						</div>
					</div>`);
					return;
				}

				const esc = frappe.utils.escape_html;
				cards.forEach((c) => {
					const sev = c.severity || "Info";
					const sevCls = "ch-rpt-ai-sev--" + sev.toLowerCase();
					const icon = c.icon || "fa-info-circle";
					const metric = c.metric
						? `<span class="ch-rpt-ai-metric">${esc(c.metric)}</span>` : "";
					const action = (c.ref_doctype && c.ref_name)
						? `<button class="btn btn-xs btn-default" data-action="open-ref" data-dt="${esc(c.ref_doctype)}" data-dn="${esc(c.ref_name)}"><i class="fa fa-external-link"></i> ${__("Open")} ${esc(c.ref_name)}</button>`
						: (c.href ? `<span class="ch-rpt-ai-hint">${__("Click to view")} →</span>` : "");

					const card = $(`
						<div class="ch-rpt-ai-card ${sevCls}">
							<div class="ch-rpt-ai-ico"><i class="fa ${esc(icon)}"></i></div>
							<div class="ch-rpt-ai-main">
								<div class="ch-rpt-ai-card-head">
									<span class="ch-rpt-ai-sev">${esc(sev)}</span>
									<strong class="ch-rpt-ai-title">${esc(c.title || "")}</strong>
									${metric}
								</div>
								<div class="ch-rpt-ai-detail">${esc(c.detail || "")}</div>
								${action ? `<div class="ch-rpt-ai-footer">${action}</div>` : ""}
							</div>
						</div>
					`);
					if (c.href) card.attr("data-href", c.href);
					list.append(card);
				});
			},
			error: () => {
				loading.html(`<div class="ch-rpt-empty" style="padding:16px;color:#dc2626"><i class="fa fa-exclamation-triangle"></i> ${__("Unable to load insights")}</div>`);
			},
		});
	}

	_start_store_count() {
		if (!PosState.pos_profile) {
			frappe.msgprint(__("No POS profile — cannot resolve this store's warehouse."));
			return;
		}
		const d = new frappe.ui.Dialog({
			title: __("Start Cycle Count"),
			fields: [
				{
					fieldname: "class_filter",
					label: __("Count Class"),
					fieldtype: "Select",
					options: "\nA\nB\nC",
					description: __("Leave blank to count all classes."),
				},
				{
					fieldname: "only_due",
					label: __("Only items due for count"),
					fieldtype: "Check",
					default: 0,
				},
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

	_show_store_stock() {
		if (!PosState.pos_profile) {
			frappe.msgprint(__("No POS profile — cannot resolve this store's warehouse."));
			return;
		}
		frappe.xcall("ch_pos.api.stock_report.get_store_stock_report", {
			pos_profile: PosState.pos_profile,
		}).then((d) => {
			const rows = (d.rows || []).map((r) => {
				const due_badge = r.due
					? `<span class="badge" style="background:#dc2626;color:#fff">${__("Due")}</span>`
					: "";
				const last = r.last_verified
					? frappe.datetime.str_to_user(r.last_verified)
					: `<span class="text-muted">${__("Never")}</span>`;
				const since = (r.days_since_count || r.days_since_count === 0)
					? `${r.days_since_count}d`
					: "—";
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

			const s = d.summary || {};
			frappe.msgprint({
				title: __("Store Stock — {0}", [d.warehouse]),
				wide: true,
				message: `
				<div style="font-size:0.9rem">
					<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px">
						<div style="background:#eff6ff;border-radius:6px;padding:10px;text-align:center">
							<div style="font-size:1.2rem;font-weight:600">${s.items || 0}</div>
							<div class="text-muted">${__("Items")}</div></div>
						<div style="background:#fef2f2;border-radius:6px;padding:10px;text-align:center">
							<div style="font-size:1.2rem;font-weight:600;color:#dc2626">${s.due_for_count || 0}</div>
							<div class="text-muted">${__("Due for Count")}</div></div>
						<div style="background:#f0fdf4;border-radius:6px;padding:10px;text-align:center">
							<div style="font-size:1.2rem;font-weight:600">${frappe.format(s.total_stock_value || 0, { fieldtype: "Currency" })}</div>
							<div class="text-muted">${__("Stock Value")}</div></div>
					</div>
					<div style="max-height:380px;overflow:auto">
					<table class="table table-bordered" style="font-size:0.82rem">
						<thead><tr>
							<th>${__("Item")}</th><th class="text-right">${__("On Hand")}</th>
							<th class="text-right">${__("Value")}</th><th class="text-center">${__("Class")}</th>
							<th>${__("Last Verified")}</th><th class="text-center">${__("Since")}</th>
							<th class="text-center">${__("Due?")}</th>
						</tr></thead>
						<tbody>${rows || `<tr><td colspan="7" class="text-center text-muted">${__("No stock on hand")}</td></tr>`}</tbody>
					</table>
					</div>
				</div>`,
			});
		}).catch((e) => frappe.msgprint(__("Could not load store stock: {0}", [e.message || e])));
	}

	_show_z_report() {
		const store = PosState.store;
		const business_date = PosState.business_date;
		if (!store || !business_date) {
			frappe.msgprint(__("No active session — store and business date required for Z Report."));
			return;
		}

		frappe.xcall("ch_pos.api.session_api.get_z_report", {
			store,
			business_date,
		}).then((d) => {
			const status_badge = d.all_sessions_closed
				? `<span class="badge" style="background:#16a34a;color:#fff">✔ ${__("All Sessions Closed")}</span>`
				: `<span class="badge" style="background:#d97706;color:#fff">⚠ ${__("Shift(s) Still Open")}</span>`;

			// Payment modes table
			const payment_rows = (d.payment_modes || [])
				.map((p) => {
					const cls = flt(p.total) < 0 ? "color:#dc2626" : "";
					return `<tr>
						<td>${frappe.utils.escape_html(p.mode)}</td>
						<td class="text-right" style="${cls}">${frappe.format(p.total, { fieldtype: "Currency" })}</td>
					</tr>`;
				})
				.join("");

			// Sessions breakdown table
			const session_rows = (d.sessions || []).map((s) => {
				const status_cls = s.status === "Closed" ? "color:#16a34a" : "color:#d97706";
				const shift_end = s.shift_end ? frappe.datetime.str_to_user(s.shift_end) : __("Open");
				const variance = flt(s.cash_variance);
				const var_cls = variance === 0 ? "" : variance > 0 ? "color:#16a34a" : "color:#dc2626";
				return `<tr>
					<td style="font-size:0.8rem">${frappe.utils.escape_html(s.name)}</td>
					<td>${frappe.utils.escape_html(s.user || "")}</td>
					<td style="${status_cls}">${frappe.utils.escape_html(s.status)}</td>
					<td class="text-right">${s.total_invoices || 0}</td>
					<td class="text-right">${frappe.format(s.net_sales || 0, { fieldtype: "Currency" })}</td>
					<td class="text-right" style="${var_cls}">${frappe.format(variance, { fieldtype: "Currency" })}</td>
				</tr>`;
			}).join("");

			frappe.msgprint({
				title: __("Z Report — {0}", [d.business_date]),
				message: `
				<div style="font-size:0.9rem">
					<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
						<div>
							<strong>${frappe.utils.escape_html(d.store)}</strong>
							<span class="text-muted" style="margin-left:8px">${d.business_date}</span>
						</div>
						${status_badge}
					</div>

					<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px">
						<div style="background:#f0fdf4;border-radius:6px;padding:10px;text-align:center">
							<div style="font-size:1.2rem;font-weight:700;color:#16a34a">
								${frappe.format(d.total_net_sales, { fieldtype: "Currency" })}
							</div>
							<div class="text-muted" style="font-size:0.78rem">${__("Net Sales")}</div>
						</div>
						<div style="background:#eff6ff;border-radius:6px;padding:10px;text-align:center">
							<div style="font-size:1.2rem;font-weight:700;color:#2563eb">${d.total_invoices}</div>
							<div class="text-muted" style="font-size:0.78rem">${__("Invoices")}</div>
						</div>
						<div style="background:#fefce8;border-radius:6px;padding:10px;text-align:center">
							<div style="font-size:1.2rem;font-weight:700;color:#d97706">${d.total_sessions}</div>
							<div class="text-muted" style="font-size:0.78rem">${__("Sessions")}</div>
						</div>
						<div style="background:${flt(d.total_variance) !== 0 ? "#fef2f2" : "#f0fdf4"};border-radius:6px;padding:10px;text-align:center">
							<div style="font-size:1.2rem;font-weight:700;color:${flt(d.total_variance) !== 0 ? "#dc2626" : "#16a34a"}">
								${frappe.format(d.total_variance, { fieldtype: "Currency" })}
							</div>
							<div class="text-muted" style="font-size:0.78rem">${__("Cash Variance")}</div>
						</div>
					</div>

					<h6 style="margin-bottom:6px">${__("Payment Modes")}</h6>
					<table class="table table-sm table-bordered" style="margin-bottom:16px">
						<thead><tr>
							<th>${__("Mode")}</th>
							<th class="text-right">${__("Total")}</th>
						</tr></thead>
						<tbody>${payment_rows || `<tr><td colspan="2" class="text-muted text-center">${__("No payments")}</td></tr>`}</tbody>
					</table>

					<h6 style="margin-bottom:6px">${__("Sessions Breakdown")}</h6>
					<table class="table table-sm table-bordered">
						<thead><tr>
							<th>${__("Session")}</th>
							<th>${__("Cashier")}</th>
							<th>${__("Status")}</th>
							<th class="text-right">${__("Bills")}</th>
							<th class="text-right">${__("Net Sales")}</th>
							<th class="text-right">${__("Variance")}</th>
						</tr></thead>
						<tbody>${session_rows || `<tr><td colspan="6" class="text-muted text-center">${__("No sessions found")}</td></tr>`}</tbody>
					</table>
				</div>`,
				wide: true,
			});
		}).catch((err) => {
			frappe.msgprint({
				title: __("Z Report Error"),
				message: err.message || err.exc || __("Failed to load Z Report"),
				indicator: "red",
			});
		});
	}
}
