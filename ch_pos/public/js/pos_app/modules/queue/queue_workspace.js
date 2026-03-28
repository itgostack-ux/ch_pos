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
			? __("Waiting tokens from the kiosk — create a GoFix request in one click")
			: __("Walk-in tokens — start billing or drop from here");
		const icon_bg = is_svc ? "#fce7f3" : "#ede9fe";
		const icon_fg = is_svc ? "#be185d" : "#6d28d9";

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:${icon_bg};color:${icon_fg}">
							<i class="fa fa-ticket"></i>
						</span>
						${title}
					</h4>
					<span class="ch-mode-hint">${hint}</span>
					<button class="btn btn-sm btn-default ch-queue-refresh-btn" style="margin-left:auto">
						<i class="fa fa-refresh"></i> ${__("Refresh")}
					</button>
				</div>

				<div class="ch-queue-token-list" style="margin-top:var(--pos-space-md)">
					<div class="ch-queue-loading" style="text-align:center;padding:40px;color:var(--text-muted)">
						<i class="fa fa-spinner fa-spin"></i> ${__("Loading tokens…")}
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
				`<div style="text-align:center;padding:40px;color:var(--text-muted)">
					<i class="fa fa-spinner fa-spin"></i> ${__("Loading tokens…")}
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
						`<div style="text-align:center;padding:40px;color:var(--pos-danger)">
							<i class="fa fa-exclamation-circle"></i> ${__("Failed to load queue")}
						</div>`
					);
				}
			});
	}

	_renderTokenList(tokens) {
		if (!this._panel) return;
		const list = this._panel.find(".ch-queue-token-list");

		if (!tokens || !tokens.length) {
			list.html(`
				<div style="text-align:center;padding:60px 20px;color:var(--text-muted)">
					<div style="font-size:3rem;margin-bottom:12px">🎉</div>
					<div style="font-size:1.1rem;font-weight:600">${__("Queue is clear!")}</div>
					<div style="font-size:.85rem;margin-top:6px">${__("No waiting or in-progress tokens right now.")}</div>
				</div>
			`);
			return;
		}

		const cards = tokens.map((t) => this._tokenCard(t)).join("");
		list.html(`
			<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--pos-space-sm)">
				<span style="font-size:.85rem;color:var(--text-muted)">${tokens.length} ${__("token(s)")}</span>
				<span style="font-size:.85rem;color:var(--text-muted)">${__("Auto-refreshes every 30s")}</span>
			</div>
			<div class="ch-queue-cards" style="display:flex;flex-direction:column;gap:var(--pos-space-sm)">
				${cards}
			</div>
		`);

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
		const statusColors = { Waiting: "#f59e0b", Engaged: "#3b82f6", "In Progress": "#8b5cf6" };
		const statusIcons  = { Waiting: "fa-clock-o", Engaged: "fa-handshake-o", "In Progress": "fa-cogs" };
		const statusColor = statusColors[t.status] || "#6b7280";
		const statusIcon  = statusIcons[t.status] || "fa-circle";
		const timeAgo     = frappe.datetime.comment_when(t.creation);

		// Build detail lines based on company type
		let detail_html = "";
		if (is_svc) {
			const device = [t.device_brand, t.device_model].filter(Boolean).join(" ") || t.device_type || "—";
			detail_html = `
				<div style="color:var(--text-muted);font-size:.85rem;margin-bottom:2px">
					<i class="fa fa-mobile"></i> ${frappe.utils.escape_html(device)}
				</div>
				<div style="color:var(--text-muted);font-size:.82rem">
					<i class="fa fa-wrench"></i> ${frappe.utils.escape_html(t.issue_category || "—")}
					${t.issue_description ? `<span style="margin-left:6px;font-style:italic">${frappe.utils.escape_html(t.issue_description.substring(0, 60))}${t.issue_description.length > 60 ? "…" : ""}</span>` : ""}
				</div>`;
		} else {
			// Retail: show purpose, category, brand, budget
			const purpose = t.visit_purpose || "Sales";
			const tags = [t.category_interest, t.brand_interest, t.budget_range].filter(Boolean);
			detail_html = `
				<div style="color:var(--text-muted);font-size:.85rem;margin-bottom:2px">
					<i class="fa fa-tag"></i> ${frappe.utils.escape_html(purpose)}
				</div>`;
			if (tags.length) {
				detail_html += `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:3px">
					${tags.map(tag => `<span style="font-size:.72rem;background:${statusColor}15;color:${statusColor};
						padding:2px 8px;border-radius:10px;font-weight:600">${frappe.utils.escape_html(tag)}</span>`).join("")}
				</div>`;
			}
		}

		// Action buttons
		let actions_html = "";
		if (is_svc) {
			actions_html = `
				<button class="btn btn-primary btn-sm ch-queue-convert-btn"
					data-token="${frappe.utils.escape_html(t.name)}"
					style="white-space:nowrap">
					<i class="fa fa-plus"></i> ${__("GoFix Request")}
				</button>`;
		} else {
			// Retail: Bill + Drop
			const bill_disabled = t.status === "In Progress" ? "disabled" : "";
			actions_html = `
				<div style="display:flex;gap:6px">
					<button class="btn btn-primary btn-sm ch-queue-bill-btn"
						data-token="${frappe.utils.escape_html(t.name)}"
						style="white-space:nowrap" ${bill_disabled}>
						<i class="fa fa-shopping-bag"></i> ${__("Bill")}
					</button>
					<button class="btn btn-outline-danger btn-sm ch-queue-drop-btn"
						data-token="${frappe.utils.escape_html(t.name)}"
						style="white-space:nowrap">
						<i class="fa fa-times"></i> ${__("Drop")}
					</button>
				</div>`;
		}

		return `
			<div class="ch-pos-section-card" style="border-left:4px solid ${statusColor}">
				<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
					<div style="flex:1;min-width:0">
						<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
							<span style="font-size:1.15rem;font-weight:700;color:${statusColor};letter-spacing:.5px">
								${frappe.utils.escape_html(t.token_display || t.name)}
							</span>
							<span style="font-size:.72rem;background:${statusColor}22;color:${statusColor};
								padding:2px 8px;border-radius:12px;font-weight:600">
								<i class="fa ${statusIcon}"></i> ${__(t.status)}
							</span>
						</div>
						<div style="font-weight:600;font-size:.95rem;margin-bottom:2px">
							${frappe.utils.escape_html(t.customer_name)}
							<span style="font-weight:400;color:var(--text-muted);font-size:.85rem;margin-left:6px">
								${frappe.utils.escape_html(t.customer_phone || "")}
							</span>
						</div>
						${detail_html}
					</div>
					<div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px;flex-shrink:0">
						<span style="font-size:.75rem;color:var(--text-muted)">${timeAgo}</span>
						${actions_html}
					</div>
				</div>
			</div>
		`;
	}

	// ── Retail: Start Billing ───────────────────────────────────

	_startBilling(token) {
		const _proceed = () => {
			// Store token reference in state — will be passed to Sales Invoice
			PosState.kiosk_token = token.name;

			// Switch to sell mode
			PosState.active_mode = "sell";
			EventBus.emit("mode:set", "sell");
			EventBus.emit("mode:switch", "sell");

			frappe.show_alert({
				message: __("Billing started for token {0} — {1}", [
					token.token_display || token.name,
					token.customer_name,
				]),
				indicator: "blue",
			}, 5);
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
							<a href="/app/service-request/${r.service_request}" target="_blank"
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
