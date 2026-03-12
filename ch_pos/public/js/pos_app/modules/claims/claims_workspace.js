/**
 * CH POS — Warranty Claims Workspace
 *
 * Allows POS staff to:
 * 1. Scan IMEI → see device + warranty status + claim history
 * 2. Initiate a warranty claim → auto-routes for approval or directly to GoFix
 * 3. View & track pending claims with real-time status updates
 *
 * Works with:
 * - CH Serial Lifecycle records (full device history)
 * - ERPNext Serial No fallback (for devices without lifecycle records)
 */
import { PosState, EventBus } from "../../state.js";

export class ClaimsWorkspace {
	constructor() {
		this._device_data = null;
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
						${__("Warranty Claims")}
					</h4>
					<span class="ch-mode-hint">${__("Scan IMEI to check warranty and raise a claim for GoFix repair")}</span>
				</div>

				<!-- IMEI Scan -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header"><i class="fa fa-barcode"></i> ${__("Scan Device")}</div>
					<div class="section-body">
						<div style="display:flex;gap:var(--pos-space-sm)">
							<input type="text" class="form-control ch-claim-imei"
								placeholder="${__("Enter IMEI / Serial Number...")}"
								style="flex:1;font-size:16px;font-family:monospace">
							<button class="btn btn-primary ch-claim-lookup">
								<i class="fa fa-search"></i> ${__("Lookup")}
							</button>
						</div>
					</div>
				</div>

				<!-- Device Info (hidden until lookup) -->
				<div class="ch-claim-device-info" style="display:none"></div>

				<!-- Claim Form (hidden until lookup) -->
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
		panel.on("keydown", ".ch-claim-imei", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				panel.find(".ch-claim-lookup").trigger("click");
			}
		});

		panel.on("click", ".ch-claim-lookup", () => {
			const imei = panel.find(".ch-claim-imei").val().trim();
			if (!imei) {
				frappe.show_alert({ message: __("Enter an IMEI or Serial Number"), indicator: "orange" });
				return;
			}
			this._lookup_device(panel, imei);
		});

		panel.on("click", ".ch-claim-refresh", () => {
			this._load_claims_pipeline(panel);
		});

		panel.on("click", ".ch-claim-submit", () => {
			this._submit_claim(panel);
		});

		// Claim status detail view
		panel.on("click", ".ch-claim-track", (e) => {
			const claim_name = $(e.currentTarget).data("claim");
			if (claim_name) this._show_claim_detail(panel, claim_name);
		});

		setTimeout(() => panel.find(".ch-claim-imei").focus(), 200);
	}

	// ── Device Lookup ───────────────────────────────────────────────

	_lookup_device(panel, imei) {
		panel.find(".ch-claim-lookup").prop("disabled", true).html(
			`<i class="fa fa-spinner fa-spin"></i> ${__("Looking up...")}`
		);

		frappe.xcall(
			"ch_item_master.ch_item_master.warranty_api.get_device_claim_info",
			{ serial_no: imei, company: PosState.company }
		).then((data) => {
			this._render_device_info(panel, data, imei);
			this._render_claim_form(panel, data, imei);
		}).catch((err) => {
			panel.find(".ch-claim-device-info").html(
				`<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-body text-center" style="padding:24px;color:var(--pos-danger)">
						<i class="fa fa-exclamation-triangle fa-2x"></i>
						<p style="margin-top:8px">${__("Device not found or error occurred")}</p>
						<small class="text-muted">${err.message || ""}</small>
					</div>
				</div>`
			).show();
			panel.find(".ch-claim-form-area").hide();
		}).finally(() => {
			panel.find(".ch-claim-lookup").prop("disabled", false).html(
				`<i class="fa fa-search"></i> ${__("Lookup")}`
			);
		});
	}

	_render_device_info(panel, data, imei) {
		const dev = data.device_info;
		const warranty = data.warranty_info || {};
		const claims = data.existing_claims || [];
		const source = data.source;

		if (!dev) {
			panel.find(".ch-claim-device-info").html(
				`<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-body text-center" style="padding:24px;color:var(--pos-muted)">
						<i class="fa fa-question-circle fa-2x"></i>
						<p style="margin-top:8px">${__("No device record found for")} <b>${imei}</b></p>
						<small>${__("This serial number does not exist in the system")}</small>
					</div>
				</div>`
			).show();
			panel.find(".ch-claim-form-area").hide();
			return;
		}

		// Source badge (lifecycle vs serial no)
		const source_badge = source === "serial_no"
			? `<span class="badge" style="background:#e0e7ff;color:#3730a3;font-size:10px;padding:2px 6px"
				title="${__("Device found via ERPNext Serial No (no lifecycle record)")}">Serial No</span>`
			: "";

		// Warranty badge
		const w_covered = warranty.warranty_covered;
		const w_status = warranty.warranty_status || "No Warranty";
		const w_badge = w_covered
			? `<span class="badge" style="background:#dcfce7;color:#166534;padding:4px 10px;font-size:12px">
				🛡 ${w_status}</span>`
			: `<span class="badge" style="background:#fef2f2;color:#991b1b;padding:4px 10px;font-size:12px">
				❌ ${w_status}</span>`;

		// Covering plan info
		let plan_html = "";
		if (warranty.covering_plan) {
			const cp = warranty.covering_plan;
			plan_html = `
				<div style="margin-top:8px;padding:8px 12px;background:#f0fdf4;border-radius:6px;font-size:12px">
					<b>${cp.plan_title || cp.warranty_plan}</b> (${cp.plan_type})
					<br>Valid: ${cp.start_date || "?"} → ${cp.end_date || "?"}
					${cp.deductible_amount > 0 ? `<br>Deductible: ₹${cp.deductible_amount}` : ""}
					<br>Claims: ${cp.claims_used || 0} / ${cp.max_claims || "∞"}
				</div>`;
		}

		// Active / recent claims for this device
		let claims_html = "";
		if (claims.length) {
			claims_html = `
				<div style="margin-top:12px">
					<b style="font-size:12px;color:var(--pos-muted)">${__("Claim History")}:</b>
					<div style="margin-top:4px;max-height:150px;overflow-y:auto">
					${claims.map(c => {
						const sc = this._status_color(c.claim_status);
						return `
						<div class="ch-claim-track" data-claim="${c.name}"
							style="display:flex;justify-content:space-between;align-items:center;padding:6px 8px;
							border-bottom:1px solid #f3f4f6;font-size:12px;cursor:pointer;border-radius:4px;
							transition:background 0.15s"
							onmouseover="this.style.background='#f8fafc'"
							onmouseout="this.style.background='transparent'">
							<span><b>${c.name}</b></span>
							<span class="text-muted">${c.claim_date}</span>
							<span class="badge" style="background:${sc.bg};color:${sc.fg};font-size:10px;padding:2px 6px">
								${c.claim_status}
							</span>
							<span style="color:#6b7280"><i class="fa fa-chevron-right"></i></span>
						</div>`;
					}).join("")}
					</div>
				</div>`;
		}

		panel.find(".ch-claim-device-info").html(`
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
				<div class="section-header" style="display:flex;justify-content:space-between;align-items:center">
					<span><i class="fa fa-mobile"></i> ${__("Device Details")} ${source_badge}</span>
					${w_badge}
				</div>
				<div class="section-body">
					<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px">
						<div><span class="text-muted">${__("Item")}:</span> <b>${dev.item_name || dev.item_code}</b></div>
						<div><span class="text-muted">${__("Serial")}:</span> <code>${dev.serial_no}</code></div>
						<div><span class="text-muted">${__("IMEI")}:</span> <code>${dev.imei_number || "N/A"}</code></div>
						<div><span class="text-muted">${__("Status")}:</span> ${dev.lifecycle_status || "N/A"}</div>
						<div><span class="text-muted">${__("Customer")}:</span> ${dev.customer_name || dev.customer || `<em class="text-warning">${__("Not linked")}</em>`}</div>
						<div><span class="text-muted">${__("Sale Date")}:</span> ${dev.sale_date || "N/A"}</div>
					</div>
					${plan_html}
					${claims_html}
				</div>
			</div>
		`).show();

		this._device_data = data;
	}

	// ── Claim Form ──────────────────────────────────────────────────

	_render_claim_form(panel, data, imei) {
		const dev = data.device_info;
		if (!dev) {
			panel.find(".ch-claim-form-area").hide();
			return;
		}

		const warranty = data.warranty_info || {};
		const w_covered = warranty.warranty_covered;
		const has_customer = !!(dev.customer);

		// Customer field: pre-filled if known, editable if not
		const customer_field = has_customer
			? `<div class="ch-pos-field-group">
				<label>${__("Customer")}</label>
				<input type="text" class="form-control" value="${dev.customer_name || dev.customer}" disabled
					style="background:#f9fafb">
			   </div>`
			: `<div class="ch-pos-field-group">
				<label>${__("Customer")} <span style="color:var(--pos-danger)">*</span></label>
				<div class="ch-claim-customer-field"></div>
			   </div>`;

		panel.find(".ch-claim-form-area").html(`
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);border:2px solid ${w_covered ? "#86efac" : "#fca5a5"}">
				<div class="section-header" style="background:${w_covered ? "#f0fdf4" : "#fef2f2"}">
					<i class="fa fa-plus-circle"></i> ${__("Raise Warranty Claim")}
				</div>
				<div class="section-body">
					<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md)">
						${customer_field}
						<div class="ch-pos-field-group">
							<label>${__("Issue Category")}</label>
							<div class="ch-claim-issue-cat"></div>
						</div>
					</div>
					<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md);margin-top:var(--pos-space-sm)">
						<div class="ch-pos-field-group">
							<label>${__("Customer Phone")}</label>
							<input type="text" class="form-control ch-claim-phone" placeholder="${__("Contact number for updates")}">
						</div>
						<div class="ch-pos-field-group">
							<label>${__("Estimated Repair Cost (₹)")}</label>
							<input type="number" class="form-control ch-claim-est-cost" min="0" step="100"
								placeholder="${__("e.g. 2500")}">
						</div>
					</div>
					<div class="ch-pos-field-group" style="margin-top:var(--pos-space-sm)">
						<label>${__("Issue Description")} <span style="color:var(--pos-danger)">*</span></label>
						<textarea class="form-control ch-claim-issue-desc" rows="3"
							style="min-height:80px;resize:vertical"
							placeholder="${__("Describe what's wrong with the device...")}"></textarea>
					</div>

					${w_covered ? `
					<div style="margin-top:12px;padding:10px 14px;background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;font-size:12px">
						<b>⚠ ${__("In-Warranty Claim")}</b><br>
						${__("GoGizmo will cover this repair. Approval from GoGizmo Head is required.")}
						${warranty.covering_plan?.deductible_amount > 0 ?
							`<br>${__("Customer deductible")}: <b>₹${warranty.covering_plan.deductible_amount}</b>` : ""}
					</div>` : `
					<div style="margin-top:12px;padding:10px 14px;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;font-size:12px">
						<b>ℹ ${__("Out-of-Warranty")}</b><br>
						${__("Customer will be billed by GoFix. GoFix ticket will be created immediately.")}
					</div>`}

					<button class="btn btn-primary ch-claim-submit" style="width:100%;margin-top:var(--pos-space-md);padding:10px">
						<i class="fa fa-paper-plane"></i>
						${w_covered ? __("Submit Claim (Needs Approval)") : __("Submit Claim & Create GoFix Ticket")}
					</button>
				</div>
			</div>
		`).show();

		// Issue category link field
		frappe.ui.form.make_control({
			df: { fieldname: "issue_category", fieldtype: "Link", options: "Issue Category",
			      placeholder: __("Select issue category") },
			parent: panel.find(".ch-claim-issue-cat"),
			render_input: true,
		});

		// Customer link field (only if no customer on device)
		if (!has_customer) {
			frappe.ui.form.make_control({
				df: { fieldname: "customer", fieldtype: "Link", options: "Customer",
				      placeholder: __("Select customer"), reqd: 1 },
				parent: panel.find(".ch-claim-customer-field"),
				render_input: true,
			});
		}
	}

	// ── Submit Claim ────────────────────────────────────────────────

	_submit_claim(panel) {
		const data = this._device_data;
		if (!data || !data.device_info) {
			frappe.show_alert({ message: __("Look up a device first"), indicator: "orange" });
			return;
		}

		const dev = data.device_info;
		const issue_desc = panel.find(".ch-claim-issue-desc").val().trim();
		if (!issue_desc) {
			frappe.show_alert({ message: __("Issue description is required"), indicator: "orange" });
			return;
		}

		// Customer: from device or from form field
		let customer = dev.customer;
		if (!customer) {
			customer = panel.find(".ch-claim-customer-field .link-field input").val();
			if (!customer) {
				frappe.show_alert({ message: __("Please select a customer"), indicator: "orange" });
				return;
			}
		}

		const btn = panel.find(".ch-claim-submit");
		btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Submitting...")}`);

		frappe.xcall(
			"ch_item_master.ch_item_master.warranty_api.initiate_warranty_claim",
			{
				serial_no: dev.serial_no,
				customer: customer,
				item_code: dev.item_code,
				company: PosState.company,
				issue_description: issue_desc,
				issue_category: panel.find(".ch-claim-issue-cat .link-field input").val() || "",
				reported_at_company: PosState.company,
				reported_at_store: PosState.store || "",
				estimated_repair_cost: parseFloat(panel.find(".ch-claim-est-cost").val()) || 0,
				customer_phone: panel.find(".ch-claim-phone").val().trim() || "",
			}
		).then((result) => {
			this._show_claim_result(panel, result);
			this._load_claims_pipeline(panel);
		}).catch((err) => {
			frappe.show_alert({ message: err.message || __("Failed to create claim"), indicator: "red" });
		}).finally(() => {
			btn.prop("disabled", false).html(
				`<i class="fa fa-paper-plane"></i> ${__("Submit Claim")}`
			);
		});
	}

	_show_claim_result(panel, result) {
		const is_approved = result.claim_status === "Approved";
		const needs_approval = result.requires_approval;

		let status_html;
		if (is_approved && result.service_request) {
			status_html = `
				<div style="text-align:center;padding:24px;color:#166534">
					<i class="fa fa-check-circle fa-3x"></i>
					<h5 style="margin-top:12px">${__("Claim Submitted & GoFix Ticket Created!")}</h5>
					<p>${__("Claim")}: <b>${result.claim_name}</b></p>
					<p>${__("GoFix Ticket")}: <b>${result.service_request}</b></p>
					<p style="font-size:13px;color:var(--pos-muted)">${__("Customer pays")}: ₹${result.customer_share || 0}</p>
				</div>`;
		} else if (needs_approval) {
			status_html = `
				<div style="text-align:center;padding:24px;color:#d97706">
					<i class="fa fa-clock-o fa-3x"></i>
					<h5 style="margin-top:12px">${__("Claim Submitted — Pending GoGizmo Approval")}</h5>
					<p>${__("Claim")}: <b>${result.claim_name}</b></p>
					<div style="display:flex;justify-content:center;gap:20px;margin-top:8px;font-size:13px">
						<span>${__("GoGizmo pays")}: <b>₹${result.gogizmo_share || 0}</b></span>
						<span>${__("Customer pays")}: <b>₹${result.customer_share || 0}</b></span>
					</div>
					<p style="font-size:12px;color:var(--pos-muted);margin-top:8px">
						${__("GoFix ticket will be created after GoGizmo Head approves")}
					</p>
				</div>`;
		} else {
			status_html = `
				<div style="text-align:center;padding:24px;color:#2563eb">
					<i class="fa fa-info-circle fa-3x"></i>
					<h5 style="margin-top:12px">${__("Claim Created")}</h5>
					<p><b>${result.claim_name}</b></p>
					<p>${result.coverage_type} — ${result.warranty_status}</p>
				</div>`;
		}

		// Info the exec can tell the customer
		const customer_msg = needs_approval
			? __("Please inform the customer: Your claim is under review. We'll update you once approved.")
			: (result.service_request
				? __("Please inform the customer: Your device has been sent for repair. Track with reference: ") + result.claim_name
				: "");

		if (customer_msg) {
			status_html += `
				<div style="margin-top:12px;padding:10px 14px;background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;font-size:12px">
					<b>💬 ${__("Tell the Customer")}:</b><br>
					${customer_msg}
				</div>`;
		}

		panel.find(".ch-claim-form-area").html(
			`<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);border:2px solid #86efac">
				<div class="section-body">${status_html}</div>
			</div>`
		);
	}

	// ── Claim Detail View (Track Status) ────────────────────────────

	_show_claim_detail(panel, claim_name) {
		frappe.xcall("frappe.client.get", {
			doctype: "CH Warranty Claim",
			name: claim_name,
		}).then((claim) => {
			const sc = this._status_color(claim.claim_status);
			const steps = this._get_progress_steps(claim);

			let log_html = "";
			if (claim.claim_log && claim.claim_log.length) {
				log_html = claim.claim_log.map(l => `
					<div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid #f3f4f6;font-size:11px">
						<span style="color:#9ca3af;min-width:130px">${l.log_timestamp}</span>
						<span style="flex:1"><b>${l.action}</b> — ${l.remarks || ""}</span>
						<span class="text-muted">${l.performed_by || ""}</span>
					</div>
				`).join("");
			}

			panel.find(".ch-claim-device-info").html(`
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header" style="display:flex;justify-content:space-between;align-items:center">
						<span>
							<button class="btn btn-xs btn-default ch-claim-back" style="margin-right:8px;border-radius:4px">
								<i class="fa fa-arrow-left"></i>
							</button>
							<b>${claim.name}</b> — ${claim.customer_name || claim.customer}
						</span>
						<span class="badge" style="background:${sc.bg};color:${sc.fg};padding:4px 12px;font-size:12px">
							${claim.claim_status}
						</span>
					</div>
					<div class="section-body">
						<!-- Progress Steps -->
						<div style="display:flex;gap:4px;margin-bottom:16px">
							${steps.map(s => `
								<div style="flex:1;text-align:center;padding:6px 4px;border-radius:4px;font-size:10px;
									background:${s.done ? s.color : "#f3f4f6"};color:${s.done ? "white" : "#9ca3af"}">
									<i class="fa ${s.icon}"></i><br>${s.label}
								</div>
							`).join("")}
						</div>

						<!-- Claim Info -->
						<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px;margin-bottom:12px">
							<div><span class="text-muted">${__("Device")}:</span> ${claim.item_name || claim.item_code || claim.serial_no}</div>
							<div><span class="text-muted">${__("Serial")}:</span> <code>${claim.serial_no}</code></div>
							<div><span class="text-muted">${__("Coverage")}:</span> ${claim.coverage_type || "N/A"}</div>
							<div><span class="text-muted">${__("Filed")}:</span> ${claim.claim_date}</div>
							<div><span class="text-muted">${__("GoGizmo pays")}:</span> ₹${claim.gogizmo_share || 0}</div>
							<div><span class="text-muted">${__("Customer pays")}:</span> ₹${claim.customer_share || 0}</div>
							${claim.service_request ? `
								<div><span class="text-muted">${__("GoFix Ticket")}:</span>
									<a href="/app/service-request/${claim.service_request}" target="_blank">
										${claim.service_request}
									</a>
								</div>` : ""}
							${claim.repair_status ? `
								<div><span class="text-muted">${__("Repair")}:</span> ${claim.repair_status}</div>` : ""}
						</div>

						<!-- Issue -->
						<div style="padding:8px 12px;background:#f9fafb;border-radius:6px;font-size:12px;margin-bottom:12px">
							<b>${__("Issue")}:</b> ${claim.issue_description || "N/A"}
						</div>

						<!-- Activity Log -->
						${log_html ? `
						<details style="font-size:12px">
							<summary style="cursor:pointer;color:var(--pos-muted);margin-bottom:4px">
								<b>${__("Activity Log")} (${claim.claim_log.length})</b>
							</summary>
							${log_html}
						</details>` : ""}
					</div>
				</div>
			`).show();
			panel.find(".ch-claim-form-area").hide();

			// Back button
			panel.find(".ch-claim-back").on("click", () => {
				const imei = panel.find(".ch-claim-imei").val().trim();
				if (imei) {
					this._lookup_device(panel, imei);
				} else {
					panel.find(".ch-claim-device-info").hide();
				}
			});
		}).catch(() => {
			frappe.show_alert({ message: __("Could not load claim details"), indicator: "red" });
		});
	}

	_get_progress_steps(claim) {
		const s = claim.claim_status;
		const steps = [
			{ label: __("Filed"), icon: "fa-file-text", done: true, color: "#3b82f6" },
			{ label: claim.requires_approval ? __("Approval") : __("Auto-OK"),
			  icon: "fa-check-circle",
			  done: ["Approved","Ticket Created","In Repair","Repair Complete","Closed"].includes(s) ||
			        (s === "Rejected"),
			  color: s === "Rejected" ? "#ef4444" : "#22c55e" },
			{ label: __("GoFix"), icon: "fa-wrench",
			  done: ["Ticket Created","In Repair","Repair Complete","Closed"].includes(s),
			  color: "#8b5cf6" },
			{ label: __("Repair"), icon: "fa-cog",
			  done: ["In Repair","Repair Complete","Closed"].includes(s),
			  color: "#eab308" },
			{ label: __("Done"), icon: "fa-trophy",
			  done: ["Repair Complete","Closed"].includes(s),
			  color: "#22c55e" },
			{ label: __("Closed"), icon: "fa-lock",
			  done: s === "Closed",
			  color: "#6b7280" },
		];
		return steps;
	}

	// ── Claims Pipeline ─────────────────────────────────────────────

	_load_claims_pipeline(panel) {
		const pipe = panel.find(".ch-claim-pipeline");
		pipe.html(`<div class="text-muted text-center" style="padding:16px"><i class="fa fa-spinner fa-spin"></i></div>`);

		frappe.xcall("frappe.client.get_list", {
			doctype: "CH Warranty Claim",
			filters: {
				docstatus: ["!=", 2],
				claim_status: ["not in", ["Closed", "Cancelled"]],
			},
			fields: [
				"name", "claim_date", "claim_status", "serial_no",
				"customer_name", "customer", "item_name", "item_code",
				"coverage_type", "service_request", "approval_status",
				"customer_phone",
			],
			order_by: "modified desc",
			limit_page_length: 20,
		}).then((claims) => {
			if (!claims || !claims.length) {
				pipe.html(`<div class="text-muted text-center" style="padding:16px">${__("No pending claims")}</div>`);
				return;
			}

			pipe.html(claims.map(c => {
				const sc = this._status_color(c.claim_status);
				return `
				<div class="ch-claim-track" data-claim="${c.name}"
					style="display:flex;align-items:center;gap:8px;padding:10px 8px;border-bottom:1px solid #f3f4f6;
					font-size:12px;cursor:pointer;transition:background 0.15s;border-radius:4px"
					onmouseover="this.style.background='#f8fafc'"
					onmouseout="this.style.background='transparent'">
					<span class="badge" style="background:${sc.bg};color:${sc.fg};font-size:10px;min-width:80px;text-align:center;padding:3px 6px">
						${c.claim_status}
					</span>
					<span style="flex:1">
						<b>${c.customer_name || c.customer || ""}</b>
						<span class="text-muted">— ${c.item_name || c.item_code || c.serial_no}</span>
					</span>
					<span class="text-muted" style="font-size:11px">${c.claim_date}</span>
					<span>
						<span class="badge" style="background:${c.coverage_type === "In Warranty" ? "#dcfce7" :
							c.coverage_type === "Partial Coverage" ? "#fff7ed" : "#fef2f2"};
							color:${c.coverage_type === "In Warranty" ? "#166534" :
							c.coverage_type === "Partial Coverage" ? "#92400e" : "#991b1b"};font-size:10px;padding:2px 6px">
							${c.coverage_type || "N/A"}
						</span>
					</span>
					<span style="color:#6b7280"><i class="fa fa-chevron-right"></i></span>
				</div>`;
			}).join(""));
		}).catch(() => {
			pipe.html(`<div class="text-muted text-center" style="padding:16px">${__("Error loading claims")}</div>`);
		});
	}

	// ── Auto-refresh (real-time status updates) ─────────────────────

	_start_auto_refresh(panel) {
		this._stop_auto_refresh();
		// Refresh pipeline every 30 seconds for near real-time status
		this._refresh_timer = setInterval(() => {
			this._load_claims_pipeline(panel);
		}, 30000);
	}

	_stop_auto_refresh() {
		if (this._refresh_timer) {
			clearInterval(this._refresh_timer);
			this._refresh_timer = null;
		}
	}

	// ── Helpers ──────────────────────────────────────────────────────

	_status_color(status) {
		const map = {
			"Draft":             { bg: "#f3f4f6", fg: "#374151" },
			"Pending Approval":  { bg: "#fef3c7", fg: "#92400e" },
			"Approved":          { bg: "#dbeafe", fg: "#1e40af" },
			"Rejected":          { bg: "#fef2f2", fg: "#991b1b" },
			"Ticket Created":    { bg: "#ede9fe", fg: "#5b21b6" },
			"In Repair":         { bg: "#fef9c3", fg: "#854d0e" },
			"Repair Complete":   { bg: "#dcfce7", fg: "#166534" },
			"Closed":            { bg: "#f3f4f6", fg: "#374151" },
			"Cancelled":         { bg: "#fee2e2", fg: "#991b1b" },
		};
		return map[status] || { bg: "#f3f4f6", fg: "#374151" };
	}
}
