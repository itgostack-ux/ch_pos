/**
 * CH POS — Stock Transfer Workspace
 *
 * View incoming/outgoing stock transfers and create
 * ad-hoc transfers between warehouses from POS.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class StockTransferWorkspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "stock_transfer") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		this.panel = panel;
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#fef3c7;color:#d97706">
							<i class="fa fa-truck"></i>
						</span>
						${__("Stock Transfers")}
					</h4>
					<span class="ch-mode-hint">${__("Track incoming & outgoing stock movements")}</span>
				</div>

				<!-- Tab Pills -->
				<div class="ch-st-tabs" style="display:flex;gap:8px;margin-bottom:var(--pos-space-md)">
					<button class="ch-pos-category-chip active" data-tab="incoming">
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
					<div class="ch-st-loading" style="padding:40px;text-align:center">
						<i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.3"></i>
					</div>
					<div class="ch-st-body"></div>
				</div>
			</div>
		`);

		this._bind(panel);
		this._load_tab(panel, "incoming");
	}

	_bind(panel) {
		panel.on("click", ".ch-st-tabs .ch-pos-category-chip", (e) => {
			const tab = $(e.currentTarget).data("tab");
			panel.find(".ch-st-tabs .ch-pos-category-chip").removeClass("active");
			$(e.currentTarget).addClass("active");
			if (tab === "new") {
				this._render_new_transfer(panel);
			} else {
				this._load_tab(panel, tab);
			}
		});
		panel.on("click", ".ch-st-view-detail", function () {
			frappe.set_route("Form", "Stock Entry", $(this).data("name"));
		});
		panel.on("click", ".ch-st-accept-btn", function () {
			const name = $(this).data("name");
			panel.trigger("st:accept", [name]);
		});
		panel.on("st:accept", (e, name) => this._accept_transfer(panel, name));

		panel.on("click", ".ch-st-handover-btn", (e) => {
			const name = $(e.currentTarget).data("name");
			frappe.confirm(
				__("Confirm handover of {0}? This will move stock to the transit warehouse.", [name]),
				() => {
					frappe.call({
						method: "ch_erp15.ch_erp15.custom.stock_entry.set_pending_qty",
						args: { StockEntry: name },
						freeze: true,
						freeze_message: __("Moving stock to transit..."),
						callback: (r) => {
							if (r.message) {
								frappe.show_alert({ message: __("Handover complete — {0} is now Pending With Goods", [name]), indicator: "orange" });
								this._load_tab(panel, "outgoing");
							}
						},
					});
				}
			);
		});

		panel.on("click", ".ch-st-cancel-btn", (e) => {
			const name = $(e.currentTarget).data("name");
			frappe.confirm(
				__("Cancel transfer {0}? This will delete the draft Stock Entry.", [name]),
				() => {
					frappe.call({
						method: "frappe.client.delete",
						args: { doctype: "Stock Entry", name },
						freeze: true,
						callback: () => {
							frappe.show_alert({ message: __("Transfer {0} cancelled", [name]), indicator: "red" });
							this._load_tab(panel, "outgoing");
						},
					});
				}
			);
		});
	}

	_load_tab(panel, tab) {
		const loading = panel.find(".ch-st-loading");
		const body = panel.find(".ch-st-body");
		loading.show();
		body.empty();

		frappe.call({
			method: "ch_pos.api.pos_api.get_stock_transfers",
			args: {
				pos_profile: PosState.pos_profile,
				direction: tab,
			},
			callback: (r) => {
				loading.hide();
				const entries = r.message || [];
				if (!entries.length) {
					body.html(`
						<div class="ch-pos-empty-state" style="padding:40px">
							<div class="empty-icon"><i class="fa fa-${tab === "incoming" ? "arrow-down" : "arrow-up"}"></i></div>
							<div class="empty-title">${__("No {0} transfers", [tab])}</div>
							<div class="empty-subtitle">${__("No recent stock movements found")}</div>
						</div>
					`);
					return;
				}
				body.html(`<div class="ch-st-list">${entries.map(se => this._transfer_row(se, tab)).join("")}</div>`);
			},
		});
	}

	_transfer_row(se, tab) {
		// Use custom_status for workflow-aware display
		const cs = se.custom_status || "";
		const ls = se.custom_logistics_status || "";
		const STATUS_COLORS = {
			"Draft": "ch-pos-badge-muted",
			"Pending With Goods": "ch-pos-badge-warning",
			"Ready For Pickup": "ch-pos-badge-info",
			"In Transit": "ch-pos-badge-info",
			"Ready For Receive": "ch-pos-badge-warning",
			"Receive At Transit": "ch-pos-badge-warning",
			"Partially Transferred": "ch-pos-badge-warning",
			"Transferred": "ch-pos-badge-success",
			"Force Closed": "ch-pos-badge-muted",
		};
		const status_label = cs || (se.docstatus === 0 ? __("Draft") : se.docstatus === 1 ? __("Submitted") : __("Cancelled"));
		const status_cls = STATUS_COLORS[cs] || (se.docstatus === 1 ? "ch-pos-badge-success" : "ch-pos-badge-muted");

		const direction_icon = tab === "incoming"
			? `<i class="fa fa-arrow-right" style="color:var(--pos-success);margin:0 6px"></i>`
			: `<i class="fa fa-arrow-right" style="color:var(--pos-danger);margin:0 6px"></i>`;

		const from_wh = frappe.utils.escape_html(se.from_warehouse || "");
		const to_wh = frappe.utils.escape_html(se.to_warehouse || "");

		// Receive button — only when ready (after logistics delivers)
		const can_receive = tab === "incoming" && ["Ready For Receive", "Receive At Transit"].includes(cs);
		const accept_btn = can_receive
			? `<button class="btn btn-xs btn-success ch-st-accept-btn" data-name="${frappe.utils.escape_html(se.name)}" style="border-radius:var(--pos-radius-sm)">
				<i class="fa fa-barcode"></i> ${__("Scan & Receive")}
			   </button>`
			: "";

		// Outgoing: Handover button when goods are still at source (Draft, no custom_status yet)
		const can_handover = tab === "outgoing" && se.docstatus === 0 && !cs;
		const handover_btn = can_handover
			? `<button class="btn btn-xs btn-warning ch-st-handover-btn" data-name="${frappe.utils.escape_html(se.name)}" style="border-radius:var(--pos-radius-sm)">
				<i class="fa fa-sign-out"></i> ${__("Handover")}
			   </button>`
			: "";

		// Outgoing: Cancel button when still Draft (goods not yet handed over)
		const can_cancel = tab === "outgoing" && se.docstatus === 0 && !cs;
		const cancel_btn = can_cancel
			? `<button class="btn btn-xs btn-danger ch-st-cancel-btn" data-name="${frappe.utils.escape_html(se.name)}" style="border-radius:var(--pos-radius-sm)">
				<i class="fa fa-times"></i> ${__("Cancel")}
			   </button>`
			: "";

		// Logistics status badge
		let logistics_badge = "";
		if (ls) {
			const lc = { "Pending Pickup": "#fbbf24", "Picked Up": "#60a5fa", "In Transit": "#3b82f6", "Delivered": "#34d399", "Revert Requested": "#f87171", "Reverted": "#9ca3af" };
			logistics_badge = `<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;background:${lc[ls] || "#e5e7eb"}20;color:${lc[ls] || "#6b7280"}">
				<i class="fa fa-truck" style="font-size:9px"></i> ${frappe.utils.escape_html(ls)}
			</span>`;
		}

		// Courier info from remarks
		let courier_html = "";
		if (se.remarks) {
			const parts = [];
			if (se.remarks.includes("Courier:")) {
				const m = se.remarks.match(/Courier:\s*([^|]+)/);
				if (m) parts.push(`<i class="fa fa-truck"></i> ${frappe.utils.escape_html(m[1].trim())}`);
			}
			if (se.remarks.includes("Tracking:")) {
				const m = se.remarks.match(/Tracking:\s*([^|]+)/);
				if (m) parts.push(`<i class="fa fa-barcode"></i> ${frappe.utils.escape_html(m[1].trim())}`);
			}
			if (parts.length) {
				courier_html = `<div style="display:flex;gap:10px;margin-top:6px;font-size:11px;color:var(--pos-text-muted)">${parts.join(" <span style='opacity:0.3'>·</span> ")}</div>`;
			}
		}

		// Logistics person
		const logistics_person = se.custom_logistics_person
			? `<div style="font-size:11px;color:var(--pos-text-muted);margin-top:4px"><i class="fa fa-user"></i> ${frappe.utils.escape_html(se.custom_logistics_person)}</div>`
			: "";

		return `
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-sm)">
				<div class="section-body" style="padding:12px 16px">
					<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
						<div>
							<div style="font-weight:700;font-size:var(--pos-fs-sm)">${frappe.utils.escape_html(se.name)}</div>
							<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
								${frappe.datetime.str_to_user(se.posting_date)} · ${se.item_count} ${__("items")}
							</div>
						</div>
						<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end">
							<span class="ch-pos-badge ${status_cls}">${frappe.utils.escape_html(status_label)}</span>
							${logistics_badge}
							${accept_btn}
							${handover_btn}
							${cancel_btn}
							<button class="btn btn-xs btn-outline-secondary ch-st-view-detail" data-name="${frappe.utils.escape_html(se.name)}" style="border-radius:var(--pos-radius-sm)">
								<i class="fa fa-external-link"></i>
							</button>
						</div>
					</div>
					<div style="display:flex;align-items:center;font-size:var(--pos-fs-xs);color:var(--pos-text-secondary)">
						<span style="padding:3px 8px;background:var(--pos-surface-sunken);border-radius:var(--pos-radius-sm)">${from_wh}</span>
						${direction_icon}
						<span style="padding:3px 8px;background:var(--pos-surface-sunken);border-radius:var(--pos-radius-sm)">${to_wh}</span>
					</div>
					${courier_html}
					${logistics_person}
				</div>
			</div>`;
	}

	_render_new_transfer(panel) {
		const body = panel.find(".ch-st-body");
		const loading = panel.find(".ch-st-loading").hide();
		this.transfer_items = [];

		body.html(`
			<div class="ch-pos-section-card">
				<div class="section-header"><i class="fa fa-plus-circle"></i> ${__("Create Stock Transfer")}</div>
				<div class="section-body">
					<!-- Mandatory warehouse selection -->
					<div class="ch-st-wh-alert alert alert-warning" style="padding:8px 12px;font-size:12px;font-weight:600;margin-bottom:12px;border-radius:var(--pos-radius-sm)">
						<i class="fa fa-exclamation-triangle"></i> ${__("Both source and destination warehouses are required")}
					</div>
					<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
						<div>
							<label style="font-size:var(--pos-fs-xs);font-weight:700;color:var(--pos-text-secondary);margin-bottom:4px;display:block">
								${__("From Warehouse")} <span style="color:var(--pos-danger)">*</span>
							</label>
							<div class="ch-st-from-wh"></div>
						</div>
						<div>
							<label style="font-size:var(--pos-fs-xs);font-weight:700;color:var(--pos-text-secondary);margin-bottom:4px;display:block">
								${__("To Warehouse")} <span style="color:var(--pos-danger)">*</span>
							</label>
							<div class="ch-st-to-wh"></div>
						</div>
					</div>
					<div style="display:flex;gap:8px;margin-bottom:12px">
						<div class="ch-st-item-field" style="flex:2"></div>
						<input type="number" class="form-control ch-st-qty-input" placeholder="${__("Qty")}" min="1" value="1" style="flex:0 0 80px;border-radius:var(--pos-radius-sm);text-align:center">
						<button class="btn btn-primary ch-st-add-item-btn" style="border-radius:var(--pos-radius-sm);white-space:nowrap">
							<i class="fa fa-plus"></i> ${__("Add")}
						</button>
					</div>
					<div class="ch-st-items-table"></div>

					<!-- Courier Hand-over Section -->
					<div class="ch-st-courier-section" style="display:none;margin-top:16px;padding-top:14px;border-top:1px solid var(--pos-border-light)">
						<div style="font-weight:700;font-size:var(--pos-fs-sm);margin-bottom:10px;color:var(--pos-text-secondary)">
							<i class="fa fa-truck"></i> ${__("Courier Hand-over")}
						</div>
						<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
							<div>
								<label style="font-size:var(--pos-fs-2xs);font-weight:600;color:var(--pos-text-muted)">${__("Courier / Agent Name")}</label>
								<input type="text" class="form-control ch-st-courier-name" placeholder="${__("e.g. BlueDart, Delhivery, Store agent")}" style="border-radius:var(--pos-radius-sm);height:36px">
							</div>
							<div>
								<label style="font-size:var(--pos-fs-2xs);font-weight:600;color:var(--pos-text-muted)">${__("Tracking / AWB No")}</label>
								<input type="text" class="form-control ch-st-courier-tracking" placeholder="${__("Tracking number")}" style="border-radius:var(--pos-radius-sm);height:36px">
							</div>
						</div>
						<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
							<div>
								<label style="font-size:var(--pos-fs-2xs);font-weight:600;color:var(--pos-text-muted)">${__("Expected Delivery Date")}</label>
								<input type="date" class="form-control ch-st-delivery-date" style="border-radius:var(--pos-radius-sm);height:36px">
							</div>
							<div>
								<label style="font-size:var(--pos-fs-2xs);font-weight:600;color:var(--pos-text-muted)">${__("Handover Notes")}</label>
								<input type="text" class="form-control ch-st-handover-notes" placeholder="${__("Special instructions...")}" style="border-radius:var(--pos-radius-sm);height:36px">
							</div>
						</div>
					</div>

					<div class="ch-st-new-actions" style="display:none;text-align:right;padding-top:12px;border-top:1px solid var(--pos-border-light);margin-top:12px">
						<button class="btn btn-primary ch-st-submit-transfer" style="border-radius:var(--pos-radius-sm)">
							<i class="fa fa-paper-plane"></i> ${__("Create Transfer")}
						</button>
					</div>
				</div>
			</div>
		`);

		// Init warehouse fields
		this.from_wh_field = frappe.ui.form.make_control({
			df: { fieldname: "from_wh", fieldtype: "Link", options: "Warehouse", placeholder: __("Source warehouse") },
			parent: body.find(".ch-st-from-wh"),
			render_input: true,
		});
		this.from_wh_field.set_value(PosState.warehouse || "");

		this.to_wh_field = frappe.ui.form.make_control({
			df: { fieldname: "to_wh", fieldtype: "Link", options: "Warehouse", placeholder: __("Target warehouse") },
			parent: body.find(".ch-st-to-wh"),
			render_input: true,
		});

		this.st_item_field = frappe.ui.form.make_control({
			df: {
				fieldname: "item_code",
				fieldtype: "Link",
				options: "Item",
				placeholder: __("Search item..."),
				get_query: () => ({ filters: { disabled: 0, is_stock_item: 1, has_variants: 0 }, page_length: 99 }),
			},
			parent: body.find(".ch-st-item-field"),
			render_input: true,
		});

		// Validate both warehouses selected before allowing add
		const validate_wh = () => {
			const f = this.from_wh_field.get_value();
			const t = this.to_wh_field.get_value();
			const alert_el = body.find(".ch-st-wh-alert");
			if (f && t && f !== t) {
				alert_el.hide();
			} else {
				alert_el.show();
			}
		};
		this.from_wh_field.$input.on("change", validate_wh);
		this.to_wh_field.$input.on("change", validate_wh);

		body.on("click", ".ch-st-add-item-btn", () => {
			const item_code = this.st_item_field.get_value();
			const qty = parseInt(body.find(".ch-st-qty-input").val()) || 1;
			if (!item_code) { frappe.show_alert({ message: __("Select an item"), indicator: "orange" }); return; }

			const existing = this.transfer_items.find(r => r.item_code === item_code);
			if (existing) { existing.qty += qty; }
			else {
				frappe.call({
					method: "frappe.client.get_value",
					args: { doctype: "Item", filters: { name: item_code }, fieldname: ["item_name", "stock_uom"] },
					async: false,
					callback: (r) => {
						const d = r.message || {};
						this.transfer_items.push({ item_code, item_name: d.item_name || item_code, uom: d.stock_uom || "Nos", qty });
					},
				});
			}
			this.st_item_field.set_value("");
			body.find(".ch-st-qty-input").val(1);
			this._render_transfer_items(body);
		});

		body.on("click", ".ch-st-remove-row", function () {
			const idx = $(this).data("idx");
			panel.trigger("st:removeline", [idx]);
		});
		panel.on("st:removeline", (e, idx) => {
			this.transfer_items.splice(idx, 1);
			this._render_transfer_items(body);
		});

		body.on("click", ".ch-st-submit-transfer", () => this._submit_transfer(panel, body));
		this._render_transfer_items(body);
	}

	_render_transfer_items(container) {
		const table = container.find(".ch-st-items-table");
		const actions = container.find(".ch-st-new-actions");
		const courier_section = container.find(".ch-st-courier-section");
		if (!this.transfer_items.length) {
			table.html(`<div class="text-muted text-center" style="padding:16px">${__("No items added yet")}</div>`);
			actions.hide();
			courier_section.hide();
			return;
		}
		actions.show();
		courier_section.show();
		table.html(`
			<table class="ch-rpt-table" style="margin:0">
				<thead><tr>
					<th>${__("Item")}</th>
					<th class="text-center" style="width:80px">${__("Qty")}</th>
					<th class="text-center" style="width:80px">${__("UOM")}</th>
					<th style="width:40px"></th>
				</tr></thead>
				<tbody>
					${this.transfer_items.map((r, idx) => `
						<tr>
							<td>
								<div style="font-weight:600;font-size:var(--pos-fs-sm)">${frappe.utils.escape_html(r.item_name)}</div>
								<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">${frappe.utils.escape_html(r.item_code)}</div>
							</td>
							<td class="text-center"><strong>${r.qty}</strong></td>
							<td class="text-center" style="color:var(--pos-text-muted)">${frappe.utils.escape_html(r.uom)}</td>
							<td class="text-center">
								<button class="btn btn-link text-danger ch-st-remove-row" data-idx="${idx}" style="padding:2px">
									<i class="fa fa-trash-o"></i>
								</button>
							</td>
						</tr>
					`).join("")}
				</tbody>
			</table>
		`);
	}

	_submit_transfer(panel, body) {
		const from_wh = this.from_wh_field.get_value();
		const to_wh = this.to_wh_field.get_value();
		if (!from_wh || !to_wh) {
			frappe.show_alert({ message: __("Both source and destination warehouses are required"), indicator: "red" });
			return;
		}
		if (from_wh === to_wh) {
			frappe.show_alert({ message: __("Source and destination warehouse must be different"), indicator: "orange" });
			return;
		}
		if (!this.transfer_items.length) return;

		const courier_name = body.find(".ch-st-courier-name").val() || "";
		const courier_tracking = body.find(".ch-st-courier-tracking").val() || "";
		const expected_delivery_date = body.find(".ch-st-delivery-date").val() || "";
		const handover_notes = body.find(".ch-st-handover-notes").val() || "";

		frappe.call({
			method: "ch_pos.api.pos_api.create_stock_transfer",
			args: {
				from_warehouse: from_wh,
				to_warehouse: to_wh,
				items: this.transfer_items,
				courier_name: courier_name || undefined,
				courier_tracking: courier_tracking || undefined,
				handover_notes: handover_notes || undefined,
				expected_delivery_date: expected_delivery_date || undefined,
			},
			freeze: true,
			freeze_message: __("Creating Stock Transfer..."),
			callback: (r) => {
				if (r.message) {
					frappe.show_alert({ message: __("Stock Entry {0} created", [r.message]), indicator: "green" });
					this.transfer_items = [];
					// Switch to outgoing tab
					panel.find(".ch-st-tabs .ch-pos-category-chip").removeClass("active");
					panel.find('.ch-st-tabs .ch-pos-category-chip[data-tab="outgoing"]').addClass("active");
					this._load_tab(panel, "outgoing");
				}
			},
		});
	}

	_accept_transfer(panel, name) {
		// Fetch items, then show scan-to-receive dialog
		frappe.call({
			method: "ch_pos.api.pos_api.get_stock_transfer_items",
			args: { stock_entry: name },
			freeze: true,
			callback: (r) => {
				if (!r.message) return;
				this._show_scan_receive_dialog(panel, r.message);
			},
		});
	}

	_show_scan_receive_dialog(panel, data) {
		const items = data.items || [];
		const se_name = data.name;
		// Track received state per item
		const recv_state = items.map(item => ({
			item_code: item.item_code,
			item_name: item.item_name || item.item_code,
			qty: item.qty,
			received: 0,
		}));

		const _render_items = () => {
			return recv_state.map((s, idx) => {
				const done = s.received >= s.qty;
				const cls = done ? "color:var(--pos-success);font-weight:700" : "";
				return `<tr>
					<td>
						<div style="font-weight:600">${frappe.utils.escape_html(s.item_name)}</div>
						<div style="font-size:11px;color:var(--pos-text-muted)">${frappe.utils.escape_html(s.item_code)}</div>
					</td>
					<td class="text-center" style="font-weight:600">${s.qty}</td>
					<td class="text-center" style="${cls}">${s.received}</td>
					<td class="text-center">${s.qty - s.received}</td>
				</tr>`;
			}).join("");
		};

		const _all_done = () => recv_state.every(s => s.received >= s.qty);

		const dialog = new frappe.ui.Dialog({
			title: __("Scan & Receive — {0}", [se_name]),
			size: "large",
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "header_html",
					options: `
						<div style="margin-bottom:12px;color:var(--pos-text-secondary);font-size:13px">
							<strong>${frappe.utils.escape_html(data.from_warehouse || "")}</strong>
							<i class="fa fa-arrow-right" style="margin:0 8px"></i>
							<strong>${frappe.utils.escape_html(data.to_warehouse || "")}</strong>
						</div>`,
				},
				{
					fieldtype: "Data",
					fieldname: "barcode_input",
					label: __("Scan IMEI / Barcode"),
					placeholder: __("Scan or type barcode then press Enter"),
				},
				{
					fieldtype: "HTML",
					fieldname: "scan_status",
					options: "",
				},
				{
					fieldtype: "HTML",
					fieldname: "items_table",
					options: `
						<table class="table table-bordered ch-scan-recv-table" style="margin:0">
							<thead><tr>
								<th>${__("Item")}</th>
								<th class="text-center" style="width:70px">${__("Sent")}</th>
								<th class="text-center" style="width:80px">${__("Received")}</th>
								<th class="text-center" style="width:80px">${__("Pending")}</th>
							</tr></thead>
							<tbody>${_render_items()}</tbody>
						</table>`,
				},
			],
			primary_action_label: __("Confirm Received"),
			primary_action: () => {
				dialog.hide();
				frappe.call({
					method: "ch_pos.api.pos_api.pos_confirm_receive",
					args: { stock_entry: se_name },
					freeze: true,
					freeze_message: __("Finalizing receive..."),
					callback: (r) => {
						if (r.message) {
							const msg = r.message.partial
								? __("Transfer {0} partially received", [se_name])
								: __("Transfer {0} received in full", [se_name]);
							frappe.show_alert({ message: msg, indicator: "green" });
							this._load_tab(panel, "incoming");
						}
					},
				});
			},
		});

		// Barcode scan handler
		const barcode_field = dialog.fields_dict.barcode_input;
		barcode_field.$input.on("keydown", (e) => {
			if (e.key !== "Enter") return;
			e.preventDefault();
			const barcode = barcode_field.get_value().trim();
			if (!barcode) return;
			barcode_field.set_value("");

			frappe.call({
				method: "ch_pos.api.pos_api.pos_scan_receive",
				args: { stock_entry: se_name, barcode },
				callback: (r) => {
					if (r.message) {
						// Update local state from server response
						(r.message.items || []).forEach(si => {
							const local = recv_state.find(s => s.item_code === si.item_code);
							if (local) local.received = si.received_qty;
						});
						// Update table
						dialog.$wrapper.find(".ch-scan-recv-table tbody").html(_render_items());
						// Show success
						dialog.fields_dict.scan_status.$wrapper.html(
							`<div class="alert alert-success" style="padding:6px 10px;font-size:12px;margin:4px 0">
								<i class="fa fa-check-circle"></i> ${frappe.utils.escape_html(r.message.item_code)} scanned
							</div>`
						);
						// Auto-focus barcode field
						setTimeout(() => barcode_field.$input.focus(), 100);

						if (_all_done()) {
							dialog.fields_dict.scan_status.$wrapper.html(
								`<div class="alert alert-info" style="padding:8px 12px;font-size:13px;margin:4px 0">
									<i class="fa fa-check-circle"></i> <strong>${__("All items scanned!")}</strong> ${__("Click Confirm Received to complete.")}
								</div>`
							);
						}
					}
				},
				error: () => {
					dialog.fields_dict.scan_status.$wrapper.html(
						`<div class="alert alert-danger" style="padding:6px 10px;font-size:12px;margin:4px 0">
							<i class="fa fa-exclamation-triangle"></i> ${__("Barcode not found or already fully received")}
						</div>`
					);
				},
			});
		});

		dialog.show();
		// Focus scan input immediately
		setTimeout(() => barcode_field.$input.focus(), 200);
	}
}
