/**
 * CH POS — Warranty Claims Workspace
 *
 * Allows POS staff to:
 * 1. Scan IMEI → see device + warranty status + claim history
 * 2. Initiate a warranty claim → auto-routes for approval or directly to GoFix
 * 3. View pending claims and their status
 *
 * Accessible by both GoGizmo (retail) and GoFix (service) companies.
 */
import { PosState, EventBus } from "../../state.js";

export class ClaimsWorkspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "claims") return;
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
	}

	_bind(panel) {
		// Enter key in IMEI input triggers lookup
		panel.on("keydown", ".ch-claim-imei", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				panel.find(".ch-claim-lookup").trigger("click");
			}
		});

		// Lookup button
		panel.on("click", ".ch-claim-lookup", () => {
			const imei = panel.find(".ch-claim-imei").val().trim();
			if (!imei) {
				frappe.show_alert({ message: __("Enter an IMEI or Serial Number"), indicator: "orange" });
				return;
			}
			this._lookup_device(panel, imei);
		});

		// Refresh pipeline
		panel.on("click", ".ch-claim-refresh", () => {
			this._load_claims_pipeline(panel);
		});

		// Submit claim (delegated)
		panel.on("click", ".ch-claim-submit", () => {
			this._submit_claim(panel);
		});

		// Auto-focus IMEI field
		setTimeout(() => panel.find(".ch-claim-imei").focus(), 200);
	}

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

		if (!dev) {
			panel.find(".ch-claim-device-info").html(
				`<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-body text-center" style="padding:24px;color:var(--pos-muted)">
						<i class="fa fa-question-circle fa-2x"></i>
						<p style="margin-top:8px">${__("No lifecycle record found for")} <b>${imei}</b></p>
						<small>${__("The device may not have been received through our system")}</small>
					</div>
				</div>`
			).show();
			return;
		}

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

		// Claims history
		let claims_html = "";
		if (claims.length) {
			claims_html = `
				<div style="margin-top:12px">
					<b style="font-size:12px;color:var(--pos-muted)">${__("Previous Claims")}:</b>
					<div style="margin-top:4px;max-height:120px;overflow-y:auto">
					${claims.map(c => `
						<div style="display:flex;justify-content:space-between;padding:4px 8px;border-bottom:1px solid #f3f4f6;font-size:11px">
							<span><a href="/app/ch-warranty-claim/${c.name}" target="_blank">${c.name}</a></span>
							<span>${c.claim_date}</span>
							<span class="badge" style="font-size:10px">${c.claim_status}</span>
						</div>
					`).join("")}
					</div>
				</div>`;
		}

		panel.find(".ch-claim-device-info").html(`
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
				<div class="section-header" style="display:flex;justify-content:space-between;align-items:center">
					<span><i class="fa fa-mobile"></i> ${__("Device Details")}</span>
					${w_badge}
				</div>
				<div class="section-body">
					<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px">
						<div><span class="text-muted">${__("Item")}:</span> <b>${dev.item_name || dev.item_code}</b></div>
						<div><span class="text-muted">${__("Serial")}:</span> <code>${dev.serial_no}</code></div>
						<div><span class="text-muted">${__("IMEI")}:</span> <code>${dev.imei_number || "N/A"}</code></div>
						<div><span class="text-muted">${__("Status")}:</span> ${dev.lifecycle_status}</div>
						<div><span class="text-muted">${__("Customer")}:</span> ${dev.customer_name || dev.customer || "N/A"}</div>
						<div><span class="text-muted">${__("Sale Date")}:</span> ${dev.sale_date || "N/A"}</div>
						<div><span class="text-muted">${__("Services")}:</span> ${dev.service_count || 0}</div>
						<div><span class="text-muted">${__("Last Service")}:</span> ${dev.last_service_date || "None"}</div>
					</div>
					${plan_html}
					${claims_html}
				</div>
			</div>
		`).show();

		// Store data for form usage
		this._device_data = data;
	}

	_render_claim_form(panel, data, imei) {
		const dev = data.device_info;
		if (!dev) {
			panel.find(".ch-claim-form-area").hide();
			return;
		}

		const warranty = data.warranty_info || {};
		const w_covered = warranty.warranty_covered;

		panel.find(".ch-claim-form-area").html(`
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);border:2px solid ${w_covered ? "#86efac" : "#fca5a5"}">
				<div class="section-header" style="background:${w_covered ? "#f0fdf4" : "#fef2f2"}">
					<i class="fa fa-plus-circle"></i> ${__("Raise Warranty Claim")}
				</div>
				<div class="section-body">
					<div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--pos-space-md)">
						<div class="ch-pos-field-group">
							<label>${__("Issue Category")}</label>
							<div class="ch-claim-issue-cat"></div>
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
					<div class="ch-pos-field-group" style="margin-top:var(--pos-space-sm)">
						<label>${__("Customer Phone")}</label>
						<input type="text" class="form-control ch-claim-phone" placeholder="${__("Contact number")}">
					</div>

					${w_covered ? `
					<div style="margin-top:12px;padding:10px 14px;background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;font-size:12px">
						<b>⚠ ${__("In-Warranty Claim")}</b><br>
						${__("GoGizmo will pay for this repair. GoGizmo Head approval is required before GoFix ticket is created.")}
						${warranty.covering_plan?.deductible_amount > 0 ?
							`<br>${__("Customer deductible")}: <b>₹${warranty.covering_plan.deductible_amount}</b>` : ""}
					</div>` : `
					<div style="margin-top:12px;padding:10px 14px;background:#fef2f2;border:1px solid #fecaca;border-radius:6px;font-size:12px">
						<b>ℹ ${__("Out-of-Warranty")}</b><br>
						${__("Customer will be billed by GoFix. No approval needed — GoFix ticket will be created immediately.")}
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
	}

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

		const btn = panel.find(".ch-claim-submit");
		btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Submitting...")}`);

		frappe.xcall(
			"ch_item_master.ch_item_master.warranty_api.initiate_warranty_claim",
			{
				serial_no: dev.serial_no,
				customer: dev.customer,
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
					<p>${__("Claim")}: <a href="/app/ch-warranty-claim/${result.claim_name}" target="_blank"><b>${result.claim_name}</b></a></p>
					<p>${__("GoFix Ticket")}: <a href="/app/service-request/${result.service_request}" target="_blank"><b>${result.service_request}</b></a></p>
					<p style="font-size:13px;color:var(--pos-muted)">${__("Customer pays")}: ₹${result.customer_share || 0}</p>
				</div>`;
		} else if (needs_approval) {
			status_html = `
				<div style="text-align:center;padding:24px;color:#d97706">
					<i class="fa fa-clock-o fa-3x"></i>
					<h5 style="margin-top:12px">${__("Claim Submitted — Pending GoGizmo Approval")}</h5>
					<p>${__("Claim")}: <a href="/app/ch-warranty-claim/${result.claim_name}" target="_blank"><b>${result.claim_name}</b></a></p>
					<p style="font-size:13px">${__("GoGizmo share")}: ₹${result.gogizmo_share || 0}
						| ${__("Customer share")}: ₹${result.customer_share || 0}</p>
					<p style="font-size:12px;color:var(--pos-muted)">${__("GoFix ticket will be created after GoGizmo Head approves")}</p>
				</div>`;
		} else {
			status_html = `
				<div style="text-align:center;padding:24px;color:#2563eb">
					<i class="fa fa-info-circle fa-3x"></i>
					<h5 style="margin-top:12px">${__("Claim Created")}</h5>
					<p><a href="/app/ch-warranty-claim/${result.claim_name}" target="_blank"><b>${result.claim_name}</b></a></p>
					<p>${result.coverage_type} — ${result.warranty_status}</p>
				</div>`;
		}

		panel.find(".ch-claim-form-area").html(
			`<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);border:2px solid #86efac">
				<div class="section-body">${status_html}</div>
			</div>`
		);
	}

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
				"customer_name", "item_name", "coverage_type",
				"service_request", "approval_status",
			],
			order_by: "modified desc",
			limit_page_length: 20,
		}).then((claims) => {
			if (!claims || !claims.length) {
				pipe.html(`<div class="text-muted text-center" style="padding:16px">${__("No pending claims")}</div>`);
				return;
			}

			const STATUS_COLORS = {
				"Draft": "#9ca3af",
				"Pending Approval": "#f59e0b",
				"Approved": "#3b82f6",
				"Rejected": "#ef4444",
				"Ticket Created": "#8b5cf6",
				"In Repair": "#eab308",
				"Repair Complete": "#22c55e",
			};

			pipe.html(claims.map(c => `
				<div style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid #f3f4f6;font-size:12px;cursor:pointer"
					data-claim="${c.name}">
					<span class="badge" style="background:${STATUS_COLORS[c.claim_status] || "#6b7280"};color:white;font-size:10px;min-width:65px;text-align:center">
						${c.claim_status}
					</span>
					<span style="flex:1">
						<b>${c.customer_name || ""}</b> — ${c.item_name || c.serial_no}
					</span>
					<span class="text-muted">${c.claim_date}</span>
					<span>
						<span class="badge" style="background:${c.coverage_type === "In Warranty" ? "#dcfce7" : "#fef2f2"};
							color:${c.coverage_type === "In Warranty" ? "#166534" : "#991b1b"};font-size:10px">
							${c.coverage_type || ""}
						</span>
					</span>
				</div>
			`).join(""));

			// Click to open claim
			pipe.find("[data-claim]").on("click", function() {
				const name = $(this).data("claim");
				window.open(`/app/ch-warranty-claim/${name}`, "_blank");
			});
		}).catch(() => {
			pipe.html(`<div class="text-muted text-center" style="padding:16px">${__("Error loading claims")}</div>`);
		});
	}
}
