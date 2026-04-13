/**
 * CH POS — Material Request Workspace
 *
 * Store staff can create Material Requests (stock requisitions)
 * from the POS, and view / append to existing draft requests.
 *
 * Features:
 *  - Zone-based auto warehouse routing (no source warehouse needed)
 *  - Qty validation against Warehouse Capacity (alert on exceed)
 *  - Draft request list: select an existing draft to append items
 *  - Create new request or add items to an existing draft
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
		this.selected_draft = null;
		this.zone_info = null;

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

				<!-- Zone info banner -->
				<div class="ch-mr-zone-banner" style="display:none;margin-bottom:var(--pos-space-md);padding:10px 14px;background:#f0f9ff;border:1px solid #bae6fd;border-radius:var(--pos-radius-sm)">
					<i class="fa fa-map-marker" style="color:#0284c7"></i>
					<span class="ch-mr-zone-text" style="font-size:var(--pos-fs-sm);color:#0369a1"></span>
				</div>

				<!-- Draft Requests Section -->
				<div class="ch-mr-success-banner" style="display:none"></div>
				<div class="ch-pos-section-card ch-mr-drafts-section" style="margin-bottom:var(--pos-space-md);display:none">
					<div class="section-header"><i class="fa fa-pencil-square-o"></i> ${__("Draft Requests (add items before submitting)")}</div>
					<div class="section-body" style="padding:0">
						<div class="ch-mr-drafts-list"></div>
					</div>
				</div>

				<!-- New Request Form -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header">
						<span class="ch-mr-form-title"><i class="fa fa-plus-circle"></i> ${__("New Request")}</span>
						<span class="ch-mr-editing-badge" style="display:none;font-size:var(--pos-fs-2xs);background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:10px;margin-left:8px">${__("Adding to draft")}</span>
					</div>
					<div class="section-body">
						<!-- Urgency / due target -->
						<div style="display:grid;grid-template-columns:1.1fr 1fr 0.9fr;gap:10px;margin-bottom:12px">
							<div class="ch-pos-field-group">
								<label style="font-size:var(--pos-fs-2xs);font-weight:700;color:var(--pos-text-secondary)">${__("Request Type")}</label>
								<select class="form-control ch-mr-urgency" style="border-radius:var(--pos-radius-sm);height:36px">
									<option value="Urgent">${__("Urgent")}</option>
									<option value="Standard" selected>${__("Standard")}</option>
									<option value="Low">${__("Low")}</option>
								</select>
							</div>
							<div class="ch-pos-field-group">
								<label style="font-size:var(--pos-fs-2xs);font-weight:700;color:var(--pos-text-secondary)">${__("Need By Date")}</label>
								<input type="date" class="form-control ch-mr-needed-date" style="border-radius:var(--pos-radius-sm);height:36px">
							</div>
							<div class="ch-pos-field-group">
								<label style="font-size:var(--pos-fs-2xs);font-weight:700;color:var(--pos-text-secondary)">${__("Need By Time")}</label>
								<input type="time" class="form-control ch-mr-needed-time" style="border-radius:var(--pos-radius-sm);height:36px">
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
						<!-- Capacity alert -->
						<div class="ch-mr-capacity-alert" style="display:none;margin-bottom:12px;padding:8px 12px;border-radius:var(--pos-radius-sm);font-size:var(--pos-fs-sm)"></div>
						<div class="ch-mr-items-list"></div>
						<!-- Notes -->
						<div class="ch-mr-notes-area" style="display:none;margin-top:12px">
							<textarea class="form-control ch-mr-notes" rows="2" placeholder="${__("Notes for central team (optional)...")}" style="border-radius:var(--pos-radius-sm);font-size:var(--pos-fs-sm);resize:vertical"></textarea>
						</div>
						<div class="ch-mr-actions" style="display:none;padding-top:12px;border-top:1px solid var(--pos-border-light);margin-top:12px;text-align:right">
							<button class="btn btn-outline-secondary ch-mr-deselect-draft-btn" style="border-radius:var(--pos-radius-sm);margin-right:8px;display:none">
								${__("New Request Instead")}
							</button>
							<button class="btn btn-outline-danger ch-mr-clear-btn" style="border-radius:var(--pos-radius-sm);margin-right:8px">
								${__("Clear")}
							</button>
							<button class="btn btn-primary ch-mr-submit-btn" style="border-radius:var(--pos-radius-sm)">
								<i class="fa fa-paper-plane"></i> ${__("Create Request")}
							</button>
						</div>
					</div>
				</div>

				<!-- Pending Requests -->
				<div class="ch-pos-section-card">
					<div class="section-header"><i class="fa fa-clock-o"></i> ${__("Submitted Requests")}</div>
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
		this._bind(panel);
		this._apply_due_defaults(panel, true);
		this._load_zone_info(panel);
		this._load_drafts(panel);
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

	_get_due_defaults(urgency) {
		const now = new Date();
		let target = new Date(now);

		if (urgency === "Urgent") {
			target = new Date(now.getTime() + (2 * 60 * 60 * 1000));
		} else if (urgency === "Low") {
			target = new Date(now.getTime() + (7 * 24 * 60 * 60 * 1000));
			target.setHours(18, 0, 0, 0);
		} else {
			target = new Date(now.getTime() + (3 * 24 * 60 * 60 * 1000));
			target.setHours(13, 0, 0, 0);
		}

		const local = new Date(target.getTime() - (target.getTimezoneOffset() * 60000));
		return {
			date: local.toISOString().slice(0, 10),
			time: local.toISOString().slice(11, 16),
		};
	}

	_apply_due_defaults(panel, force = false) {
		const dateInput = panel.find(".ch-mr-needed-date");
		const timeInput = panel.find(".ch-mr-needed-time");
		if (!dateInput.length || !timeInput.length) return;
		if (!force && dateInput.val() && timeInput.val()) return;

		const defaults = this._get_due_defaults(panel.find(".ch-mr-urgency").val() || "Standard");
		dateInput.val(defaults.date);
		timeInput.val(defaults.time);
	}

	_format_delay(minutes) {
		const total = Math.max(parseInt(minutes, 10) || 0, 0);
		const days = Math.floor(total / 1440);
		const hours = Math.floor((total % 1440) / 60);
		const mins = total % 60;
		const parts = [];
		if (days) parts.push(`${days}d`);
		if (hours) parts.push(`${hours}h`);
		if (mins || !parts.length) parts.push(`${mins}m`);
		return parts.join(" ");
	}

	_load_zone_info(panel) {
		frappe.call({
			method: "ch_pos.api.pos_api.get_store_zone_info",
			args: { pos_profile: PosState.pos_profile },
			callback: (r) => {
				this.zone_info = r.message || {};
				const banner = panel.find(".ch-mr-zone-banner");
				if (this.zone_info.zone && this.zone_info.source_warehouse) {
					banner.find(".ch-mr-zone-text").text(
						__("Zone: {0} — Requests route to {1}", [
							this.zone_info.zone,
							this.zone_info.source_warehouse,
						])
					);
					banner.show();
				} else {
					banner.find(".ch-mr-zone-text").html(
						'<span style="color:#dc2626"><i class="fa fa-exclamation-triangle"></i> ' +
						__("No zone configured for this store. Please ask admin to set up a zone.") +
						"</span>"
					);
					banner.css({ background: "#fef2f2", "border-color": "#fecaca" });
					banner.show();
				}
			},
		});
	}

	_bind(panel) {
		panel.on("change", ".ch-mr-urgency", () => this._apply_due_defaults(panel, true));
		panel.on("click", ".ch-mr-add-btn", () => this._add_item(panel));
		panel.on("click", ".ch-mr-clear-btn", () => {
			this.request_items = [];
			this._render_items(panel);
		});
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
		// Draft selection
		panel.on("click", ".ch-mr-draft-select", (e) => {
			const name = $(e.currentTarget).data("name");
			this._select_draft(panel, name);
		});
		panel.on("click", ".ch-mr-deselect-draft-btn", () => {
			this._deselect_draft(panel);
		});
	}

	_add_item(panel) {
		const item_code = this.item_field.get_value();
		const qty = parseInt(panel.find(".ch-mr-qty-input").val()) || 1;
		if (!item_code) {
			frappe.show_alert({ message: __("Select an item first"), indicator: "orange" });
			return;
		}

		// Validate capacity before adding
		this._check_capacity(panel, item_code, qty, () => {
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
			panel.find(".ch-mr-capacity-alert").hide();
			this._render_items(panel);
		});
	}

	_check_capacity(panel, item_code, qty, on_proceed) {
		const alert_el = panel.find(".ch-mr-capacity-alert");
		frappe.call({
			method: "ch_pos.api.pos_api.check_material_request_capacity",
			args: {
				pos_profile: PosState.pos_profile,
				items: JSON.stringify([{ item_code, qty }]),
			},
			callback: (r) => {
				const data = r.message || {};
				const info = data[item_code];
				if (info && info.exceeds) {
					alert_el.html(
						`<i class="fa fa-exclamation-triangle" style="color:#dc2626"></i> ` +
						`<strong>${__("Capacity Warning")}:</strong> ` +
						__("Max: {0}, Current: {1}, Pending: {2}, Headroom: {3}. Requesting {4} would exceed limit.", [
							info.max_qty, info.current_qty, info.pending_qty, info.headroom, qty
						])
					).css({ background: "#fef2f2", border: "1px solid #fecaca", color: "#991b1b" }).show();
					frappe.confirm(
						__("{0}: Requesting {1} would exceed warehouse capacity (Max: {2}, Headroom: {3}). Add anyway?", [
							item_code, qty, info.max_qty, info.headroom
						]),
						() => on_proceed(),
						() => {} // cancelled
					);
				} else if (info && info.has_capacity_rule) {
					alert_el.html(
						`<i class="fa fa-check-circle" style="color:#16a34a"></i> ` +
						__("Stock: {0}, Pending: {1}, Headroom: {2}", [
							info.current_qty, info.pending_qty, info.headroom
						])
					).css({ background: "#f0fdf4", border: "1px solid #bbf7d0", color: "#166534" }).show();
					on_proceed();
				} else {
					alert_el.hide();
					on_proceed();
				}
			},
		});
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
		// Update submit button text based on whether editing a draft
		const submit_btn = panel.find(".ch-mr-submit-btn");
		if (this.selected_draft) {
			submit_btn.html(`<i class="fa fa-plus-circle"></i> ${__("Add to {0}", [this.selected_draft])}`);
		} else {
			submit_btn.html(`<i class="fa fa-paper-plane"></i> ${__("Create Request")}`);
		}
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

		if (this.selected_draft) {
			// Append items to existing draft
			const submit_btn = panel.find(".ch-mr-submit-btn");
			submit_btn.prop("disabled", true);
			console.log("[POS MR] Adding items to draft:", this.selected_draft, this.request_items);
			frappe.call({
				method: "ch_pos.api.pos_api.add_items_to_material_request",
				args: {
					request_name: this.selected_draft,
					items: this.request_items,
				},
				freeze: true,
				freeze_message: __("Adding items to {0}...", [this.selected_draft]),
				callback: (r) => {
					console.log("[POS MR] Add items response:", r);
					submit_btn.prop("disabled", false);
					if (r.message) {
						const banner = panel.find(".ch-mr-success-banner");
						banner.html(`<i class="fa fa-check-circle"></i> ${__("{0} updated — now has {1} items", [r.message.name, r.message.item_count])}`)
							.css({display:"flex",alignItems:"center",gap:"8px",padding:"12px 16px",background:"#dcfce7",color:"#166534",borderRadius:"var(--pos-radius-sm)",fontWeight:600,fontSize:"var(--pos-fs-sm)",marginBottom:"12px"})
							.show();
						setTimeout(() => banner.fadeOut(400), 5000);

						this.request_items = [];
						this.selected_draft = null;
						panel.find(".ch-mr-notes").val("");
						this._render_items(panel);
						this._load_drafts(panel);
						this._update_form_mode(panel);
					}
				},
				error: (err) => {
					console.error("[POS MR] Add items error:", err);
					submit_btn.prop("disabled", false);
					frappe.show_alert({ message: __("Failed to add items. Check console for details."), indicator: "red" });
				},
			});
			return;
		}

		// Create new request
		const urgency = panel.find(".ch-mr-urgency").val() || "Standard";
		const required_by_date = panel.find(".ch-mr-needed-date").val() || "";
		const required_by_time = panel.find(".ch-mr-needed-time").val() || "";
		const notes = panel.find(".ch-mr-notes").val() || "";
		if (!required_by_date || !required_by_time) {
			frappe.show_alert({ message: __("Please choose the required date and time."), indicator: "orange" });
			return;
		}
		const submit_btn = panel.find(".ch-mr-submit-btn");
		submit_btn.prop("disabled", true);
		frappe.call({
			method: "ch_pos.api.pos_api.create_material_request",
			args: {
				pos_profile: PosState.pos_profile,
				items: this.request_items,
				urgency,
				required_by_date,
				required_by_time,
				notes: notes || undefined,
			},
			freeze: true,
			freeze_message: __("Creating Material Request..."),
			callback: (r) => {
				submit_btn.prop("disabled", false);
				if (r.message) {
					const mr_name = r.message;
					// Show prominent success banner
					const banner = panel.find(".ch-mr-success-banner");
					banner.html(`<i class="fa fa-check-circle"></i> ${__("Request {0} created — pending manager approval", [mr_name])}`)
						.css({display:"flex",alignItems:"center",gap:"8px",padding:"12px 16px",background:"#dcfce7",color:"#166534",borderRadius:"var(--pos-radius-sm)",fontWeight:600,fontSize:"var(--pos-fs-sm)",marginBottom:"12px"})
						.show();
					setTimeout(() => banner.fadeOut(400), 5000);

					this.request_items = [];
					panel.find(".ch-mr-notes").val("");
					this._render_items(panel);
					this._load_drafts(panel);
					this._load_pending(panel);

					// Scroll to drafts section so user can see the new draft
					setTimeout(() => {
						const section = panel.find(".ch-mr-drafts-section");
						if (section.length && section.is(":visible")) {
							section[0].scrollIntoView({ behavior: "smooth", block: "start" });
						}
					}, 300);
				}
			},
			error: () => {
				submit_btn.prop("disabled", false);
			},
		});
	}

	// ── Draft request management ──────────────────────────────────

	_load_drafts(panel) {
		frappe.call({
			method: "ch_pos.api.pos_api.get_draft_material_requests",
			args: { pos_profile: PosState.pos_profile },
			callback: (r) => {
				const drafts = r.message || [];
				const section = panel.find(".ch-mr-drafts-section");
				const list = panel.find(".ch-mr-drafts-list");

				if (!drafts.length) {
					section.hide();
					return;
				}
				section.show();
				list.html(drafts.map((d) => {
					const items_text = (d.items || []).map(i =>
						`${frappe.utils.escape_html(i.item_name || i.item_code)} x${i.qty}`
					).join(", ");
					const time = d.request_datetime
						? frappe.datetime.prettyDate(d.request_datetime)
						: frappe.datetime.prettyDate(d.creation);
					const selected = this.selected_draft === d.name;
					return `
						<div class="ch-mr-draft-row ch-mr-draft-select" data-name="${frappe.utils.escape_html(d.name)}"
							style="display:flex;justify-content:space-between;align-items:center;
							padding:12px 16px;border-bottom:1px solid var(--pos-border-light);
							cursor:pointer;${selected ? "background:#eff6ff;border-left:3px solid #2563eb" : ""}">
							<div style="flex:1;min-width:0">
								<div style="display:flex;align-items:center;gap:8px">
									<span style="font-weight:700;font-size:var(--pos-fs-sm)">${frappe.utils.escape_html(d.name)}</span>
									<span class="ch-pos-badge ch-pos-badge-warning" style="font-size:10px">${__("Draft")}</span>
									<span style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">${d.priority}</span>
								</div>
								<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
									${d.item_count} ${__("items")} · ${items_text}
								</div>
								<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">${time}</div>
							</div>
							<div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
								${selected
									? `<span class="ch-pos-badge ch-pos-badge-info">${__("Selected")}</span>`
									: `<button class="btn btn-xs btn-outline-primary" style="border-radius:var(--pos-radius-sm)">
										<i class="fa fa-plus"></i> ${__("Add Items")}
									</button>`
								}
								<button class="btn btn-xs btn-outline-secondary ch-mr-view-detail" data-name="${frappe.utils.escape_html(d.name)}" style="border-radius:var(--pos-radius-sm)">
									<i class="fa fa-external-link"></i>
								</button>
							</div>
						</div>`;
				}).join(""));
			},
		});
	}

	_select_draft(panel, name) {
		this.selected_draft = name;
		this._update_form_mode(panel);
		this._load_drafts(panel); // re-render to show selection
		this._render_items(panel);
		frappe.show_alert({ message: __("Adding items to {0}", [name]), indicator: "blue" });
	}

	_deselect_draft(panel) {
		this.selected_draft = null;
		this._update_form_mode(panel);
		this._load_drafts(panel);
		this._render_items(panel);
	}

	_update_form_mode(panel) {
		const editing_badge = panel.find(".ch-mr-editing-badge");
		const deselect_btn = panel.find(".ch-mr-deselect-draft-btn");
		const urgency_row = panel.find(".ch-mr-urgency").closest(".ch-pos-field-group").parent();
		if (this.selected_draft) {
			editing_badge.text(__("Adding to {0}", [this.selected_draft])).show();
			deselect_btn.show();
			urgency_row.hide(); // urgency already set on draft
		} else {
			editing_badge.hide();
			deselect_btn.hide();
			urgency_row.show();
		}
	}

	// ── Pending (submitted) requests ──────────────────────────────

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
				const requests = (r.message || []).filter(mr => mr.approval_status !== "Pending Approval");
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
					const status_cls = ["Draft", "Pending"].includes(mr.status) ? "ch-pos-badge-warning"
						: ["Ordered", "Partially Ordered", "Partially Received"].includes(mr.status) ? "ch-pos-badge-info"
						: ["Received", "Transferred"].includes(mr.status) ? "ch-pos-badge-success"
						: ["Stopped", "Cancelled"].includes(mr.status) ? "ch-pos-badge-muted"
						: "ch-pos-badge-muted";
					const sla_warn = mr.sla_breached
						? ` <span style="color:#dc2626;font-size:10px"><i class="fa fa-exclamation-circle"></i> SLA</span>` : "";
					const dueText = mr.sla_due_by
						? `${__("Need by")}: ${frappe.datetime.str_to_user(mr.sla_due_by)}`
						: `${__("Need by")}: ${frappe.datetime.str_to_user(mr.transaction_date)}`;
					const delayText = mr.delay_state === "delayed"
						? `<span style="color:#dc2626;font-size:10px;font-weight:700"><i class="fa fa-clock-o"></i> ${__("Delayed by")} ${frappe.utils.escape_html(mr.delay_label || this._format_delay(mr.delay_minutes))}</span>`
						: (mr.delay_state === "due" && mr.delay_label
							? `<span style="color:#92400e;font-size:10px;font-weight:700"><i class="fa fa-hourglass-half"></i> ${__("Due in")} ${frappe.utils.escape_html(mr.delay_label)}</span>`
							: "");
					return `
						<div class="ch-mr-request-row" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid var(--pos-border-light)">
							<div>
								<div style="font-weight:700;font-size:var(--pos-fs-sm)">${frappe.utils.escape_html(mr.name)}${sla_warn}</div>
								<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
									${dueText} · ${mr.item_count} ${__("items")} · ${mr.priority || "Standard"}
								</div>
								${delayText ? `<div style="margin-top:4px">${delayText}</div>` : ""}
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
