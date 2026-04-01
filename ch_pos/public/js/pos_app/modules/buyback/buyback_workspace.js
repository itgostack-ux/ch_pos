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
		if (["Customer Approved", "Ready to Pay"].includes(s)) return STAGE.SETTLE;
		if (["Approved", "Awaiting Customer Approval", "Awaiting OTP", "OTP Verified"].includes(s)) return STAGE.APPROVE;
		if (["Draft", "Awaiting Approval"].includes(s)) return STAGE.INSPECT;
	}
	// Assessment-based: inspection exists but no order yet
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
		frappe.xcall("ch_pos.api.pos_api.get_pos_buyback_detail", { assessment_name: name })
			.then(data => {
				this._current_data = data;
				this._render_detail(detail, data);
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
			// INSPECT stage: show inline inspection form
			// If inspection data is already in the detail payload, render directly
			if (data.inspection) {
				body_html = this._html_inspect_inline(data.inspection, data);
			} else if (data.order && ["Draft", "Awaiting Approval"].includes(data.order.status)) {
				// Order exists but awaiting manager approval
				body_html = this._html_inspect_awaiting(data);
			} else {
				// Inspection exists but rich data not loaded yet — show loading
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
			<div class="ch-bb-actions" style="margin-top:12px">
				<button class="btn btn-outline-secondary ch-bb-act ch-bb-refresh-stage"
					style="width:100%;border-radius:var(--pos-radius,8px)">
					<i class="fa fa-refresh"></i> ${__("Refresh Status")}
				</button>
			</div>`;
	}

	// ─────────────────────────────── stage: APPROVE ──
	_html_approve(data) {
		const order = data.order;
		const price = order ? order.final_price : (data.quoted_price || data.estimated_price);
		const mobile = data.mobile_no || "";
		const masked = mobile ? mobile.slice(0, 2) + "****" + mobile.slice(-2) : "—";
		const order_status = order ? order.status : "";
		const is_waiting = order_status === "Awaiting Customer Approval";
		const approval_url = order ? (order.approval_url || "") : "";

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
					<button class="btn btn-outline-primary ch-bb-act ch-bb-resend-link"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:600">
						<i class="fa fa-paper-plane"></i> ${__("Resend Link")}
					</button>
					<button class="btn btn-outline-secondary ch-bb-act ch-bb-approve-instor"
						style="width:100%;border-radius:var(--pos-radius,8px)">
						<i class="fa fa-pencil"></i> ${__("Approve In-Store Instead")}
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

		// ── ASSESS: Start/continue inspection inline ────
		el.on("click.bbstage", ".ch-bb-start-inspection", (e) => {
			const btn = $(e.currentTarget);
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Loading...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_create_inspection", {
				assessment_name: data.name,
			}).then((ins) => {
				// Reload full detail to get proper INSPECT stage with rich data
				this._reload();
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

			const btn = $(e.currentTarget);
			btn.prop("disabled", true)
				.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Completing...")}`);

			frappe.xcall("ch_pos.api.pos_api.pos_complete_inspection", {
				inspection_name,
				condition_grade: grade,
				final_price: price,
				price_override_reason: override_reason,
				remarks,
			}).then(() => {
				frappe.show_alert({ message: __("Inspection complete — Order created"), indicator: "green" });
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-check-circle"></i> ${__("Complete Inspection & Create Order")}`);
			});
		});

		// ── INSPECT INLINE: Open inspection in desk ─────
		el.on("click.bbstage", ".ch-bb-inspection-to-desk", (e) => {
			frappe.set_route("Form", "Buyback Inspection", $(e.currentTarget).data("inspection"));
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
			PosState.exchange_assessment = btn.data("name");
			PosState.exchange_amount = flt(btn.data("amount"));
			EventBus.emit("exchange:applied", {
				assessment: btn.data("name"),
				buyback_amount: flt(btn.data("amount")),
				item_name: btn.data("item-name"),
				imei_serial: btn.data("imei"),
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
	_show_instore_approval_dialog(data) {
		const order = data.order;
		const price = order.final_price || 0;
		const mobile = data.mobile_no || order.mobile_no || "";
		const masked = mobile ? mobile.slice(0, 2) + "****" + mobile.slice(-2) : "—";

		const dlg = new frappe.ui.Dialog({
			title: __("Customer Approval — In-Store Verification"),
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "price_summary", options: `
					<div style="text-align:center;padding:12px 0 16px;border-bottom:1px solid var(--border-color)">
						<div style="font-size:11px;text-transform:uppercase;color:var(--text-muted);letter-spacing:.05em;font-weight:600">
							${__("Buyback Amount to be Paid")}
						</div>
						<div style="font-size:32px;font-weight:800;color:var(--primary);line-height:1.2;margin:4px 0">
							₹${format_number(price)}
						</div>
						<div style="font-size:12px;color:var(--text-muted)">
							${__("Device:")} ${frappe.utils.escape_html(data.item_name || "")} ·
							${__("Order:")} ${order.name}
						</div>
					</div>
				` },

				{ fieldtype: "Section Break", label: __("Step 1: Send OTP to Customer") },
				{ fieldtype: "HTML", fieldname: "otp_info", options: `
					<p class="text-muted" style="font-size:12px;margin-bottom:8px">
						<i class="fa fa-info-circle"></i>
						${__("An OTP will be sent to customer's number {0} for price approval.", [masked])}
					</p>
				` },
				{ fieldname: "otp_code", fieldtype: "Data", label: __("Enter OTP"), reqd: 1,
					description: __("6-digit OTP sent to customer's mobile") },

				{ fieldtype: "Section Break", label: __("Step 2: Customer KYC Verification") },
				{ fieldname: "kyc_id_type", fieldtype: "Select", label: __("ID Type"), reqd: 1,
					options: "\nAadhaar\nPAN\nPassport\nDriving Licence\nVoter ID",
					default: order.customer_id_type || "" },
				{ fieldtype: "Column Break" },
				{ fieldname: "kyc_id_number", fieldtype: "Data", label: __("ID Number"), reqd: 1,
					default: order.customer_id_number || "" },
			],
			primary_action_label: __("Verify & Approve"),
			primary_action: (values) => {
				if (!values.otp_code || values.otp_code.length < 4) {
					frappe.show_alert({ message: __("Enter a valid OTP"), indicator: "orange" });
					return;
				}
				if (!values.kyc_id_type || !values.kyc_id_number) {
					frappe.show_alert({ message: __("KYC ID type and number are required"), indicator: "orange" });
					return;
				}

				dlg.disable_primary_action();
				dlg.set_title(__("Verifying..."));

				frappe.xcall("ch_pos.api.pos_api.pos_approve_customer_buyback", {
					order_name: order.name,
					method: "OTP",
					otp_code: values.otp_code,
					kyc_id_type: values.kyc_id_type,
					kyc_id_number: values.kyc_id_number,
				}).then(() => {
					dlg.hide();
					frappe.show_alert({ message: __("Customer approved (OTP verified + KYC)"), indicator: "green" });
					this._reload();
				}).catch((e) => {
					dlg.enable_primary_action();
					dlg.set_title(__("Customer Approval — In-Store Verification"));
					frappe.show_alert({ message: e.message || __("Verification failed"), indicator: "red" });
				});
			},
		});

		// Add "Send OTP" button below the form
		dlg.$wrapper.find('[data-fieldname="otp_code"]').closest(".frappe-control").before(`
			<div style="margin-bottom:10px">
				<button class="btn btn-sm btn-primary ch-bb-send-otp-btn" style="border-radius:6px;font-weight:600">
					<i class="fa fa-paper-plane"></i> ${__("Send OTP to")} ${masked}
				</button>
				<span class="ch-bb-otp-status" style="margin-left:8px;font-size:12px"></span>
			</div>
		`);

		dlg.$wrapper.find(".ch-bb-send-otp-btn").on("click", function () {
			const btn = $(this);
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Sending...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_send_customer_otp", {
				order_name: order.name,
			}).then((res) => {
				btn.prop("disabled", true)
					.html(`<i class="fa fa-check"></i> ${__("OTP Sent")}`)
					.removeClass("btn-primary").addClass("btn-success");
				dlg.$wrapper.find(".ch-bb-otp-status")
					.html(`<span style="color:var(--green-600)"><i class="fa fa-check-circle"></i> ${__("Sent to {0}", [res.masked_mobile])}</span>`);
			}).catch((e) => {
				btn.prop("disabled", false)
					.html(`<i class="fa fa-paper-plane"></i> ${__("Retry Send OTP")}`);
				frappe.show_alert({ message: e.message || __("Failed to send OTP"), indicator: "red" });
			});
		});

		dlg.show();
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
					get_query: () => ({ filters: { has_serial_no: 1 } }),
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
					options: "\nAadhaar\nPAN\nPassport\nDriving Licence\nVoter ID" },
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
