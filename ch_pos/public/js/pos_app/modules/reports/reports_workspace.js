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
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "reports") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		const today = frappe.datetime.get_today();
		const pp = encodeURIComponent(PosState.pos_profile || "");
		const wh = encodeURIComponent(PosState.warehouse || "");
		const enc = (v) => encodeURIComponent(JSON.stringify(v));

		/* KPI definitions — { cls, icon, color, bg, label, link? } */
		const footfall = [
{ cls: "walkins",   icon: "fa-sign-in",    color: "#4f46e5", bg: "#e0e7ff", label: __("Walk-ins"),   link: `/app/pos-kiosk-token?pos_profile=${pp}&visit_source=Counter&creation=${enc([">", today])}&status=${enc(["!=", "Cancelled"])}` },
{ cls: "kiosk",     icon: "fa-tablet",     color: "#7c3aed", bg: "#f3e8ff", label: __("Kiosk"),      link: `/app/pos-kiosk-token?pos_profile=${pp}&visit_source=Kiosk&creation=${enc([">", today])}&status=${enc(["!=", "Cancelled"])}` },
			{ cls: "conversion",icon: "fa-percent",    color: "#16a34a", bg: "#dcfce7", label: __("Conversion") },
{ cls: "repairs",   icon: "fa-wrench",     color: "#d97706", bg: "#fef3c7", label: __("Repairs"),    link: `/app/pos-kiosk-token?pos_profile=${pp}&visit_purpose=Repair&creation=${enc([">", today])}` },
{ cls: "buybacks",  icon: "fa-exchange",   color: "#dc2626", bg: "#fef2f2", label: __("Buybacks"),   link: `/app/pos-kiosk-token?pos_profile=${pp}&visit_purpose=Buyback&creation=${enc([">", today])}` },
{ cls: "cancelled", icon: "fa-ban",        color: "#ef4444", bg: "#fee2e2", label: __("Cancelled"),  link: `/app/pos-kiosk-token?pos_profile=${pp}&status=Cancelled&creation=${enc([">", today])}` },
{ cls: "dropped",   icon: "fa-user-times", color: "#f97316", bg: "#fff7ed", label: __("Dropped"),    link: `/app/pos-kiosk-token?pos_profile=${pp}&status=Dropped&creation=${enc([">", today])}` },
		];
		const sales = [
			{ cls: "revenue",    icon: "fa-inr",         color: "#2563eb", bg: "#dbeafe", label: __("Revenue"),    link: `/app/sales-invoice?pos_profile=${pp}&posting_date=${today}&docstatus=1&is_return=0` },
			{ cls: "invoices",   icon: "fa-file-text-o", color: "#16a34a", bg: "#dcfce7", label: __("Invoices"),   link: `/app/sales-invoice?pos_profile=${pp}&posting_date=${today}&docstatus=1&is_return=0` },
			{ cls: "items-sold", icon: "fa-shopping-bag", color: "#d97706", bg: "#fef3c7", label: __("Items Sold") },
			{ cls: "avg-basket", icon: "fa-calculator",  color: "#4f46e5", bg: "#e0e7ff", label: __("Avg Basket") },
			{ cls: "returns",    icon: "fa-undo",        color: "#dc2626", bg: "#fef2f2", label: __("Returns"),    link: `/app/sales-invoice?pos_profile=${pp}&posting_date=${today}&docstatus=1&is_return=1` },
		];

		const kpiCard = (k, def) => {
			const clickable = k.link ? " ch-rpt-kpi--link" : "";
			const href = k.link ? ` data-href="${frappe.utils.escape_html(k.link)}"` : "";
			return `<div class="ch-rpt-kpi${clickable}"${href}>
				<div class="ch-rpt-kpi-accent" style="background:${k.color}"></div>
				<div class="ch-rpt-kpi-body">
					<div class="ch-rpt-kpi-icon" style="background:${k.bg};color:${k.color}"><i class="fa ${k.icon}"></i></div>
					<div class="ch-rpt-kpi-info">
						<div class="ch-rpt-kpi-value ch-rpt-${k.cls}">${def || "0"}</div>
						<div class="ch-rpt-kpi-label">${k.label}</div>
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
						<span class="ch-mode-hint">${__("Today's performance at a glance")}</span>
					</div>
					<div class="ch-rpt-header-actions">
						<button class="btn btn-sm btn-default ch-rpt-z-report"
							title="${__("End-of-day store summary across all sessions")}">
							<i class="fa fa-file-text"></i> ${__("Z Report")}
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

					<div class="ch-rpt-section">
						<div class="ch-rpt-section-head"><i class="fa fa-area-chart"></i> ${__("Hourly Sales")}</div>
						<div class="ch-rpt-section-body">
							<div class="ch-rpt-chart" data-chart="hourly"></div>
							<div class="ch-rpt-chart-empty" style="display:none;">
								<div class="ch-rpt-empty">
									<i class="fa fa-bar-chart"></i>
									<span>${__("No sales recorded yet today")}</span>
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
										<th>${__("Cashier")}</th>
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
								<a class="ch-rpt-view-all" href="/app/material-request?set_warehouse=${wh}" target="_blank">${__("View All")} →</a>
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
		panel.on("click", ".ch-rpt-refresh", () => {
			panel.find(".ch-rpt-content").hide();
			panel.find(".ch-rpt-loading").show();
			this._load_data(panel);
		});
		panel.on("click", ".ch-rpt-z-report", () => this._show_z_report());

		// KPI cards → open filtered list in new tab
		panel.on("click", ".ch-rpt-kpi--link", function () {
			const href = $(this).attr("data-href");
			if (href) window.open(href, "_blank");
		});
		// MR / STE / inventory pill clicks
		panel.on("click", ".ch-rpt-doc-row[data-href]", function () {
			window.open($(this).attr("data-href"), "_blank");
		});
		panel.on("click", ".ch-rpt-inv-pill[data-item]", function () {
			window.open(`/app/item/${encodeURIComponent($(this).attr("data-item"))}`, "_blank");
		});

		EventBus.on("walkin:logged", (d) => {
			panel.find(".ch-rpt-walkins").text(d.walkin_count || 0);
			panel.find(".ch-rpt-kiosk").text(d.kiosk_count || 0);
		});
	}

	_load_data(panel) {
		frappe.call({
			method: "ch_pos.api.pos_api.get_today_footfall",
			args: { pos_profile: PosState.pos_profile },
			callback: (r) => {
				const f = r.message || {};
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
			args: { pos_profile: PosState.pos_profile },
			callback: (r) => {
				panel.find(".ch-rpt-loading").hide();
				const content = panel.find(".ch-rpt-content");
				content.show();
				const d = r.message || {};

				// KPIs
				content.find(".ch-rpt-revenue").text(`₹${format_number(d.total_revenue || 0)}`);
				content.find(".ch-rpt-invoices").text(d.total_invoices || 0);
				content.find(".ch-rpt-items-sold").text(d.total_items_sold || 0);
				const avg = d.total_invoices ? (d.total_revenue / d.total_invoices) : 0;
				content.find(".ch-rpt-avg-basket").text(`₹${format_number(avg)}`);
				content.find(".ch-rpt-returns").text(d.total_returns || 0);

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
					top_body.append(`<tr><td colspan="4" class="text-muted text-center" style="padding:20px">${__("No sales today")}</td></tr>`);
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
						<div class="ch-rpt-doc-row" data-href="/app/material-request/${encodeURIComponent(mr.name)}">
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
					st_list.append(`
						<div class="ch-rpt-doc-row" data-href="/app/stock-entry/${encodeURIComponent(se.name)}">
							<div>
								<div class="ch-rpt-doc-id">${frappe.utils.escape_html(se.name)}</div>
								<div class="ch-rpt-doc-meta">
									${frappe.datetime.str_to_user(se.posting_date)} · ${se.item_count} ${__("items")}
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
