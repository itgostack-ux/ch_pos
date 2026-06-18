/**
 * CH POS — Buyback Workspace (Full POS-Native Flow)
 *
 * Complete buyback lifecycle inside POS:
 *
 *  SEARCH → ASSESS → INSPECT → APPROVE → SETTLE
 *
 *  - Search assessments from mobile diagnostic app or created manually
 *  - Review mobile diagnostic results inline
 *  - Inspector sets final price inline
 *  - Customer approval via OTP or in-store signature
 *  - Settlement: Cashback (record payment) or Exchange (apply credit + go to Sell)
 */
import { PosState, EventBus } from "../../state.js";
import { format_number, validate_india_phone, validate_id_number } from "../../shared/helpers.js";

// ──────────────────────────────────────────── Stage helpers ──────────
const STAGE = {
	ASSESS: "assess",   // Assessment selected, no order yet (or pre-inspection)
	INSPECT: "inspect", // Inspection in progress — inspector evaluates device
	APPROVE: "approve", // Price confirmed, awaiting customer sign-off
	SETTLE: "settle",   // Customer approved, choose cashback/exchange
	DONE: "done",       // Paid / Closed
};

function _determine_stage(data) {
	// Order-based stages take priority when order exists
	if (data.order) {
		const s = data.order.status || "";
		if (["Paid", "Closed"].includes(s)) return STAGE.DONE;
		if (["Customer Approved", "Ready to Pay", "OTP Verified"].includes(s)) return STAGE.SETTLE;
		if (["Approved", "Awaiting Customer Approval", "Awaiting OTP"].includes(s)) return STAGE.APPROVE;
		if (["Draft", "Awaiting Approval"].includes(s)) return STAGE.INSPECT;
	}
	// Assessment-based: inspection completed but no order yet → show approve/create order
	if (data.inspection && data.inspection.status === "Completed" && !data.order) {
		return STAGE.APPROVE;
	}
	// Assessment-based: inspection exists but not completed yet
	const status = data.status || "";
	if (status === "Inspection Created" && data.buyback_inspection) return STAGE.INSPECT;
	return STAGE.ASSESS;
}

// Stage labels for progress bar
const STAGE_LABELS = [
	{ key: STAGE.ASSESS, label: "Assess" },
	{ key: STAGE.INSPECT, label: "Inspect" },
	{ key: STAGE.APPROVE, label: "Approve" },
	{ key: STAGE.SETTLE, label: "Settle" },
];

function _api_error_message(e, fallback) {
	let msg = "";
	if (typeof e === "string") {
		msg = e;
	} else {
		msg = (e && (e.message || e.exc_type || e.exc)) || "";
	}
	if (!msg && e && e._server_messages) {
		try {
			const raw = JSON.parse(e._server_messages);
			if (Array.isArray(raw) && raw.length) {
				msg = raw[0];
			}
		} catch (_err) {
			// ignore parsing errors; fallback below
		}
	}

	msg = frappe.utils.strip_html(String(msg || "")).trim();
	if (!msg) {
		return fallback;
	}

	const lmsg = msg.toLowerCase();
	if (lmsg.includes("not whitelisted") || lmsg.includes("login to access")) {
		return __("Session expired. Please login again and retry.");
	}

	return msg;
}

function _render_detail_error(detail, message, retry) {
	detail.html(`
		<div style="padding:36px 24px;text-align:center;color:#475569">
			<div style="width:44px;height:44px;border-radius:50%;background:#fee2e2;color:#b91c1c;display:inline-flex;align-items:center;justify-content:center;margin-bottom:14px">
				<i class="fa fa-exclamation-triangle"></i>
			</div>
			<div style="font-weight:700;color:#111827;margin-bottom:6px">${__("Unable to load buyback details")}</div>
			<div style="font-size:13px;line-height:1.5;max-width:420px;margin:0 auto 16px">
				${frappe.utils.escape_html(message)}
			</div>
			<div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
				<button class="btn btn-sm btn-primary ch-bb-retry-detail">
					<i class="fa fa-refresh"></i> ${__("Retry")}
				</button>
				<button class="btn btn-sm btn-outline-secondary ch-bb-reload-page">
					<i class="fa fa-sign-in"></i> ${__("Reload POS")}
				</button>
			</div>
		</div>`);
	detail.find(".ch-bb-retry-detail").on("click", retry);
	detail.find(".ch-bb-reload-page").on("click", () => window.location.reload());
}

export class BuybackWorkspace {
	constructor() {
		this._panel = null;
		this._current_data = null;
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "buyback") return;
			this._panel = ctx.panel;
			this.render(ctx.panel);
		});
	}

	// ─────────────────────────────── render shell ──
	render(panel) {
		panel.html(`
			<div class="ch-pos-mode-panel ch-bb-root">
				<!-- Header -->
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:var(--pos-warning-light);color:#92400e">
							<i class="fa fa-exchange"></i>
						</span>
						${__("Buyback & Exchange")}
					</h4>
					<span class="ch-mode-hint">${__("Search mobile diagnostics, inspect, approve and settle")}</span>
				</div>

				<!-- Search Row -->
				<div style="display:flex;gap:10px;margin-bottom:14px;">
					<div class="ch-pos-search-wrap" style="flex:1;max-width:none">
						<i class="fa fa-search ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-pos-search ch-bb-search"
							placeholder="${__("Mobile number, IMEI, assessment ID, or customer name...")}">
					</div>
					<button class="btn btn-primary ch-bb-lookup"
						style="border-radius:var(--pos-radius,8px);font-weight:700;padding:0 18px">
						<i class="fa fa-search"></i>
					</button>
					<button class="btn btn-outline-secondary ch-bb-refresh-btn"
						style="border-radius:var(--pos-radius,8px);font-weight:700;white-space:nowrap"
						title="${__("Refresh")}">
						<i class="fa fa-refresh"></i>
					</button>
					<button class="btn btn-outline-primary ch-bb-new-btn"
						style="border-radius:var(--pos-radius,8px);font-weight:700;white-space:nowrap">
						<i class="fa fa-plus"></i> ${__("New")}
					</button>
				</div>

				<!-- Split -->
				<div class="ch-bb-split">
					<div class="ch-bb-results-col">
						<div class="ch-bb-results">
							<div class="ch-pos-empty-state" style="padding:40px 16px">
								<div class="empty-icon"><i class="fa fa-mobile fa-2x"></i></div>
								<div class="empty-title">${__("Search or create an assessment")}</div>
								<div class="empty-subtitle">${__("Mobile diagnostics submitted from the app will appear here")}</div>
							</div>
						</div>
					</div>
					<div class="ch-bb-detail-col">
						<div class="ch-bb-detail">
							<div class="ch-pos-empty-state" style="padding:40px 16px">
								<div class="empty-icon"><i class="fa fa-file-text-o fa-2x"></i></div>
								<div class="empty-title">${__("Select an assessment")}</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		`);
		this._bind(panel);
	}

	// ─────────────────────────────── bind events ──
	_bind(panel) {
		const do_search = () => {
			const q = panel.find(".ch-bb-search").val().trim();
			if (!q) {
				frappe.show_alert({ message: __("Enter a search term"), indicator: "orange" });
				return;
			}
			this._search(panel, q);
		};

		panel.on("click", ".ch-bb-lookup", do_search);
		panel.find(".ch-bb-search").on("keypress", (e) => { if (e.which === 13) do_search(); });
		// TC_037 — top-level Refresh button: re-runs the last search and
		// reloads the currently-selected detail so cashiers can pick up
		// status changes (mobile-app submissions, manager approvals,
		// settlement updates) without leaving and re-entering the workspace.
		panel.on("click", ".ch-bb-refresh-btn", () => {
			const q = panel.find(".ch-bb-search").val().trim();
			if (q) {
				this._search(panel, q);
			}
			if (this._current_data && this._current_data.name) {
				this._load_detail(panel, this._current_data.name);
			}
			if (!q && !(this._current_data && this._current_data.name)) {
				frappe.show_alert({ message: __("Enter a search term to refresh"), indicator: "orange" });
			}
		});
		panel.on("click", ".ch-bb-card", (e) => {
			panel.find(".ch-bb-card").removeClass("selected");
			$(e.currentTarget).addClass("selected");
			this._load_detail(panel, $(e.currentTarget).data("name"));
		});
		panel.on("click", ".ch-bb-new-btn", () => {
			// Pass store (warehouse) and pos_profile so the form can auto-fill & lock the field
			frappe.route_options = {
				source: "Store Manual",
				store: PosState.warehouse || "",
				_from_pos: "1",
			};
			frappe.new_doc("Buyback Assessment");
		});
	}

	// ─────────────────────────────── search ──
	_search(panel, q) {
		panel.find(".ch-bb-results").html(
			`<div style="padding:24px;text-align:center"><i class="fa fa-spinner fa-spin"></i></div>`
		);
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Buyback Assessment",
				or_filters: [
					["name", "like", `%${q}%`],
					["mobile_no", "like", `%${q}%`],
					["imei_serial", "like", `%${q}%`],
					["customer_name", "like", `%${q}%`],
				],
				fields: ["name", "item_name", "brand", "mobile_no", "imei_serial",
					"estimated_grade", "estimated_price", "quoted_price",
					"status", "source", "creation"],
				order_by: "creation desc",
				limit_page_length: 25,
			},
			callback: (r) => this._render_results(panel, r.message || [], q),
		});
	}

	_render_results(panel, items, query) {
		const el = panel.find(".ch-bb-results");
		if (!items.length) {
			el.html(`<div class="ch-pos-empty-state" style="padding:30px 16px">
				<div class="empty-icon"><i class="fa fa-search"></i></div>
				<div class="empty-title">${__("No results for")} "${frappe.utils.escape_html(query)}"</div>
				<div class="empty-subtitle">
					<button class="btn btn-sm btn-primary ch-bb-new-btn" style="margin-top:8px">
						<i class="fa fa-plus"></i> ${__("Create New Assessment")}
					</button>
				</div>
			</div>`);
			return;
		}
		const cards = items.map(a => {
			const price = a.quoted_price || a.estimated_price;
			const from_app = a.source === "Mobile App";
			return `
				<div class="ch-bb-card" data-name="${a.name}">
					<div class="ch-bb-card-top">
						<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">
							<span class="ch-bb-card-id">${a.name}</span>
							${from_app ? `<span class="ch-pos-badge badge-primary" style="font-size:10px"><i class="fa fa-mobile"></i> ${__("App")}</span>` : ""}
							<span class="ch-pos-badge badge-${this._status_cls(a.status)}">${a.status || __("Draft")}</span>
						</div>
						<span class="ch-bb-card-price">₹${format_number(price)}</span>
					</div>
					<div class="ch-bb-card-body">
						<span style="font-weight:600">${frappe.utils.escape_html(a.item_name || __("Unknown Device"))}</span>
						<span>${a.brand ? frappe.utils.escape_html(a.brand) + " · " : ""}${frappe.utils.escape_html(a.imei_serial || a.mobile_no || "")}</span>
					</div>
				</div>`;
		}).join("");
		el.html(cards);
		el.find(".ch-bb-card").first().addClass("selected");
		this._load_detail(panel, items[0].name);
	}

	// ─────────────────────────────── load detail ──
	_load_detail(panel, name) {
		const detail = panel.find(".ch-bb-detail");
		detail.html(`<div style="padding:40px;text-align:center">
			<i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.3"></i>
		</div>`);
		frappe.xcall(
			"ch_pos.api.pos_api.get_pos_buyback_detail",
			{ assessment_name: name },
			undefined,
			{ silent: true }
		)
			.then(data => {
				this._current_data = data;
				this._render_detail(detail, data);
			})
			.catch(e => {
				const message = _api_error_message(e, __("Failed to load buyback details."));
				_render_detail_error(detail, message, () => this._load_detail(panel, name));
			});
	}

	_reload() {
		if (!this._current_data || !this._panel) return;
		this._load_detail(this._panel, this._current_data.name);
	}

	// ─────────────────────────────── detail router ──
	_render_detail(el, data) {
		const stage = _determine_stage(data);
		const active_idx = STAGE_LABELS.findIndex(s => s.key === stage);

		const progress_html = `<div class="ch-bb-progress">
			${STAGE_LABELS.map((s, i) => `
				<div class="ch-bb-progress-step ${i < active_idx ? "done" : i === active_idx ? "active" : ""}">
					<div class="ch-bb-progress-dot">${i < active_idx ? '<i class="fa fa-check"></i>' : i + 1}</div>
					<div class="ch-bb-progress-label">${__(s.label)}</div>
				</div>
				${i < STAGE_LABELS.length - 1 ? `<div class="ch-bb-progress-line${i < active_idx ? " done" : ""}"></div>` : ""}
			`).join("")}
		</div>`;

		let body_html = "";
		if (stage === STAGE.DONE) body_html = this._html_done(data);
		else if (stage === STAGE.SETTLE) body_html = this._html_settle(data);
		else if (stage === STAGE.APPROVE) body_html = this._html_approve(data);
		else if (stage === STAGE.INSPECT) {
			// INSPECT stage: open full form instead of inline
			const ins_name = data.buyback_inspection
				|| (data.inspection && data.inspection.name);
			if (ins_name) {
				body_html = `
					<div style="padding:20px;text-align:center">
						<div style="margin-bottom:16px;font-size:13px;color:var(--pos-text-muted)">
							${__("Inspection")} <strong>${frappe.utils.escape_html(ins_name)}</strong>
						</div>
						<button class="btn btn-primary btn-lg ch-bb-act ch-bb-open-inspection-form"
							data-inspection="${frappe.utils.escape_html(ins_name)}"
							style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
							<i class="fa fa-external-link"></i> ${__("Open Inspection Form")}
						</button>
					</div>`;
			} else if (data.order && ["Draft", "Awaiting Approval"].includes(data.order.status)) {
				body_html = this._html_inspect_awaiting(data);
			} else {
				body_html = `<div style="padding:30px;text-align:center">
					<i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.3"></i>
					<div style="margin-top:8px;font-size:12px;color:var(--pos-text-muted)">${__("Loading inspection...")}</div>
				</div>`;
			}
		}
		else body_html = this._html_assess(data);

		el.html(`
			<div class="ch-bb-detail-card">
				<!-- Title Bar -->
				<div class="ch-bb-detail-header">
					<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
						<span style="font-weight:700;font-size:var(--pos-fs-md,14px)">${data.name}</span>
						${data.source === "Mobile App"
							? `<span class="ch-pos-badge badge-primary"><i class="fa fa-mobile"></i> ${__("Mobile Diagnostic")}</span>`
							: `<span class="ch-pos-badge badge-muted">${__("Manual")}</span>`}
					</div>
					<a href="#" class="ch-bb-open-desk text-muted" style="font-size:12px" data-name="${data.name}">
						<i class="fa fa-external-link"></i> ${__("Desk")}
					</a>
				</div>

				<!-- Device strip -->
				<div class="ch-bb-device-strip">
					<div><span class="ch-bb-strip-label">${__("Device")}</span>
						<span class="ch-bb-strip-val">${frappe.utils.escape_html(data.item_name || "—")}</span></div>
					<div><span class="ch-bb-strip-label">${__("IMEI")}</span>
						<span class="ch-bb-strip-val" style="font-family:monospace">${frappe.utils.escape_html(data.imei_serial || "—")}</span></div>
					<div><span class="ch-bb-strip-label">${__("Mobile")}</span>
						<span class="ch-bb-strip-val">${frappe.utils.escape_html(data.mobile_no || "—")}</span></div>
					<div><span class="ch-bb-strip-label">${__("Grade")}</span>
						<span class="ch-bb-strip-val">${frappe.utils.escape_html(data.estimated_grade || "—")}</span></div>
				</div>

				<!-- Progress -->
				${progress_html}

				<!-- Stage body -->
				<div class="ch-bb-stage-body">${body_html}</div>
			</div>
		`);

		el.find(".ch-bb-open-desk").on("click", function (e) {
			e.preventDefault();
			frappe.set_route("Form", "Buyback Assessment", $(this).data("name"));
		});

		this._bind_stage_actions(el, data, stage);
	}

	// ─────────────────────────────── IMEI / Sanchar Saathi check card (reused at ASSESS stage) ──
	_html_imei_check_card(data, scope) {
		const imei = data.imei_serial || "—";
		return `
			<div class="ch-bb-imei-card" style="margin-top:16px;border:1px solid var(--border-color);border-radius:var(--pos-radius,8px);padding:14px;background:var(--subtle-fg)">
				<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;font-weight:700">
					<i class="fa fa-shield" style="color:var(--blue-600,#1565c0)"></i>
					${__("IMEI / Sanchar Saathi Check")}
				</div>
				<div class="ch-bb-info-note" style="margin-bottom:10px">
					<i class="fa fa-info-circle"></i>
					${__("Recommended before inspection: check this IMEI on the government registry before spending time grading the device.")}
				</div>
				<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;line-height:1.6">
					${__("Visit")} <a href="https://ceir.sancharsaathi.gov.in" target="_blank" rel="noopener">ceir.sancharsaathi.gov.in</a>
					${__("or SMS")} <strong>"KYM ${frappe.utils.escape_html(imei)}"</strong> ${__("to")} <strong>14422</strong> —
					${__("IMEI")}: <strong>${frappe.utils.escape_html(imei)}</strong>
				</div>
				<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
					<div>
						<label class="ch-bb-field-label">${__("Validation Result")}</label>
						<select class="form-control form-control-sm ch-bb-imei-status" style="border-radius:6px">
							<option value="">${__("-- Select --")}</option>
							<option value="Verified Clean">${__("Verified Clean")}</option>
							<option value="Blacklisted">${__("Blacklisted")}</option>
							<option value="Duplicate IMEI">${__("Duplicate IMEI")}</option>
							<option value="Already In Use">${__("Already In Use")}</option>
							<option value="Could Not Verify">${__("Could Not Verify")}</option>
						</select>
					</div>
					<div>
						<label class="ch-bb-field-label">${__("Screenshot")}</label>
						<div class="ch-bb-imei-file-drop" style="border:1px dashed var(--gray-300);border-radius:6px;padding:6px 10px;cursor:pointer;font-size:12px;text-align:center;min-height:31px;display:flex;align-items:center;justify-content:center">
							<input type="file" accept="image/*" style="display:none">
							<span class="ch-bb-imei-file-label">${__("Click to upload")}</span>
						</div>
					</div>
				</div>
				<div style="margin-bottom:10px">
					<label class="ch-bb-field-label">${__("Remarks")} <span style="font-weight:400">(${__("required if 'Could Not Verify'")})</span></label>
					<textarea class="form-control form-control-sm ch-bb-imei-remarks" rows="2" style="border-radius:6px"></textarea>
				</div>
				<button class="btn btn-primary btn-sm ch-bb-act ch-bb-save-imei-check"
					data-scope="${scope}" data-name="${frappe.utils.escape_html(data.name)}"
					style="width:100%;border-radius:6px;font-weight:600">
					<i class="fa fa-check"></i> ${__("Save & Continue")}
				</button>
			</div>`;
	}

	// ─────────────────────────────── stage: ASSESS ──
	_html_assess(data) {
		const has_diag = data.diagnostics && data.diagnostics.length > 0;
		const diag_html = has_diag
			? `<div class="ch-bb-section-label" style="margin-top:12px">${__("Diagnostic Results (from Mobile App)")}</div>
			   <div class="ch-bb-diag-grid">
				${data.diagnostics.map(d => `
					<div class="ch-bb-diag-item ${d.result === "Pass" ? "pass" : "fail"}">
						<i class="fa ${d.result === "Pass" ? "fa-check-circle" : "fa-times-circle"}"></i>
						<span>${frappe.utils.escape_html(d.test_name)}</span>
						<span class="ch-bb-diag-result">${__(d.result)}</span>
					</div>`).join("")}
			   </div>`
			: `<div class="ch-bb-empty-note">${__("No automated diagnostics — manual condition check used for grading.")}</div>`;

		const status = data.status;
		let action_btn = "";

		// Assessment status field options: Draft | Submitted | Inspection Created | Expired | Cancelled
		// source: "Mobile App" → full physical inspection inline before order creation
		// source: "Store Manual" / other → walk-in: set price and create order directly
		// "Quote Generated" is set by the mobile app diagnostic pipeline (same as Submitted for our purposes)
		const is_mobile_app = (data.source || "") === "Mobile App";
		const can_start = status === "Submitted" || status === "Quote Generated";
		const inspection_in_progress = status === "Inspection Created" && !!data.buyback_inspection;

		// IMEI / Sanchar Saathi check — recommended before inspection starts so
		// staff don't waste time grading a device that's nationally blacklisted.
		// create_inspection() hard-blocks on this server-side; this panel is what
		// lets staff actually clear it from the ASSESS stage.
		const imei_clean = data.imei_validation_status === "Verified Clean";
		if (can_start && !imei_clean) {
			return `
				<div class="ch-bb-valuation-banner">
					<div class="ch-bb-val-label">${__("Assessed Value")}</div>
					<div class="ch-bb-val-amount">₹${format_number(data.quoted_price || data.estimated_price)}</div>
					<div class="ch-bb-val-sub">
						${__("Grade")} ${frappe.utils.escape_html(data.estimated_grade || "—")} ·
						${frappe.utils.escape_html(data.warranty_status || "—")}
					</div>
				</div>
				${diag_html}
				${this._html_imei_check_card(data, "assessment")}
			`;
		}

		if (status === "Draft") {
			action_btn = `
				<div class="ch-bb-info-note">
					<i class="fa fa-info-circle"></i>
					${__("Assessment is in Draft. Submit it to proceed with inspection.")}
				</div>
				<button class="btn btn-primary btn-lg ch-bb-act ch-bb-submit-assessment"
					data-name="${data.name}"
					style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px;margin-bottom:8px">
					<i class="fa fa-check"></i> ${__("Submit Assessment")}
				</button>
				<button class="btn btn-outline-secondary ch-bb-act ch-bb-open-assessment"
					data-name="${data.name}"
					style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700">
					<i class="fa fa-external-link"></i> ${__("Open Assessment Form")}
				</button>`;

		} else if (can_start && is_mobile_app) {
			// Mobile App: customer self-assessed remotely → store inspects device physically
			action_btn = `
				<button class="btn btn-primary btn-lg ch-bb-act ch-bb-start-inspection"
					data-name="${data.name}"
					style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700">
					<i class="fa fa-search-plus"></i> ${__("Inspect Device")}
				</button>`;

		} else if (can_start && !is_mobile_app) {
			// Store walk-in: assess + inspect happen together → set price and create order directly
			action_btn = `
				<div class="ch-bb-info-note">
					<i class="fa fa-info-circle"></i>
					${__("Store walk-in: set final buyback price and create order for customer approval.")}
				</div>
				<div style="margin-bottom:10px">
					<label class="ch-bb-field-label">${__("Final Buyback Price (₹)")}</label>
					<input type="number" class="form-control ch-bb-walkin-price"
						value="${data.quoted_price || data.estimated_price}" min="0" step="1"
						style="font-size:20px;font-weight:700;text-align:right;padding:10px;border-radius:var(--pos-radius,8px)">
				</div>
				<button class="btn btn-primary btn-lg ch-bb-act ch-bb-create-walkin-order"
					data-name="${data.name}"
					style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
					<i class="fa fa-check-circle"></i> ${__("Create Order & Send for Approval")}
				</button>`;

		} else if (inspection_in_progress) {
			action_btn = `
				<div class="ch-bb-info-note">
					<i class="fa fa-info-circle"></i>
					${__("Inspection created. Click below to continue the evaluation.")}
				</div>
				<button class="btn btn-primary btn-lg ch-bb-act ch-bb-start-inspection"
					data-name="${data.name}"
					style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700">
					<i class="fa fa-search-plus"></i> ${__("Continue Inspection")}
				</button>`;
		}

		return `
			<div class="ch-bb-valuation-banner">
				<div class="ch-bb-val-label">${__("Assessed Value")}</div>
				<div class="ch-bb-val-amount">₹${format_number(data.quoted_price || data.estimated_price)}</div>
				<div class="ch-bb-val-sub">
					${__("Grade")} ${frappe.utils.escape_html(data.estimated_grade || "—")} ·
					${frappe.utils.escape_html(data.warranty_status || "—")}
				</div>
			</div>
			${diag_html}
			<div class="ch-bb-actions" style="margin-top:16px;display:block">
				${action_btn}
			</div>`;
	}

	// ─────────────────────────────── stage: INSPECT (inline form) ──
	_html_inspect_inline(ins, data) {
		// ins = inspection object (from data.inspection or pos_create_inspection API)
		// data = full assessment data (optional — for context)
		const quoted = ins.quoted_price || (data && data.quoted_price) || 0;
		const revised = ins.revised_price || quoted;
		const is_completed = ins.status === "Completed";
		const is_mobile = data && data.source === "Mobile App";

		// ── Grade selector ──
		const grade_options = (ins.grades || []).map(g =>
			`<option value="${frappe.utils.escape_html(g.name)}"
				${(ins.post_inspection_grade || ins.condition_grade || ins.pre_inspection_grade) === g.name ? "selected" : ""}>
				${frappe.utils.escape_html(g.label)} (${g.name})
			</option>`
		).join("");

		// ── Customer Self-Assessment (read-only) ──
		const responses = ins.responses || [];
		const has_responses = responses.length > 0;

		let comparison_html = "";
		if (has_responses) {
			const rows = responses.map((r, i) => {
				const cust_label = r.assessment_answer_label || r.assessment_answer || "—";
				const cust_impact = r.assessment_impact || 0;
				const insp_answer = r.inspector_answer || "";
				const insp_impact = r.inspector_impact || 0;
				const has_mismatch = insp_answer && insp_answer !== r.assessment_answer;

				// Build inspector dropdown from question options
				const opt_html = (r.options || []).map(o =>
					`<option value="${frappe.utils.escape_html(o.value)}"
						${insp_answer === o.value ? "selected" : ""}
						data-impact="${o.impact}">
						${frappe.utils.escape_html(o.label)}${o.impact ? ` (${o.impact > 0 ? "+" : ""}${o.impact}%)` : ""}
					</option>`
				).join("");

				return `
				<div class="ch-ins-row ${has_mismatch ? "ch-ins-mismatch" : ""} ${i % 2 === 0 ? "" : "ch-ins-row-alt"}">
					<div class="ch-ins-question">
						<span class="ch-ins-q-num">${i + 1}</span>
						<span class="ch-ins-q-text">${frappe.utils.escape_html(r.question_text || "—")}</span>
					</div>
					<div class="ch-ins-compare">
						<div class="ch-ins-col ch-ins-col-cust">
							<div class="ch-ins-col-label">${__("Customer")}</div>
							<div class="ch-ins-col-val ${cust_impact < 0 ? "ch-ins-deduct" : "ch-ins-pass"}">
								${frappe.utils.escape_html(cust_label)}
								${cust_impact ? `<span class="ch-ins-impact">${cust_impact}%</span>` : ""}
							</div>
						</div>
						<div class="ch-ins-col-arrow">
							${has_mismatch
								? '<i class="fa fa-exclamation-triangle" style="color:var(--orange-500,#f97316)"></i>'
								: (insp_answer ? '<i class="fa fa-check" style="color:var(--green-500,#22c55e)"></i>' : '<i class="fa fa-arrow-right" style="opacity:0.3"></i>')}
						</div>
						<div class="ch-ins-col ch-ins-col-insp">
							<div class="ch-ins-col-label">${__("Inspector")}</div>
							${is_completed
								? `<div class="ch-ins-col-val ${insp_impact < 0 ? "ch-ins-deduct" : "ch-ins-pass"}">
									${frappe.utils.escape_html(r.inspector_answer_label || insp_answer || "—")}
									${insp_impact ? `<span class="ch-ins-impact">${insp_impact}%</span>` : ""}
								  </div>`
								: `<select class="form-control form-control-sm ch-ins-answer"
									data-question="${frappe.utils.escape_html(r.question_code || r.question || "")}"
									data-idx="${i}">
									<option value="">${__("-- Select --")}</option>
									${opt_html}
								  </select>`}
						</div>
					</div>
				</div>`;
			}).join("");

			comparison_html = `
				<div class="ch-ins-section">
					<div class="ch-ins-section-header">
						<i class="fa fa-clipboard"></i>
						<span>${is_mobile ? __("Customer Self-Assessment vs Inspector Findings") : __("Condition Evaluation")}</span>
						<span class="ch-ins-badge">${responses.length} ${__("checks")}</span>
					</div>
					<div class="ch-ins-grid">${rows}</div>
				</div>`;
		}

		// ── Automated Diagnostics (if any) ──
		const diag = ins.diagnostics || [];
		let diag_html = "";
		if (diag.length) {
			const diag_rows = diag.map(d => {
				const assess = d.assessment_result || "—";
				const insp = d.inspector_result || "";
				const mismatch = insp && insp !== d.assessment_result;
				return `
				<div class="ch-ins-diag-row ${mismatch ? "ch-ins-mismatch" : ""}">
					<span class="ch-ins-diag-name">${frappe.utils.escape_html(d.test_name)}</span>
					<span class="ch-pos-badge badge-${assess === "Pass" ? "success" : assess === "Fail" ? "danger" : "muted"}">
						${__(assess)}</span>
					<i class="fa fa-arrow-right" style="opacity:0.3;font-size:10px"></i>
					<span class="ch-pos-badge badge-${insp === "Pass" ? "success" : insp === "Fail" ? "danger" : "muted"}">
						${insp ? __(insp) : "—"}</span>
				</div>`;
			}).join("");

			diag_html = `
				<div class="ch-ins-section" style="margin-top:12px">
					<div class="ch-ins-section-header">
						<i class="fa fa-microchip"></i>
						<span>${__("Automated Diagnostics")}</span>
						<span class="ch-ins-badge">${diag.length} ${__("tests")}</span>
					</div>
					<div class="ch-ins-diag-grid">${diag_rows}</div>
				</div>`;
		}

		// ── Price & Grade Summary ──
		return `
			<!-- Assessment Price Banner -->
			<div class="ch-ins-price-banner">
				<div class="ch-ins-price-col">
					<div class="ch-ins-price-label">${__("Quoted Price")}</div>
					<div class="ch-ins-price-val">₹${format_number(quoted)}</div>
					<div class="ch-ins-price-sub">${__("Grade")} ${frappe.utils.escape_html(
						(ins.pre_inspection_grade ? (ins.grades || []).find(g => g.name === ins.pre_inspection_grade)?.label : "") || "—"
					)}</div>
				</div>
				<div class="ch-ins-price-arrow"><i class="fa fa-long-arrow-right"></i></div>
				<div class="ch-ins-price-col ch-ins-price-revised">
					<div class="ch-ins-price-label">${__("Revised Price")}</div>
					<div class="ch-ins-price-val ch-ins-revised-display">₹${format_number(revised)}</div>
					<div class="ch-ins-price-sub ch-ins-grade-display">${ins.post_inspection_grade
						? __("Grade") + " " + ((ins.grades || []).find(g => g.name === ins.post_inspection_grade)?.label || "")
						: "&nbsp;"}</div>
				</div>
			</div>

			${comparison_html}
			${diag_html}

			${is_completed ? "" : `
			<!-- Inspector Evaluation Panel -->
			<div class="ch-ins-eval-panel">
				<div class="ch-ins-section-header" style="margin-bottom:10px">
					<i class="fa fa-user-md"></i>
					<span>${__("Inspector Decision")}</span>
				</div>

				<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
					<div>
						<label class="ch-ins-field-label">
							${__("Final Condition Grade")} <span style="color:var(--red,#e53e3e)">*</span>
						</label>
						<select class="form-control ch-bb-ins-grade"
							style="border-radius:var(--pos-radius,8px);font-weight:600;font-size:14px">
							<option value="">${__("-- Select Grade --")}</option>
							${grade_options}
						</select>
					</div>
					<div>
						<label class="ch-ins-field-label">${__("Final Buyback Price (₹)")}</label>
						<input type="number" class="form-control ch-bb-ins-price"
							value="${revised}" min="0" step="1"
							style="font-size:18px;font-weight:700;text-align:right;padding:8px 12px;border-radius:var(--pos-radius,8px)">
					</div>
				</div>

				<div style="margin-bottom:10px">
					<label class="ch-ins-field-label">${__("Reason for Price Change")}</label>
					<input type="text" class="form-control ch-bb-ins-override-reason"
						value="${frappe.utils.escape_html(ins.price_override_reason || "")}"
						placeholder="${__("Required if price differs from quoted — e.g., screen crack, battery issue...")}"
						style="border-radius:var(--pos-radius,8px)">
				</div>

				<div style="margin-bottom:14px">
					<label class="ch-ins-field-label">${__("Inspector Notes")}</label>
					<textarea class="form-control ch-bb-ins-remarks" rows="2"
						style="border-radius:var(--pos-radius,8px)"
						placeholder="${__("Additional observations...")}">${frappe.utils.escape_html(ins.remarks || "")}</textarea>
				</div>

				<div style="margin-bottom:14px;padding:10px 12px;border:1px solid var(--border-color);border-radius:var(--pos-radius,8px);background:var(--subtle-fg)">
					<label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer;font-weight:600;margin-bottom:6px">
						<input type="checkbox" class="ch-bb-ins-lock-cleared" style="margin-top:3px;width:16px;height:16px"
							${ins.account_lock_cleared ? "checked" : ""}>
						<span>${__("FRP / iCloud Lock Cleared (Factory Reset Confirmed)")}</span>
					</label>
					<div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">
						${__("A device still signed into the previous owner's Google/Apple account cannot be resold even with a clean IMEI and good grade.")}
					</div>
					<textarea class="form-control form-control-sm ch-bb-ins-lock-notes" rows="1"
						style="border-radius:6px;font-size:12px"
						placeholder="${__("e.g. customer signed out in-store")}">${frappe.utils.escape_html(ins.account_lock_check_notes || "")}</textarea>
				</div>

				<div class="ch-ins-actions">
					<button class="btn btn-primary btn-lg ch-bb-act ch-bb-complete-inspection"
						data-inspection="${frappe.utils.escape_html(ins.name)}"
						style="flex:1;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
						<i class="fa fa-check-circle"></i> ${__("Complete Inspection & Create Order")}
					</button>
					<button class="btn btn-outline-secondary ch-bb-act ch-bb-inspection-to-desk"
						data-inspection="${frappe.utils.escape_html(ins.name)}"
						style="border-radius:var(--pos-radius,8px);min-height:48px;padding:0 16px"
						title="${__("Open in Desk")}">
						<i class="fa fa-external-link"></i>
					</button>
				</div>
			</div>
			`}`;
	}

	// ─────────────────────────────── stage: INSPECT (awaiting manager approval) ──
	_html_inspect_awaiting(data) {
		const order = data.order;
		const price = order ? order.final_price : (data.quoted_price || data.estimated_price);
		const order_name = order ? frappe.utils.escape_html(order.name) : "";

		// INSPECT stage is reached only when order.status === "Awaiting Approval":
		// a manager must approve in Desk before the flow can proceed to APPROVE.
		return `
			<div class="ch-bb-valuation-banner"
				style="background:#fef3c7;border-color:#f59e0b">
				<div class="ch-bb-val-label" style="color:#92400e">
					<i class="fa fa-clock-o"></i> ${__("Awaiting Manager Approval")}
				</div>
				<div class="ch-bb-val-amount" style="color:#92400e">₹${format_number(price)}</div>
				<div class="ch-bb-val-sub" style="color:#92400e">
					${__("Order {0} — a manager must approve before handing to the customer.", [order_name])}
				</div>
			</div>
			<div class="ch-bb-info-note" style="margin-top:12px">
				<i class="fa fa-info-circle"></i>
				${__("The manager will review and approve this order in Desk. Refresh to check status.")}
			</div>
			<div class="ch-bb-actions" style="margin-top:12px;flex-direction:column;gap:8px">
				<button class="btn btn-primary btn-lg ch-bb-act ch-bb-manager-approve"
					data-order="${order_name}"
					style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
					<i class="fa fa-check"></i> ${__("Approve (Manager In-Store)")}
				</button>
				<button class="btn btn-outline-secondary ch-bb-act ch-bb-refresh-stage"
					style="width:100%;border-radius:var(--pos-radius,8px)">
					<i class="fa fa-refresh"></i> ${__("Refresh Status")}
				</button>
			</div>`;
	}

	// ─────────────────────────────── stage: APPROVE ──
	_html_approve(data) {
		const order = data.order;
		const ins = data.inspection;

		// Inspection completed but no order created yet — need to create order first
		if (!order && ins && ins.status === "Completed") {
			const price = ins.revised_price || ins.quoted_price || data.quoted_price || data.estimated_price;
			const grade = ins.post_inspection_grade || ins.condition_grade || ins.pre_inspection_grade || "";
			return `
				<div class="ch-bb-valuation-banner"
					style="background:var(--pos-success-light,#d1fae5);border-color:var(--pos-success,#10b981)">
					<div class="ch-bb-val-label" style="color:var(--pos-success-dark,#065f46)">
						<i class="fa fa-check-circle"></i> ${__("Inspection Complete")}
					</div>
					<div class="ch-bb-val-amount" style="color:var(--pos-success-dark,#065f46)">
						₹${format_number(price)}
					</div>
					<div class="ch-bb-val-sub">
						${grade ? __("Grade") + " " + frappe.utils.escape_html(grade) + " · " : ""}
						${frappe.utils.escape_html(data.customer_name || data.mobile_no || "—")}
					</div>
				</div>
				<div class="ch-bb-actions" style="margin-top:16px;flex-direction:column;gap:10px">
					<button class="btn btn-primary btn-lg ch-bb-act ch-bb-create-order-from-inspection"
						data-name="${frappe.utils.escape_html(data.name)}"
						data-inspection="${frappe.utils.escape_html(ins.name)}"
						data-price="${price}"
						data-grade="${frappe.utils.escape_html(grade)}"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
						<i class="fa fa-check-circle"></i> ${__("Create Order & Send for Approval")}
					</button>
				</div>`;
		}

		const price = order ? order.final_price : (data.quoted_price || data.estimated_price);
		const mobile = data.mobile_no || "";
		const masked = mobile ? mobile.slice(0, 2) + "****" + mobile.slice(-2) : "—";
		const order_status = order ? order.status : "";
		const is_waiting = order_status === "Awaiting Customer Approval";
		const approval_url = order ? (order.approval_url || "") : "";

		// ── "Awaiting OTP" / "OTP Verified": resume the single approval wizard ─
		// SINGLE FLOW: every customer-approval action funnels through
		// _show_instore_approval_dialog → pos_approve_customer_buyback.
		// The wizard adapts to the current order.status (skips Send-OTP when
		// already sent, skips OTP entry entirely when already verified) so the
		// cashier always has one entry point with one backend call.
		if (order_status === "Awaiting OTP" || order_status === "OTP Verified") {
			const is_verified = order_status === "OTP Verified";
			return `
				<div class="ch-bb-valuation-banner" style="background:${is_verified ? "#d1fae5" : "#fef3c7"};border-color:${is_verified ? "#10b981" : "#f59e0b"}">
					<div class="ch-bb-val-label" style="color:${is_verified ? "#065f46" : "#92400e"}">
						<i class="fa fa-${is_verified ? "check-circle" : "mobile"}"></i>
						${is_verified ? __("OTP Verified — Complete KYC & Settlement") : __("OTP Sent — Awaiting Verification")}
					</div>
					<div class="ch-bb-val-amount" style="color:${is_verified ? "#065f46" : "#92400e"}">₹${format_number(price)}</div>
					<div class="ch-bb-val-sub" style="color:${is_verified ? "#065f46" : "#92400e"}">
						${is_verified
							? __("Customer approved. Capture ID proof and payout details to continue.")
							: (mobile ? __("OTP sent to {0}", [masked]) : __("No mobile number on record"))}
					</div>
				</div>
				<div class="ch-bb-info-note" style="margin-top:12px">
					<i class="fa fa-info-circle"></i>
					${is_verified
						? __("Resume the approval wizard to capture KYC and settlement preference.")
						: __("Ask the customer for the OTP they received and resume the approval wizard to verify it together with KYC and settlement details.")}
				</div>
				<div class="ch-bb-actions" style="margin-top:14px;flex-direction:column;gap:8px">
					<button class="btn btn-primary btn-lg ch-bb-act ch-bb-approve-instor"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
						<i class="fa fa-arrow-right"></i> ${__("Continue Customer Approval")}
					</button>
				</div>`;
		}
		// ────────────────────────────────────────────────────────────────────────

		const price_banner = `
			<div class="ch-bb-valuation-banner"
				style="background:var(--pos-success-light,#d1fae5);border-color:var(--pos-success,#10b981)">
				<div class="ch-bb-val-label" style="color:var(--pos-success-dark,#065f46)">
					${is_waiting
						? `<i class="fa fa-clock-o"></i> ${__("Awaiting Customer Approval")}`
						: __("Inspection Complete — Get Customer Approval")}
				</div>
				<div class="ch-bb-val-amount" style="color:var(--pos-success-dark,#065f46)">
					₹${format_number(price)}
				</div>
				${is_waiting
					? `<div class="ch-bb-val-sub">${__("Link sent to")} <b>${masked}</b></div>`
					: `<div class="ch-bb-val-sub">${__("Customer:")} ${frappe.utils.escape_html(data.customer_name || mobile || "—")}</div>`}
			</div>`;

		if (is_waiting) {
			// Link already sent — show "waiting" state with resend + in-store fallback
			return `${price_banner}
				<div class="ch-bb-info-note" style="margin-top:12px;background:#fef9c3;border-color:#facc15;color:#713f12">
					<i class="fa fa-hourglass-half"></i>
					${__("The approval link has been sent. Waiting for the customer to tap and approve.")}
					${approval_url
						? `<br><small style="word-break:break-all;opacity:0.7">${frappe.utils.escape_html(approval_url)}</small>`
						: ""}
				</div>
				<div class="ch-bb-actions" style="margin-top:14px;flex-direction:column;gap:8px">
					<button class="btn btn-success btn-lg ch-bb-act ch-bb-approve-instor"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
						<i class="fa fa-check-circle"></i> ${__("Customer Approved (In-Store)")}
					</button>
					<button class="btn btn-outline-primary ch-bb-act ch-bb-resend-link"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:600">
						<i class="fa fa-paper-plane"></i> ${__("Resend Link")}
					</button>
					<button class="btn btn-link ch-bb-act ch-bb-refresh-stage"
						style="font-size:12px;margin-top:4px">
						<i class="fa fa-refresh"></i> ${__("Refresh Status")}
					</button>
				</div>`;
		}

		// Default: not yet sent — primary CTA is "Send Approval Link"
		return `${price_banner}
			<div class="ch-bb-info-note" style="margin-top:12px">
				<i class="fa fa-info-circle"></i>
				${__("Share the approval link with the customer. They tap it, review the price, and approve — no app needed.")}
			</div>
			<div class="ch-bb-actions" style="margin-top:14px;flex-direction:column;gap:10px">
				<button class="btn btn-primary btn-lg ch-bb-act ch-bb-send-link"
					data-order="${order ? frappe.utils.escape_html(order.name) : ""}"
					style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:56px;font-size:15px">
					<i class="fa fa-whatsapp" style="font-size:20px;margin-right:6px"></i>
					${__("Send Approval Link")}
					<div style="font-size:11px;font-weight:400;opacity:0.85;margin-top:2px">
						${__("WhatsApp / SMS to")} ${masked}
					</div>
				</button>
				<button class="btn btn-outline-secondary ch-bb-act ch-bb-approve-instor"
					style="width:100%;border-radius:var(--pos-radius,8px)">
					<i class="fa fa-pencil"></i> ${__("Approve In-Store (Customer Present)")}
				</button>
			</div>`;
	}

	// ─────────────────────────────── stage: SETTLE ──
	_html_settle(data) {
		const order = data.order;
		const price = order ? order.final_price : (data.quoted_price || data.estimated_price);

		return `
			<div class="ch-bb-valuation-banner" style="background:#f0f9ff;border-color:#0ea5e9">
				<div class="ch-bb-val-label" style="color:#0284c7">
					${__("Customer Approved ✓ — Choose Settlement")}
				</div>
				<div class="ch-bb-val-amount" style="color:#0284c7">₹${format_number(price)}</div>
			</div>
			<div class="ch-bb-section-label" style="margin-top:14px">
				${__("How does the customer want to receive value?")}
			</div>
			<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px">
				<!-- Cashback -->
				<div class="ch-bb-settle-card">
					<div class="ch-bb-settle-icon" style="background:#fef3c7;color:#92400e">
						<i class="fa fa-money fa-2x"></i>
					</div>
					<div class="ch-bb-settle-title">${__("Cashback")}</div>
					<div class="ch-bb-settle-desc">
						${__("Pay customer ₹{0} directly. No new purchase needed.", [format_number(price)])}
					</div>
					<div style="margin:10px 0">
						<label class="ch-bb-field-label">${__("Payment Method")}</label>
						<select class="form-control ch-bb-cashback-mode"
							style="border-radius:var(--pos-radius,8px)">
							<option value="Cash">${__("Cash")}</option>
							<option value="UPI">${__("UPI")}</option>
							<option value="Bank Transfer">${__("Bank Transfer")}</option>
						</select>
					</div>
					<button class="btn btn-warning ch-bb-act ch-bb-cashback"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:44px"
						data-price="${price}">
						<i class="fa fa-money"></i> ${__("Settle as Cashback")}
					</button>
				</div>
				<!-- Exchange -->
				<div class="ch-bb-settle-card">
					<div class="ch-bb-settle-icon" style="background:#d1fae5;color:#065f46">
						<i class="fa fa-exchange fa-2x"></i>
					</div>
					<div class="ch-bb-settle-title">${__("Exchange")}</div>
					<div class="ch-bb-settle-desc">
						${__("Apply ₹{0} as credit toward a new device purchase.", [format_number(price)])}
					</div>
					<div style="margin:10px 0;font-size:12px;color:var(--pos-text-muted)">
						${__("Credit will appear in cart. Cashier selects the new device.")}
					</div>
					<button class="btn btn-success ch-bb-act ch-bb-exchange"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:44px"
						data-name="${data.name}"
						data-amount="${price}"
						data-order-name="${order ? order.name : ""}"
						data-item-name="${frappe.utils.escape_html(data.item_name || "")}"
						data-imei="${data.imei_serial || ""}"
						data-grade="${data.estimated_grade || ""}">
						<i class="fa fa-exchange"></i> ${__("Add to Cart & Sell")}
					</button>
				</div>
			</div>`;
	}

	// ─────────────────────────────── stage: DONE ──
	_html_done(data) {
		const order = data.order;
		const price = order ? order.final_price : 0;
		return `
			<div class="ch-bb-valuation-banner" style="background:#d1fae5;border-color:#10b981">
				<div class="ch-bb-val-label" style="color:#065f46">
					<i class="fa fa-check-circle"></i> ${__("Buyback Complete")}
				</div>
				<div class="ch-bb-val-amount" style="color:#065f46">₹${format_number(price)}</div>
				<div class="ch-bb-val-sub">
					${__("Order")} ${order ? order.name : ""} · ${order ? __(order.status) : ""}
				</div>
			</div>
			<div style="text-align:center;margin-top:16px;display:flex;justify-content:center;gap:10px;flex-wrap:wrap">
				<button class="btn btn-primary ch-bb-print-receipt"
					data-order="${order ? order.name : ""}"
					style="border-radius:var(--pos-radius,8px);font-weight:600;min-height:40px">
					<i class="fa fa-print"></i> ${__("Print Receipt")}
				</button>
				<button class="btn btn-outline-secondary ch-bb-open-desk" data-name="${data.name}"
					style="border-radius:var(--pos-radius,8px);min-height:40px">
					<i class="fa fa-external-link"></i> ${__("View in Desk")}
				</button>
			</div>`;
	}

	// ─────────────────────────────── stage action wiring ──
	_bind_stage_actions(el, data, stage) {
		// Unbind ALL previous delegated handlers using namespace
		// (jQuery .off with comma-separated selectors doesn't work for delegated events)
		el.off(".bbstage");

		// ── IMEI / Sanchar Saathi check card (ASSESS stage) ─────────
		let imei_card_upload_url = null;
		el.find(".ch-bb-imei-file-drop").each(function () {
			const $drop = $(this);
			const $input = $drop.find('input[type="file"]');
			$drop.on("click.bbstage", () => $input.trigger("click"));
			$input.on("change.bbstage", function () {
				if (!this.files.length) return;
				const file = this.files[0];
				$drop.find(".ch-bb-imei-file-label").html(`<i class="fa fa-spinner fa-spin"></i> ${__("Uploading...")}`);
				const fd = new FormData();
				fd.append("file", file, file.name);
				fd.append("doctype", "Buyback Assessment");
				fd.append("docname", data.name);
				fd.append("fieldname", "imei_validation_screenshot");
				fd.append("is_private", 1);
				$.ajax({
					url: "/api/method/upload_file", type: "POST", data: fd,
					processData: false, contentType: false,
					headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
				}).done((r) => {
					imei_card_upload_url = r.message.file_url;
					$drop.find(".ch-bb-imei-file-label").html(`<i class="fa fa-check-circle" style="color:var(--green-600)"></i> ${frappe.utils.escape_html(file.name)}`);
				}).fail(() => {
					$drop.find(".ch-bb-imei-file-label").html(`<span style="color:var(--red-500)">${__("Upload failed")}</span>`);
					imei_card_upload_url = null;
				});
			});
		});

		el.on("click.bbstage", ".ch-bb-save-imei-check", (e) => {
			const btn = $(e.currentTarget);
			const status = el.find(".ch-bb-imei-status").val();
			const remarks = el.find(".ch-bb-imei-remarks").val() || "";
			if (!status) {
				frappe.show_alert({ message: __("Please select the validation result"), indicator: "orange" });
				return;
			}
			const needs_screenshot = status !== "Could Not Verify";
			if (needs_screenshot && !imei_card_upload_url) {
				frappe.show_alert({ message: __("Please upload the Sanchar Saathi screenshot"), indicator: "orange" });
				return;
			}
			if (status === "Could Not Verify" && !remarks.trim()) {
				frappe.show_alert({ message: __("Please explain why it could not be verified"), indicator: "orange" });
				return;
			}
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Saving...")}`);
			frappe.xcall("buyback.api.submit_assessment_imei_validation", {
				assessment_name: btn.data("name"),
				status,
				screenshot: imei_card_upload_url || null,
				remarks: remarks || null,
			}).then((res) => {
				if (res.blocked && res.status === "Cancelled") {
					frappe.msgprint({ title: __("Assessment Cancelled"), message: res.message, indicator: "red" });
					this._reload();
					return;
				}
				if (res.blocked) {
					btn.prop("disabled", false).html(`<i class="fa fa-check"></i> ${__("Save & Continue")}`);
					frappe.show_alert({ message: res.message || __("Could not verify — please retry."), indicator: "orange" });
					return;
				}
				frappe.show_alert({ message: __("IMEI verified clean."), indicator: "green" });
				this._reload();
			}).catch((err) => {
				btn.prop("disabled", false).html(`<i class="fa fa-check"></i> ${__("Save & Continue")}`);
				frappe.show_alert({ message: _api_error_message(err, __("Failed to save IMEI validation")), indicator: "red" });
			});
		});

		// ── ASSESS: Submit assessment from POS ─────────
		el.on("click.bbstage", ".ch-bb-submit-assessment", (e) => {
			const btn = $(e.currentTarget);
			const name = btn.data("name");
			btn.prop("disabled", true)
				.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Submitting...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_submit_assessment", { assessment_name: name })
				.then(() => {
					frappe.show_alert({ message: __("Assessment submitted"), indicator: "green" });
					this._reload();
				})
				.catch(() => {
					btn.prop("disabled", false)
						.html(`<i class="fa fa-check"></i> ${__("Submit Assessment")}`);
				});
		});

		// ── ASSESS: Open assessment in desk ────────────
		el.on("click.bbstage", ".ch-bb-open-assessment", (e) => {
			frappe.set_route("Form", "Buyback Assessment", $(e.currentTarget).data("name"));
		});

		// ── ASSESS: Start/continue inspection — open form ────
		el.on("click.bbstage", ".ch-bb-start-inspection", (e) => {
			const btn = $(e.currentTarget);
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Loading...")}`);

			// If inspection already exists, open it directly
			if (data.buyback_inspection) {
				frappe.set_route("Form", "Buyback Inspection", data.buyback_inspection);
				return;
			}

			frappe.xcall("ch_pos.api.pos_api.pos_create_inspection", {
				assessment_name: data.name,
			}).then((ins) => {
				const ins_name = ins && (ins.name || ins.inspection_name);
				if (ins_name) {
					frappe.set_route("Form", "Buyback Inspection", ins_name);
				} else {
					this._reload();
				}
			}).catch(() => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-search-plus"></i> ${__("Start Inspection")}`);
			});
		});

		// ── INSPECT INLINE: Complete inspection ─────────
		el.on("click.bbstage", ".ch-bb-complete-inspection", (e) => {
			const inspection_name = $(e.currentTarget).data("inspection");
			const grade = el.find(".ch-bb-ins-grade").val();
			if (!grade) {
				frappe.show_alert({ message: __("Please select a Condition Grade"), indicator: "orange" });
				return;
			}
			const price = parseFloat(el.find(".ch-bb-ins-price").val()) || 0;
			if (price <= 0) {
				frappe.show_alert({ message: __("Enter a valid Final Price"), indicator: "orange" });
				return;
			}
			const override_reason = el.find(".ch-bb-ins-override-reason").val() || "";
			const remarks = el.find(".ch-bb-ins-remarks").val() || "";
			const lock_cleared = el.find(".ch-bb-ins-lock-cleared").is(":checked");
			if (!lock_cleared) {
				frappe.show_alert({ message: __("Confirm FRP / iCloud Lock Cleared before completing inspection"), indicator: "orange" });
				return;
			}
			const lock_notes = el.find(".ch-bb-ins-lock-notes").val() || "";

			const btn = $(e.currentTarget);
			btn.prop("disabled", true)
				.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Completing...")}`);

			frappe.xcall("ch_pos.api.pos_api.pos_complete_inspection", {
				inspection_name,
				condition_grade: grade,
				final_price: price,
				price_override_reason: override_reason,
				remarks,
				account_lock_cleared: 1,
				account_lock_check_notes: lock_notes,
			}).then(() => {
				frappe.show_alert({ message: __("Inspection complete — Order created"), indicator: "green" });
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-check-circle"></i> ${__("Complete Inspection & Create Order")}`);
			});
		});

		// ── INSPECT INLINE: Open inspection in desk ─────
		el.on("click.bbstage", ".ch-bb-inspection-to-desk, .ch-bb-open-inspection-form", (e) => {
			frappe.set_route("Form", "Buyback Inspection", $(e.currentTarget).data("inspection"));
		});

		// ── APPROVE: Create order from completed inspection ─────
		el.on("click.bbstage", ".ch-bb-create-order-from-inspection", (e) => {
			const btn = $(e.currentTarget);
			const price = parseFloat(btn.data("price")) || 0;
			btn.prop("disabled", true)
				.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Creating Order...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_start_buyback_order", {
				assessment_name: data.name,
				pos_profile: PosState.pos_profile || "",
				final_price: price,
			}).then(() => {
				frappe.show_alert({ message: __("Order created — send for approval"), indicator: "green" });
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-check-circle"></i> ${__("Create Order & Send for Approval")}`);
			});
		});

		// ── ASSESS: Walk-in — create order directly ─────
		el.on("click.bbstage", ".ch-bb-create-walkin-order", (e) => {
			const btn = $(e.currentTarget);
			const price = parseFloat(el.find(".ch-bb-walkin-price").val()) || 0;
			if (price <= 0) {
				frappe.show_alert({ message: __("Enter a valid buyback price"), indicator: "orange" });
				return;
			}
			btn.prop("disabled", true)
				.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Creating Order...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_start_buyback_order", {
				assessment_name: data.name,
				pos_profile: PosState.pos_profile || "",
				final_price: price,
			}).then(() => {
				frappe.show_alert({ message: __("Order created"), indicator: "green" });
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-check-circle"></i> ${__("Create Order & Send for Approval")}`);
			});
		});

		// ── INSPECT: Manager Approve (in-store) ──────────
		el.on("click.bbstage", ".ch-bb-manager-approve", (e) => {
			const order_name = $(e.currentTarget).data("order");
			if (!order_name) return;
			frappe.confirm(
				__("Confirm manager approval for order {0}?", [order_name]),
				() => {
					frappe.xcall("buyback.api.approve_order", {
						order_name,
						remarks: "Approved in-store via POS",
					}).then(() => {
						frappe.show_alert({ message: __("Order approved"), indicator: "green" });
						this._reload();
					});
				}
			);
		});

		// ── INSPECT: Refresh status ──────────────────────
		el.on("click.bbstage", ".ch-bb-refresh-stage", () => {
			this._reload();
		});

		// ── APPROVE: Send approval link (primary Cashify-style flow) ──
		el.on("click.bbstage", ".ch-bb-send-link, .ch-bb-resend-link", (e) => {
			const btn = $(e.currentTarget);
			btn.prop("disabled", true)
				.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Sending...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_send_approval_link", {
				order_name: data.order.name,
			}).then((res) => {
				frappe.show_alert({
					message: __("Approval link sent to {0}", [res.mobile_masked]),
					indicator: "green",
				});
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-paper-plane"></i> ${__("Send Approval Link")}`);
			});
		});

		// ── APPROVE: In-Store OTP + KYC Approval ─────────────────
		el.on("click.bbstage", ".ch-bb-approve-instor", () => {
			if (!data.order || !data.order.name) {
				frappe.show_alert({ message: __("No Buyback Order found — complete inspection first"), indicator: "orange" });
				return;
			}
			this._show_instore_approval_dialog(data);
		});

		// ── APPROVE (Awaiting OTP): Verify OTP directly ───────────
		el.on("click.bbstage", ".ch-bb-verify-otp-direct", (e) => {
			const otp_code = el.find(".ch-bb-otp-input").val().trim();
			if (!otp_code || otp_code.length < 4) {
				frappe.show_alert({ message: __("Enter a valid OTP"), indicator: "orange" });
				return;
			}
			const btn = $(e.currentTarget);
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i>`);
			frappe.xcall("ch_pos.api.pos_api.pos_approve_customer_buyback", {
				order_name: data.order.name,
				method: "OTP",
				otp_code,
			}).then(() => {
				frappe.show_alert({ message: __("OTP Verified!"), indicator: "green" });
				this._reload();
			}).catch((e) => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-check-circle"></i> ${__("Verify OTP")}`);
				frappe.show_alert({ message: _api_error_message(e, __("Invalid OTP")), indicator: "red" });
			});
		});

		// ── APPROVE (Awaiting OTP): Resend OTP ───────────────────
		el.on("click.bbstage", ".ch-bb-resend-otp-direct", (e) => {
			const btn = $(e.currentTarget);
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Sending...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_send_customer_otp", {
				order_name: data.order.name,
			}).then((res) => {
				frappe.show_alert({
					message: __("OTP resent to {0}", [res.masked_mobile || data.mobile_no]),
					indicator: "green",
				});
				btn.prop("disabled", true)
					.html(`<i class="fa fa-check"></i> ${__("OTP Resent")}`);
			}).catch((e) => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-paper-plane"></i> ${__("Resend OTP")}`);
				frappe.show_alert({ message: _api_error_message(e, __("Failed to resend OTP")), indicator: "red" });
			});
		});

		// ── APPROVE (Awaiting OTP): Bypass OTP In-Store ──────────
		el.on("click.bbstage", ".ch-bb-bypass-otp", (e) => {
			const order_name = data.order && data.order.name;
			if (!order_name) return;
			frappe.prompt([{
				label: __("Remarks"),
				fieldname: "remarks",
				fieldtype: "Small Text",
				description: __("Reason for bypassing OTP — will be logged for audit"),
			}], (values) => {
				frappe.confirm(
					__("Confirm: customer is physically present and has approved in-store. OTP will be bypassed and this action will be logged."),
					() => {
						frappe.xcall("ch_pos.api.pos_api.bypass_otp_instore", {
							name: order_name,
							remarks: values.remarks || null,
						}).then(() => {
							frappe.show_alert({ message: __("In-store approval recorded — OTP bypassed"), indicator: "green" });
							this._reload();
						}).catch((e) => {
							frappe.show_alert({ message: e.message || __("Failed"), indicator: "red" });
						});
					}
				);
			}, __("In-Store Approval"), __("Confirm Bypass"));
		});

		// ── SETTLE: Cashback ────────────────────────────
		el.on("click.bbstage", ".ch-bb-cashback", (e) => {
			const price = flt($(e.currentTarget).data("price"));
			const payment_method = el.find(".ch-bb-cashback-mode").val();
			const btn = el.find(".ch-bb-cashback");
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i>`);
			frappe.xcall("ch_pos.api.pos_api.pos_settle_buyback_cashback", {
				order_name: data.order.name,
				payment_method,
			}).then(() => {
				frappe.show_alert({
					message: __("Cashback ₹{0} recorded via {1}", [format_number(price), payment_method]),
					indicator: "green",
				});
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-money"></i> ${__("Settle as Cashback")}`);
			});
		});

		// ── SETTLE: Exchange → go to Sell ───────────────
		el.on("click.bbstage", ".ch-bb-exchange", (e) => {
			const btn = $(e.currentTarget);
			PosState.exchange_assessment = btn.data("name");      // BBA name
			PosState.exchange_order     = btn.data("order-name"); // BBO name
			PosState.exchange_amount = flt(btn.data("amount"));
			EventBus.emit("exchange:applied", {
				assessment: btn.data("name"),
				buyback_amount: flt(btn.data("amount")),
				item_name: btn.data("item-name"),
				// .attr() preserves the string form of all-digit IMEIs that
				// .data() would otherwise coerce to Number.
				imei_serial: btn.attr("data-imei") || "",
				condition_grade: btn.data("grade"),
			});
			EventBus.emit("cart:updated");
			EventBus.emit("mode:set", "sell");
			EventBus.emit("mode:switch", "sell");
			frappe.show_alert({
				message: __("Exchange credit ₹{0} applied — add new device to cart", [
					format_number(btn.data("amount")),
				]),
				indicator: "green",
			});
		});

		// ── DONE: Print / Reprint buyback receipt ──────
		el.on("click.bbstage", ".ch-bb-print-receipt", (e) => {
			const order_name = $(e.currentTarget).data("order");
			if (!order_name) return;
			const url = `/printview?doctype=Buyback%20Order&name=${encodeURIComponent(order_name)}`
				+ `&format=Buyback%20Receipt&no_letterhead=0&_lang=en`;
			const w = window.open(url, "_blank");
			if (w) {
				w.addEventListener("load", () => setTimeout(() => w.print(), 600));
			}
		});
	}

	// ─────────────────────────────── In-Store Approval (OTP + KYC) ──
	// SINGLE FLOW: this is the ONLY customer-approval entry point.
	// All status branches (Approved / Awaiting Customer Approval / Awaiting OTP
	// / OTP Verified) are handled here and finalised by a single backend call
	// to pos_approve_customer_buyback. The OTP step uses method="OTP" and
	// the final step reuses the same endpoint to persist KYC + settlement.
	_show_instore_approval_dialog(data) {
		const order = data.order;
		const price = order.final_price || 0;
		const mobile = data.mobile_no || order.mobile_no || "";
		const masked = mobile ? mobile.slice(0, 2) + "****" + mobile.slice(-2) : "—";
		const device_label = frappe.utils.escape_html(data.item_name || "");
		const self = this;

		// Resume-state derived from order.status. Drives:
		//   • whether Step "OTP" is rendered at all (skipped if already verified)
		//   • whether the "Send OTP" button is shown (hidden if already sent)
		//   • which `method` is passed to pos_approve_customer_buyback
		const otp_already_sent = order.status === "Awaiting OTP";
		const otp_already_verified = ["OTP Verified", "Ready to Pay", "Paid", "Closed"].includes(order.status);
		// True if user-entered OTP is required from this dialog session.
		// (Awaiting OTP: user types the OTP that was sent earlier.
		//  Approved / Awaiting Customer Approval: user clicks "Send OTP" first.)
		const otp_required = !otp_already_verified;
		// OTP sent flag inside this dialog session. Pre-seeded if status says so.
		let otp_sent_in_session = otp_already_sent;
		// Tracks a successful backend OTP verification performed from this dialog.
		let otp_verified_in_session = otp_already_verified;

		// IMEI / Sanchar Saathi check must be Verified Clean before this wizard
		// can reach OTP/KYC — the backend hard-blocks send_otp/customer_approve
		// otherwise (BuybackOrder._validate_imei_check_before_kyc). Skip the step
		// entirely once it's already been recorded clean.
		const imei_already_clean = order.imei_validation_status === "Verified Clean";

		const STEPS = [
			...(imei_already_clean ? [] : [{ key: "imei", label: __("IMEI Check") }]),
			...(otp_already_verified ? [] : [{ key: "otp", label: __("OTP") }]),
			{ key: "kyc", label: __("KYC & Photos") },
			{ key: "settlement", label: __("Settlement") },
		];
		let step = 0;
		const state = {
			imei_status: order.imei_validation_status === "Verified Clean" ? "Verified Clean" : "",
			imei_remarks: order.imei_validation_remarks || "",
			otp_code: "", kyc_id_type: order.customer_id_type || "", kyc_id_number: order.customer_id_number || "",
			settlement_type: order.settlement_type || "", payout_mode: order.customer_payout_mode || "",
			upi_id: order.customer_upi_id || "",
			bank_account_holder: order.customer_bank_account_holder || "",
			bank_account_number: order.customer_bank_account_number || "",
			bank_ifsc: order.customer_bank_ifsc || "", bank_name: order.customer_bank_name || "",
			customer_confirm: false,
			ownership_proof_type: order.ownership_proof_type || "",
			ownership_proof_remarks: order.ownership_proof_remarks || "",
		};

		// KYC proves who the seller is, not that they own this device —
		// require a purchase/ownership proof above this threshold (0 = never required).
		const ownership_proof_required = (
			flt(order.require_ownership_proof_above) > 0
			&& flt(order.final_price) > flt(order.require_ownership_proof_above)
		);

		/* ── File upload state ── */
		const uploads = {
			customer_id_front: null, customer_id_back: null, customer_photo: null,
			imei_screenshot: order.imei_validation_screenshot || null,
			ownership_proof_document: order.ownership_proof_document || null,
		};

		const dlg = new frappe.ui.Dialog({
			title: __("Customer Approval — In-Store Verification"),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "wizard_html" }],
			primary_action_label: __("Verify & Approve"),
			primary_action: () => _submit(),
		});

		const $body = dlg.fields_dict.wizard_html.$wrapper;
		dlg.$wrapper.find(".modal-body").css({ overflow: "hidden", padding: 0 });
		$body.css({ padding: 0 });

		/* ── Helpers ── */
		function _esc(v) { return frappe.utils.escape_html(v || ""); }

		function _stepper_html() {
			return STEPS.map((s, i) => {
				const cls = i < step ? "done" : i === step ? "active" : "";
				const num = i < step ? '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M13.485 1.929a1 1 0 010 1.414l-7.07 7.071a1 1 0 01-1.415 0L1.929 7.343a1 1 0 111.414-1.414L5.5 8.086l6.364-6.364a1 1 0 011.414 0l.207.207z"/></svg>' : (i + 1);
				return `${i > 0 ? `<div class="ch-wz-line ${i <= step ? 'done' : ''}"></div>` : ""}
					<div class="ch-wz-dot ${cls}"><span class="ch-wz-n">${num}</span><span class="ch-wz-lbl">${s.label}</span></div>`;
			}).join("");
		}

		function _input(name, label, type, opts = {}) {
			const val = _esc(state[name]);
			const req = opts.reqd ? '<span style="color:var(--red-500)"> *</span>' : "";
			if (type === "select") {
				const options = (opts.options || []).map(o => `<option value="${_esc(o)}" ${state[name] === o ? "selected" : ""}>${_esc(o || `— ${label} —`)}</option>`).join("");
				return `<div class="ch-wz-field" ${opts.hidden ? 'style="display:none"' : ""} data-field="${name}">
					<label class="ch-wz-label">${label}${req}</label>
					<select class="ch-wz-select" data-name="${name}">${options}</select>
					${opts.desc ? `<div class="ch-wz-desc">${opts.desc}</div>` : ""}
				</div>`;
			}
			return `<div class="ch-wz-field" ${opts.hidden ? 'style="display:none"' : ""} data-field="${name}">
				<label class="ch-wz-label">${label}${req}</label>
				<input class="ch-wz-input" type="${type || "text"}" data-name="${name}" value="${val}" placeholder="${_esc(opts.placeholder || "")}" />
				${opts.desc ? `<div class="ch-wz-desc">${opts.desc}</div>` : ""}
			</div>`;
		}

		function _file_input(name, label, desc) {
			return `<div class="ch-wz-file-box" data-upload="${name}">
				<label class="ch-wz-label">${label}</label>
				<div class="ch-wz-file-drop">
					<div class="ch-wz-file-icon"><i class="fa fa-cloud-upload"></i></div>
					<div class="ch-wz-file-text">${__("Click or drag to upload")}</div>
					<input type="file" accept="image/*" style="display:none" />
					<div class="ch-wz-file-preview" style="display:none"></div>
				</div>
				${desc ? `<div class="ch-wz-desc">${desc}</div>` : ""}
			</div>`;
		}

		/* ── Step content builders ── */
		function _step_imei() {
			const imei = order.serial_no || order.imei_serial || "—";
			return `<div class="ch-wz-card">
				<div class="ch-wz-hint">
					<i class="fa fa-shield"></i>
					${__("Before continuing, check this device's IMEI on the government Sanchar Saathi (CEIR) registry to confirm it hasn't been reported lost or stolen. There is no automatic check — please do this manually.")}
				</div>
				<div class="ch-wz-desc" style="margin-bottom:14px;line-height:1.6">
					<strong>${__("How to check")}:</strong>
					<ol style="margin:6px 0 0 18px;padding:0">
						<li>${__("Visit")} <a href="https://ceir.sancharsaathi.gov.in" target="_blank" rel="noopener">ceir.sancharsaathi.gov.in</a> (${__("Know Your Mobile")}) ${__("or SMS")} <strong>"KYM ${frappe.utils.escape_html(imei)}"</strong> ${__("to")} <strong>14422</strong></li>
						<li>${__("Look up IMEI")}: <strong>${frappe.utils.escape_html(imei)}</strong></li>
						<li>${__("Take a screenshot of the result and upload it below")}</li>
					</ol>
				</div>
				${_input("imei_status", __("Validation Result"), "select", {
					reqd: true,
					options: ["", "Verified Clean", "Blacklisted", "Duplicate IMEI", "Already In Use", "Could Not Verify"],
				})}
				${_file_input("imei_screenshot", __("Sanchar Saathi Screenshot"), __("Screenshot of the IMEI lookup result page"))}
				<div class="ch-wz-field" data-field="imei_remarks">
					<label class="ch-wz-label">${__("Remarks")} <span style="font-weight:400;color:var(--text-muted)">(${__("required if 'Could Not Verify'")})</span></label>
					<textarea class="ch-wz-input" data-name="imei_remarks" rows="2" style="width:100%;padding:8px 12px;border:1px solid var(--gray-300);border-radius:6px;font-size:13px">${_esc(state.imei_remarks)}</textarea>
				</div>
			</div>`;
		}

		function _step_otp() {
			// Render mode depends on resume state. When OTP was already sent
			// (status = Awaiting OTP), do NOT show another "Send OTP" button —
			// just prompt for the code, with a resend link as escape hatch.
			const sent_badge = otp_sent_in_session
				? `<span style="color:var(--green-600);font-size:12px"><i class="fa fa-check-circle"></i> ${__("Sent to {0}", [masked])}</span>`
				: "";
			const send_btn = otp_sent_in_session
				? `<button class="btn btn-link btn-sm ch-wz-send-otp" style="padding:0;font-size:12px">
						<i class="fa fa-paper-plane"></i> ${__("Resend OTP")}
				   </button>`
				: `<button class="btn btn-primary btn-sm ch-wz-send-otp" style="border-radius:6px;font-weight:600;padding:6px 16px">
						<i class="fa fa-paper-plane"></i> ${__("Send OTP to")} ${masked}
				   </button>`;
			const hint = otp_sent_in_session
				? __("OTP has been sent to {0}. Ask the customer to read it out and enter it below.", [masked])
				: __("An OTP will be sent to {0} for price approval.", [masked]);
			return `<div class="ch-wz-card">
				<div class="ch-wz-hint">
					<i class="fa fa-info-circle"></i>
					${hint}
				</div>
				<div style="margin:16px 0;display:flex;align-items:center;gap:12px">
					${send_btn}
					<span class="ch-wz-otp-status">${sent_badge}</span>
				</div>
				${_input("otp_code", __("Enter OTP"), "text", { reqd: true, placeholder: "e.g. 123456", desc: __("6-digit OTP sent to customer's mobile") })}
			</div>`;
		}

		function _step_kyc() {
			return `<div class="ch-wz-card">
				<div class="ch-wz-row">
					${_input("kyc_id_type", __("ID Type"), "select", { reqd: true, options: ["", "Aadhar Card", "PAN Card", "Passport", "Driving License", "Voter ID"] })}
					${_input("kyc_id_number", __("ID Number"), "text", { reqd: true })}
				</div>
				<div class="ch-wz-row ch-wz-row-3" style="margin-top:16px">
					${_file_input("customer_id_front", __("ID Proof — Front"), __("Front side of ID card"))}
					${_file_input("customer_id_back", __("ID Proof — Back"), __("Back side of ID card"))}
					${_file_input("customer_photo", __("Customer Photo"), __("Selfie for identity verification"))}
				</div>
				<div class="ch-wz-divider"></div>
				<div class="ch-wz-section-title">
					${__("Ownership / Purchase Proof")}
					${ownership_proof_required
						? `<span style="color:var(--red-500);font-size:11px;font-weight:600">${__("Required — above ₹{0}", [format_number(order.require_ownership_proof_above)])}</span>`
						: `<span style="color:var(--text-muted);font-size:11px;font-weight:400">(${__("optional")})</span>`}
				</div>
				<div class="ch-wz-hint" style="margin-bottom:14px">
					<i class="fa fa-info-circle"></i>
					${__("ID proof shows who the seller is, not that they own this device. Attach an invoice/box-bill, or note why one isn't available.")}
				</div>
				<div class="ch-wz-row">
					${_input("ownership_proof_type", __("Proof Type"), "select", {
						reqd: ownership_proof_required,
						options: ["", "Purchase Invoice", "Original Box / Bill", "Insurance Document", "Not Available"],
					})}
					${_file_input("ownership_proof_document", __("Proof Document"), __("Invoice / box-bill / insurance doc"))}
				</div>
				<div class="ch-wz-field" data-field="ownership_proof_remarks" style="display:${state.ownership_proof_type === "Not Available" ? "block" : "none"}">
					<label class="ch-wz-label">${__("Reason proof isn't available")}</label>
					<textarea class="ch-wz-input" data-name="ownership_proof_remarks" rows="2" style="width:100%;padding:8px 12px;border:1px solid var(--gray-300);border-radius:6px;font-size:13px">${_esc(state.ownership_proof_remarks)}</textarea>
				</div>
			</div>`;
		}

		function _step_settlement() {
			return `<div class="ch-wz-card">
				<div class="ch-wz-alert">
					<i class="fa fa-hand-pointer-o"></i>
					<div>
						<strong>${__("Please hand the device to the customer")}</strong><br/>
						<span>${__("Customer should select their preference and enter payment details.")}</span>
					</div>
				</div>
				${_input("settlement_type", __("I want to"), "select", { reqd: true, options: ["", "Buyback", "Exchange"], desc: __("Buyback = cash/UPI/bank payout · Exchange = trade-in for a new device") })}
				<div class="ch-wz-payout-section" style="display:${state.settlement_type === "Buyback" ? "block" : "none"}">
					<div class="ch-wz-divider"></div>
					<div class="ch-wz-section-title">${__("Payout Details")}</div>
					${_input("payout_mode", __("Receive payment via"), "select", { reqd: true, options: ["", "Cash", "UPI", "Bank Transfer"] })}
					<div class="ch-wz-payout-upi" style="display:${state.payout_mode === "UPI" ? "block" : "none"}">
						${_input("upi_id", __("Your UPI ID"), "text", { reqd: true, placeholder: "e.g. 9876543210@upi" })}
					</div>
					<div class="ch-wz-payout-bank" style="display:${state.payout_mode === "Bank Transfer" ? "block" : "none"}">
						<div class="ch-wz-row">
							${_input("bank_account_holder", __("Account Holder Name"), "text", { reqd: true })}
							${_input("bank_account_number", __("Account Number"), "text", { reqd: true })}
						</div>
						<div class="ch-wz-row">
							${_input("bank_ifsc", __("IFSC Code"), "text", { reqd: true })}
							${_input("bank_name", __("Bank Name"), "text")}
						</div>
					</div>
					<div class="ch-wz-payout-confirm" style="display:${state.payout_mode ? "flex" : "none"}">
						<label class="ch-wz-check">
							<input type="checkbox" data-name="customer_confirm" ${state.customer_confirm ? "checked" : ""} />
							<span>${__("I confirm the above details are correct")}</span>
						</label>
					</div>
				</div>
			</div>`;
		}

		/* ── Full render ── */
		function _render() {
			// Resolve step builder by key so the OTP step can be omitted when
			// the order is already past OTP Verified (single-flow resume).
			const builders = { imei: _step_imei, otp: _step_otp, kyc: _step_kyc, settlement: _step_settlement };
			const step_fn = builders[STEPS[step].key];
			$body.html(`
				<style>
					.ch-wz{font-family:var(--font-stack);padding:0}
					.ch-wz-header{text-align:center;padding:20px 24px 16px;background:var(--subtle-fg);border-bottom:1px solid var(--border-color)}
					.ch-wz-price-label{font-size:11px;text-transform:uppercase;color:var(--text-muted);letter-spacing:.06em;font-weight:600}
					.ch-wz-price-val{font-size:30px;font-weight:800;color:var(--primary);margin:2px 0}
					.ch-wz-price-sub{font-size:12px;color:var(--text-muted)}
					.ch-wz-stepper{display:flex;align-items:center;justify-content:center;padding:16px 24px;border-bottom:1px solid var(--border-color);gap:0}
					.ch-wz-dot{display:flex;align-items:center;gap:6px}
					.ch-wz-n{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;
						font-size:12px;font-weight:700;border:2px solid var(--gray-300);color:var(--gray-400);background:#fff;flex-shrink:0}
					.ch-wz-lbl{font-size:13px;font-weight:600;color:var(--text-muted);white-space:nowrap}
					.ch-wz-dot.active .ch-wz-n{border-color:var(--primary);background:var(--primary);color:#fff}
					.ch-wz-dot.active .ch-wz-lbl{color:var(--text-color)}
					.ch-wz-dot.done .ch-wz-n{border-color:var(--green-500);background:var(--green-50);color:var(--green-600)}
					.ch-wz-dot.done .ch-wz-lbl{color:var(--green-600)}
					.ch-wz-line{width:32px;height:2px;background:var(--gray-200);margin:0 8px;flex-shrink:0}
					.ch-wz-line.done{background:var(--green-500)}
					.ch-wz-content{padding:20px 24px 16px}
					.ch-wz-card{}
					.ch-wz-hint{background:var(--blue-50,#e3f2fd);border-radius:8px;padding:12px 16px;font-size:13px;color:var(--blue-700,#1565c0);margin-bottom:16px}
					.ch-wz-hint i{margin-right:6px}
					.ch-wz-alert{display:flex;gap:12px;background:var(--yellow-50,#fffde7);border:1px solid var(--yellow-200,#fff9c4);border-radius:8px;padding:14px 16px;margin-bottom:18px;font-size:13px}
					.ch-wz-alert i{font-size:18px;color:var(--yellow-700);margin-top:2px}
					.ch-wz-alert span{color:var(--text-muted);font-size:12px}
					.ch-wz-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
					.ch-wz-row-3{grid-template-columns:1fr 1fr 1fr}
					.ch-wz-field{margin-bottom:14px}
					.ch-wz-label{display:block;font-size:12px;font-weight:600;color:var(--heading-color);margin-bottom:6px}
					.ch-wz-input,.ch-wz-select{width:100%;padding:8px 12px;border:1px solid var(--gray-300);border-radius:6px;font-size:14px;background:#fff;
						color:var(--text-color);outline:none;transition:border-color .15s}
					.ch-wz-input:focus,.ch-wz-select:focus{border-color:var(--primary);box-shadow:0 0 0 2px rgba(var(--primary-rgb,.1),.15)}
					.ch-wz-desc{font-size:11px;color:var(--text-muted);margin-top:4px}
					.ch-wz-divider{border-top:1px solid var(--border-color);margin:18px 0}
					.ch-wz-section-title{font-size:14px;font-weight:700;margin-bottom:14px;color:var(--heading-color)}
					.ch-wz-payout-confirm{display:flex;padding:12px 0}
					.ch-wz-check{display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;font-weight:500;color:var(--heading-color)}
					.ch-wz-check input{width:18px;height:18px;accent-color:var(--primary)}
					.ch-wz-file-box{margin-bottom:14px}
					.ch-wz-file-drop{border:2px dashed var(--gray-300);border-radius:8px;padding:16px;text-align:center;cursor:pointer;transition:border-color .15s;position:relative;min-height:80px;display:flex;flex-direction:column;align-items:center;justify-content:center}
					.ch-wz-file-drop:hover{border-color:var(--primary)}
					.ch-wz-file-drop.has-file{border-style:solid;border-color:var(--green-400);background:var(--green-50)}
					.ch-wz-file-icon{font-size:22px;color:var(--gray-400);margin-bottom:4px}
					.ch-wz-file-text{font-size:12px;color:var(--text-muted)}
					.ch-wz-file-preview{font-size:12px;color:var(--green-700);font-weight:600}
					.ch-wz-file-preview i{margin-right:4px}
					.ch-wz-nav{display:flex;justify-content:space-between;align-items:center;padding:14px 24px;border-top:1px solid var(--border-color)}
					.ch-wz-nav .btn{border-radius:6px;font-weight:600;padding:8px 20px;font-size:13px}
				</style>
				<div class="ch-wz">
					<div class="ch-wz-header">
						<div class="ch-wz-price-label">${__("Buyback Amount to be Paid")}</div>
						<div class="ch-wz-price-val">₹${format_number(price)}</div>
						<div class="ch-wz-price-sub">${device_label} · ${order.name}</div>
					</div>
					<div class="ch-wz-stepper">${_stepper_html()}</div>
					<div class="ch-wz-content">${step_fn()}</div>
					<div class="ch-wz-nav">
						<div>${step > 0 ? `<button class="btn btn-default ch-wz-back"><i class="fa fa-arrow-left"></i> ${__("Back")}</button>` : ""}</div>
						<div>
							${step < STEPS.length - 1
								? `<button class="btn btn-primary ch-wz-next">${__("Next")} <i class="fa fa-arrow-right"></i></button>`
								: `<button class="btn btn-primary ch-wz-submit"><i class="fa fa-check"></i> ${__("Verify & Approve")}</button>`}
						</div>
					</div>
				</div>
			`);
			// Hide dialog's default footer since we have our own nav
			dlg.get_primary_btn().closest(".modal-footer").hide();
			_bind_events();
		}

		/* ── Sync input values to state on every change ── */
		function _sync_state() {
			$body.find(".ch-wz-input, .ch-wz-select").each(function () {
				const name = $(this).data("name");
				if (name) state[name] = $(this).val();
			});
			$body.find('input[type="checkbox"][data-name]').each(function () {
				state[$(this).data("name")] = $(this).is(":checked");
			});
		}

		/* ── Event binding ── */
		function _bind_events() {
			$body.find(".ch-wz-back").on("click", () => { _sync_state(); step--; _render(); });
			$body.find(".ch-wz-next").on("click", function () {
				_sync_state();
				const key = STEPS[step].key;
				if (key === "imei") {
					if (!state.imei_status) {
						frappe.show_alert({ message: __("Please select the validation result"), indicator: "orange" }); return;
					}
					const needs_screenshot = state.imei_status !== "Could Not Verify";
					if (needs_screenshot && !uploads.imei_screenshot) {
						frappe.show_alert({ message: __("Please upload the Sanchar Saathi screenshot"), indicator: "orange" }); return;
					}
					if (state.imei_status === "Could Not Verify" && !(state.imei_remarks || "").trim()) {
						frappe.show_alert({ message: __("Please explain why it could not be verified"), indicator: "orange" }); return;
					}
					const btn = $(this);
					btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Saving...")}`);
					frappe.xcall("ch_pos.api.pos_api.pos_submit_imei_validation", {
						order_name: order.name,
						status: state.imei_status,
						screenshot: uploads.imei_screenshot || null,
						remarks: state.imei_remarks || null,
					}).then((res) => {
						if (res.blocked && res.order_status === "Rejected") {
							frappe.msgprint({
								title: __("Order Rejected"),
								message: res.message,
								indicator: "red",
							});
							dlg.hide();
							self._reload();
							return;
						}
						if (res.blocked) {
							btn.prop("disabled", false).html(`${__("Next")} <i class="fa fa-arrow-right"></i>`);
							frappe.show_alert({ message: res.message || __("Could not verify — please retry."), indicator: "orange" });
							return;
						}
						frappe.show_alert({ message: __("IMEI verified clean."), indicator: "green" });
						step++;
						_render();
					}).catch((e) => {
						btn.prop("disabled", false).html(`${__("Next")} <i class="fa fa-arrow-right"></i>`);
						frappe.show_alert({ message: _api_error_message(e, __("Failed to save IMEI validation")), indicator: "red" });
					});
					return;
				}
				if (key === "otp") {
					if (!_validate()) return;
					const btn = $(this);
					btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Verifying OTP...")}`);
					frappe.xcall("ch_pos.api.pos_api.pos_approve_customer_buyback", {
						order_name: order.name,
						method: "OTP",
						otp_code: state.otp_code,
					}).then(() => {
						otp_verified_in_session = true;
						step++;
						_render();
						frappe.show_alert({ message: __("OTP verified. Continue with KYC."), indicator: "green" });
					}).catch((e) => {
						btn.prop("disabled", false).html(`${__("Next")} <i class="fa fa-arrow-right"></i>`);
						frappe.show_alert({ message: _api_error_message(e, __("OTP verification failed")), indicator: "red" });
					});
					return;
				}

				if (_validate()) {
					step++;
					_render();
				}
			});
			$body.find(".ch-wz-submit").on("click", () => { _sync_state(); _submit(); });

			/* settlement_type toggle */
			$body.find('[data-name="settlement_type"]').on("change", function () {
				state.settlement_type = $(this).val();
				$body.find(".ch-wz-payout-section").toggle(state.settlement_type === "Buyback");
			});
			/* payout_mode toggle */
			$body.find('[data-name="payout_mode"]').on("change", function () {
				state.payout_mode = $(this).val();
				$body.find(".ch-wz-payout-upi").toggle(state.payout_mode === "UPI");
				$body.find(".ch-wz-payout-bank").toggle(state.payout_mode === "Bank Transfer");
				$body.find(".ch-wz-payout-confirm").toggle(!!state.payout_mode);
			});
			/* ownership_proof_type toggle */
			$body.find('[data-name="ownership_proof_type"]').on("change", function () {
				state.ownership_proof_type = $(this).val();
				$body.find('[data-field="ownership_proof_remarks"]').toggle(state.ownership_proof_type === "Not Available");
			});

			/* Send OTP */
			$body.find(".ch-wz-send-otp").on("click", function () {
				const btn = $(this);
				btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Sending...")}`);
				frappe.xcall("ch_pos.api.pos_api.pos_send_customer_otp", { order_name: order.name })
				.then((res) => {
					otp_sent_in_session = true;
					btn.prop("disabled", true).html(`<i class="fa fa-check"></i> ${__("OTP Sent")}`).removeClass("btn-primary").addClass("btn-success");
					$body.find(".ch-wz-otp-status").html(`<span style="color:var(--green-600)"><i class="fa fa-check-circle"></i> ${__("Sent to {0}", [res.masked_mobile])}</span>`);
				}).catch((e) => {
					btn.prop("disabled", false).html(`<i class="fa fa-paper-plane"></i> ${__("Retry Send OTP")}`);
					frappe.show_alert({ message: _api_error_message(e, __("Failed to send OTP")), indicator: "red" });
				});
			});

			/* File uploads */
			$body.find(".ch-wz-file-drop").each(function () {
				const $drop = $(this);
				const $input = $drop.find('input[type="file"]');
				const name = $drop.closest("[data-upload]").data("upload");

				$drop.on("click", () => $input.trigger("click"));
				$drop.on("dragover", (e) => { e.preventDefault(); $drop.css("border-color", "var(--primary)"); });
				$drop.on("dragleave drop", () => $drop.css("border-color", ""));
				$drop.on("drop", (e) => { e.preventDefault(); if (e.originalEvent.dataTransfer.files.length) _handle_file(name, $drop, e.originalEvent.dataTransfer.files[0]); });
				$input.on("change", function () { if (this.files.length) _handle_file(name, $drop, this.files[0]); });
			});
		}

		function _handle_file(name, $drop, file) {
			$drop.find(".ch-wz-file-icon, .ch-wz-file-text").hide();
			$drop.find(".ch-wz-file-preview").show().html(`<i class="fa fa-spinner fa-spin"></i> ${__("Uploading...")}`);
			$drop.addClass("has-file");

			const form_data = new FormData();
			form_data.append("file", file, file.name);
			form_data.append("doctype", "Buyback Order");
			form_data.append("docname", order.name);
			form_data.append("fieldname", name);
			form_data.append("is_private", 1);

			$.ajax({
				url: "/api/method/upload_file",
				type: "POST",
				data: form_data,
				processData: false,
				contentType: false,
				headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
			}).done((r) => {
				const url = r.message.file_url;
				uploads[name] = url;
				$drop.find(".ch-wz-file-preview").html(`<i class="fa fa-check-circle"></i> ${frappe.utils.escape_html(file.name)}`);
			}).fail(() => {
				$drop.find(".ch-wz-file-preview").html(`<span style="color:var(--red-500)"><i class="fa fa-times"></i> ${__("Upload failed")}</span>`);
				$drop.removeClass("has-file");
				uploads[name] = null;
			});
		}

		/* ── Per-step validation ── */
		function _validate() {
			const key = STEPS[step].key;
			if (key === "otp") {
				if (!otp_sent_in_session) {
					frappe.show_alert({ message: __("Please send OTP first"), indicator: "orange" }); return false;
				}
				if (!state.otp_code || state.otp_code.length < 4) {
					frappe.show_alert({ message: __("Please enter a valid OTP"), indicator: "orange" }); return false;
				}
			} else if (key === "kyc") {
				if (!state.kyc_id_type || !state.kyc_id_number) {
					frappe.show_alert({ message: __("ID Type and ID Number are required"), indicator: "orange" }); return false;
				}
			}
			return true;
		}

		/* ── Final submit ── */
		function _submit() {
			_sync_state();
			if (!state.settlement_type) {
				frappe.show_alert({ message: __("Please select Buyback or Exchange"), indicator: "orange" }); return;
			}
			if (state.settlement_type === "Buyback") {
				if (!state.payout_mode) { frappe.show_alert({ message: __("Please select a payout mode"), indicator: "orange" }); return; }
				if (state.payout_mode === "UPI" && !state.upi_id) { frappe.show_alert({ message: __("UPI ID is required"), indicator: "orange" }); return; }
				if (state.payout_mode === "Bank Transfer" && (!state.bank_account_holder || !state.bank_account_number || !state.bank_ifsc)) {
					frappe.show_alert({ message: __("Bank account details are required"), indicator: "orange" }); return;
				}
				if (!state.customer_confirm) { frappe.show_alert({ message: __("Customer must confirm the details are correct"), indicator: "orange" }); return; }
			}

			$body.find(".ch-wz-submit").prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Verifying...")}`);

			// SINGLE FLOW: one and only one customer-approval API call.
			// Backend method selection rules:
			//   • If OTP is required AND user entered a code → "OTP"
			//     (backend verifies code, sets customer_approved, status → OTP Verified)
			//   • If OTP is already verified upstream → "In-Store Signature"
			//     (backend skips OTP verify, only persists KYC/settlement;
			//      customer_approve is a no-op because it's already approved)
			const send_method = (otp_required && !otp_verified_in_session) ? "OTP" : "In-Store Signature";
			const send_otp_code = (otp_required && !otp_verified_in_session) ? state.otp_code : null;

			frappe.xcall("ch_pos.api.pos_api.pos_approve_customer_buyback", {
				order_name: order.name,
				method: send_method,
				otp_code: send_otp_code,
				kyc_id_type: state.kyc_id_type,
				kyc_id_number: state.kyc_id_number,
				customer_id_front: uploads.customer_id_front || null,
				customer_id_back: uploads.customer_id_back || null,
				customer_photo: uploads.customer_photo || null,
				settlement_type: state.settlement_type,
				payout_mode: state.settlement_type === "Buyback" ? state.payout_mode : null,
				upi_id: state.upi_id || null,
				bank_account_holder: state.bank_account_holder || null,
				bank_account_number: state.bank_account_number || null,
				bank_ifsc: state.bank_ifsc || null,
				bank_name: state.bank_name || null,
			}).then(() => {
				dlg.hide();
				frappe.show_alert({ message: __("Customer approved ✓"), indicator: "green" });
				self._reload();
			}).catch((e) => {
				$body.find(".ch-wz-submit").prop("disabled", false).html(`<i class="fa fa-check"></i> ${__("Verify & Approve")}`);
				frappe.show_alert({ message: _api_error_message(e, __("Verification failed")), indicator: "red" });
			});
		}

		dlg.show();
		_render();
	}

	// ─────────────────────────────── new assessment dialog ──
	_new_assessment_dialog(panel) {
		const condition_keys = ["screen", "body", "buttons", "charging", "camera", "speaker_mic"];
		const condition_labels = {
			screen: __("Screen"), body: __("Body / Frame"), buttons: __("Buttons"),
			charging: __("Charging Port"), camera: __("Camera"), speaker_mic: __("Speaker / Mic"),
		};

		const dlg = new frappe.ui.Dialog({
			title: __("New Buyback Assessment"),
			size: "extra-large",
			fields: [
				{ fieldtype: "Section Break", label: __("Device Information") },
				{ fieldname: "mobile_no", fieldtype: "Data", label: __("Customer Mobile"), reqd: 1,
					description: __("Customer's registered mobile number") },
				{ fieldname: "customer", fieldtype: "Link", options: "Customer", label: __("Customer") },
				{ fieldtype: "Column Break" },
				{ fieldname: "item", fieldtype: "Link", options: "Item",
					get_query: () => ({ filters: { has_serial_no: 1, has_variants: 0 } }),
					label: __("Device Model"), reqd: 1 },
				{ fieldname: "imei_serial", fieldtype: "Data", label: __("IMEI / Serial No"),
					description: __("15-digit IMEI or serial number") },

				{ fieldtype: "Section Break", label: __("Device Condition") },
				{ fieldtype: "HTML", fieldname: "cond_hint",
					options: `<p class="text-muted small">${__("Toggle each — unchecked means defective, a deduction is applied")}</p>` },
				...condition_keys.flatMap((key, i) => [
					{ fieldname: `cond_${key}`, fieldtype: "Check",
						label: condition_labels[key] + " " + __("OK"), default: 1 },
					...(i % 2 === 0 ? [{ fieldtype: "Column Break" }] : []),
					...(i % 2 === 1 && i < condition_keys.length - 1
						? [{ fieldtype: "Section Break", hide_border: 1 }] : []),
				]),

				{ fieldtype: "HTML", fieldname: "valuation_display", options: `
					<div class="ch-bb-live-valuation"
						style="background:var(--pos-bg-alt,#f8f9fb);border-radius:var(--pos-radius,8px);padding:16px;margin:10px 0;text-align:center">
						<div style="font-size:11px;color:var(--pos-text-muted);text-transform:uppercase;letter-spacing:.06em">
							${__("Estimated Buyback Value")}
						</div>
						<div class="ch-bb-live-price"
							style="font-size:30px;font-weight:800;color:var(--pos-primary,#6366f1);margin-top:4px">—</div>
						<div class="ch-bb-live-grade" style="font-size:12px;color:var(--pos-text-muted)"></div>
					</div>` },

				{ fieldtype: "Section Break", label: __("KYC Details") },
				{ fieldname: "kyc_id_type", fieldtype: "Select", label: __("ID Type"),
					options: "\nAadhar Card\nPAN Card\nPassport\nDriving License\nVoter ID" },
				{ fieldname: "kyc_id_number", fieldtype: "Data", label: __("ID Number") },
				{ fieldtype: "Column Break" },
				{ fieldname: "kyc_name", fieldtype: "Data", label: __("Name on ID") },
			],
			primary_action_label: __("Create Assessment"),
			primary_action: (values) => {
				if (!values.item || !values.mobile_no) return;
				if (!validate_india_phone(values.mobile_no)) {
					frappe.show_alert({ message: __("Enter a valid Indian mobile number (10 digits starting with 6-9)"), indicator: "orange" });
					return;
				}
				if (!validate_id_number(values.kyc_id_type, values.kyc_id_number)) return;
				const checks = {};
				condition_keys.forEach(k => { checks[k] = values[`cond_${k}`] ? true : false; });
				dlg.disable_primary_action();

				const proceed = () => {
					frappe.xcall("ch_pos.api.pos_api.create_buyback_assessment_with_grading", {
						mobile_no: values.mobile_no,
						item_code: values.item,
						imei_serial: values.imei_serial || "",
						customer: values.customer || "",
						condition_checks: checks,
						kyc_id_type: values.kyc_id_type || "",
						kyc_id_number: values.kyc_id_number || "",
						kyc_name: values.kyc_name || "",
					}).then((doc) => {
						dlg.hide();
						frappe.show_alert({
							message: `${__("Assessment")} <b>${doc.name}</b> ${__("created")} · ${__("Grade")} ${doc.estimated_grade} · ₹${format_number(doc.estimated_price)}`,
							indicator: "green",
						});
						// Increment buyback walk-in counter
						if (PosState.pos_profile) {
							frappe.call({ method: "ch_pos.api.pos_api.increment_buyback_count", args: { pos_profile: PosState.pos_profile } });
						}
						panel.find(".ch-bb-search").val(doc.name);
						panel.find(".ch-bb-lookup").click();
					}).catch(() => dlg.enable_primary_action());
				};

				if (values.imei_serial) {
					frappe.xcall("ch_pos.api.pos_api.check_imei_blacklist", { imei: values.imei_serial })
						.then(res => {
							if (res && res.blacklisted) {
								dlg.enable_primary_action();
								frappe.msgprint({
									title: __("Blacklisted Device"),
									message: __("IMEI {0} is blacklisted — {1}", [values.imei_serial, res.reason]),
									indicator: "red",
								});
							} else { proceed(); }
						}).catch(() => proceed());
				} else { proceed(); }
			},
		});

		// Live valuation on item / condition change
		const update_val = () => {
			const item_code = dlg.get_value("item");
			if (!item_code) {
				dlg.$wrapper.find(".ch-bb-live-price").text("—");
				dlg.$wrapper.find(".ch-bb-live-grade").text("");
				return;
			}
			const checks = {};
			condition_keys.forEach(k => { checks[k] = dlg.get_value(`cond_${k}`) ? true : false; });
			frappe.xcall("ch_pos.api.pos_api.calculate_buyback_valuation", {
				item_code, condition_checks: checks,
			}).then(val => {
				dlg.$wrapper.find(".ch-bb-live-price").text(`₹${format_number(val.final_price || val.offered_price)}`);
				dlg.$wrapper.find(".ch-bb-live-grade").text(
					`${__("Grade")}: ${val.grade} · ${__("Base")}: ₹${format_number(val.base_price)} · ${__("Deduction")}: ₹${format_number(val.total_deduction)}`
				);
			});
		};

		dlg.fields_dict.item.$input.on("change", update_val);
		condition_keys.forEach(k => dlg.fields_dict[`cond_${k}`].$input.on("change", update_val));
		dlg.show();
	}

	// ─────────────────────────────── helpers ──
	_status_cls(status) {
		if (["Submitted", "Approved", "Complete", "Inspection Created",
			"Customer Approved", "Paid", "Closed"].includes(status)) return "success";
		if (["Draft", "Awaiting Approval", "Awaiting Customer Approval",
			"Ready to Pay"].includes(status)) return "warning";
		if (["Rejected", "Cancelled"].includes(status)) return "danger";
		return "muted";
	}
}
