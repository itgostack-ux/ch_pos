/**
 * CH POS — Warranty & Service Dashboard
 *
 * Search by phone number OR IMEI/serial to see:
 * - All customer devices with warranty/VAS plan status
 * - Manufacturer warranty (from Serial No.warranty_expiry_date)
 * - Plan eligibility: active, expired, exhausted
 * - Raise claims → routed to GoFix or pending approval
 * - Track pending claims pipeline
 */
import { PosState, EventBus } from "../../state.js";
import { assert_india_phone } from "../../shared/helpers.js";

export class ClaimsWorkspace {
	constructor() {
		this._dashboard = null;
		this._selected_device = null;
		this._refresh_timer = null;
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "claims") {
				this._stop_auto_refresh();
				return;
			}
			this.render(ctx.panel);
		});
	}

	render(panel) {
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#fef3c7;color:#d97706">
							<i class="fa fa-shield"></i>
						</span>
						${__("Warranty & Service")}
					</h4>
					<span class="ch-mode-hint">${__("Search by phone number or IMEI to view all warranties and raise claims")}</span>
				</div>

				<!-- Search -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header"><i class="fa fa-search"></i> ${__("Customer / Device Lookup")}</div>
					<div class="section-body">
						<div style="display:flex;gap:var(--pos-space-sm)">
							<input type="text" class="form-control ch-claim-search"
								placeholder="${__("Phone number, IMEI, or Serial No...")}"
								style="flex:1;font-size:16px">
							<button class="btn btn-primary ch-claim-lookup">
								<i class="fa fa-search"></i> ${__("Search")}
							</button>
						</div>
					</div>
				</div>

				<!-- Dashboard Area (customer + devices + plans) -->
				<div class="ch-claim-dashboard" style="display:none"></div>

				<!-- Claim Form (hidden until a device is selected) -->
				<div class="ch-claim-form-area" style="display:none"></div>

				<!-- Pending Claims Pipeline -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header" style="display:flex;align-items:center;justify-content:space-between">
						<span><i class="fa fa-list"></i> ${__("Recent Claims")}</span>
						<button class="btn btn-xs btn-default ch-claim-refresh" style="border-radius:var(--pos-radius-sm)">
							<i class="fa fa-refresh"></i>
						</button>
					</div>
					<div class="section-body ch-claim-pipeline">
						<div class="text-muted text-center" style="padding:16px">${__("Loading...")}</div>
					</div>
				</div>
			</div>
		`);

		this._bind(panel);
		this._load_claims_pipeline(panel);
		this._start_auto_refresh(panel);
	}

	_bind(panel) {
		panel.on("keydown", ".ch-claim-search", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				panel.find(".ch-claim-lookup").trigger("click");
			}
		});

		panel.on("click", ".ch-claim-lookup", () => {
			const q = panel.find(".ch-claim-search").val().trim();
			if (!q) {
				frappe.show_alert({ message: __("Enter a phone number, IMEI, or serial"), indicator: "orange" });
				return;
			}
			this._search(panel, q);
		});

		panel.on("click", ".ch-claim-refresh", () => this._load_claims_pipeline(panel));

		// Raise claim for a device
		panel.on("click", ".ch-dev-raise-claim", (e) => {
			const idx = $(e.currentTarget).data("idx");
			this._show_claim_form(panel, idx);
		});

		panel.on("click", ".ch-claim-submit", () => this._submit_claim(panel));
		panel.on("click", ".ch-claim-cancel-form", () => {
			panel.find(".ch-claim-form-area").hide();
		});

		panel.on("change", ".ch-claim-mode", (e) => {
			const mode = $(e.currentTarget).val();
			const needs_pickup = ["Pickup", "Courier"].includes(mode);
			panel.find(".ch-claim-pickup-fields").toggle(needs_pickup);
		});

		panel.on("click", ".ch-claim-log-action", (e) => {
			const $btn = $(e.currentTarget);
			const claim_name = $btn.data("claim");
			const action = $btn.data("action");
			if (claim_name && action) {
				this._handle_logistics_action(panel, claim_name, action);
			}
		});

		// Claim detail
		panel.on("click", ".ch-claim-track", (e) => {
			const name = $(e.currentTarget).data("claim");
			if (name) this._show_claim_detail(panel, name);
		});

		// Back from detail
		panel.on("click", ".ch-claim-back", () => {
			if (this._dashboard) {
				this._render_dashboard(panel, this._dashboard);
			}
		});

		setTimeout(() => panel.find(".ch-claim-search").focus(), 200);
	}

	// ── Search (phone / IMEI / serial) ──────────────────────────────

	_search(panel, query) {
		const btn = panel.find(".ch-claim-lookup");
		btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i>`);
		panel.find(".ch-claim-form-area").hide();

		frappe.xcall(
			"ch_item_master.ch_item_master.warranty_api.get_customer_warranty_dashboard",
			{ identifier: query, company: PosState.company }
		).then((data) => {
			this._dashboard = data;
			if (!data.found) {
				panel.find(".ch-claim-dashboard").html(`
					<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
						<div class="section-body text-center" style="padding:24px;color:var(--pos-muted)">
							<i class="fa fa-question-circle fa-2x"></i>
							<p style="margin-top:8px">${data.message || __("No results found")}</p>
						</div>
					</div>
				`).show();
			} else {
				this._render_dashboard(panel, data);
			}
		}).catch((err) => {
			panel.find(".ch-claim-dashboard").html(`
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-body text-center" style="padding:24px;color:var(--pos-danger)">
						<i class="fa fa-exclamation-triangle fa-2x"></i>
						<p style="margin-top:8px">${err.message || __("Search failed")}</p>
					</div>
				</div>
			`).show();
		}).finally(() => {
			btn.prop("disabled", false).html(`<i class="fa fa-search"></i> ${__("Search")}`);
		});
	}

	// ── Customer Dashboard ──────────────────────────────────────────

	_render_dashboard(panel, data) {
		const { customer, customer_name, customer_phone, devices, summary } = data;
		const unlinked_plans = data.unlinked_plans || [];
		panel.find(".ch-claim-form-area").hide();

		let html = `
			<!-- Customer Header -->
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
				<div class="section-header" style="display:flex;justify-content:space-between;align-items:center">
					<span><i class="fa fa-user"></i> ${__("Customer")}</span>
					<div style="display:flex;gap:8px">
						<span class="badge" style="background:#dbeafe;color:#1e40af;padding:3px 8px;font-size:11px">
							${summary.total_devices} ${__("device(s)")}
						</span>
						<span class="badge" style="background:${summary.active_plans > 0 ? '#dcfce7' : '#fef2f2'};
							color:${summary.active_plans > 0 ? '#166534' : '#991b1b'};padding:3px 8px;font-size:11px">
							${summary.active_plans} ${__("active plan(s)")}
						</span>
					</div>
				</div>
				<div class="section-body">
					<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:13px">
						<div><span class="text-muted">${__("Name")}:</span> <b>${customer_name || customer}</b></div>
						<div><span class="text-muted">${__("Phone")}:</span> ${customer_phone || "N/A"}</div>
						<div><span class="text-muted">${__("ID")}:</span> <code style="font-size:11px">${customer}</code></div>
					</div>
				</div>
			</div>`;

		// Devices
		if (!devices.length && !unlinked_plans.length) {
			html += `<div class="text-muted text-center" style="padding:16px">${__("No devices found")}</div>`;
		} else {
			devices.forEach((dev, idx) => {
				html += this._render_device_card(dev, idx);
			});
		}

		// Unlinked plans (not attached to any device)
		if (unlinked_plans.length) {
			html += this._render_unlinked_plans(unlinked_plans);
		}

		panel.find(".ch-claim-dashboard").html(html).show();
	}

	_render_device_card(dev, idx) {
		const plans = dev.plans || [];
		const claims = dev.claims || [];
		const has_active = dev.has_active_warranty;
		const mfr_active = dev.manufacturer_warranty_active;
		const mfr_end = dev.manufacturer_warranty_end;

		// Overall warranty badge
		let w_badge;
		if (has_active) {
			w_badge = `<span class="badge" style="background:#dcfce7;color:#166534;padding:3px 8px;font-size:11px">
				${__("Covered")}</span>`;
		} else if (mfr_active) {
			w_badge = `<span class="badge" style="background:#dbeafe;color:#1e40af;padding:3px 8px;font-size:11px">
				${__("Mfr Warranty")}</span>`;
		} else {
			w_badge = `<span class="badge" style="background:#fef2f2;color:#991b1b;padding:3px 8px;font-size:11px">
				${__("No Coverage")}</span>`;
		}

		// Plans table
		let plans_html = "";
		if (plans.length || mfr_end) {
			plans_html = `<div style="margin-top:8px">
				<table style="width:100%;font-size:11px;border-collapse:collapse">
					<thead>
						<tr style="border-bottom:2px solid #e5e7eb;text-align:left">
							<th style="padding:4px 6px">${__("Plan")}</th>
							<th style="padding:4px 6px">${__("Type")}</th>
							<th style="padding:4px 6px">${__("Valid Till")}</th>
							<th style="padding:4px 6px">${__("Claims")}</th>
							<th style="padding:4px 6px">${__("Status")}</th>
						</tr>
					</thead>
					<tbody>`;

			// Manufacturer warranty row (from Serial No)
			if (mfr_end) {
				const st = mfr_active ? "active" : "expired";
				const sc = this._plan_status_style(st);
				plans_html += `
					<tr style="border-bottom:1px solid #f3f4f6">
						<td style="padding:4px 6px"><b>${__("Manufacturer Warranty")}</b></td>
						<td style="padding:4px 6px;color:#6b7280">${__("OEM")}</td>
						<td style="padding:4px 6px">${mfr_end}</td>
						<td style="padding:4px 6px;color:#9ca3af">—</td>
						<td style="padding:4px 6px">
							<span class="badge" style="background:${sc.bg};color:${sc.fg};font-size:10px;padding:1px 6px">${sc.label}</span>
						</td>
					</tr>`;
			}

			for (const p of plans) {
				const sc = this._plan_status_style(p.display_status);
				const claims_text = p.claims_remaining === -1
					? `${p.claims_used || 0} / ∞`
					: `${p.claims_used || 0} / ${p.max_claims}`;
				const days_text = p.display_status === "active" && p.days_remaining > 0
					? ` (${p.days_remaining}d)` : "";

				plans_html += `
					<tr style="border-bottom:1px solid #f3f4f6">
						<td style="padding:4px 6px"><b>${p.plan_title || p.warranty_plan}</b></td>
						<td style="padding:4px 6px;color:#6b7280">${p.plan_type}</td>
						<td style="padding:4px 6px">${p.end_date || "—"}${days_text}</td>
						<td style="padding:4px 6px">${claims_text}</td>
						<td style="padding:4px 6px">
							<span class="badge" style="background:${sc.bg};color:${sc.fg};font-size:10px;padding:1px 6px">${sc.label}</span>
						</td>
					</tr>`;
			}

			plans_html += `</tbody></table></div>`;
		} else {
			plans_html = `<div style="margin-top:8px;font-size:12px;color:#9ca3af">${__("No plans purchased")}</div>`;
		}

		// Recent claims (compact)
		let claims_html = "";
		if (claims.length) {
			claims_html = `<div style="margin-top:8px;border-top:1px solid #f3f4f6;padding-top:6px">
				<b style="font-size:11px;color:#6b7280">${__("Claims")}:</b>`;
			for (const c of claims.slice(0, 3)) {
				const sc = this._status_color(c.claim_status);
				claims_html += `
					<div class="ch-claim-track" data-claim="${c.name}"
						style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;
						font-size:11px;cursor:pointer" title="${__("Click to view details")}">
						<span>${c.name}</span>
						<span class="text-muted">${c.claim_date}</span>
						<span class="badge" style="background:${sc.bg};color:${sc.fg};font-size:10px;padding:1px 6px">${c.claim_status}</span>
					</div>`;
			}
			if (claims.length > 3) {
				claims_html += `<div class="text-muted" style="font-size:10px;text-align:right">+${claims.length - 3} ${__("more")}</div>`;
			}
			claims_html += `</div>`;
		}

		return `
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-sm)">
				<div class="section-header" style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px">
					<span style="font-size:13px">
						<i class="fa fa-mobile"></i>
						<b>${dev.item_name || dev.item_code || __("Device")}</b>
						<code style="font-size:11px;margin-left:4px">${dev.serial_no}</code>
					</span>
					<span style="display:flex;gap:6px;align-items:center">
						${w_badge}
						<button class="btn btn-xs btn-primary ch-dev-raise-claim" data-idx="${idx}"
							style="border-radius:4px;font-size:11px">
							<i class="fa fa-plus"></i> ${__("Raise Claim")}
						</button>
					</span>
				</div>
				<div class="section-body" style="padding:6px 12px 10px">
					${plans_html}
					${claims_html}
				</div>
			</div>`;
	}

	_render_unlinked_plans(plans) {
		let rows = "";
		for (const p of plans) {
			const sc = this._plan_status_style(p.display_status);
			const claims_text = p.claims_remaining === -1
				? `${p.claims_used || 0} / ∞`
				: `${p.claims_used || 0} / ${p.max_claims}`;
			const days_text = p.display_status === "active" && p.days_remaining > 0
				? ` (${p.days_remaining}d)` : "";

			rows += `
				<tr style="border-bottom:1px solid #f3f4f6">
					<td style="padding:4px 6px"><b>${p.plan_title || p.warranty_plan}</b></td>
					<td style="padding:4px 6px;color:#6b7280">${p.plan_type || ""}</td>
					<td style="padding:4px 6px">${p.end_date || "—"}${days_text}</td>
					<td style="padding:4px 6px">${claims_text}</td>
					<td style="padding:4px 6px">
						<span class="badge" style="background:${sc.bg};color:${sc.fg};font-size:10px;padding:1px 6px">${sc.label}</span>
					</td>
				</tr>`;
		}

		return `
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-sm)">
				<div class="section-header" style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px">
					<span style="font-size:13px">
						<i class="fa fa-shield"></i>
						<b>${__("Plans Not Linked to a Device")}</b>
					</span>
					<span class="badge" style="background:#fef3c7;color:#92400e;padding:3px 8px;font-size:11px">
						${__("No Device")}
					</span>
				</div>
				<div class="section-body" style="padding:6px 12px 10px">
					<div style="margin-top:4px;padding:6px 8px;background:#fef9c3;border-radius:4px;font-size:11px;color:#854d0e">
						<i class="fa fa-info-circle"></i>
						${__("These plans were purchased but not linked to a device serial number. They cannot be used for claims until a device IMEI is assigned.")}
					</div>
					<div style="margin-top:8px">
						<table style="width:100%;font-size:11px;border-collapse:collapse">
							<thead>
								<tr style="border-bottom:2px solid #e5e7eb;text-align:left">
									<th style="padding:4px 6px">${__("Plan")}</th>
									<th style="padding:4px 6px">${__("Type")}</th>
									<th style="padding:4px 6px">${__("Valid Till")}</th>
									<th style="padding:4px 6px">${__("Claims")}</th>
									<th style="padding:4px 6px">${__("Status")}</th>
								</tr>
							</thead>
							<tbody>${rows}</tbody>
						</table>
					</div>
				</div>
			</div>`;
	}

	// ── Claim Form ──────────────────────────────────────────────────

	_show_claim_form(panel, device_idx) {
		const data = this._dashboard;
		if (!data || !data.devices || !data.devices[device_idx]) return;

		const dev = data.devices[device_idx];
		const has_active = dev.has_active_warranty;
		this._selected_device = dev;

		const cust_phone = data.customer_phone || "";
		const cust_email = data.customer_email || "";

		panel.find(".ch-claim-form-area").html(`
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);border:2px solid ${has_active ? "#86efac" : "#fca5a5"}">
				<div class="section-header" style="background:${has_active ? "#f0fdf4" : "#fef2f2"};display:flex;justify-content:space-between;align-items:center">
					<span><i class="fa fa-plus-circle"></i> ${__("Raise Claim")} — ${dev.item_name || dev.serial_no}</span>
					<button class="btn btn-xs btn-default ch-claim-cancel-form" style="border-radius:4px">
						<i class="fa fa-times"></i>
					</button>
				</div>
				<div class="section-body">
					<div class="ch-pos-field-group" style="margin-bottom:var(--pos-space-sm)">
						<label>${__("Service Mode")} <span style="color:var(--pos-danger)">*</span></label>
						<select class="form-control ch-claim-mode">
							<option value="Walk-in">${__("Walk-in")}</option>
							<option value="Pickup">${__("Pickup")}</option>
							<option value="Courier">${__("Courier")}</option>
							<option value="On-site">${__("On-site")}</option>
						</select>
					</div>

					<div class="ch-claim-pickup-fields" style="display:none;margin-bottom:var(--pos-space-md)">
						<div class="ch-pos-field-group" style="margin-top:var(--pos-space-sm)">
							<label>${__("Pickup Address")} <span style="color:var(--pos-danger)">*</span></label>
							<textarea class="form-control ch-claim-pickup-address" rows="2"
								placeholder="${__("Door number, street, area, city, pincode")}"></textarea>
						</div>
						<div class="ch-pos-field-group" style="margin-top:var(--pos-space-sm)">
							<label>${__("Preferred Pickup Slot")}</label>
							<input type="datetime-local" class="form-control ch-claim-pickup-slot">
						</div>
					</div>

					<!-- Customer Contact (read-only from Customer master) -->
					<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:var(--pos-space-md);
						padding:8px 12px;background:var(--subtle-fg);border-radius:6px;font-size:13px">
						<div>
							<span class="text-muted">${__("Phone")}:</span>
							<b>${cust_phone ? frappe.utils.escape_html(cust_phone) : '<span class="text-danger">Not set</span>'}</b>
						</div>
						<div>
							<span class="text-muted">${__("Email")}:</span>
							<b>${cust_email ? frappe.utils.escape_html(cust_email) : '<span class="text-muted">N/A</span>'}</b>
						</div>
					</div>

					<!-- Claim Against Plan -->
					${(dev.active_plans && dev.active_plans.length > 0) ? `
					<div class="ch-pos-field-group" style="margin-bottom:var(--pos-space-md)">
						<label>${__("Claim Against Plan")} <span style="color:var(--pos-danger)">*</span></label>
						<div class="ch-claim-plan-options" style="display:flex;flex-direction:column;gap:6px;margin-top:4px">
							${dev.active_plans.map((p, i) => `
								<label class="ch-claim-plan-opt" data-plan="${frappe.utils.escape_html(p.name)}" style="
									display:flex;align-items:center;gap:10px;padding:8px 12px;
									border:2px solid var(--border-color);border-radius:8px;cursor:pointer;
									background:var(--fg-color);transition:all 0.15s;font-weight:normal;margin:0">
									<input type="radio" name="ch_claim_plan" value="${frappe.utils.escape_html(p.name)}"
										${dev.active_plans.length === 1 ? 'checked' : ''} style="margin:0;flex-shrink:0">
									<div style="flex:1;min-width:0">
										<div style="font-weight:600;font-size:13px">${frappe.utils.escape_html(p.plan_title || p.warranty_plan)}</div>
										<div class="text-muted" style="font-size:11px">${frappe.utils.escape_html(p.plan_type)}
											— Valid till ${p.end_date || 'N/A'}
											${p.deductible_amount > 0 ? ' — Deductible: ₹' + format_number(p.deductible_amount) : ''}
										</div>
									</div>
									<span class="badge" style="background:#dcfce7;color:#166534;font-size:10px;padding:2px 6px">Active</span>
								</label>
							`).join("")}
						</div>
					</div>
					` : ''}

					<!-- Issue Categories (multi-select) -->
					<div class="ch-pos-field-group">
						<label>${__("Issue Categories")} <span style="color:var(--pos-danger)">*</span></label>
						<div class="ch-claim-issue-cats-wrap"></div>
					</div>

					<div class="ch-pos-field-group" style="margin-top:var(--pos-space-sm)">
						<label>${__("Estimated Repair Cost")} (₹)</label>
						<input type="number" class="form-control ch-claim-est-cost" min="0" step="100"
							placeholder="${__("e.g. 2500")}">
					</div>
					<div class="ch-pos-field-group" style="margin-top:var(--pos-space-sm)">
						<label>${__("Issue Description")} <span style="color:var(--pos-danger)">*</span></label>
						<textarea class="form-control ch-claim-issue-desc" rows="3"
							style="min-height:70px;resize:vertical"
							placeholder="${__("Describe what's wrong with the device...")}"></textarea>
					</div>

					<!-- Device Images (6 slots) -->
					<div class="ch-pos-field-group" style="margin-top:var(--pos-space-md)">
						<label>${__("Device Images")} <span class="text-muted">(${__("at least 4 required")})</span></label>
						<div class="ch-claim-images" style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:6px">
							${["Front", "Back", "Left Side", "Right Side", "Top", "Bottom"].map((label, i) => `
								<div class="ch-claim-img-slot" data-idx="${i}" style="
									display:flex;flex-direction:column;align-items:center;justify-content:center;
									border:2px dashed var(--border-color);border-radius:8px;padding:12px 4px;
									cursor:pointer;min-height:90px;text-align:center;position:relative;
									background:var(--fg-color);transition:border-color 0.2s">
									<i class="fa fa-camera" style="font-size:20px;color:var(--text-muted);margin-bottom:4px"></i>
									<span style="font-size:11px;color:var(--text-muted)">${__(label)}</span>
									<input type="file" accept="image/*" capture="environment" class="ch-claim-img-input"
										data-idx="${i}" data-label="${label}" style="display:none">
									<img class="ch-claim-img-preview" style="display:none;max-width:100%;max-height:70px;
										border-radius:4px;margin-top:4px;object-fit:cover">
								</div>
							`).join("")}
						</div>
					</div>

					${dev.manufacturer_warranty_active ? `
					<div style="margin-top:10px;padding:8px 12px;background:#dbeafe;border:1px solid #93c5fd;border-radius:6px;font-size:12px">
						<b>${__("Manufacturer Warranty Active")}</b> — ${__("Device will be sent to brand/OEM for repair. No cost to customer.")}
						<br><span class="text-muted">${__("Expires")}: ${dev.manufacturer_warranty_end || "N/A"}</span>
					</div>` : has_active ? `
					<div style="margin-top:10px;padding:8px 12px;background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;font-size:12px">
						<b>${__("In-Warranty Claim")}</b> — ${__("GoGizmo covers repair cost. Approval from GoGizmo Head required.")}
						${(dev.active_plans[0] || {}).deductible_amount > 0 ?
							`<br>${__("Customer deductible")}: <b>₹${dev.active_plans[0].deductible_amount}</b>` : ""}
					</div>` : `
					<div style="margin-top:10px;padding:8px 12px;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;font-size:12px">
						<b>${__("Out-of-Warranty")}</b> — ${__("Customer pays GoFix directly. Ticket created immediately.")}
					</div>`}

					<button class="btn btn-primary ch-claim-submit" style="width:100%;margin-top:var(--pos-space-md);padding:10px">
						<i class="fa fa-paper-plane"></i>
						${dev.manufacturer_warranty_active ? __("Submit & Send to Manufacturer") :
						  has_active ? __("Submit Claim (Needs Approval)") : __("Submit & Create GoFix Ticket")}
					</button>
				</div>
			</div>
		`).show();

		// Scroll to form
		panel.find(".ch-claim-form-area")[0]?.scrollIntoView({ behavior: "smooth", block: "nearest" });

		// ── Issue Categories multi-select ──
		// Fetch categories list, render as selectable chips
		frappe.xcall("frappe.client.get_list", {
			doctype: "Issue Category",
			filters: { is_active: 1 },
			fields: ["name"],
			limit_page_length: 0,
			order_by: "name asc",
		}).then((cats) => {
			const wrap = panel.find(".ch-claim-issue-cats-wrap");
			if (!cats || !cats.length) {
				wrap.html(`<span class="text-muted">${__("No issue categories defined")}</span>`);
				return;
			}
			let chips = `<div class="ch-claim-cat-chips" style="display:flex;flex-wrap:wrap;gap:6px">`;
			for (const c of cats) {
				chips += `<span class="ch-claim-cat-chip" data-cat="${frappe.utils.escape_html(c.name)}" style="
					padding:4px 10px;border:1px solid var(--border-color);border-radius:14px;
					cursor:pointer;font-size:12px;user-select:none;transition:all 0.15s;
					background:var(--fg-color);color:var(--text-color)">${frappe.utils.escape_html(c.name)}</span>`;
			}
			chips += `</div>`;
			wrap.html(chips);

			// Toggle selection on click
			wrap.on("click", ".ch-claim-cat-chip", function () {
				$(this).toggleClass("ch-cat-selected");
				if ($(this).hasClass("ch-cat-selected")) {
					$(this).css({ background: "var(--primary)", color: "#fff", "border-color": "var(--primary)" });
				} else {
					$(this).css({ background: "var(--fg-color)", color: "var(--text-color)", "border-color": "var(--border-color)" });
				}
			});
		});

		// ── Plan selection highlight ──
		panel.find('.ch-claim-plan-opt input[type="radio"]').on("change", function () {
			panel.find(".ch-claim-plan-opt").css({ "border-color": "var(--border-color)", background: "var(--fg-color)" });
			$(this).closest(".ch-claim-plan-opt").css({ "border-color": "var(--primary)", background: "var(--subtle-accent)" });
		});
		// Trigger initial highlight if single plan auto-checked
		panel.find('.ch-claim-plan-opt input[type="radio"]:checked').trigger("change");

		// ── Image slot handlers ──
		panel.find(".ch-claim-img-slot").on("click", function () {
			$(this).find(".ch-claim-img-input").trigger("click");
		});
		panel.find(".ch-claim-img-input").on("click", function (e) {
			e.stopPropagation();
		});
		panel.find(".ch-claim-img-input").on("change", function () {
			const file = this.files && this.files[0];
			const slot = $(this).closest(".ch-claim-img-slot");
			const preview = slot.find(".ch-claim-img-preview");
			const icon = slot.find(".fa-camera");
			if (file) {
				const reader = new FileReader();
				reader.onload = (e) => {
					preview.attr("src", e.target.result).show();
					icon.hide();
				};
				reader.readAsDataURL(file);
				slot.css("border-color", "var(--primary)");
			} else {
				preview.hide().attr("src", "");
				icon.show();
				slot.css("border-color", "var(--border-color)");
			}
		});
	}

	// ── Submit Claim ────────────────────────────────────────────────

	_submit_claim(panel) {
		const data = this._dashboard;
		const dev = this._selected_device;
		if (!data || !dev) {
			frappe.show_alert({ message: __("Select a device first"), indicator: "orange" });
			return;
		}

		// ── Validations ──

		// Selected plan (required if device has active plans)
		let selected_sold_plan = "";
		if (dev.active_plans && dev.active_plans.length > 0) {
			const checked = panel.find('input[name="ch_claim_plan"]:checked');
			if (!checked.length) {
				frappe.show_alert({ message: __("Select a plan to claim against"), indicator: "orange" });
				return;
			}
			selected_sold_plan = checked.val();
		}

		// Issue categories (required, at least 1)
		const selected_cats = [];
		panel.find(".ch-claim-cat-chip.ch-cat-selected").each(function () {
			selected_cats.push($(this).data("cat"));
		});
		if (!selected_cats.length) {
			frappe.show_alert({ message: __("Select at least one issue category"), indicator: "orange" });
			return;
		}

		// Issue description (required)
		const issue_desc = panel.find(".ch-claim-issue-desc").val().trim();
		if (!issue_desc) {
			frappe.show_alert({ message: __("Issue description is required"), indicator: "orange" });
			panel.find(".ch-claim-issue-desc").focus();
			return;
		}

		// Images (at least 4)
		const img_fields = ["device_image_front", "device_image_back", "device_image_left",
			"device_image_right", "device_image_top", "device_image_bottom"];
		const img_inputs = panel.find(".ch-claim-img-input");
		const img_files = [];
		img_inputs.each(function (i) {
			const f = this.files && this.files[0];
			img_files.push({ field: img_fields[i], file: f || null });
		});
		const filled = img_files.filter(f => f.file);
		if (filled.length < 4) {
			frappe.show_alert({ message: __("Please upload at least 4 device images"), indicator: "orange" });
			return;
		}

		const btn = panel.find(".ch-claim-submit");
		const mode_of_service = panel.find(".ch-claim-mode").val() || "Walk-in";
		const pickup_address = panel.find(".ch-claim-pickup-address").val().trim();
		const pickup_slot = panel.find(".ch-claim-pickup-slot").val() || null;
		if (["Pickup", "Courier"].includes(mode_of_service) && !pickup_address) {
			frappe.show_alert({ message: __("Pickup address is required for {0}", [mode_of_service]), indicator: "orange" });
			panel.find(".ch-claim-pickup-address").focus();
			return;
		}
		btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Submitting...")}`);

		frappe.xcall(
			"ch_item_master.ch_item_master.warranty_api.initiate_warranty_claim",
			{
				serial_no: dev.serial_no,
				customer: data.customer,
				item_code: dev.item_code,
				company: PosState.company,
				issue_description: issue_desc,
				issue_categories: JSON.stringify(selected_cats),
				reported_at_company: PosState.company,
				reported_at_store: PosState.store || "",
				estimated_repair_cost: parseFloat(panel.find(".ch-claim-est-cost").val()) || 0,
				mode_of_service,
				pickup_address: pickup_address || undefined,
				pickup_slot: pickup_slot || undefined,
				sold_plan: selected_sold_plan || undefined,
			}
		).then(async (result) => {
			// Upload images to the created claim
			if (result.claim_name && filled.length) {
				try {
					await this._upload_claim_images(result.claim_name, img_files);
				} catch (e) {
					frappe.show_alert({ message: __("Claim created but some images failed to upload"), indicator: "orange" });
				}
			}
			this._show_claim_result(panel, result);
			this._load_claims_pipeline(panel);
			// Re-search to refresh dashboard
			const q = panel.find(".ch-claim-search").val().trim();
			if (q) setTimeout(() => this._search(panel, q), 1500);
		}).catch((err) => {
			frappe.show_alert({ message: err.message || __("Failed to create claim"), indicator: "red" });
		}).finally(() => {
			btn.prop("disabled", false).html(`<i class="fa fa-paper-plane"></i> ${__("Submit Claim")}`);
		});
	}

	/**
	 * Upload device images to an existing warranty claim document.
	 * Uploads each file, collects URLs, then sets all field values at once.
	 */
	async _upload_claim_images(claim_name, img_files) {
		const field_urls = {};

		for (const { field, file } of img_files) {
			if (!file) continue;
			const form_data = new FormData();
			form_data.append("file", file, file.name);
			form_data.append("doctype", "CH Warranty Claim");
			form_data.append("docname", claim_name);
			form_data.append("fieldname", field);
			form_data.append("is_private", "1");

			const resp = await fetch("/api/method/upload_file", {
				method: "POST",
				body: form_data,
				headers: {
					"X-Frappe-CSRF-Token": frappe.csrf_token,
				},
			});
			if (!resp.ok) throw new Error(`Upload failed: ${resp.status}`);
			const result = await resp.json();
			const file_url = result.message && result.message.file_url;
			if (file_url) field_urls[field] = file_url;
		}

		// Set all image field values on the submitted claim
		if (Object.keys(field_urls).length) {
			await frappe.xcall("frappe.client.set_value", {
				doctype: "CH Warranty Claim",
				name: claim_name,
				fieldname: field_urls,
			});
		}
	}

	_show_claim_result(panel, result) {
		const is_approved = result.claim_status === "Approved";
		const is_mfr = result.coverage_type === "manufacturer_warranty";
		const is_sent_mfr = result.claim_status === "Sent to Manufacturer";
		const needs_approval = result.requires_approval;
		const pickup_flow = ["Pickup Requested", "Pickup Scheduled"].includes(result.claim_status)
			|| ["Pickup", "Courier"].includes(result.mode_of_service);

		let html;
		if (is_sent_mfr || is_mfr) {
			html = `
				<div style="text-align:center;padding:20px;color:#1e40af">
					<i class="fa fa-truck fa-3x"></i>
					<h5 style="margin-top:10px">${__("Sent to Manufacturer")}</h5>
					<p>${__("Claim")}: <b>${result.claim_name}</b></p>
					<p style="font-size:12px;color:#6b7280">${__("Device under manufacturer warranty — sent to brand for repair")}</p>
					<p style="font-size:12px">${__("No cost to customer")} ✓</p>
				</div>`;
		} else if (is_approved && result.service_request) {
			html = `
				<div style="text-align:center;padding:20px;color:#166534">
					<i class="fa fa-check-circle fa-3x"></i>
					<h5 style="margin-top:10px">${__("GoFix Ticket Created!")}</h5>
					<p>${__("Claim")}: <b>${result.claim_name}</b></p>
					<p>${__("GoFix Ticket")}: <b>${result.service_request}</b></p>
					<p style="font-size:12px;color:#6b7280">${__("Customer pays")}: ₹${result.customer_share || 0}</p>
				</div>`;
		} else if (pickup_flow) {
			html = `
				<div style="text-align:center;padding:20px;color:#0f766e">
					<i class="fa fa-map-marker fa-3x"></i>
					<h5 style="margin-top:10px">${__("Pickup Flow Started")}</h5>
					<p>${__("Claim")}: <b>${result.claim_name}</b></p>
					<p style="font-size:12px;color:#6b7280">${__("Device pickup will be scheduled before repair processing")}</p>
				</div>`;
		} else if (needs_approval) {
			html = `
				<div style="text-align:center;padding:20px;color:#d97706">
					<i class="fa fa-clock-o fa-3x"></i>
					<h5 style="margin-top:10px">${__("Pending GoGizmo Approval")}</h5>
					<p>${__("Claim")}: <b>${result.claim_name}</b></p>
					<div style="display:flex;justify-content:center;gap:16px;margin-top:6px;font-size:13px">
						<span>${__("GoGizmo")}: <b>₹${result.gogizmo_share || 0}</b></span>
						<span>${__("Customer")}: <b>₹${result.customer_share || 0}</b></span>
					</div>
				</div>`;
		} else {
			html = `
				<div style="text-align:center;padding:20px;color:#2563eb">
					<i class="fa fa-info-circle fa-3x"></i>
					<h5 style="margin-top:10px">${__("Claim Created")}</h5>
					<p><b>${result.claim_name}</b> — ${result.coverage_type}</p>
				</div>`;
		}

		// "Tell the Customer" guidance
		let cust_msg = "";
		if (is_sent_mfr || is_mfr) {
			cust_msg = __("Your device is under manufacturer warranty. We are sending it to the brand for repair. Reference: ") + result.claim_name;
		} else if (needs_approval) {
			cust_msg = __("Your claim is under review. We will update you once approved.");
		} else if (pickup_flow) {
			cust_msg = __("Your claim is created. We will schedule pickup and share tracking details shortly.");
		} else if (result.service_request) {
			cust_msg = __("Your device has been sent for repair. Reference: ") + result.claim_name;
		}
		if (cust_msg) {
			html += `
				<div style="margin-top:10px;padding:8px 12px;background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;font-size:12px">
					<b>${__("Tell the Customer")}:</b> ${cust_msg}
				</div>`;
		}

		panel.find(".ch-claim-form-area").html(`
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);border:2px solid #86efac">
				<div class="section-body">${html}</div>
			</div>
		`);
	}

	// ── Claim Detail View ───────────────────────────────────────────

	_show_claim_detail(panel, claim_name) {
		frappe.xcall("frappe.client.get", {
			doctype: "CH Warranty Claim", name: claim_name,
		}).then((claim) => {
			const sc = this._status_color(claim.claim_status);
			const steps = this._progress_steps(claim);

			let log_html = "";
			if (claim.claim_log && claim.claim_log.length) {
				log_html = `<details style="font-size:12px;margin-top:8px">
					<summary style="cursor:pointer;color:#6b7280"><b>${__("Activity Log")} (${claim.claim_log.length})</b></summary>
					${claim.claim_log.map(l => `
						<div style="display:flex;gap:8px;padding:4px 0;border-bottom:1px solid #f3f4f6;font-size:11px">
							<span style="color:#9ca3af;min-width:120px">${l.log_timestamp}</span>
							<span style="flex:1"><b>${l.action}</b> — ${l.remarks || ""}</span>
						</div>`).join("")}
				</details>`;
			}

			const logistics = this._render_logistics_block(claim);

			panel.find(".ch-claim-dashboard").html(`
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header" style="display:flex;justify-content:space-between;align-items:center">
						<span>
							<button class="btn btn-xs btn-default ch-claim-back" style="margin-right:6px;border-radius:4px">
								<i class="fa fa-arrow-left"></i>
							</button>
							<b>${claim.name}</b> — ${claim.customer_name || claim.customer}
						</span>
						<span class="badge" style="background:${sc.bg};color:${sc.fg};padding:3px 10px;font-size:12px">
							${claim.claim_status}
						</span>
					</div>
					<div class="section-body">
						<!-- Progress -->
						<div style="display:flex;gap:4px;margin-bottom:12px">
							${steps.map(s => `
								<div style="flex:1;text-align:center;padding:5px 2px;border-radius:4px;font-size:10px;
									background:${s.done ? s.color : "#f3f4f6"};color:${s.done ? "white" : "#9ca3af"}">
									<i class="fa ${s.icon}"></i><br>${s.label}
								</div>`).join("")}
						</div>

						<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;margin-bottom:10px">
							<div><span class="text-muted">${__("Device")}:</span> ${claim.item_name || claim.serial_no}</div>
							<div><span class="text-muted">${__("Serial")}:</span> <code>${claim.serial_no}</code></div>
							<div><span class="text-muted">${__("Coverage")}:</span>
								<span class="badge" style="background:${this._coverage_style(claim.coverage_type).bg};color:${this._coverage_style(claim.coverage_type).fg};font-size:10px;padding:1px 5px">${this._coverage_label(claim.coverage_type)}</span>
								${claim.coverage_source ? `<span class="text-muted" style="font-size:10px">(${claim.coverage_source})</span>` : ""}
							</div>
							<div><span class="text-muted">${__("Filed")}:</span> ${claim.claim_date}</div>
							<div><span class="text-muted">${__("GoGizmo pays")}:</span> ₹${claim.gogizmo_share || 0}</div>
							<div><span class="text-muted">${__("Customer pays")}:</span> ₹${claim.customer_share || 0}</div>
							${claim.entitlement_decision ? `<div><span class="text-muted">${__("Entitlement")}:</span> ${claim.entitlement_decision}</div>` : ""}
							${claim.final_outcome ? `<div><span class="text-muted">${__("Outcome")}:</span> ${claim.final_outcome}</div>` : ""}
							${claim.service_request ? `<div><span class="text-muted">${__("GoFix")}:</span>
								<a href="/app/service-request/${claim.service_request}" target="_blank">${claim.service_request}</a></div>` : ""}
							${claim.repair_status ? `<div><span class="text-muted">${__("Repair")}:</span> ${claim.repair_status}</div>` : ""}
						</div>

						${claim.coverage_type === "repair_warranty" && claim.previous_service_ticket ? `
						<div style="padding:8px 10px;background:#dbeafe;border:1px solid #93c5fd;border-radius:6px;font-size:12px;margin-bottom:8px">
							<b><i class="fa fa-history"></i> ${__("Repair Warranty — Previous Service")}</b>
							<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:6px">
								<div><span class="text-muted">${__("Prev Ticket")}:</span> ${claim.previous_service_ticket || "—"}</div>
								<div><span class="text-muted">${__("Prev Invoice")}:</span> ${claim.previous_service_invoice || "—"}</div>
								<div><span class="text-muted">${__("Part")}:</span> ${claim.replaced_part_reference || "—"}</div>
								<div><span class="text-muted">${__("Valid Till")}:</span> ${claim.part_warranty_valid_till || "—"}</div>
							</div>
						</div>` : ""}

						${claim.coverage_type === "manufacturer_warranty" ? `
						<div style="padding:8px 10px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;font-size:12px;margin-bottom:8px">
							<b><i class="fa fa-truck"></i> ${__("Manufacturer Service Details")}</b>
							<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:6px">
								<div><span class="text-muted">${__("Service Center")}:</span> ${claim.manufacturer_service_center || "—"}</div>
								<div><span class="text-muted">${__("Job ID")}:</span> ${claim.manufacturer_job_id || "—"}</div>
								<div><span class="text-muted">${__("Handed Over")}:</span> ${claim.handover_date || "—"}</div>
								<div><span class="text-muted">${__("Expected Return")}:</span> ${claim.expected_return_date || "—"}</div>
								${claim.actual_return_date ? `<div><span class="text-muted">${__("Returned")}:</span> ${claim.actual_return_date}</div>` : ""}
								${claim.manufacturer_contact_person ? `<div><span class="text-muted">${__("Contact")}:</span> ${claim.manufacturer_contact_person}${claim.manufacturer_contact_phone ? " (" + claim.manufacturer_contact_phone + ")" : ""}</div>` : ""}
							</div>
						</div>` : ""}

						<div style="padding:6px 10px;background:#f9fafb;border-radius:6px;font-size:12px">
							<b>${__("Issue")}:</b> ${claim.issue_description || "N/A"}
						</div>
						${this._render_receiving_block(claim)}
						${this._render_qc_block(claim)}
						${this._render_fee_block(claim)}
						${logistics}
						${log_html}
					</div>
				</div>
			`).show();
			panel.find(".ch-claim-form-area").hide();
		}).catch(() => {
			frappe.show_alert({ message: __("Could not load claim"), indicator: "red" });
		});
	}

	_progress_steps(claim) {
		const s = claim.claim_status;
		const is_mfr = claim.coverage_type === "manufacturer_warranty";

		if (is_mfr) {
			return [
				{ label: __("Filed"), icon: "fa-file-text", done: true, color: "#3b82f6" },
				{ label: __("Sent to OEM"), icon: "fa-truck",
				  done: ["Sent to Manufacturer","Repair Complete","Closed"].includes(s), color: "#1e40af" },
				{ label: __("Received"), icon: "fa-check-circle",
				  done: ["Repair Complete","Closed"].includes(s), color: "#22c55e" },
				{ label: __("Closed"), icon: "fa-lock", done: s === "Closed", color: "#6b7280" },
			];
		}

		const AFTER_APPROVAL = ["Approved","Pickup Requested","Pickup Scheduled","Picked Up",
			"Device Received","QC Pending","QC Passed","QC Failed","Fee Pending","Fee Paid","Fee Waived",
			"Ticket Created","In Repair","Repair Complete","Out for Delivery","Delivered","Closed"];
		const AFTER_RECEIVED = ["Device Received","QC Pending","QC Passed","QC Failed",
			"Fee Pending","Fee Paid","Fee Waived","Ticket Created","In Repair","Repair Complete",
			"Out for Delivery","Delivered","Closed"];
		const AFTER_QC = ["QC Passed","Fee Pending","Fee Paid","Fee Waived",
			"Ticket Created","In Repair","Repair Complete","Out for Delivery","Delivered","Closed"];
		const AFTER_FEE = ["Fee Paid","Fee Waived","Ticket Created","In Repair",
			"Repair Complete","Out for Delivery","Delivered","Closed"];
		const AFTER_TICKET = ["Ticket Created","In Repair","Repair Complete",
			"Out for Delivery","Delivered","Closed"];
		const AFTER_REPAIR = ["Repair Complete","Out for Delivery","Delivered","Closed"];

		return [
			{ label: __("Filed"), icon: "fa-file-text", done: true, color: "#3b82f6" },
			{ label: claim.requires_approval ? __("Approval") : __("Auto"),
			  icon: "fa-check-circle",
			  done: [...AFTER_APPROVAL, "Rejected"].includes(s),
			  color: s === "Rejected" ? "#ef4444" : "#22c55e" },
			{ label: __("Pickup"), icon: "fa-truck",
			  done: AFTER_RECEIVED.includes(s) || s === "Picked Up",
			  color: "#0f766e" },
			{ label: __("Received"), icon: "fa-inbox",
			  done: AFTER_RECEIVED.includes(s), color: "#06b6d4" },
			{ label: __("QC"), icon: "fa-check-square-o",
			  done: AFTER_QC.includes(s),
			  color: s === "QC Failed" ? "#ef4444" : "#8b5cf6" },
			{ label: __("Fee"), icon: "fa-credit-card",
			  done: AFTER_FEE.includes(s), color: "#f59e0b" },
			{ label: __("GoFix"), icon: "fa-wrench",
			  done: AFTER_TICKET.includes(s), color: "#8b5cf6" },
			{ label: __("Repair"), icon: "fa-cog",
			  done: AFTER_REPAIR.includes(s), color: "#eab308" },
			{ label: __("Closed"), icon: "fa-lock", done: s === "Closed", color: "#6b7280" },
		];
	}

	_render_logistics_block(claim) {
		const mode = claim.mode_of_service || "Walk-in";
		const lstat = claim.logistics_status || "Not Required";
		const s = claim.claim_status;

		// ── Action buttons based on current status ──
		const actions = [];

		// Pickup flow
		if (["Approved", "Pickup Requested"].includes(s) && claim.pickup_required) {
			actions.push(`<button class="btn btn-xs btn-primary ch-claim-log-action" data-claim="${claim.name}" data-action="schedule_pickup"><i class="fa fa-calendar"></i> ${__("Schedule Pickup")}</button>`);
		}
		if (s === "Pickup Scheduled") {
			actions.push(`<button class="btn btn-xs btn-warning ch-claim-log-action" data-claim="${claim.name}" data-action="mark_picked_up"><i class="fa fa-truck"></i> ${__("Mark Picked Up")}</button>`);
		}

		// Device Receiving (walk-in: from Approved; pickup: from Picked Up)
		if ((s === "Approved" && !claim.pickup_required) || s === "Picked Up") {
			actions.push(`<button class="btn btn-xs btn-info ch-claim-log-action" data-claim="${claim.name}" data-action="receive_device"><i class="fa fa-inbox"></i> ${__("Receive Device")}</button>`);
		}

		// Intake QC (after receiving)
		if (["Device Received", "QC Pending"].includes(s)) {
			actions.push(`<button class="btn btn-xs btn-primary ch-claim-log-action" data-claim="${claim.name}" data-action="perform_qc"><i class="fa fa-check-square-o"></i> ${__("Perform Intake QC")}</button>`);
		}

		// Processing Fee (after QC passed)
		if (s === "QC Passed" && !claim.processing_fee_amount) {
			actions.push(`<button class="btn btn-xs btn-warning ch-claim-log-action" data-claim="${claim.name}" data-action="generate_fee"><i class="fa fa-calculator"></i> ${__("Generate Fee")}</button>`);
		}
		if (["Fee Pending", "QC Passed"].includes(s) && flt(claim.processing_fee_amount) > 0
		    && !["Paid","Waived","Not Required"].includes(claim.processing_fee_status)) {
			actions.push(`<button class="btn btn-xs btn-success ch-claim-log-action" data-claim="${claim.name}" data-action="collect_fee"><i class="fa fa-credit-card"></i> ${__("Collect Fee ₹") + format_number(claim.processing_fee_amount)}</button>`);
			actions.push(`<button class="btn btn-xs btn-default ch-claim-log-action" data-claim="${claim.name}" data-action="send_fee_link"><i class="fa fa-paper-plane"></i> ${__("Send Link")}</button>`);
			actions.push(`<button class="btn btn-xs btn-default ch-claim-log-action" data-claim="${claim.name}" data-action="waive_fee"><i class="fa fa-ban"></i> ${__("Waive Fee")}</button>`);
		}

		// Create Repair Ticket (after all gates pass)
		if (["Fee Paid", "Fee Waived"].includes(s)
		    || (s === "QC Passed" && claim.processing_fee_status === "Not Required")) {
			if (!claim.service_request) {
				actions.push(`<button class="btn btn-xs btn-primary ch-claim-log-action" data-claim="${claim.name}" data-action="create_ticket"><i class="fa fa-wrench"></i> ${__("Create GoFix Ticket")}</button>`);
			}
		}

		// Additional damage/cost approval (during repair)
		if (["In Repair", "Ticket Created"].includes(s)) {
			actions.push(`<button class="btn btn-xs btn-warning ch-claim-log-action" data-claim="${claim.name}" data-action="request_additional_approval"><i class="fa fa-exclamation-triangle"></i> ${__("Additional Cost")}</button>`);
		}
		if (s === "Additional Approval Pending") {
			actions.push(`<button class="btn btn-xs btn-success ch-claim-log-action" data-claim="${claim.name}" data-action="resolve_additional_approved"><i class="fa fa-check"></i> ${__("Customer Approved")}</button>`);
			actions.push(`<button class="btn btn-xs btn-danger ch-claim-log-action" data-claim="${claim.name}" data-action="resolve_additional_rejected"><i class="fa fa-times"></i> ${__("Customer Rejected")}</button>`);
		}

		// Final QC (after repair)
		if (s === "Repair Complete" || s === "Final QC Pending") {
			actions.push(`<button class="btn btn-xs btn-primary ch-claim-log-action" data-claim="${claim.name}" data-action="perform_final_qc"><i class="fa fa-check-double"></i> ${__("Final QC")}</button>`);
		}

		// Post-repair delivery
		if (["Repair Complete", "Final QC Passed", "Payment Received", "Invoice Raised"].includes(s)) {
			actions.push(`<button class="btn btn-xs btn-info ch-claim-log-action" data-claim="${claim.name}" data-action="mark_out_for_delivery"><i class="fa fa-motorcycle"></i> ${__("Out for Delivery")}</button>`);
		}
		if (s === "Out for Delivery") {
			actions.push(`<button class="btn btn-xs btn-success ch-claim-log-action" data-claim="${claim.name}" data-action="mark_delivered_back"><i class="fa fa-check"></i> ${__("Mark Delivered")}</button>`);
		}

		// ── Summary card ──
		const fee_status_badge = this._fee_status_badge(claim);
		const qc_badge = claim.intake_qc_status
			? `<span class="badge" style="background:${claim.intake_qc_status === 'Passed' ? '#dcfce7' : '#fef2f2'};color:${claim.intake_qc_status === 'Passed' ? '#166534' : '#991b1b'};font-size:10px;padding:1px 5px">${claim.intake_qc_status}</span>`
			: "";

		let logistics_detail = "";
		const pickup_flow = ["Pickup", "Courier"].includes(mode);
		if (pickup_flow || claim.pickup_required || claim.pickup_address) {
			logistics_detail = `
			<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:6px;font-size:11px">
				<div><span class="text-muted">${__("Mode")}:</span> ${mode}</div>
				<div><span class="text-muted">${__("Logistics")}:</span> ${lstat}</div>
				<div><span class="text-muted">${__("Pickup Slot")}:</span> ${claim.pickup_slot || "—"}</div>
				<div><span class="text-muted">${__("Tracking")}:</span> ${claim.pickup_tracking_no || "—"}</div>
				<div><span class="text-muted">${__("Partner")}:</span> ${claim.pickup_partner || "—"}</div>
				<div><span class="text-muted">${__("Received At")}:</span> ${claim.device_received_at || "—"}</div>
			</div>
			${claim.pickup_address ? `<div style="margin-top:4px;font-size:11px"><span class="text-muted">${__("Address")}:</span> ${frappe.utils.escape_html(claim.pickup_address)}</div>` : ""}`;
		}

		return `
		<div style="padding:8px 10px;background:#f0fdfa;border:1px solid #99f6e4;border-radius:6px;font-size:12px;margin-top:8px">
			<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
				<b><i class="fa fa-tasks"></i> ${__("Claim Progress")}</b>
				<div style="display:flex;gap:4px">${qc_badge} ${fee_status_badge}</div>
			</div>
			<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;font-size:11px">
				<div><span class="text-muted">${__("GoGizmo")}:</span> <b>₹${format_number(claim.gogizmo_share || 0)}</b></div>
				<div><span class="text-muted">${__("Customer")}:</span> <b>₹${format_number(claim.customer_share || 0)}</b></div>
				<div><span class="text-muted">${__("Fee")}:</span> <b>₹${format_number(claim.processing_fee_amount || 0)}</b></div>
			</div>
			${logistics_detail}
			${actions.length ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${actions.join("")}</div>` : ""}
		</div>`;
	}

	_fee_status_badge(claim) {
		const s = claim.processing_fee_status || "Not Required";
		const m = {
			"Not Required": { bg: "#f3f4f6", fg: "#6b7280" },
			"Pending": { bg: "#fef3c7", fg: "#92400e" },
			"Link Sent": { bg: "#dbeafe", fg: "#1e40af" },
			"Paid": { bg: "#dcfce7", fg: "#166534" },
			"Waived": { bg: "#ede9fe", fg: "#5b21b6" },
		};
		const c = m[s] || m["Not Required"];
		return `<span class="badge" style="background:${c.bg};color:${c.fg};font-size:10px;padding:1px 5px">${__("Fee")}: ${s}</span>`;
	}

	_render_receiving_block(claim) {
		if (!claim.device_received_at) return "";
		return `
		<div style="padding:8px 10px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:6px;font-size:12px;margin-top:8px">
			<b><i class="fa fa-inbox"></i> ${__("Device Received")}</b>
			<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:6px">
				<div><span class="text-muted">${__("Received At")}:</span> ${claim.device_received_at}</div>
				<div><span class="text-muted">${__("By")}:</span> ${claim.device_received_by || "—"}</div>
				<div><span class="text-muted">${__("Condition")}:</span> ${claim.condition_on_receipt || "—"}</div>
				<div><span class="text-muted">${__("IMEI OK")}:</span> ${claim.imei_verified ? "✓ Yes" : "✗ No"}</div>
				${claim.accessories_received ? `<div style="grid-column:span 2"><span class="text-muted">${__("Accessories")}:</span> ${frappe.utils.escape_html(claim.accessories_received)}</div>` : ""}
				${claim.receiving_remarks ? `<div style="grid-column:span 2"><span class="text-muted">${__("Remarks")}:</span> ${frappe.utils.escape_html(claim.receiving_remarks)}</div>` : ""}
			</div>
		</div>`;
	}

	_render_qc_block(claim) {
		if (!claim.intake_qc_status) return "";
		const passed = claim.intake_qc_status === "Passed";
		const bg = passed ? "#dcfce7" : "#fef2f2";
		const border = passed ? "#bbf7d0" : "#fecaca";
		return `
		<div style="padding:8px 10px;background:${bg};border:1px solid ${border};border-radius:6px;font-size:12px;margin-top:8px">
			<b><i class="fa fa-check-square-o"></i> ${__("Intake QC")} — ${claim.intake_qc_status}</b>
			<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:6px">
				<div><span class="text-muted">${__("Done By")}:</span> ${claim.intake_qc_by || "—"}</div>
				<div><span class="text-muted">${__("Done At")}:</span> ${claim.intake_qc_at || "—"}</div>
				${claim.intake_qc_result_reason ? `<div style="grid-column:span 2"><span class="text-muted">${__("Reason")}:</span> ${frappe.utils.escape_html(claim.intake_qc_result_reason)}</div>` : ""}
				${claim.intake_qc_remarks ? `<div style="grid-column:span 2"><span class="text-muted">${__("Remarks")}:</span> ${frappe.utils.escape_html(claim.intake_qc_remarks)}</div>` : ""}
			</div>
		</div>`;
	}

	_render_fee_block(claim) {
		if (!claim.processing_fee_amount && claim.processing_fee_status === "Not Required") return "";
		const s = claim.processing_fee_status || "Pending";
		const paid = ["Paid","Waived","Not Required"].includes(s);
		const bg = paid ? "#dcfce7" : "#fef3c7";
		const border = paid ? "#bbf7d0" : "#fde68a";
		return `
		<div style="padding:8px 10px;background:${bg};border:1px solid ${border};border-radius:6px;font-size:12px;margin-top:8px">
			<b><i class="fa fa-credit-card"></i> ${__("Processing Fee")} — ${s}</b>
			<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:6px">
				<div><span class="text-muted">${__("Amount")}:</span> <b>₹${format_number(claim.processing_fee_amount || 0)}</b></div>
				${s === "Paid" ? `<div><span class="text-muted">${__("Paid")}:</span> ₹${format_number(claim.processing_fee_paid_amount || 0)} via ${claim.payment_mode || "—"}</div>` : ""}
				${s === "Paid" && claim.processing_fee_paid_at ? `<div><span class="text-muted">${__("Paid At")}:</span> ${claim.processing_fee_paid_at}</div>` : ""}
				${claim.processing_fee_payment_ref ? `<div><span class="text-muted">${__("Ref")}:</span> ${claim.processing_fee_payment_ref}</div>` : ""}
				${s === "Link Sent" ? `<div><span class="text-muted">${__("Link Sent")}:</span> ${claim.processing_fee_link_sent_at || "—"} via ${claim.processing_fee_link_sent_via || "—"}</div>` : ""}
				${s === "Waived" ? `<div><span class="text-muted">${__("Waived")}:</span> ₹${format_number(claim.waived_amount || 0)}</div>` : ""}
				${claim.waiver_reason ? `<div style="grid-column:span 2"><span class="text-muted">${__("Reason")}:</span> ${frappe.utils.escape_html(claim.waiver_reason)}</div>` : ""}
			</div>
		</div>`;
	}

	_handle_logistics_action(panel, claim_name, action) {
		const refresh = (msg) => {
			frappe.show_alert({ message: __(msg || "Updated"), indicator: "green" });
			this._show_claim_detail(panel, claim_name);
			this._load_claims_pipeline(panel);
		};
		const call_logistics = (args = {}) => {
			return frappe.xcall("ch_item_master.ch_item_master.warranty_api.update_claim_logistics", {
				claim_name, action, ...args,
			}).then(() => refresh("Claim logistics updated"));
		};

		// ── Pickup / Delivery (existing) ──
		if (action === "schedule_pickup") {
			frappe.prompt([
				{ fieldname: "pickup_address", fieldtype: "Small Text", label: __("Pickup Address"), reqd: 1 },
				{ fieldname: "pickup_slot", fieldtype: "Datetime", label: __("Pickup Slot") },
				{ fieldname: "pickup_partner", fieldtype: "Data", label: __("Pickup Partner") },
				{ fieldname: "pickup_tracking_no", fieldtype: "Data", label: __("Tracking Number") },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			], (v) => call_logistics(v), __("Schedule Pickup"), __("Save"));
			return;
		}
		if (action === "mark_picked_up") {
			frappe.prompt([
				{ fieldname: "delivery_otp", fieldtype: "Data", label: __("Pickup OTP"), description: __("Optional") },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			], (v) => call_logistics(v), __("Mark Picked Up"), __("Confirm"));
			return;
		}
		if (action === "mark_out_for_delivery") {
			frappe.prompt([
				{ fieldname: "pickup_partner", fieldtype: "Select", label: __("Delivery Partner"),
				  options: "Delhivery\nBluedart\nDTDC\nEcom Express\nXpressbees\nShadowfax\nSelf" },
				{ fieldname: "pickup_tracking_no", fieldtype: "Data", label: __("Tracking Number") },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			], (v) => call_logistics(v), __("Out for Delivery"), __("Confirm"));
			return;
		}
		if (action === "mark_delivered_back") {
			frappe.prompt([
				{ fieldname: "delivery_otp", fieldtype: "Data", label: __("Delivery OTP"), description: __("Optional") },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			], (v) => call_logistics(v), __("Mark Delivered"), __("Confirm"));
			return;
		}

		// ── Device Receiving ──
		if (action === "receive_device") {
			frappe.prompt([
				{ fieldname: "condition_on_receipt", fieldtype: "Select", label: __("Device Condition"),
				  options: "\nGood\nMinor Damage\nMajor Damage\nWrong Device\nEmpty Parcel", reqd: 1 },
				{ fieldname: "accessories_received", fieldtype: "Small Text", label: __("Accessories Received"),
				  description: __("Charger, cable, box, etc.") },
				{ fieldname: "imei_verified", fieldtype: "Check", label: __("IMEI Verified"), default: 1 },
				{ fieldname: "receiving_remarks", fieldtype: "Small Text", label: __("Remarks") },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.receive_claim_device", {
					claim_name,
					condition_on_receipt: v.condition_on_receipt,
					accessories_received: v.accessories_received,
					imei_verified: v.imei_verified,
					receiving_remarks: v.receiving_remarks,
				}).then(() => refresh("Device received successfully"));
			}, __("Receive Device"), __("Confirm Receipt"));
			return;
		}

		// ── Intake QC ──
		if (action === "perform_qc") {
			frappe.prompt([
				{ fieldname: "qc_result", fieldtype: "Select", label: __("QC Result"),
				  options: "\nPassed\nFailed\nNot Repairable", reqd: 1 },
				{ fieldname: "qc_remarks", fieldtype: "Small Text", label: __("QC Remarks") },
				{ fieldname: "qc_result_reason", fieldtype: "Small Text", label: __("Reason"),
				  depends_on: "eval:doc.qc_result!='Passed'", mandatory_depends_on: "eval:doc.qc_result!='Passed'" },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.perform_claim_qc", {
					claim_name,
					qc_result: v.qc_result,
					qc_remarks: v.qc_remarks,
					qc_result_reason: v.qc_result_reason,
				}).then(() => refresh("Intake QC completed"));
			}, __("Perform Intake QC"), __("Submit QC"));
			return;
		}

		// ── Generate Fee ──
		if (action === "generate_fee") {
			frappe.prompt([
				{ fieldname: "fee_amount", fieldtype: "Currency", label: __("Fee Amount (override)"),
				  description: __("Leave blank for auto-calculation") },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.generate_claim_fee", {
					claim_name,
					fee_amount: v.fee_amount || null,
				}).then(() => refresh("Processing fee generated"));
			}, __("Generate Processing Fee"), __("Generate"));
			return;
		}

		// ── Collect Fee (mark paid) ──
		if (action === "collect_fee") {
			frappe.prompt([
				{ fieldname: "paid_amount", fieldtype: "Currency", label: __("Amount Paid"), reqd: 1 },
				{ fieldname: "payment_mode", fieldtype: "Select", label: __("Payment Mode"),
				  options: "\nCash\nUPI\nCard\nOnline\nNEFT", reqd: 1 },
				{ fieldname: "payment_ref", fieldtype: "Data", label: __("Transaction Reference") },
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.mark_claim_fee_paid", {
					claim_name,
					paid_amount: v.paid_amount,
					payment_mode: v.payment_mode,
					payment_ref: v.payment_ref,
					remarks: v.remarks,
				}).then(() => refresh("Processing fee collected"));
			}, __("Collect Processing Fee"), __("Confirm Payment"));
			return;
		}

		// ── Send Fee Link ──
		if (action === "send_fee_link") {
			frappe.prompt([
				{ fieldname: "channel", fieldtype: "Select", label: __("Send Via"),
				  options: "WhatsApp\nSMS", default: "WhatsApp", reqd: 1 },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.send_claim_fee_link", {
					claim_name,
					channel: v.channel,
				}).then(() => refresh("Payment link sent"));
			}, __("Send Payment Link"), __("Send"));
			return;
		}

		// ── Waive Fee ──
		if (action === "waive_fee") {
			frappe.prompt([
				{ fieldname: "waiver_reason", fieldtype: "Small Text", label: __("Waiver Reason"), reqd: 1 },
				{ fieldname: "waived_amount", fieldtype: "Currency", label: __("Amount to Waive"),
				  description: __("Leave blank to waive full amount") },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.waive_claim_fee", {
					claim_name,
					waiver_reason: v.waiver_reason,
					waived_amount: v.waived_amount || null,
				}).then(() => refresh("Fee waiver processed"));
			}, __("Waive Processing Fee"), __("Submit"));
			return;
		}

		// ── Create GoFix Ticket ──
		if (action === "create_ticket") {
			frappe.confirm(
				__("Create GoFix repair ticket for this claim?"),
				() => {
					frappe.xcall("ch_item_master.ch_item_master.warranty_api.create_claim_repair_ticket", {
						claim_name,
					}).then(() => refresh("GoFix ticket created"));
				}
			);
			return;
		}

		// ── Request Additional Approval ──
		if (action === "request_additional_approval") {
			frappe.prompt([
				{ fieldname: "additional_issue_description", fieldtype: "Small Text",
				  label: __("Additional Issue Found"), reqd: 1 },
				{ fieldname: "additional_cost_estimated", fieldtype: "Currency",
				  label: __("Estimated Additional Cost"), reqd: 1 },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.request_additional_approval_claim", {
					claim_name,
					additional_issue_description: v.additional_issue_description,
					additional_cost_estimated: v.additional_cost_estimated,
				}).then(() => refresh("Additional approval requested"));
			}, __("Request Customer Approval for Additional Cost"), __("Send Request"));
			return;
		}

		// ── Resolve Additional Approval (Approved) ──
		if (action === "resolve_additional_approved") {
			frappe.prompt([
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.resolve_additional_approval_claim", {
					claim_name, decision: "Approved", remarks: v.remarks,
				}).then(() => refresh("Customer approved additional cost"));
			}, __("Customer Approved Additional Cost"), __("Confirm"));
			return;
		}

		// ── Resolve Additional Approval (Rejected) ──
		if (action === "resolve_additional_rejected") {
			frappe.prompt([
				{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.resolve_additional_approval_claim", {
					claim_name, decision: "Rejected", remarks: v.remarks,
				}).then(() => refresh("Customer rejected additional cost"));
			}, __("Customer Rejected Additional Cost"), __("Confirm"));
			return;
		}

		// ── Perform Final QC ──
		if (action === "perform_final_qc") {
			frappe.prompt([
				{ fieldname: "qc_result", fieldtype: "Select", label: __("Final QC Result"),
				  options: "\nPassed\nFailed", reqd: 1 },
				{ fieldname: "qc_remarks", fieldtype: "Small Text", label: __("QC Remarks") },
			], (v) => {
				frappe.xcall("ch_item_master.ch_item_master.warranty_api.perform_final_qc_claim", {
					claim_name,
					qc_result: v.qc_result,
					qc_remarks: v.qc_remarks,
				}).then(() => refresh("Final QC completed"));
			}, __("Final QC After Repair"), __("Submit QC"));
			return;
		}
	}

	// ── Claims Pipeline ─────────────────────────────────────────────

	_load_claims_pipeline(panel) {
		const pipe = panel.find(".ch-claim-pipeline");
		pipe.html(`<div class="text-muted text-center" style="padding:12px"><i class="fa fa-spinner fa-spin"></i></div>`);

		frappe.xcall("frappe.client.get_list", {
			doctype: "CH Warranty Claim",
			filters: { docstatus: ["!=", 2], claim_status: ["not in", ["Closed", "Cancelled"]] },
			fields: ["name", "claim_date", "claim_status", "serial_no", "customer_name",
				"customer", "item_name", "coverage_type"],
			order_by: "modified desc",
			limit_page_length: 15,
		}).then((claims) => {
			if (!claims || !claims.length) {
				pipe.html(`<div class="text-muted text-center" style="padding:12px">${__("No pending claims")}</div>`);
				return;
			}
			pipe.html(claims.map(c => {
				const sc = this._status_color(c.claim_status);
				const cov = this._coverage_style(c.coverage_type);
				return `
				<div class="ch-claim-track" data-claim="${c.name}"
					style="display:flex;align-items:center;gap:6px;padding:8px 6px;border-bottom:1px solid #f3f4f6;
					font-size:12px;cursor:pointer;border-radius:4px;transition:background 0.15s"
					onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='transparent'">
					<span class="badge" style="background:${sc.bg};color:${sc.fg};font-size:10px;min-width:70px;text-align:center;padding:2px 4px">
						${c.claim_status}</span>
					<span style="flex:1"><b>${c.customer_name || c.customer || ""}</b>
						<span class="text-muted">— ${c.item_name || c.serial_no}</span></span>
					<span class="text-muted" style="font-size:11px">${c.claim_date}</span>
					<span class="badge" style="background:${cov.bg};color:${cov.fg};font-size:10px;padding:1px 5px">${c.coverage_type || "N/A"}</span>
					<i class="fa fa-chevron-right" style="color:#d1d5db"></i>
				</div>`;
			}).join(""));
		}).catch(() => {
			pipe.html(`<div class="text-muted text-center" style="padding:12px">${__("Error loading claims")}</div>`);
		});
	}

	// ── Auto-refresh ────────────────────────────────────────────────

	_start_auto_refresh(panel) {
		this._stop_auto_refresh();
		this._refresh_timer = setInterval(() => this._load_claims_pipeline(panel), 30000);
	}

	_stop_auto_refresh() {
		if (this._refresh_timer) {
			clearInterval(this._refresh_timer);
			this._refresh_timer = null;
		}
	}

	// ── Style helpers ───────────────────────────────────────────────

	_status_color(status) {
		const m = {
			"Draft":            { bg: "#f3f4f6", fg: "#374151" },
			"Pending Coverage Check": { bg: "#f3f4f6", fg: "#6b7280" },
			"Coverage Identified": { bg: "#dbeafe", fg: "#1e40af" },
			"Pending Approval": { bg: "#fef3c7", fg: "#92400e" },
			"Need More Information": { bg: "#fef9c3", fg: "#854d0e" },
			"Approved":         { bg: "#dbeafe", fg: "#1e40af" },
			"Partially Approved": { bg: "#fff7ed", fg: "#c2410c" },
			"Pickup Requested": { bg: "#ccfbf1", fg: "#115e59" },
			"Pickup Scheduled": { bg: "#99f6e4", fg: "#115e59" },
			"Picked Up":        { bg: "#5eead4", fg: "#134e4a" },
			"Device Received":  { bg: "#a7f3d0", fg: "#065f46" },
			"QC Pending":       { bg: "#fef3c7", fg: "#92400e" },
			"QC Passed":        { bg: "#bbf7d0", fg: "#166534" },
			"QC Failed":        { bg: "#fecaca", fg: "#991b1b" },
			"Fee Pending":      { bg: "#fef3c7", fg: "#92400e" },
			"Fee Paid":         { bg: "#dcfce7", fg: "#166534" },
			"Fee Waived":       { bg: "#ede9fe", fg: "#5b21b6" },
			"Rejected":         { bg: "#fef2f2", fg: "#991b1b" },
			"Ticket Created":   { bg: "#ede9fe", fg: "#5b21b6" },
			"Sent to Manufacturer": { bg: "#dbeafe", fg: "#1e3a8a" },
			"In Repair":        { bg: "#fef9c3", fg: "#854d0e" },
			"Additional Approval Pending": { bg: "#fef3c7", fg: "#92400e" },
			"Awaiting Spare":   { bg: "#fef9c3", fg: "#854d0e" },
			"Repair Complete":  { bg: "#dcfce7", fg: "#166534" },
			"Final QC Pending": { bg: "#fef3c7", fg: "#92400e" },
			"Final QC Passed":  { bg: "#bbf7d0", fg: "#166534" },
			"Invoice Pending":  { bg: "#fef3c7", fg: "#92400e" },
			"Invoice Raised":   { bg: "#dbeafe", fg: "#1e40af" },
			"Payment Pending":  { bg: "#fef3c7", fg: "#92400e" },
			"Payment Received": { bg: "#dcfce7", fg: "#166534" },
			"Out for Delivery": { bg: "#e0f2fe", fg: "#075985" },
			"Delivered":        { bg: "#bbf7d0", fg: "#166534" },
			"Closed":           { bg: "#f3f4f6", fg: "#374151" },
			"Cancelled":        { bg: "#fee2e2", fg: "#991b1b" },
			"Not Repairable":   { bg: "#fecaca", fg: "#991b1b" },
		};
		return m[status] || { bg: "#f3f4f6", fg: "#374151" };
	}

	_coverage_style(cov) {
		const styles = {
			"anniversary_warranty": { bg: "#dcfce7", fg: "#166534" },
			"vas_plan": { bg: "#fff7ed", fg: "#92400e" },
			"repair_warranty": { bg: "#dbeafe", fg: "#1e40af" },
			"manufacturer_warranty": { bg: "#e0f2fe", fg: "#075985" },
			"paid_repair": { bg: "#fef2f2", fg: "#991b1b" },
			"goodwill": { bg: "#ede9fe", fg: "#5b21b6" },
		};
		return styles[cov] || { bg: "#f3f4f6", fg: "#6b7280" };
	}

	_coverage_label(cov) {
		const labels = {
			"anniversary_warranty": __("Anniversary Warranty"),
			"vas_plan": __("VAS Plan"),
			"repair_warranty": __("Repair Warranty"),
			"manufacturer_warranty": __("Manufacturer Warranty"),
			"paid_repair": __("Paid Repair"),
			"goodwill": __("Goodwill"),
		};
		return labels[cov] || cov || "N/A";
	}

	_plan_status_style(status) {
		const m = {
			active:    { bg: "#dcfce7", fg: "#166534", label: __("Active") },
			expired:   { bg: "#fef2f2", fg: "#991b1b", label: __("Expired") },
			exhausted: { bg: "#fef3c7", fg: "#92400e", label: __("Exhausted") },
			void:      { bg: "#f3f4f6", fg: "#6b7280", label: __("Void") },
			claimed:   { bg: "#fef3c7", fg: "#92400e", label: __("Claimed") },
		};
		return m[status] || { bg: "#f3f4f6", fg: "#6b7280", label: status || "—" };
	}
}