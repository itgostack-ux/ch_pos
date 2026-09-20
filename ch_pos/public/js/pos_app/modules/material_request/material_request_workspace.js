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

// Frappe marks any image over 200 KB for compression as it is picked, and
// hiding the Optimize checkbox only removes the way to refuse — the squeezing
// still happens. A store's photo of a cracked panel is evidence, so the flag
// is cleared on every file the dialog takes while it is open.
function _ch_keep_photos_unoptimized(uploader) {
	const clear = () => (uploader.uploader?.files || []).forEach((f) => {
		if (f.optimize) f.optimize = false;
	});
	const timer = setInterval(clear, 150);
	const stop = () => clearInterval(timer);
	uploader.dialog?.$wrapper?.on("hidden.bs.modal", stop);
	// A dialog that is never closed cannot hold the timer for the session.
	setTimeout(stop, 10 * 60 * 1000);
}

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
		// Track the Request Type / Need By Date / Need By Time the current
		// in-progress item list was actually built under — a Material
		// Request has exactly one value for each across the whole document,
		// not one per line, so changing any of them mid-build would
		// silently misrepresent whichever items were added under the old
		// value. See _guard_field_change.
		this._confirmed_urgency = "Standard";
		this._confirmed_needed_date = null;
		this._confirmed_needed_time = null;

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
								<div class="ch-mr-needed-date"></div>
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
		this._init_needed_date_field(panel);
		this._bind(panel);
		this._apply_due_defaults(panel, true);
		this._confirmed_needed_date = this.needed_date_field.get_value();
		this._confirmed_needed_time = panel.find(".ch-mr-needed-time").val();
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
				get_query: () => ({ filters: { disabled: 0, is_stock_item: 1, has_variants: 0 }, page_length: 99 }),
			},
			parent: el,
			render_input: true,
		});
		this.item_field.$input.css({ "border-radius": "var(--pos-radius-sm)" });
		el.find(".frappe-control").css({ "margin-bottom": "0" });
	}

	_init_needed_date_field(panel) {
		// A native <input type="date"> displays per the browser/OS locale,
		// not the site's configured date_format — Frappe's own Date control
		// (same one used for every other date field on the site) reads
		// sys_defaults.date_format instead, so this always renders dd-mm-yyyy
		// regardless of the browser. get_value()/set_value() still work in
		// plain ISO (yyyy-mm-dd), so nothing downstream needs to change.
		const el = panel.find(".ch-mr-needed-date");
		this.needed_date_field = frappe.ui.form.make_control({
			df: {
				fieldname: "needed_date",
				fieldtype: "Date",
				// Stock can't be requested for a date that's already gone —
				// grey out everything before today in the picker. Must be
				// midnight, not `new Date()`'s current time-of-day: air-datepicker
				// compares each day cell's own midnight timestamp against this
				// value, so a same-day minDate carrying the current clock time
				// would push its own comparison point past midnight and disable
				// today itself along with the actual past dates.
				min_date: new Date(new Date().setHours(0, 0, 0, 0)),
			},
			parent: el,
			render_input: true,
		});
		this.needed_date_field.$input.css({ "border-radius": "var(--pos-radius-sm)", height: "36px" });
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

	_now_hhmm() {
		const now = new Date();
		return String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
	}

	/**
	 * Need By Time only makes sense restricted to "not yet passed" when Need
	 * By Date is today — any other (future) date has no such constraint.
	 * Sets the native `min` so the browser's own time picker/validity UI
	 * reflects it; submission is still hard-checked in _submit_request since
	 * `min` alone doesn't stop a typed-in past value in every browser.
	 */
	_apply_time_min(panel) {
		const timeInput = panel.find(".ch-mr-needed-time");
		if (!this.needed_date_field || !timeInput.length) return;
		const is_today = this.needed_date_field.get_value() === frappe.datetime.nowdate();
		if (is_today) {
			timeInput.attr("min", this._now_hhmm());
		} else {
			timeInput.removeAttr("min");
		}
	}

	_apply_due_defaults(panel, force = false) {
		const timeInput = panel.find(".ch-mr-needed-time");
		if (!this.needed_date_field || !timeInput.length) return;
		if (!force && this.needed_date_field.get_value() && timeInput.val()) return;

		const defaults = this._get_due_defaults(panel.find(".ch-mr-urgency").val() || "Standard");
		this.needed_date_field.set_value(defaults.date);
		timeInput.val(defaults.time);
		this._apply_time_min(panel);
	}

	/**
	 * Shared guard for Request Type / Need By Date / Need By Time — a
	 * Material Request has exactly one value for each across the whole
	 * document, not one per line, so changing any of them after items are
	 * already added would silently apply the new value to items the user
	 * added expecting the old one. If nothing's been added yet, or it's a
	 * no-op change, just accepts the new value (calling on_adopt). Otherwise
	 * confirms: Yes clears the in-progress item list and accepts the new
	 * value; No reverts the field via set_value and leaves the items
	 * untouched.
	 */
	_guard_field_change(panel, { field_label, tracker_key, get_value, set_value, on_adopt }) {
		const new_value = get_value();
		const previous = this[tracker_key];
		if (!this.request_items.length || new_value === previous) {
			this[tracker_key] = new_value;
			on_adopt && on_adopt();
			return;
		}

		const item_count = this.request_items.length;
		frappe.confirm(
			__("You already added {0} item(s) with {1} set to {2}. Changing it to {3} will clear {0} out — continue?",
				[item_count, field_label, previous, new_value]),
			() => {
				this[tracker_key] = new_value;
				this.request_items = [];
				this._render_items(panel);
				on_adopt && on_adopt();
			},
			() => {
				set_value(previous);
			}
		);
	}

	_on_urgency_change(panel, $select) {
		this._guard_field_change(panel, {
			field_label: __("Request Type"),
			tracker_key: "_confirmed_urgency",
			get_value: () => $select.val(),
			set_value: (v) => $select.val(v),
			on_adopt: () => this._apply_due_defaults(panel, true),
		});
	}

	_on_needed_date_change(panel) {
		this._guard_field_change(panel, {
			field_label: __("Need By Date"),
			tracker_key: "_confirmed_needed_date",
			get_value: () => this.needed_date_field.get_value(),
			set_value: (v) => this.needed_date_field.set_value(v),
			on_adopt: () => this._apply_time_min(panel),
		});
	}

	_on_needed_time_change(panel, $input) {
		this._guard_field_change(panel, {
			field_label: __("Need By Time"),
			tracker_key: "_confirmed_needed_time",
			get_value: () => $input.val(),
			set_value: (v) => $input.val(v),
		});
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
		panel.on("change", ".ch-mr-urgency", (e) => this._on_urgency_change(panel, $(e.currentTarget)));
		this.needed_date_field.$input.on("change", () => this._on_needed_date_change(panel));
		panel.on("change", ".ch-mr-needed-time", (e) => this._on_needed_time_change(panel, $(e.currentTarget)));
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
		panel.on("click", ".ch-mr-item-photo-btn", (e) => {
			this._upload_item_photo(panel, $(e.currentTarget).data("idx"));
		});
		panel.on("click", ".ch-mr-item-photo-thumb", (e) => {
			this._show_item_photos(panel, $(e.currentTarget).data("idx"));
		});
		panel.on("click", ".ch-mr-photo-clear", (e) => {
			e.stopPropagation();
			this._remove_item_photos(panel, $(e.currentTarget).data("idx"));
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
		panel.on("click", ".ch-mr-id-link", (e) => {
			e.stopPropagation();
			this._show_mr_detail_popup($(e.currentTarget).data("name"));
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
							photo: null,
							photos: [],
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
					<th class="text-center" style="width:70px">${__("Photo")}</th>
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
								${(r.photos || []).length
									? `<span class="ch-mr-photo-set" style="display:inline-flex;align-items:center;gap:4px">
										<img src="${frappe.utils.escape_html(r.photos[0])}" data-idx="${idx}" class="ch-mr-item-photo-thumb"
											style="width:32px;height:32px;object-fit:cover;border-radius:4px;cursor:pointer"
											title="${__("View photos")}">
										${r.photos.length > 1
											? `<span style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">+${r.photos.length - 1}</span>`
											: ""}
										<button class="btn btn-xs btn-outline-secondary ch-mr-item-photo-btn" data-idx="${idx}"
											title="${__("Add more photos")}" style="padding:2px 6px">
											<i class="fa fa-camera"></i>
										</button>
										<button class="btn btn-link text-danger ch-mr-photo-clear" data-idx="${idx}"
											title="${__("Remove photos")}" style="padding:0 2px">
											<i class="fa fa-times"></i>
										</button>
									</span>`
									: `<button class="btn btn-xs btn-outline-secondary ch-mr-item-photo-btn" data-idx="${idx}" title="${__("Upload photos")}" style="padding:4px 8px">
										<i class="fa fa-camera"></i>
									</button>`
								}
							</td>
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

	_upload_item_photo(panel, idx) {
		const row = this.request_items[idx];
		if (!row) return;
		const uploader = new frappe.ui.FileUploader({
			// One picture rarely says it: the panel, the box label and the
			// shelf it came off are three photos of the same request line.
			allow_multiple: true,
			upload_notes: __("Pick every photo for this item at once — you can select more than one."),
			// The counter is not the place to be asked about compression.
			allow_toggle_optimize: false,
			restrictions: { allowed_file_types: ["image/*"] },
			on_success: (file_doc) => {
				const url = file_doc && file_doc.file_url;
				if (!url) return;
				row.photos = row.photos || [];
				// Frappe gives the same file_url to the same image however
				// often it is uploaded, so two shots that happen to share a
				// name stay separate while the identical one is not doubled.
				if (row.photos.includes(url)) {
					frappe.show_alert({
						message: __("That photo is already on this line"), indicator: "orange" });
					return;
				}
				row.photos.push(url);
				row.photo = row.photos[0];
				this._render_items(panel);
			},
		});
		_ch_keep_photos_unoptimized(uploader);
	}

	// Thumbnails at 32px prove a photo was taken, not what it shows. Before
	// the request goes off to a hub that cannot ask "which panel?", whoever
	// took them gets to see them at a size worth checking — and drop the one
	// that turned out to be a picture of the floor.
	_show_item_photos(panel, idx) {
		const row = this.request_items[idx];
		if (!row) return;
		if (!(row.photos || []).length) return this._upload_item_photo(panel, idx);
		const esc = frappe.utils.escape_html;

		const dialog = new frappe.ui.Dialog({
			title: __("Photos — {0}", [row.item_name || row.item_code]),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "gallery" }],
			primary_action_label: __("Add More"),
			primary_action: () => {
				dialog.hide();
				this._upload_item_photo(panel, idx);
			},
		});

		const draw = () => {
			if (!row.photos.length) {
				dialog.hide();
				return;
			}
			dialog.fields_dict.gallery.$wrapper.html(`
				<div style="display:flex;flex-wrap:wrap;gap:12px">
					${row.photos.map((url, i) => `
						<div style="position:relative;text-align:center">
							<a href="${esc(url)}" target="_blank" rel="noopener"
								title="${__("Open full size")}">
								<img src="${esc(url)}" style="width:170px;height:170px;object-fit:cover;
									border-radius:6px;border:1px solid var(--pos-border-light,#e5e7eb)">
							</a>
							<button class="btn btn-xs btn-danger ch-mr-photo-drop" data-i="${i}"
								title="${__("Remove this photo")}"
								style="position:absolute;top:6px;right:6px;padding:1px 7px;line-height:1.4">
								&times;
							</button>
							<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted);margin-top:4px">
								${__("Photo {0} of {1}", [i + 1, row.photos.length])}
							</div>
						</div>`).join("")}
				</div>`);
		};

		dialog.$wrapper.on("click", ".ch-mr-photo-drop", (e) => {
			const i = parseInt($(e.currentTarget).data("i"), 10);
			row.photos.splice(i, 1);
			row.photo = row.photos[0] || null;
			this._render_items(panel);
			draw();
		});

		draw();
		dialog.show();
	}

	_remove_item_photos(panel, idx) {
		const row = this.request_items[idx];
		if (!row) return;
		row.photos = [];
		row.photo = null;
		this._render_items(panel);
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
		const required_by_date = (this.needed_date_field && this.needed_date_field.get_value()) || "";
		const required_by_time = panel.find(".ch-mr-needed-time").val() || "";
		const notes = panel.find(".ch-mr-notes").val() || "";
		if (!required_by_date || !required_by_time) {
			frappe.show_alert({ message: __("Please choose the required date and time."), indicator: "orange" });
			return;
		}
		// `min` on the time input is a UI hint only — browsers still let a
		// past value through if it was typed in directly, so re-check here.
		if (required_by_date === frappe.datetime.nowdate() && required_by_time < this._now_hhmm()) {
			frappe.show_alert({ message: __("Need By Time can't be in the past for today's date."), indicator: "orange" });
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
					// A sales company returns just the request name and the request
					// waits for a manager. A service company's request is accepted
					// and routed on the spot, and comes back as an object saying
					// where each line is coming from — say so, rather than telling
					// the store to wait for an approval that already happened.
					const res = r.message;
					const auto = (res && typeof res === "object") ? res : null;
					const mr_name = auto ? auto.name : res;
					const banner = panel.find(".ch-mr-success-banner");
					banner.html(`<i class="fa fa-check-circle"></i> ${this._request_outcome_text(auto, mr_name)}`)
						.css({display:"flex",alignItems:"center",gap:"8px",padding:"12px 16px",background:"#dcfce7",color:"#166534",borderRadius:"var(--pos-radius-sm)",fontWeight:600,fontSize:"var(--pos-fs-sm)",marginBottom:"12px"})
						.show();
					setTimeout(() => banner.fadeOut(auto ? 9000 : 5000), 5000);

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

	/**
	 * What to tell the store about the request they just made.
	 *
	 * Three outcomes, and the difference matters to them: it is waiting on a
	 * person, it is on its way from a named store, or it has to be bought and
	 * will take a supplier lead time.
	 */
	_request_outcome_text(auto, mr_name) {
		if (!auto || !auto.auto_sourced) {
			return __("Request {0} created — pending manager approval", [mr_name]);
		}
		if (auto.converted_to_purchase) {
			return __("Request {0} accepted — not available anywhere in the network, released for purchase", [mr_name]);
		}
		const from = [...new Set((auto.transferred || []).map((t) => t.source_warehouse))];
		const moving = from.length
			? __("Request {0} accepted — transferring from {1}", [mr_name, from.join(", ")])
			: __("Request {0} accepted", [mr_name]);
		const short = (auto.purchased || []).length;
		if (short && auto.purchase_request) {
			return `${moving}. ${__("{0} item(s) not in the network — purchase request {1} raised", [short, auto.purchase_request])}`;
		}
		return moving;
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
				this._mr_lookup = this._mr_lookup || {};
				drafts.forEach((d) => { this._mr_lookup[d.name] = d; });
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
									<span class="ch-mr-id-link" data-name="${frappe.utils.escape_html(d.name)}"
										style="font-weight:700;font-size:var(--pos-fs-sm);cursor:pointer;text-decoration:underline dotted"
										title="${__("View details")}">${frappe.utils.escape_html(d.name)}</span>
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

	_show_mr_detail_popup(name) {
		const d = (this._mr_lookup || {})[name];
		if (!d) return;
		const esc = frappe.utils.escape_html;
		const priority = esc(d.priority || "");
		const rows = (d.items || []).length
			? d.items.map(it => `
				<tr>
					<td>
						<div style="font-weight:600">${esc(it.item_name || it.item_code)}</div>
						<div style="font-size:11px;color:var(--pos-text-muted)">${esc(it.item_code)}</div>
					</td>
					<td class="text-center" style="font-weight:600">${flt(it.qty)} ${esc(it.uom || "")}</td>
					<td class="text-center">${priority}</td>
					<td class="text-center">
						${it.photo
							? `<img src="${esc(it.photo)}" style="width:32px;height:32px;object-fit:cover;border-radius:4px">`
							: ""}
					</td>
				</tr>`).join("")
			: `<tr><td colspan="4" style="text-align:center;color:var(--pos-text-muted)">${__("No items found")}</td></tr>`;

		const dialog = new frappe.ui.Dialog({
			title: __("{0} details", [name]),
			fields: [{
				fieldtype: "HTML",
				fieldname: "draft_detail_html",
				options: `
					<table class="table table-bordered" style="margin-bottom:0">
						<thead>
							<tr>
								<th>${__("Item")}</th>
								<th class="text-center" style="width:100px">${__("Qty")}</th>
								<th class="text-center" style="width:120px">${__("Request Type")}</th>
								<th class="text-center" style="width:60px">${__("Photo")}</th>
							</tr>
						</thead>
						<tbody>${rows}</tbody>
					</table>`,
			}],
		});
		dialog.show();
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
				this._mr_lookup = this._mr_lookup || {};
				requests.forEach((mr) => { this._mr_lookup[mr.name] = mr; });
				list.html(requests.map(mr => {
					// Prefer the server-computed display_status so terminal MR
					// states (Stopped / Received / Transferred / Issued / short-
					// closed) render as a single friendly "Completed" badge.
					const shown_status = mr.display_status || mr.status;
					const status_cls = shown_status === "Completed" ? "ch-pos-badge-success"
						: ["Draft", "Pending"].includes(shown_status) ? "ch-pos-badge-warning"
						: ["Ordered", "Partially Ordered", "Partially Received"].includes(shown_status) ? "ch-pos-badge-info"
						: ["Received", "Transferred"].includes(shown_status) ? "ch-pos-badge-success"
						: ["Stopped", "Cancelled"].includes(shown_status) ? "ch-pos-badge-muted"
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
					const priority = mr.priority || "Standard";
					return `
						<div class="ch-mr-request-row" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid var(--pos-border-light)">
							<div>
								<div style="display:flex;align-items:center;gap:8px">
									<span class="ch-mr-id-link" data-name="${frappe.utils.escape_html(mr.name)}"
										style="font-weight:700;font-size:var(--pos-fs-sm);cursor:pointer;text-decoration:underline dotted"
										title="${__("View details")}">${frappe.utils.escape_html(mr.name)}</span>${sla_warn}
									<span style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">${frappe.utils.escape_html(priority)}</span>
								</div>
								<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
									${dueText} · ${mr.item_count} ${__("items")}
								</div>
								${delayText ? `<div style="margin-top:4px">${delayText}</div>` : ""}
							</div>
							<div style="display:flex;gap:8px;align-items:center">
								<span class="ch-pos-badge ${status_cls}">${frappe.utils.escape_html(shown_status)}</span>
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
