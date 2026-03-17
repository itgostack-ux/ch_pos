/**
 * CH POS — Queue Workspace
 *
 * Shows all Waiting / In-Progress tokens for the current store.
 * The executive can create a GoFix Service Request from any token
 * with one click, pre-filling all device and issue details.
 */
import { PosState, EventBus } from "../../state.js";

export class QueueWorkspace {
	constructor() {
		this._panel = null;
		this._refreshTimer = null;
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
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#fce7f3;color:#be185d">
							<i class="fa fa-ticket"></i>
						</span>
						${__("Service Queue")}
					</h4>
					<span class="ch-mode-hint">${__("Waiting tokens from the kiosk — create a GoFix request in one click")}</span>
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

		frappe.xcall("ch_pos.api.token_api.get_pos_waiting_tokens", { pos_profile })
			.then((tokens) => this._renderTokenList(tokens))
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
				<span style="font-size:.85rem;color:var(--text-muted)">${tokens.length} ${__("token(s) waiting")}</span>
				<span style="font-size:.85rem;color:var(--text-muted)">${__("Auto-refreshes every 30s")}</span>
			</div>
			<div class="ch-queue-cards" style="display:flex;flex-direction:column;gap:var(--pos-space-sm)">
				${cards}
			</div>
		`);

		// Bind convert buttons
		list.find(".ch-queue-convert-btn").on("click", (e) => {
			const name = $(e.currentTarget).data("token");
			const token = tokens.find((t) => t.name === name);
			if (token) this._showConvertDialog(token);
		});
	}

	_tokenCard(t) {
		const statusColor = t.status === "Waiting" ? "#f59e0b" : "#3b82f6";
		const statusIcon  = t.status === "Waiting" ? "fa-clock-o" : "fa-cogs";
		const timeAgo     = frappe.datetime.comment_when(t.creation);
		const device      = [t.device_brand, t.device_model].filter(Boolean).join(" ") || t.device_type || "—";

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
						<div style="color:var(--text-muted);font-size:.85rem;margin-bottom:2px">
							<i class="fa fa-mobile"></i> ${frappe.utils.escape_html(device)}
						</div>
						<div style="color:var(--text-muted);font-size:.82rem">
							<i class="fa fa-wrench"></i> ${frappe.utils.escape_html(t.issue_category || "—")}
							${t.issue_description ? `<span style="margin-left:6px;font-style:italic">${frappe.utils.escape_html(t.issue_description.substring(0, 60))}${t.issue_description.length > 60 ? "…" : ""}</span>` : ""}
						</div>
					</div>
					<div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px;flex-shrink:0">
						<span style="font-size:.75rem;color:var(--text-muted)">${timeAgo}</span>
						<button class="btn btn-primary btn-sm ch-queue-convert-btn"
							data-token="${frappe.utils.escape_html(t.name)}"
							style="white-space:nowrap">
							<i class="fa fa-plus"></i> ${__("GoFix Request")}
						</button>
					</div>
				</div>
			</div>
		`;
	}

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
					// Refresh the queue
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
