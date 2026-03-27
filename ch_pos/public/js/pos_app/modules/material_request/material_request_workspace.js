/**
 * CH POS — Material Request Workspace
 *
 * Store staff can create Material Requests (stock requisitions)
 * from the POS, and view existing pending requests.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class MaterialRequestWorkspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "material_request") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		this.panel = panel;
		this.request_items = [];

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#dbeafe;color:#2563eb">
							<i class="fa fa-clipboard"></i>
						</span>
						${__("Request Stock")}
					</h4>
					<span class="ch-mode-hint">${__("Request models from central warehouse to your store")}</span>
				</div>

				<!-- New Request Form -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header"><i class="fa fa-plus-circle"></i> ${__("New Request")}</div>
					<div class="section-body">
						<!-- Source warehouse + urgency -->
						<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
							<div class="ch-pos-field-group">
								<label style="font-size:var(--pos-fs-2xs);font-weight:700;color:var(--pos-text-secondary)">${__("Source Warehouse")}</label>
								<div class="ch-mr-source-wh"></div>
							</div>
							<div class="ch-pos-field-group">
								<label style="font-size:var(--pos-fs-2xs);font-weight:700;color:var(--pos-text-secondary)">${__("Urgency")}</label>
								<select class="form-control ch-mr-urgency" style="border-radius:var(--pos-radius-sm);height:36px">
									<option value="Standard">${__("Standard (3 days)")}</option>
									<option value="Urgent">${__("Urgent (today)")}</option>
									<option value="Low">${__("Low (1 week)")}</option>
								</select>
							</div>
						</div>
						<!-- Item + qty row -->
						<div class="ch-mr-add-row" style="display:flex;gap:8px;margin-bottom:12px;align-items:center">
							<div class="ch-mr-item-field" style="flex:2"></div>
							<input type="number" class="form-control ch-mr-qty-input" placeholder="${__("Qty")}" min="1" value="1" style="flex:0 0 80px;border-radius:var(--pos-radius-sm);text-align:center">
							<button class="btn btn-primary ch-mr-add-btn" style="border-radius:var(--pos-radius-sm);white-space:nowrap">
								<i class="fa fa-plus"></i> ${__("Add")}
							</button>
						</div>
						<div class="ch-mr-items-list"></div>
						<!-- Notes -->
						<div class="ch-mr-notes-area" style="display:none;margin-top:12px">
							<textarea class="form-control ch-mr-notes" rows="2" placeholder="${__("Notes for central team (optional)...")}" style="border-radius:var(--pos-radius-sm);font-size:var(--pos-fs-sm);resize:vertical"></textarea>
						</div>
						<div class="ch-mr-actions" style="display:none;padding-top:12px;border-top:1px solid var(--pos-border-light);margin-top:12px;text-align:right">
							<button class="btn btn-outline-danger ch-mr-clear-btn" style="border-radius:var(--pos-radius-sm);margin-right:8px">
								${__("Clear")}
							</button>
							<button class="btn btn-primary ch-mr-submit-btn" style="border-radius:var(--pos-radius-sm)">
								<i class="fa fa-paper-plane"></i> ${__("Submit Request")}
							</button>
						</div>
					</div>
				</div>

				<!-- Pending Requests -->
				<div class="ch-pos-section-card">
					<div class="section-header"><i class="fa fa-clock-o"></i> ${__("Pending Requests")}</div>
					<div class="section-body" style="padding:0">
						<div class="ch-mr-pending-loading" style="padding:24px;text-align:center">
							<i class="fa fa-spinner fa-spin" style="opacity:0.3"></i>
						</div>
						<div class="ch-mr-pending-list"></div>
					</div>
				</div>
			</div>
		`);

		this._init_item_field(panel);
		this._init_source_wh(panel);
		this._bind(panel);
		this._load_pending(panel);
	}

	_init_item_field(panel) {
		const el = panel.find(".ch-mr-item-field");
		this.item_field = frappe.ui.form.make_control({
			df: {
				fieldname: "item_code",
				fieldtype: "Link",
				options: "Item",
				placeholder: __("Search model / item..."),
				get_query: () => ({ filters: { disabled: 0, is_stock_item: 1 }, page_length: 99 }),
			},
			parent: el,
			render_input: true,
		});
		this.item_field.$input.css({ "border-radius": "var(--pos-radius-sm)" });
		el.find(".frappe-control").css({ "margin-bottom": "0" });
	}

	_init_source_wh(panel) {
		this.source_wh_field = frappe.ui.form.make_control({
			df: {
				fieldname: "source_wh",
				fieldtype: "Link",
				options: "Warehouse",
				placeholder: __("Central / source warehouse"),
			},
			parent: panel.find(".ch-mr-source-wh"),
			render_input: true,
		});
		this.source_wh_field.$input.css({ "border-radius": "var(--pos-radius-sm)" });
	}

	_bind(panel) {
		panel.on("click", ".ch-mr-add-btn", () => this._add_item(panel));
		panel.on("click", ".ch-mr-clear-btn", () => { this.request_items = []; this._render_items(panel); });
		panel.on("click", ".ch-mr-submit-btn", () => this._submit_request(panel));
		panel.on("click", ".ch-mr-remove-row", function () {
			const idx = $(this).data("idx");
			panel.trigger("mr:remove", [idx]);
		});
		panel.on("mr:remove", (e, idx) => {
			this.request_items.splice(idx, 1);
			this._render_items(panel);
		});
		panel.on("click", ".ch-mr-view-detail", function () {
			const name = $(this).data("name");
			frappe.set_route("Form", "Material Request", name);
		});
	}

	_add_item(panel) {
		const item_code = this.item_field.get_value();
		const qty = parseInt(panel.find(".ch-mr-qty-input").val()) || 1;
		if (!item_code) {
			frappe.show_alert({ message: __("Select an item first"), indicator: "orange" });
			return;
		}
		const existing = this.request_items.find(r => r.item_code === item_code);
		if (existing) {
			existing.qty += qty;
		} else {
			frappe.call({
				method: "frappe.client.get_value",
				args: { doctype: "Item", filters: { name: item_code }, fieldname: ["item_name", "stock_uom"] },
				async: false,
				callback: (r) => {
					const d = r.message || {};
					this.request_items.push({
						item_code,
						item_name: d.item_name || item_code,
						uom: d.stock_uom || "Nos",
						qty,
					});
				},
			});
		}
		this.item_field.set_value("");
		panel.find(".ch-mr-qty-input").val(1);
		this._render_items(panel);
	}

	_render_items(panel) {
		const list = panel.find(".ch-mr-items-list");
		const actions = panel.find(".ch-mr-actions");
		const notes_area = panel.find(".ch-mr-notes-area");
		if (!this.request_items.length) {
			list.html(`<div class="text-muted text-center" style="padding:16px">${__("No items added yet")}</div>`);
			actions.hide();
			notes_area.hide();
			return;
		}
		actions.show();
		notes_area.show();
		list.html(`
			<table class="ch-rpt-table" style="margin:0">
				<thead><tr>
					<th>${__("Item")}</th>
					<th class="text-center" style="width:80px">${__("Qty")}</th>
					<th class="text-center" style="width:80px">${__("UOM")}</th>
					<th style="width:40px"></th>
				</tr></thead>
				<tbody>
					${this.request_items.map((r, idx) => `
						<tr>
							<td>
								<div style="font-weight:600;font-size:var(--pos-fs-sm)">${frappe.utils.escape_html(r.item_name)}</div>
								<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">${frappe.utils.escape_html(r.item_code)}</div>
							</td>
							<td class="text-center"><strong>${r.qty}</strong></td>
							<td class="text-center" style="color:var(--pos-text-muted)">${frappe.utils.escape_html(r.uom)}</td>
							<td class="text-center">
								<button class="btn btn-link text-danger ch-mr-remove-row" data-idx="${idx}" style="padding:2px">
									<i class="fa fa-trash-o"></i>
								</button>
							</td>
						</tr>
					`).join("")}
				</tbody>
			</table>
		`);
	}

	_submit_request(panel) {
		if (!this.request_items.length) return;
		const urgency = panel.find(".ch-mr-urgency").val() || "Standard";
		const notes = panel.find(".ch-mr-notes").val() || "";
		const source_wh = this.source_wh_field ? this.source_wh_field.get_value() : "";
		frappe.call({
			method: "ch_pos.api.pos_api.create_material_request",
			args: {
				pos_profile: PosState.pos_profile,
				items: this.request_items,
				urgency,
				notes: notes || undefined,
				source_warehouse: source_wh || undefined,
			},
			freeze: true,
			freeze_message: __("Creating Material Request..."),
			callback: (r) => {
				if (r.message) {
					frappe.show_alert({ message: __("Material Request {0} created", [r.message]), indicator: "green" });
					this.request_items = [];
					panel.find(".ch-mr-notes").val("");
					this._render_items(panel);
					this._load_pending(panel);
				}
			},
		});
	}

	_load_pending(panel) {
		const loading = panel.find(".ch-mr-pending-loading");
		const list = panel.find(".ch-mr-pending-list");
		loading.show();
		list.empty();

		frappe.call({
			method: "ch_pos.api.pos_api.get_pending_material_requests",
			args: { pos_profile: PosState.pos_profile },
			callback: (r) => {
				loading.hide();
				const requests = r.message || [];
				if (!requests.length) {
					list.html(`
						<div class="ch-pos-empty-state" style="padding:24px">
							<div class="empty-icon"><i class="fa fa-check-circle"></i></div>
							<div class="empty-title">${__("No pending requests")}</div>
							<div class="empty-subtitle">${__("All stock requests have been fulfilled")}</div>
						</div>
					`);
					return;
				}
				list.html(requests.map(mr => {
					const status_cls = ["Draft", "Under Review", "Partially Allocated"].includes(mr.status) ? "ch-pos-badge-warning"
						: ["Allocation Planned", "Procurement Initiated", "In Transit", "Partially Received"].includes(mr.status) ? "ch-pos-badge-info"
						: ["Fulfilled"].includes(mr.status) ? "ch-pos-badge-success"
						: ["Closed With Reason", "Cancelled"].includes(mr.status) ? "ch-pos-badge-muted"
						: "ch-pos-badge-muted";
					return `
						<div class="ch-mr-request-row" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid var(--pos-border-light)">
							<div>
								<div style="font-weight:700;font-size:var(--pos-fs-sm)">${frappe.utils.escape_html(mr.name)}</div>
								<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
									${frappe.datetime.str_to_user(mr.transaction_date)} · ${mr.item_count} ${__("items")}
								</div>
							</div>
							<div style="display:flex;gap:8px;align-items:center">
								<span class="ch-pos-badge ${status_cls}">${frappe.utils.escape_html(mr.status)}</span>
								<button class="btn btn-xs btn-outline-secondary ch-mr-view-detail" data-name="${frappe.utils.escape_html(mr.name)}" style="border-radius:var(--pos-radius-sm)">
									<i class="fa fa-external-link"></i>
								</button>
							</div>
						</div>`;
				}).join(""));
			},
		});
	}
}
