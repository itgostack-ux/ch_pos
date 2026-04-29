frappe.pages["store-hub"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Store Hub"),
		single_column: true,
	});
	wrapper.store_hub = new StoreHub(page);
};

frappe.pages["store-hub"].refresh = function (wrapper) {
	wrapper.store_hub && wrapper.store_hub.refresh();
};

class StoreHub {
	constructor(page) {
		this.page = page;
		this._timer = null;
		this._setup_controls();
		this._setup_container();
		this.refresh();
		this._start_auto_refresh();
	}

	_setup_controls() {
		this.company_field = this.page.add_field({
			fieldname: "company", label: __("Company"),
			fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.refresh(),
		});
		this.store_field = this.page.add_field({
			fieldname: "store", label: __("Store"),
			fieldtype: "Link", options: "Warehouse",
			get_query: () => {
				const company = this.company_field?.get_value();
				const filters = { is_group: 0 };
				if (company) filters.company = company;
				return { filters };
			},
			change: () => this.refresh(),
		});
		this.from_date_field = this.page.add_field({
			fieldname: "from_date", label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			change: () => this.refresh(),
		});
		this.to_date_field = this.page.add_field({
			fieldname: "to_date", label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			change: () => this.refresh(),
		});
		this.page.add_button(__("Refresh"), () => this.refresh(), { icon: "refresh" });
	}

	_setup_container() {
		this.$root = $(`<div class="hub-root"></div>`).appendTo(this.page.body);
	}

	_go_list(doctype, filters = {}) {
		const co = this.company_field?.get_value();
		if (co) filters.company = co;
		frappe.set_route("List", doctype, filters);
	}

	refresh() {
		const company = this.company_field?.get_value() || "";
		const store = this.store_field?.get_value() || "";
		const from_date = this.from_date_field?.get_value() || "";
		const to_date = this.to_date_field?.get_value() || "";
		this.$root.html(`<div class="hub-loading"><i class="fa fa-spinner fa-spin"></i> ${__("Loading Store Hub...")}</div>`);
		frappe.xcall("ch_pos.pos_core.page.store_hub.store_hub_api.get_store_hub_data",
			{ company, store, from_date, to_date })
			.then((data) => this._render(data))
			.catch(() => {
				this.$root.html(`<div class="hub-loading text-danger">${__("Failed to load data. Please try again.")}</div>`);
			});
	}

	_start_auto_refresh() {
		this._timer = setInterval(() => this.refresh(), 60000);
		$(this.page.parent).on("remove", () => clearInterval(this._timer));
	}

	_render(data) {
		this.$root.empty();
		this._render_header();
		this._render_pipeline(data.pipeline || []);
		this._render_kpis(data.kpis || []);
		this._render_actions();
		this._render_intelligence(data.ai_insights || [], data.financial_control || {});
		this._render_tables(data);
	}

	_render_header() {
		this.$root.append(`
			<div class="hub-header">
				<div>
					<div class="hub-title"><i class="fa fa-building"></i> ${__("Store Hub")}</div>
					<div class="hub-subtitle">${__("Store operations: POS Sessions → Daily Sales → Settlements → Cash Management → Inventory")}</div>
				</div>
				<div class="hub-auto-badge">
					<span class="pulse-dot"></span> ${__("Live · Auto-refreshes every 60s")}
				</div>
			</div>
		`);
	}

	_render_pipeline(steps) {
		const arrow = `<div class="hub-flow-connector">
			<svg width="32" height="24" viewBox="0 0 32 24" fill="none" stroke="currentColor"
				stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
				<path d="M4 12H24M18 6l6 6-6 6"/>
			</svg>
		</div>`;
		const nodes = steps.map((s, i) => {
			const node = `
				<div class="hub-flow-node" data-step="${s.key}">
					<div class="hub-flow-badge" style="background:${s.color}">${s.count}</div>
					<div class="hub-flow-meta">
						<i class="fa fa-${s.icon}"></i>
						<span class="hub-flow-name">${__(s.label)}</span>
					</div>
					<div class="hub-flow-sub">${s.sub || ""}</div>
				</div>`;
			return i < steps.length - 1 ? node + arrow : node;
		}).join("");
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-random"></i> ${__("Store Operations Pipeline")}</h5>
				<div class="hub-flow-wrap"><div class="hub-flow">${nodes}</div></div>
			</div>
		`);
	}

	_render_kpis(kpis) {
		const cards = kpis.map((k) => {
			const val = k.fmt === "currency"
				? frappe.format(k.value, { fieldtype: "Currency" })
				: k.value;
			return `<div class="hub-kpi-card" style="--kpi-color:${k.color}" data-kpi="${k.key}">
				<div class="hub-kpi-value">${val}</div>
				<div class="hub-kpi-label">${__(k.label)}</div>
			</div>`;
		}).join("");
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-tachometer"></i> ${__("Key Metrics")}</h5>
				<div class="hub-kpi-grid">${cards}</div>
			</div>
		`);
	}

	_render_actions() {
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-bolt"></i> ${__("Quick Actions")}</h5>
				<div class="hub-actions-grid">
					<button class="hub-action-btn" data-act="pos_sessions"><i class="fa fa-desktop"></i> ${__("POS Sessions")}</button>
					<button class="hub-action-btn" data-act="settlements"><i class="fa fa-money"></i> ${__("Settlements")}</button>
					<button class="hub-action-btn" data-act="pos_invoices"><i class="fa fa-shopping-bag"></i> ${__("POS Invoices")}</button>
					<button class="hub-action-btn" data-act="open_pos"><i class="fa fa-tv"></i> ${__("Open POS App")}</button>
					<button class="hub-action-btn" data-act="store_inv"><i class="fa fa-cubes"></i> ${__("Store Inventory")}</button>
					<button class="hub-action-btn" data-act="store_list"><i class="fa fa-building-o"></i> ${__("Store List")}</button>
				</div>
			</div>
		`);

		this.$root.on("click", ".hub-action-btn", (e) => {
			const actions = {
				pos_sessions: () => this._go_list("CH POS Session"),
				settlements:  () => this._go_list("CH POS Settlement"),
				pos_invoices: () => this._go_list("POS Invoice", { docstatus: 1 }),
				open_pos:     () => frappe.set_route("app", "ch-pos-app"),
				store_inv:    () => this._go_list("Bin", { warehouse: ["like", "%Store%"] }),
				store_list:   () => this._go_list("CH Store"),
			};
			const fn = actions[$(e.currentTarget).data("act")];
			if (fn) fn();
		});
	}

	_render_intelligence(insights, financial) {
		const insightCards = insights.map((i) => `
			<div class="hub-insight-card hub-insight-${(i.severity || 'medium').toLowerCase()}">
				<div class="hub-insight-top">
					<span class="hub-badge hub-badge-${i.severity === 'High' ? 'red' : i.severity === 'Low' ? 'green' : 'yellow'}">${i.severity}</span>
					<span class="hub-insight-title">${i.title}</span>
				</div>
				<div class="hub-insight-detail">${i.detail}</div>
				${i.action ? `<div class="hub-insight-action">${i.action}</div>` : ""}
			</div>
		`).join("");

		const fc = financial;
		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-brain"></i> ${__("AI Insights & Store Control")}</h5>
				<div class="hub-intel-grid">
					<div class="hub-intel-panel">${insightCards || '<div class="hub-empty">No insights</div>'}</div>
					<div class="hub-intel-panel">
						<div class="hub-mini-kpi-grid">
							<div class="hub-mini-kpi" style="--mini-color:#0891b2">
								<div class="hub-mini-kpi-value">${frappe.format(fc.today_sales || 0, {fieldtype:"Currency"})}</div>
								<div class="hub-mini-kpi-label">${__("Today Sales")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#059669">
								<div class="hub-mini-kpi-value">${frappe.format(fc.cash_in_hand || 0, {fieldtype:"Currency"})}</div>
								<div class="hub-mini-kpi-label">${__("Cash in Hand")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#f59e0b">
								<div class="hub-mini-kpi-value">${fc.pending_settlements || 0}</div>
								<div class="hub-mini-kpi-label">${__("Pending Settlements")}</div>
							</div>
							<div class="hub-mini-kpi" style="--mini-color:#ef4444">
								<div class="hub-mini-kpi-value">${frappe.format(fc.variance || 0, {fieldtype:"Currency"})}</div>
								<div class="hub-mini-kpi-label">${__("Cash Variance")}</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);
	}

	_render_tables(data) {
		const tabs = [
			{ key: "sessions", label: __("POS Sessions"), count: (data.sessions || []).length },
			{ key: "settlements", label: __("Settlements"), count: (data.settlements || []).length },
			{ key: "daily_summary", label: __("Daily Summary"), count: (data.daily_summary || []).length },
			{ key: "top_items", label: __("Top Items"), count: (data.top_items || []).length },
			{ key: "stock_alerts", label: __("Stock Alerts"), count: (data.stock_alerts || []).length },
			{ key: "kiosk_tokens", label: __("Kiosk Queue"), count: (data.kiosk_tokens || []).length },
			{ key: "cash_drops", label: __("Cash Drops"), count: (data.cash_drops || []).length },
			{ key: "incentives", label: __("Incentives"), count: (data.incentives || []).length },
			{ key: "audit_logs", label: __("Audit Log"), count: (data.audit_logs || []).length },
		];
		const tabBtns = tabs.map((t, i) =>
			`<button class="hub-tab${i === 0 ? " active" : ""}" data-tab="${t.key}">
				${t.label} <span class="badge">${t.count}</span>
			</button>`
		).join("");

		this.$root.append(`
			<div class="hub-section">
				<h5 class="hub-section-title"><i class="fa fa-table"></i> ${__("Detail Tables")}</h5>
				<div class="hub-tabs">${tabBtns}</div>
				<div class="hub-tab-panel active" data-panel="sessions">${this._table_sessions(data.sessions || [])}</div>
				<div class="hub-tab-panel" data-panel="settlements">${this._table_settlements(data.settlements || [])}</div>
				<div class="hub-tab-panel" data-panel="daily_summary">${this._table_daily(data.daily_summary || [])}</div>
				<div class="hub-tab-panel" data-panel="top_items">${this._table_top_items(data.top_items || [])}</div>
				<div class="hub-tab-panel" data-panel="stock_alerts">${this._table_stock(data.stock_alerts || [])}</div>
				<div class="hub-tab-panel" data-panel="kiosk_tokens">${this._table_kiosk(data.kiosk_tokens || [])}</div>
				<div class="hub-tab-panel" data-panel="cash_drops">${this._table_cash_drops(data.cash_drops || [])}</div>
				<div class="hub-tab-panel" data-panel="incentives">${this._table_incentives(data.incentives || [])}</div>
				<div class="hub-tab-panel" data-panel="audit_logs">${this._table_audit(data.audit_logs || [])}</div>
			</div>
		`);

		this.$root.find(".hub-tab").on("click", (e) => {
			const key = $(e.currentTarget).data("tab");
			this.$root.find(".hub-tab").removeClass("active");
			$(e.currentTarget).addClass("active");
			this.$root.find(".hub-tab-panel").removeClass("active");
			this.$root.find(`[data-panel="${key}"]`).addClass("active");
		});
	}

	_lnk(dt, name) { return `<a href="/app/${frappe.router.slug(dt)}/${name}">${name}</a>`; }
	_badge(status) {
		const map = { "Open": "green", "Closed": "grey", "Pending": "yellow", "Approved": "green", "Posted": "blue", "Draft": "grey" };
		return `<span class="hub-badge hub-badge-${map[status] || "grey"}">${status}</span>`;
	}

	_table_sessions(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-desktop"></i> ${__("No POS sessions")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Session")}</th><th>${__("Store")}</th><th>${__("Opened")}</th>
			<th>${__("Status")}</th><th class="text-right">${__("Sales")}</th><th class="text-right">${__("Transactions")}</th>
		</tr></thead><tbody>${rows.map((r) => {
			const store_name = r.store_name || r.store || "";
			return `<tr>
			<td>${this._lnk("CH POS Session", r.name)}</td>
			<td>${store_name}</td>
			<td>${r.shift_start ? frappe.datetime.str_to_user(r.shift_start) : "-"}</td>
			<td>${this._badge(r.status)}</td>
			<td class="text-right">${frappe.format(r.total_sales || 0, {fieldtype:"Currency"})}</td>
			<td class="text-right">${r.total_invoices || 0}</td>
		</tr>`;
		}).join("")}</tbody></table></div>`;
	}

	_table_settlements(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-money"></i> ${__("No settlements")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Settlement")}</th><th>${__("Store")}</th><th>${__("Date")}</th>
			<th>${__("Status")}</th><th class="text-right">${__("Total Sales")}</th>
			<th class="text-right">${__("Cash")}</th><th class="text-right">${__("Variance")}</th>
		</tr></thead><tbody>${rows.map((r) => {
			const store_name = r.store_name || r.store || "";
			return `<tr>
			<td>${this._lnk("CH POS Settlement", r.name)}</td>
			<td>${store_name}</td>
			<td>${r.business_date ? frappe.datetime.str_to_user(r.business_date) : "-"}</td>
			<td>${this._badge(r.settlement_status)}</td>
			<td class="text-right">${frappe.format(r.total_gross_sales || 0, {fieldtype:"Currency"})}</td>
			<td class="text-right">${frappe.format(r.total_sales_cash || 0, {fieldtype:"Currency"})}</td>
			<td class="text-right ${(r.variance_amount || 0) != 0 ? 'text-danger' : ''}">${frappe.format(r.variance_amount || 0, {fieldtype:"Currency"})}</td>
		</tr>`;
		}).join("")}</tbody></table></div>`;
	}

	_table_daily(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-calendar"></i> ${__("No daily data")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Date")}</th><th>${__("Store")}</th><th class="text-right">${__("Transactions")}</th>
			<th class="text-right">${__("Revenue")}</th><th class="text-right">${__("Avg Ticket")}</th>
		</tr></thead><tbody>${rows.map((r) => {
			const warehouse_name = r.warehouse_name || r.warehouse || "";
			return `<tr>
			<td>${frappe.datetime.str_to_user(r.posting_date)}</td>
			<td>${warehouse_name}</td>
			<td class="text-right">${r.txn_count}</td>
			<td class="text-right">${frappe.format(r.revenue, {fieldtype:"Currency"})}</td>
			<td class="text-right">${frappe.format(r.avg_ticket, {fieldtype:"Currency"})}</td>
		</tr>`;
		}).join("")}</tbody></table></div>`;
	}

	_table_top_items(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-cube"></i> ${__("No item data")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Item")}</th><th>${__("Item Name")}</th><th class="text-right">${__("Qty Sold")}</th>
			<th class="text-right">${__("Revenue")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td><a href="/app/item/${r.item_code}">${r.item_code}</a></td>
			<td>${r.item_name || ""}</td>
			<td class="text-right">${r.qty}</td>
			<td class="text-right">${frappe.format(r.revenue, {fieldtype:"Currency"})}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_stock(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-check-circle"></i> ${__("No stock alerts")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Item")}</th><th>${__("Item Name")}</th><th>${__("Warehouse")}</th>
			<th class="text-right">${__("In Stock")}</th><th class="text-right">${__("Reorder Level")}</th>
		</tr></thead><tbody>${rows.map((r) => {
			const warehouse_name = r.warehouse_name || r.warehouse || "";
			return `<tr>
			<td><a href="/app/item/${r.item_code}">${r.item_code}</a></td>
			<td>${r.item_name || ""}</td>
			<td>${warehouse_name}</td>
			<td class="text-right text-danger">${r.actual_qty}</td>
			<td class="text-right">${r.reorder_level || "-"}</td>
		</tr>`;
		}).join("")}</tbody></table></div>`;
	}

	_table_kiosk(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-ticket"></i> ${__("No kiosk tokens")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Token")}</th><th>${__("Customer")}</th><th>${__("Phone")}</th>
			<th>${__("Purpose")}</th><th>${__("Source")}</th><th>${__("Status")}</th><th>${__("Created")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${this._lnk("POS Kiosk Token", r.name)}</td>
			<td>${r.customer_name || ""}</td>
			<td>${r.customer_phone || ""}</td>
			<td>${r.visit_purpose || ""}</td>
			<td>${r.visit_source || ""}</td>
			<td>${this._badge(r.status)}</td>
			<td>${r.creation ? frappe.datetime.str_to_user(r.creation) : "-"}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_cash_drops(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-money"></i> ${__("No cash drops")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Cash Drop")}</th><th>${__("Store")}</th><th>${__("Date")}</th>
			<th>${__("Type")}</th><th class="text-right">${__("Amount")}</th>
			<th>${__("Status")}</th><th>${__("Cashier")}</th><th>${__("Approved By")}</th>
		</tr></thead><tbody>${rows.map((r) => {
			const store_name = r.store_name || r.store || "";
			return `<tr>
			<td>${this._lnk("CH Cash Drop", r.name)}</td>
			<td>${store_name}</td>
			<td>${r.business_date ? frappe.datetime.str_to_user(r.business_date) : "-"}</td>
			<td>${r.movement_type || ""}</td>
			<td class="text-right">${frappe.format(r.amount || 0, {fieldtype:"Currency"})}</td>
			<td>${this._badge(r.status)}</td>
			<td>${r.user || ""}</td>
			<td>${r.approved_by || "-"}</td>
		</tr>`;
		}).join("")}</tbody></table></div>`;
	}

	_table_incentives(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-trophy"></i> ${__("No incentive entries")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Executive")}</th><th>${__("Date")}</th><th>${__("Item")}</th>
			<th>${__("Brand")}</th><th class="text-right">${__("Billing Amt")}</th>
			<th class="text-right">${__("Incentive")}</th><th>${__("Type")}</th><th>${__("Status")}</th>
		</tr></thead><tbody>${rows.map((r) => `<tr>
			<td>${r.executive_name || r.pos_executive || ""}</td>
			<td>${r.posting_date ? frappe.datetime.str_to_user(r.posting_date) : "-"}</td>
			<td>${r.item_name || ""}</td>
			<td>${r.brand || ""}</td>
			<td class="text-right">${frappe.format(r.billing_amount || 0, {fieldtype:"Currency"})}</td>
			<td class="text-right">${frappe.format(r.incentive_amount || 0, {fieldtype:"Currency"})}</td>
			<td>${r.incentive_type || ""}</td>
			<td>${this._badge(r.status)}</td>
		</tr>`).join("")}</tbody></table></div>`;
	}

	_table_audit(rows) {
		if (!rows.length) return `<div class="hub-empty"><i class="fa fa-shield"></i> ${__("No audit entries")}</div>`;
		return `<div class="hub-table-wrap"><table class="hub-table"><thead><tr>
			<th>${__("Event")}</th><th>${__("Reference")}</th><th>${__("Store")}</th>
			<th>${__("User")}</th><th>${__("Time")}</th><th>${__("Remarks")}</th>
		</tr></thead><tbody>${rows.map((r) => {
			const store_name = r.store_name || r.store || "";
			return `<tr>
			<td><span class="hub-badge hub-badge-grey">${r.event_type || ""}</span></td>
			<td>${r.reference_doctype && r.reference_name ? `<a href="/app/${frappe.router.slug(r.reference_doctype)}/${r.reference_name}">${r.reference_name}</a>` : "-"}</td>
			<td>${store_name}</td>
			<td>${r.user || ""}</td>
			<td>${r.timestamp ? frappe.datetime.str_to_user(r.timestamp) : "-"}</td>
			<td>${r.remarks || ""}</td>
		</tr>`;
		}).join("")}</tbody></table></div>`;
	}
}
