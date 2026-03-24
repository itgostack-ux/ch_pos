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
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:var(--pos-space-xl)">
					<div class="ch-mode-header" style="margin-bottom:0">
						<h4>
							<span class="mode-icon" style="background:#e0e7ff;color:#4f46e5">
								<i class="fa fa-bar-chart"></i>
							</span>
							${__("Store Dashboard")}
						</h4>
						<span class="ch-mode-hint">${__("Today's performance at a glance")}</span>
					</div>
<div style="display:flex;gap:8px">
					<button class="btn btn-outline-secondary ch-rpt-z-report" style="border-radius:var(--pos-radius);font-weight:700"
						title="${__("End-of-day store summary across all sessions")}">
						<i class="fa fa-file-text"></i> ${__("Z Report")}
					</button>
					<button class="btn btn-outline-secondary ch-rpt-refresh" style="border-radius:var(--pos-radius);font-weight:700">
						<i class="fa fa-refresh"></i> ${__("Refresh")}
					</button>
				</div>
				</div>

				<div class="ch-rpt-loading" style="padding:40px;text-align:center">
					<i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.3"></i>
				</div>

				<div class="ch-rpt-content" style="display:none;">
					<div class="ch-rpt-kpi-row" style="margin-bottom:var(--pos-space-md)" id="ch-rpt-footfall-row">
					<div class="ch-rpt-kpi ch-pos-section-card" style="border-top:3px solid #4f46e5">
						<div class="section-body" style="display:flex;gap:12px;align-items:center">
							<div class="ch-rpt-kpi-icon" style="background:#e0e7ff;color:#4f46e5;"><i class="fa fa-sign-in"></i></div>
							<div>
								<div class="ch-rpt-kpi-value ch-rpt-walkins">0</div>
								<div class="ch-rpt-kpi-label">${__("Walk-ins")}</div>
							</div>
						</div>
					</div>
					<div class="ch-rpt-kpi ch-pos-section-card" style="border-top:3px solid #7c3aed">
						<div class="section-body" style="display:flex;gap:12px;align-items:center">
							<div class="ch-rpt-kpi-icon" style="background:#f3e8ff;color:#7c3aed;"><i class="fa fa-tablet"></i></div>
							<div>
								<div class="ch-rpt-kpi-value ch-rpt-kiosk">0</div>
								<div class="ch-rpt-kpi-label">${__("Kiosk")}</div>
							</div>
						</div>
					</div>
					<div class="ch-rpt-kpi ch-pos-section-card" style="border-top:3px solid #16a34a">
						<div class="section-body" style="display:flex;gap:12px;align-items:center">
							<div class="ch-rpt-kpi-icon" style="background:#dcfce7;color:#16a34a;"><i class="fa fa-percent"></i></div>
							<div>
								<div class="ch-rpt-kpi-value ch-rpt-conversion">0%</div>
								<div class="ch-rpt-kpi-label">${__("Conversion")}</div>
							</div>
						</div>
					</div>
					<div class="ch-rpt-kpi ch-pos-section-card" style="border-top:3px solid #d97706">
						<div class="section-body" style="display:flex;gap:12px;align-items:center">
							<div class="ch-rpt-kpi-icon" style="background:#fef3c7;color:#d97706;"><i class="fa fa-wrench"></i></div>
							<div>
								<div class="ch-rpt-kpi-value ch-rpt-repairs">0</div>
								<div class="ch-rpt-kpi-label">${__("Repairs")}</div>
							</div>
						</div>
					</div>
					<div class="ch-rpt-kpi ch-pos-section-card" style="border-top:3px solid #dc2626">
						<div class="section-body" style="display:flex;gap:12px;align-items:center">
							<div class="ch-rpt-kpi-icon" style="background:#fef2f2;color:#dc2626;"><i class="fa fa-exchange"></i></div>
							<div>
								<div class="ch-rpt-kpi-value ch-rpt-buybacks">0</div>
								<div class="ch-rpt-kpi-label">${__("Buybacks")}</div>
							</div>
						</div>
					</div>
					<div class="ch-rpt-kpi ch-pos-section-card" style="border-top:3px solid #ef4444">
						<div class="section-body" style="display:flex;gap:12px;align-items:center">
							<div class="ch-rpt-kpi-icon" style="background:#fee2e2;color:#ef4444;"><i class="fa fa-ban"></i></div>
							<div>
								<div class="ch-rpt-kpi-value ch-rpt-cancelled">0</div>
								<div class="ch-rpt-kpi-label">${__("Cancelled")}</div>
							</div>
						</div>
					</div>
					<div class="ch-rpt-kpi ch-pos-section-card" style="border-top:3px solid #f97316">
						<div class="section-body" style="display:flex;gap:12px;align-items:center">
							<div class="ch-rpt-kpi-icon" style="background:#fff7ed;color:#f97316;"><i class="fa fa-user-times"></i></div>
							<div>
								<div class="ch-rpt-kpi-value ch-rpt-dropped">0</div>
								<div class="ch-rpt-kpi-label">${__("Dropped")}</div>
							</div>
						</div>
					</div>
				</div>

				<div class="ch-rpt-kpi-row">
						<div class="ch-rpt-kpi ch-pos-section-card">
							<div class="section-body" style="display:flex;gap:12px;align-items:center">
								<div class="ch-rpt-kpi-icon" style="background:#dbeafe;color:#2563eb;"><i class="fa fa-inr"></i></div>
								<div>
									<div class="ch-rpt-kpi-value ch-rpt-revenue">₹0</div>
									<div class="ch-rpt-kpi-label">${__("Revenue")}</div>
								</div>
							</div>
						</div>
						<div class="ch-rpt-kpi ch-pos-section-card">
							<div class="section-body" style="display:flex;gap:12px;align-items:center">
								<div class="ch-rpt-kpi-icon" style="background:#dcfce7;color:#16a34a;"><i class="fa fa-file-text-o"></i></div>
								<div>
									<div class="ch-rpt-kpi-value ch-rpt-invoices">0</div>
									<div class="ch-rpt-kpi-label">${__("Invoices")}</div>
								</div>
							</div>
						</div>
						<div class="ch-rpt-kpi ch-pos-section-card">
							<div class="section-body" style="display:flex;gap:12px;align-items:center">
								<div class="ch-rpt-kpi-icon" style="background:#fef3c7;color:#d97706;"><i class="fa fa-shopping-bag"></i></div>
								<div>
									<div class="ch-rpt-kpi-value ch-rpt-items-sold">0</div>
									<div class="ch-rpt-kpi-label">${__("Items Sold")}</div>
								</div>
							</div>
						</div>
						<div class="ch-rpt-kpi ch-pos-section-card">
							<div class="section-body" style="display:flex;gap:12px;align-items:center">
								<div class="ch-rpt-kpi-icon" style="background:#e0e7ff;color:#4f46e5;"><i class="fa fa-calculator"></i></div>
								<div>
									<div class="ch-rpt-kpi-value ch-rpt-avg-basket">₹0</div>
									<div class="ch-rpt-kpi-label">${__("Avg Basket")}</div>
								</div>
							</div>
						</div>
						<div class="ch-rpt-kpi ch-pos-section-card">
							<div class="section-body" style="display:flex;gap:12px;align-items:center">
								<div class="ch-rpt-kpi-icon" style="background:#fef2f2;color:#dc2626;"><i class="fa fa-undo"></i></div>
								<div>
									<div class="ch-rpt-kpi-value ch-rpt-returns">0</div>
									<div class="ch-rpt-kpi-label">${__("Returns")}</div>
								</div>
							</div>
						</div>
					</div>

					<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
						<div class="section-header"><i class="fa fa-area-chart"></i> ${__("Hourly Sales")}</div>
						<div class="section-body">
							<div class="ch-rpt-chart" data-chart="hourly"></div>
							<div class="ch-rpt-chart-empty" style="display:none;">
								<div class="ch-pos-empty-state" style="padding:20px 0">
									<div class="empty-icon" style="width:48px;height:48px;font-size:18px"><i class="fa fa-bar-chart"></i></div>
									<div class="empty-title" style="font-size:var(--pos-fs-sm)">${__("No sales recorded yet today")}</div>
								</div>
							</div>
						</div>
					</div>

					<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md);margin-bottom:var(--pos-space-md)">
						<div class="ch-pos-section-card">
							<div class="section-header"><i class="fa fa-trophy"></i> ${__("Top Sellers")}</div>
							<div class="section-body" style="padding:0">
								<table class="ch-rpt-table">
									<thead><tr>
										<th>${__("#")}</th>
										<th>${__("Item")}</th>
										<th class="text-right">${__("Qty")}</th>
										<th class="text-right">${__("Revenue")}</th>
									</tr></thead>
									<tbody class="ch-rpt-top-items"></tbody>
								</table>
							</div>
						</div>
						<div class="ch-pos-section-card">
							<div class="section-header"><i class="fa fa-users"></i> ${__("Staff")}</div>
							<div class="section-body" style="padding:0">
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

					<div class="ch-pos-section-card">
						<div class="section-header"><i class="fa fa-exclamation-triangle"></i> ${__("Low Stock Alerts")}</div>
						<div class="section-body">
							<div class="ch-rpt-inventory-grid"></div>
						</div>
					</div>

					<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md);margin-top:var(--pos-space-md)">
						<div class="ch-pos-section-card">
							<div class="section-header"><i class="fa fa-clipboard"></i> ${__("Material Requests")}</div>
							<div class="section-body" style="padding:0">
								<div class="ch-rpt-mr-list"></div>
							</div>
						</div>
						<div class="ch-pos-section-card">
							<div class="section-header"><i class="fa fa-truck"></i> ${__("Stock Transfers")}</div>
							<div class="section-body" style="padding:0">
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
		// Update footfall row when walk-in is logged from sidebar
		EventBus.on("walkin:logged", (d) => {
			panel.find(".ch-rpt-walkins").text(d.walkin_count || 0);
			panel.find(".ch-rpt-kiosk").text(d.kiosk_count || 0);
		});
	}

	_load_data(panel) {
		// Load footfall separately (fast, always visible)
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
						<td style="width:30px;text-align:center">${medal}</td>
						<td>${frappe.utils.escape_html(item.item_name)}</td>
						<td class="text-right">${item.qty}</td>
						<td class="text-right font-weight-bold">₹${format_number(item.revenue)}</td>
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
						<td class="text-right font-weight-bold">₹${format_number(s.revenue)}</td>
					</tr>`);
				});
				if (!(d.staff_performance || []).length) {
					staff_body.append(`<tr><td colspan="3" class="text-muted text-center" style="padding:20px">${__("No staff data")}</td></tr>`);
				}

				// Inventory alerts
				const inv_grid = content.find(".ch-rpt-inventory-grid").empty();
				(d.inventory_alerts || []).forEach((item) => {
					const is_oos = item.qty <= 0;
					const cls = is_oos ? "ch-rpt-inv-pill-oos" : "ch-rpt-inv-pill-low";
					inv_grid.append(`<span class="ch-rpt-inv-pill ${cls}">
						${frappe.utils.escape_html(item.item_name)} <b>${Math.floor(item.qty)}</b>
					</span>`);
				});
				if (!(d.inventory_alerts || []).length) {
					inv_grid.html(`<div class="text-muted" style="padding:16px;text-align:center;">
						<i class="fa fa-check-circle" style="color:#16a34a;margin-right:4px;"></i>
						${__("All stock levels healthy")}
					</div>`);
				}

				// Material Requests
				const mr_list = content.find(".ch-rpt-mr-list").empty();
				(d.material_requests || []).forEach((mr) => {
					const cls = mr.status === "Pending" ? "ch-pos-badge-warning"
						: mr.status === "Partially Ordered" ? "ch-pos-badge-info"
						: mr.status === "Ordered" || mr.status === "Transferred" ? "ch-pos-badge-success"
						: "ch-pos-badge-muted";
					mr_list.append(`
						<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 16px;border-bottom:1px solid var(--pos-border-light)">
							<div>
								<div style="font-weight:600;font-size:var(--pos-fs-sm)">${frappe.utils.escape_html(mr.name)}</div>
								<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
									${frappe.datetime.str_to_user(mr.transaction_date)} · ${mr.item_count} ${__("items")}
								</div>
							</div>
							<span class="ch-pos-badge ${cls}">${frappe.utils.escape_html(mr.status)}</span>
						</div>`);
				});
				if (!(d.material_requests || []).length) {
					mr_list.html(`<div class="text-muted text-center" style="padding:20px">${__("No pending requests")}</div>`);
				}

				// Stock Transfers
				const st_list = content.find(".ch-rpt-st-list").empty();
				(d.stock_transfers || []).forEach((se) => {
					const status_label = se.docstatus === 0 ? __("Draft") : __("Completed");
					const cls = se.docstatus === 0 ? "ch-pos-badge-warning" : "ch-pos-badge-success";
					st_list.append(`
						<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 16px;border-bottom:1px solid var(--pos-border-light)">
							<div>
								<div style="font-weight:600;font-size:var(--pos-fs-sm)">${frappe.utils.escape_html(se.name)}</div>
								<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
									${frappe.datetime.str_to_user(se.posting_date)} · ${se.item_count} ${__("items")}
								</div>
							</div>
							<span class="ch-pos-badge ${cls}">${status_label}</span>
						</div>`);
				});
				if (!(d.stock_transfers || []).length) {
					st_list.html(`<div class="text-muted text-center" style="padding:20px">${__("No recent transfers")}</div>`);
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
