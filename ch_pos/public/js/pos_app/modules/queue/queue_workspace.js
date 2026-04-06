/**
 * CH POS — Queue Workspace (Universal)
 *
 * Company-aware token queue panel:
 * - GoFix (service): "GoFix Request" conversion (existing flow)
 * - GoGizmo (retail): "Start Billing" + "Drop" actions
 *
 * Shows Waiting / Engaged / In-Progress tokens for the current store.
 * Auto-refreshes every 30 seconds while the queue tab is active.
 */
import { PosState, EventBus } from "../../state.js";

/** Heuristic: service company? */
function _is_service() {
	const c = (PosState.company || "").toLowerCase();
	return c.includes("gofix") || c.includes("service");
}

const DROP_REASONS = [
	"Price Too High",
	"Item Not Available",
	"Just Browsing",
	"Found Elsewhere",
	"Will Come Back Later",
	"Long Wait Time",
	"Other",
];

export class QueueWorkspace {
	constructor() {
		this._panel = null;
		this._refreshTimer = null;
		this._tokens = [];
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "queue") return;
			this._panel = ctx.panel;
			this._render(ctx.panel);
			this._startAutoRefresh();
		});
		EventBus.on("mode:switch", (mode) => {
			if (mode !== "queue") this._stopAutoRefresh();
		});
	}

	_startAutoRefresh() {
		this._stopAutoRefresh();
		this._refreshTimer = setInterval(() => this._loadTokens(), 30000);
	}

	_stopAutoRefresh() {
		if (this._refreshTimer) {
			clearInterval(this._refreshTimer);
			this._refreshTimer = null;
		}
	}

	_render(panel) {
		const is_svc = _is_service();
		const title = is_svc ? __("Service Queue") : __("Store Queue");
		const hint = is_svc
			? __("Waiting tokens from the kiosk — accept or convert to service requests")
			: __("Manage walk-in customers — start billing or close out tokens");

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon ch-queue-icon ${is_svc ? "ch-queue-icon--service" : "ch-queue-icon--retail"}">
							<i class="fa fa-${is_svc ? "stethoscope" : "users"}"></i>
						</span>
						${title}
					</h4>
					<span class="ch-mode-hint">${hint}</span>
				</div>

				<div class="ch-queue-toolbar">
					<div class="ch-queue-stats">
						<span class="ch-queue-count"></span>
					</div>
					<button class="btn btn-xs btn-default ch-queue-refresh-btn">
						<i class="fa fa-refresh"></i> ${__("Refresh")}
					</button>
				</div>

				<div class="ch-queue-token-list">
					<div class="ch-queue-empty-state ch-queue-loading-state">
						<i class="fa fa-spinner fa-spin fa-2x"></i>
						<span>${__("Loading tokens…")}</span>
					</div>
				</div>
			</div>
		`);

		panel.find(".ch-queue-refresh-btn").on("click", () => this._loadTokens());
		this._loadTokens();
	}

	_loadTokens() {
		const pos_profile = PosState.pos_profile;
		if (!pos_profile) return;

		if (this._panel) {
			this._panel.find(".ch-queue-token-list").html(
				`<div class="ch-queue-empty-state ch-queue-loading-state">
					<i class="fa fa-spinner fa-spin fa-2x"></i>
					<span>${__("Loading tokens…")}</span>
				</div>`
			);
		}

		frappe.xcall("ch_pos.api.token_api.get_pos_waiting_tokens", { pos_profile })
			.then((tokens) => {
				this._tokens = tokens || [];
				this._renderTokenList(this._tokens);
			})
			.catch(() => {
				if (this._panel) {
					this._panel.find(".ch-queue-token-list").html(
						`<div class="ch-queue-empty-state ch-queue-error-state">
							<i class="fa fa-exclamation-circle fa-2x"></i>
							<span>${__("Failed to load queue")}</span>
							<span class="ch-queue-empty-hint">${__("Check your connection and try refreshing")}</span>
						</div>`
					);
				}
			});
	}

	_renderTokenList(tokens) {
		if (!this._panel) return;
		const list = this._panel.find(".ch-queue-token-list");

		// Update stats bar
		const stats = this._panel.find(".ch-queue-stats");
		const waiting = (tokens || []).filter((t) => t.status === "Waiting").length;
		const engaged = (tokens || []).filter((t) => t.status === "Engaged" || t.status === "In Progress").length;

		if (!tokens || !tokens.length) {
			stats.html(`<span class="ch-queue-count-text">${__("No tokens")}</span>`);
			list.html(`
				<div class="ch-queue-empty-state">
					<div class="ch-queue-empty-icon">
						<i class="fa fa-check-circle"></i>
					</div>
					<span class="ch-queue-empty-title">${__("All clear!")}</span>
					<span class="ch-queue-empty-hint">${__("No waiting or in-progress tokens right now")}</span>
				</div>
			`);
			return;
		}

		let stats_html = `<span class="ch-queue-count-text">${tokens.length} ${__("token(s)")}</span>`;
		if (waiting > 0) stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--waiting">${waiting} ${__("waiting")}</span>`;
		if (engaged > 0) stats_html += `<span class="ch-queue-stat-badge ch-queue-stat--active">${engaged} ${__("active")}</span>`;
		stats.html(stats_html);

		const cards = tokens.map((t) => this._tokenCard(t)).join("");
		list.html(`<div class="ch-queue-cards">${cards}</div>`);

		// Bind action buttons
		list.find(".ch-queue-convert-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._showConvertDialog(token);
		});
		list.find(".ch-queue-bill-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._startBilling(token);
		});
		list.find(".ch-queue-drop-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = this._tokens.find((t) => t.name === name);
			if (token) this._showDropDialog(token);
		});
	}

	// ── Token Card ──────────────────────────────────────────────

	_tokenCard(t) {
		const is_svc = _is_service();
		const statusMap = {
			Waiting:       { cls: "waiting",  icon: "fa-clock-o",     label: __("Waiting") },
			Engaged:       { cls: "engaged",  icon: "fa-handshake-o", label: __("Engaged") },
			"In Progress": { cls: "progress", icon: "fa-cogs",        label: __("In Progress") },
		};
		const st = statusMap[t.status] || { cls: "default", icon: "fa-circle", label: t.status };
		const timeAgo = frappe.datetime.comment_when(t.creation);

		// Customer display
		const cust_name  = frappe.utils.escape_html(t.customer_name || __("Walk-in"));
		const cust_phone = t.customer_phone ? frappe.utils.escape_html(t.customer_phone) : "";

		// Detail — purpose / device based on company type
		let detail_html = "";
		if (is_svc) {
			const device = [t.device_brand, t.device_model].filter(Boolean).join(" ") || t.device_type || "";
			detail_html = `
				<div class="ch-q-tags">
					${device ? `<span class="ch-q-tag"><i class="fa fa-mobile"></i> ${frappe.utils.escape_html(device)}</span>` : ""}
					${t.issue_category ? `<span class="ch-q-tag"><i class="fa fa-wrench"></i> ${frappe.utils.escape_html(t.issue_category)}</span>` : ""}
				</div>
				${t.issue_description ? `<p class="ch-q-note">${frappe.utils.escape_html(t.issue_description.substring(0, 80))}${t.issue_description.length > 80 ? "…" : ""}</p>` : ""}`;
		} else {
			const purpose = t.visit_purpose || "Sales";
			const purposeClsMap = { Sales: "ch-q-purpose--sales", Repair: "ch-q-purpose--repair", Buyback: "ch-q-purpose--buyback" };
			const purposeCls = purposeClsMap[purpose] || "ch-q-purpose--other";
			const tags = [t.category_interest, t.brand_interest, t.budget_range].filter(Boolean);
			detail_html = `
				<div class="ch-q-tags">
					<span class="ch-q-purpose ${purposeCls}">${frappe.utils.escape_html(purpose)}</span>
					${tags.map(tag => `<span class="ch-q-tag">${frappe.utils.escape_html(tag)}</span>`).join("")}
				</div>`;
		}

		// Action buttons
		let actions_html = "";
		if (is_svc) {
			actions_html = `
				<button class="btn btn-sm btn-primary ch-queue-convert-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					<i class="fa fa-plus"></i> ${__("GoFix Request")}
				</button>`;
		} else {
			actions_html = `
				<button class="btn btn-sm btn-primary ch-queue-bill-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					${__("Bill")}
				</button>
				<button class="btn btn-sm btn-default ch-queue-drop-btn"
					data-token="${frappe.utils.escape_html(t.name)}">
					${__("Drop")}
				</button>`;
		}

		return `
			<div class="ch-q-card ch-q-card--${st.cls}">
				<div class="ch-q-indicator"></div>
				<div class="ch-q-content">
					<div class="ch-q-row-top">
						<span class="ch-q-token-id">${frappe.utils.escape_html(t.token_display || t.name)}</span>
						<span class="ch-q-status ch-q-status--${st.cls}">
							<span class="ch-q-status-dot"></span> ${st.label}
						</span>
						<span class="ch-q-time">${timeAgo}</span>
					</div>
					<div class="ch-q-row-mid">
						<span class="ch-q-customer-name">${cust_name}</span>
						${cust_phone ? `<span class="ch-q-customer-phone">${cust_phone}</span>` : ""}
					</div>
					${detail_html}
				</div>
				<div class="ch-q-actions">
					${actions_html}
				</div>
			</div>
		`;
	}

	// ── Retail: Start Billing ───────────────────────────────────

	_startBilling(token) {
		const _proceed = () => {
			// Store token reference in state — will be passed to Sales Invoice
			PosState.kiosk_token = token.name;

			// Auto-set customer from token data
			this._resolve_customer(token).then((customer) => {
				if (customer) {
					PosState.customer = customer;
					EventBus.emit("customer:set", customer);
				}

				// Switch to sell mode
				PosState.active_mode = "sell";
				EventBus.emit("mode:set", "sell");
				EventBus.emit("mode:switch", "sell");

				frappe.show_alert({
					message: __("Billing started for token {0} — {1}", [
						token.token_display || token.name,
						token.customer_name || __("Walk-in"),
					]),
					indicator: "blue",
				}, 5);
			});
		};

		if (token.status === "Waiting") {
			// Engage first
			frappe.xcall("ch_pos.api.token_api.engage_token", {
				token_name: token.name,
				sales_executive: PosState.sales_executive || "",
			}).then(() => _proceed()).catch((err) => {
				frappe.show_alert({
					message: err.message || __("Failed to engage token"),
					indicator: "red",
				});
			});
		} else {
			// Already Engaged — just proceed
			_proceed();
		}
	}

	/**
	 * Resolve an ERPNext Customer from token data.
	 * Priority: linked_customer > phone lookup > default_customer (Walk-in).
	 */
	_resolve_customer(token) {
		// 1. Already linked to an ERPNext Customer
		if (token.linked_customer) {
			return Promise.resolve(token.linked_customer);
		}
		// 2. Try to find Customer by phone number
		if (token.customer_phone) {
			return frappe.xcall("ch_pos.api.token_api.find_customer_by_phone", {
				phone: token.customer_phone,
			}).then((name) => name || PosState.default_customer || null)
			  .catch(() => PosState.default_customer || null);
		}
		// 3. Fall back to POS Profile's default customer (Walk-in Customer)
		return Promise.resolve(PosState.default_customer || null);
	}

	// ── Retail: Drop Token ──────────────────────────────────────

	_showDropDialog(token) {
		const d = new frappe.ui.Dialog({
			title: `${__("Drop Token")} — ${token.token_display || token.name}`,
			fields: [
				{
					label: __("Customer"),
					fieldtype: "Data",
					fieldname: "customer_name",
					default: token.customer_name,
					read_only: 1,
				},
				{
					label: __("Drop Reason"),
					fieldtype: "Select",
					fieldname: "drop_reason",
					options: DROP_REASONS.join("\n"),
					reqd: 1,
				},
				{
					label: __("Sub-reason / Detail"),
					fieldtype: "Data",
					fieldname: "drop_sub_reason",
					depends_on: "drop_reason",
				},
				{
					label: __("Remarks"),
					fieldtype: "Small Text",
					fieldname: "drop_remarks",
					placeholder: __("Any additional notes…"),
				},
			],
			primary_action_label: `<i class="fa fa-times-circle"></i> ${__("Drop Token")}`,
			primary_action: (values) => {
				d.disable_primary_action();
				frappe.xcall("ch_pos.api.token_api.drop_token", {
					token_name: token.name,
					drop_reason: values.drop_reason,
					drop_sub_reason: values.drop_sub_reason || "",
					drop_remarks: values.drop_remarks || "",
				}).then(() => {
					d.hide();
					frappe.show_alert({
						message: __("Token {0} dropped — {1}", [
							token.token_display || token.name,
							values.drop_reason,
						]),
						indicator: "orange",
					});
					this._loadTokens();
				}).catch((err) => {
					d.enable_primary_action();
					frappe.show_alert({
						message: err.message || __("Failed to drop token"),
						indicator: "red",
					});
				});
			},
		});
		d.show();
	}

	// ── Service: GoFix Request ──────────────────────────────────

	_showConvertDialog(token) {
		const device_display = [token.device_brand, token.device_model].filter(Boolean).join(" ") || token.device_type || "";

		const d = new frappe.ui.Dialog({
			title: `${__("Create GoFix Request")} — ${token.token_display || token.name}`,
			fields: [
				{
					label: __("Customer Info"),
					fieldtype: "Section Break",
					collapsible: 0,
				},
				{
					label: __("Customer Name"),
					fieldtype: "Data",
					fieldname: "customer_name",
					default: token.customer_name,
					read_only: 1,
				},
				{
					label: __("Phone"),
					fieldtype: "Data",
					fieldname: "customer_phone",
					default: token.customer_phone,
					read_only: 1,
				},
				{
					label: __("ERPNext Customer (optional)"),
					fieldtype: "Link",
					fieldname: "customer",
					options: "Customer",
				},
				{
					label: __("Device"),
					fieldtype: "Section Break",
				},
				{
					label: __("Device"),
					fieldtype: "Data",
					fieldname: "device_display",
					default: device_display,
					read_only: 1,
				},
				{
					label: __("Device Item (optional)"),
					fieldtype: "Link",
					fieldname: "device_item",
					options: "Item",
					get_query: () => ({
						filters: {
							item_group: ["in", ["Mobiles", "Smartphones", "Tablets", "Laptops", "Devices"]],
						},
					}),
				},
				{
					label: __("Device Condition"),
					fieldtype: "Select",
					fieldname: "device_condition",
					options: "Good\nMinor Scratches\nCracked Screen\nDamaged\nWater Damage",
					default: "Good",
				},
				{
					label: __("Accessories Received"),
					fieldtype: "Data",
					fieldname: "accessories",
					placeholder: __("Charger, case, earphones…"),
				},
				{
					label: __("Service Details"),
					fieldtype: "Section Break",
				},
				{
					label: __("Issue Category"),
					fieldtype: "Data",
					fieldname: "issue_category",
					default: token.issue_category,
					read_only: 1,
				},
				{
					label: __("Issue Description"),
					fieldtype: "Small Text",
					fieldname: "issue_description",
					default: token.issue_description,
					read_only: 1,
				},
				{
					label: __("Warranty Status"),
					fieldtype: "Select",
					fieldname: "warranty_status",
					options: "Out of Warranty\nUnder Warranty\nExpired",
					default: "Out of Warranty",
				},
				{
					label: __("Customer acknowledges data may be lost"),
					fieldtype: "Check",
					fieldname: "data_disclaimer",
					default: 0,
				},
			],
			primary_action_label: `<i class="fa fa-check"></i> ${__("Create GoFix Request")}`,
			primary_action: (values) => {
				d.get_primary_btn().prop("disabled", true)
					.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Creating…")}`);

				frappe.xcall("ch_pos.api.token_api.convert_token_to_gofix", {
					token_name: token.name,
					pos_profile: PosState.pos_profile,
					customer: values.customer || null,
					device_item: values.device_item || null,
					device_condition: values.device_condition,
					accessories: values.accessories || "",
					warranty_status: values.warranty_status,
					data_disclaimer: values.data_disclaimer ? 1 : 0,
				}).then((r) => {
					d.hide();
					frappe.show_alert({
						message: `
							<b>${__("GoFix Request Created!")}</b><br>
							${r.service_request}
							<a href="/desk/service-request/${r.service_request}" target="_blank"
								style="margin-left:8px;font-size:.85rem">
								${__("Open")} <i class="fa fa-external-link"></i>
							</a>
						`,
						indicator: "green",
					}, 8);
					this._loadTokens();
				}).catch((err) => {
					d.get_primary_btn().prop("disabled", false)
						.html(`<i class="fa fa-check"></i> ${__("Create GoFix Request")}`);
					frappe.show_alert({
						message: err.message || __("Failed to create request"),
						indicator: "red",
					});
				});
			},
		});

		d.show();
	}
}
