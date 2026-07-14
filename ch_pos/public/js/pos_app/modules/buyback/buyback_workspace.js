/**
 * CH POS — Buyback Workspace (Full POS-Native Flow)
 *
 * Complete buyback lifecycle inside POS:
 *
 *  SEARCH → ASSESS → INSPECT → [KYC if pending] → APPROVE → SETTLE
 *
 *  - KYC stage appears in progress bar ONLY when:
 *      • we're past Assess (inspection done OR order creation imminent), AND
 *      • Aadhaar not yet uploaded
 *  - Once KYC uploaded, KYC step is hidden and APPROVE becomes active
 *  - "Create Order & Send for Approval" only enabled after KYC uploaded
 */
import { PosState, EventBus } from "../../state.js";
import { format_number, validate_india_phone, validate_id_number } from "../../shared/helpers.js";

// ──────────────────────────────────────────── Stage helpers ──────────
const STAGE = {
	ASSESS: "assess",
	INSPECT: "inspect",
	APPROVE: "approve",
	SETTLE: "settle",
	DONE: "done",
	// Terminal — Buyback Order auto-rejected (e.g. IMEI blacklisted on Sanchar
	// Saathi → BuybackOrder.submit_imei_validation flips status to Rejected) or
	// Buyback Assessment cancelled/expired. Must short-circuit BEFORE the older
	// "Inspection Created" fallback below, otherwise a rejected order whose
	// assessment was already at "Inspection Created" gets routed back to the
	// INSPECT panel — which made the UI appear to "move to the inspection form"
	// even after the order was closed.
	CLOSED: "closed",
};

function _determine_stage(data) {
	// Terminal states first — a rejected/cancelled order or a cancelled/expired
	// assessment must NEVER fall through to INSPECT/ASSESS rendering, even if
	// the upstream assessment.status is still "Inspection Created" and
	// data.buyback_inspection is still populated from before the rejection.
	if (data.order && ["Rejected", "Cancelled"].includes(data.order.status || "")) {
		return STAGE.CLOSED;
	}
	if (!data.order && ["Cancelled", "Expired"].includes(data.status || "")) {
		return STAGE.CLOSED;
	}
	if (data.order) {
		const s = data.order.status || "";
		if (["Paid", "Closed"].includes(s)) return STAGE.DONE;
		if (["Customer Approved", "Ready to Pay", "OTP Verified"].includes(s)) return STAGE.SETTLE;
		if (["Approved", "Awaiting Customer Approval", "Awaiting OTP"].includes(s)) return STAGE.APPROVE;
		if (["Draft", "Awaiting Approval"].includes(s)) return STAGE.INSPECT;
	}
	if (data.inspection && data.inspection.status === "Completed" && !data.order) {
		return STAGE.APPROVE;
	}
	const status = data.status || "";
	if (status === "Inspection Created" && data.buyback_inspection) return STAGE.INSPECT;
	return STAGE.ASSESS;
}

// ─── KYC done check — supports multiple field name variations ───
function _kyc_done(data) {
	if (!data) return false;
	return !!(
		data.customer_id_front ||
		data.kyc_attached ||
		data._kyc_uploaded_local ||
		(data.kyc_id_number && data.kyc_id_type)
	);
}

// ─── Build progress bar dynamically ────────────────────────────────
// KYC step is inserted AFTER Inspect ONLY when:
//   - We're past ASSESS (i.e. INSPECT, APPROVE, or SETTLE stage), AND
//   - KYC is not yet uploaded
//
// Visual sequence:
//   ASSESS pending  → 4 steps:  Assess · Inspect · Approve · Settle
//   ASSESS done     → 4 steps:  Assess · Inspect · Approve · Settle   (KYC already done)
//   ASSESS done     → 5 steps:  Assess · Inspect · KYC · Approve · Settle  (KYC pending)
function _build_progress_steps(data, stage) {
	const kyc_done = _kyc_done(data);
	// KYC step appears only past ASSESS stage, and only when KYC is pending
	const past_assess = stage !== STAGE.ASSESS;
	const needs_kyc_step = !kyc_done && past_assess;

	if (needs_kyc_step) {
		return [
			{ key: "assess", label: "Assess" },
			{ key: "inspect", label: "Inspect" },
			{ key: "kyc", label: "KYC" },          // ← inserted between Inspect & Approve
			{ key: "approve", label: "Approve" },
			{ key: "settle", label: "Settle" },
		];
	}
	// KYC done OR still at ASSESS → original 4 steps
	return [
		{ key: "assess", label: "Assess" },
		{ key: "inspect", label: "Inspect" },
		{ key: "approve", label: "Approve" },
		{ key: "settle", label: "Settle" },
	];
}

function _active_progress_index(steps, stage) {
	if (stage === STAGE.DONE) return steps.length;
	// CLOSED is terminal-but-not-success — no step should be highlighted as
	// "active" or "done"; the dedicated _html_closed panel carries the message.
	if (stage === STAGE.CLOSED) return -1;
	const idx = steps.findIndex(s => s.key === stage);
	return idx >= 0 ? idx : 0;
}

function _api_error_message(e, fallback) {
	let msg = "";
	if (typeof e === "string") msg = e;
	else msg = (e && (e.message || e.exc_type || e.exc)) || "";
	if (!msg && e && e._server_messages) {
		try {
			const raw = JSON.parse(e._server_messages);
			if (Array.isArray(raw) && raw.length) msg = raw[0];
		} catch (_err) { }
	}
	msg = frappe.utils.strip_html(String(msg || "")).trim();
	if (!msg) return fallback;
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
		this._kyc_cache = {};
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "buyback") return;
			this._panel = ctx.panel;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		panel.html(`
			<div class="ch-pos-mode-panel ch-bb-root">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:var(--pos-warning-light);color:#92400e">
							<i class="fa fa-exchange"></i>
						</span>
						${__("Buyback & Exchange")}
					</h4>
					<span class="ch-mode-hint">${__("Search mobile diagnostics, inspect, approve and settle")}</span>
				</div>

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
		panel.on("click", ".ch-bb-refresh-btn", () => {
			const q = panel.find(".ch-bb-search").val().trim();
			if (q) this._search(panel, q);
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
			frappe.route_options = {
				source: "Store Manual",
				store: PosState.warehouse || "",
				_from_pos: "1",
			};
			frappe.new_doc("Buyback Assessment");
		});
	}

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
				const cached = this._kyc_cache[name];
				if (cached) {
					if (!data.customer_id_front && cached.customer_id_front) {
						data.customer_id_front = cached.customer_id_front;
					}
					if (!data.customer_id_back && cached.customer_id_back) {
						data.customer_id_back = cached.customer_id_back;
					}
					if (!data.customer_photo && cached.customer_photo) {
						data.customer_photo = cached.customer_photo;
					}
					if (!data.kyc_id_type && cached.kyc_id_type) {
						data.kyc_id_type = cached.kyc_id_type;
					}
					if (!data.kyc_id_number && cached.kyc_id_number) {
						data.kyc_id_number = cached.kyc_id_number;
					}
					if (!data.kyc_name && cached.kyc_name) {
						data.kyc_name = cached.kyc_name;
					}
					data._kyc_uploaded_local = true;
				}
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

	_rerender_with_current_data() {
		if (!this._current_data || !this._panel) return;
		const detail = this._panel.find(".ch-bb-detail");
		this._render_detail(detail, this._current_data);
	}

	_render_detail(el, data) {
		const stage = _determine_stage(data);
		const steps = _build_progress_steps(data, stage);
		const kyc_done = _kyc_done(data);

		// Progress index — locate current stage in the step array.
		// When KYC is pending AND we're at INSPECT/APPROVE, the "KYC" step
		// is the next required action, so highlight it as active.
		let active_idx = _active_progress_index(steps, stage);

		// Special case: 5-step bar (KYC inserted between Inspect & Approve)
		// If inspection is done & order needs creation but KYC missing →
		// jump active marker to the KYC step (index 2) so the user clearly
		// sees KYC as the next action.
		if (!kyc_done && steps.length === 5) {
			// 5-step: Assess(0) · Inspect(1) · KYC(2) · Approve(3) · Settle(4)
			if (stage === STAGE.INSPECT) {
				// Inspection in progress — show Inspect as active (Assess done)
				active_idx = 1;
			} else if (stage === STAGE.APPROVE) {
				// Inspection done, but KYC blocks Approve → highlight KYC
				active_idx = 2;
			}
		}

		const progress_html = `<div class="ch-bb-progress">
			${steps.map((s, i) => `
				<div class="ch-bb-progress-step ${i < active_idx ? "done" : i === active_idx ? "active" : ""}">
					<div class="ch-bb-progress-dot">${i < active_idx ? '<i class="fa fa-check"></i>' : i + 1}</div>
					<div class="ch-bb-progress-label">${__(s.label)}</div>
				</div>
				${i < steps.length - 1 ? `<div class="ch-bb-progress-line${i < active_idx ? " done" : ""}"></div>` : ""}
			`).join("")}
		</div>`;

		let body_html = "";
		if (stage === STAGE.CLOSED) body_html = this._html_closed(data);
		else if (stage === STAGE.DONE) body_html = this._html_done(data);
		else if (stage === STAGE.SETTLE) body_html = this._html_settle(data);
		else if (stage === STAGE.APPROVE) body_html = this._html_approve(data);
		else if (stage === STAGE.INSPECT) {
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

		const show_full_header = kyc_done || stage !== STAGE.ASSESS;

		const header_html = show_full_header ? `
			<div class="ch-bb-detail-header">
				<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
					<span style="font-weight:700;font-size:var(--pos-fs-md,14px)">${data.name}</span>
					${data.source === "Mobile App"
						? `<span class="ch-pos-badge badge-primary"><i class="fa fa-mobile"></i> ${__("Mobile Diagnostic")}</span>`
						: `<span class="ch-pos-badge badge-muted">${__("Manual")}</span>`}
					${kyc_done ? `<span class="ch-pos-badge badge-success" style="font-size:10px"><i class="fa fa-check-circle"></i> ${__("KYC Verified")}</span>` : ""}
				</div>
				<a href="#" class="ch-bb-open-desk text-muted" style="font-size:12px" data-name="${data.name}">
					<i class="fa fa-external-link"></i> ${__("Desk")}
				</a>
			</div>

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
		` : "";

		el.html(`
			<div class="ch-bb-detail-card">
				${header_html}
				${progress_html}
				<div class="ch-bb-stage-body">${body_html}</div>
			</div>
		`);

		// Surface any price exceptions raised against this order (parity with
		// how sale exceptions are shown in POS).
		if (data.order && data.order.name) {
			this._render_order_exceptions(el, data.order.name);
		}

		el.find(".ch-bb-open-desk").on("click", function (e) {
			e.preventDefault();
			frappe.set_route("Form", "Buyback Assessment", $(this).data("name"));
		});

		el.find(".ch-bb-open-order-desk").on("click", function (e) {
			e.preventDefault();
			frappe.set_route("Form", "Buyback Order", $(this).data("name"));
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
						<label class="ch-bb-imei-file-drop" style="border:1px dashed var(--gray-300);border-radius:6px;padding:6px 10px;cursor:pointer;font-size:12px;text-align:center;min-height:31px;display:flex;align-items:center;justify-content:center;position:relative;user-select:none">
							<input type="file" accept="image/*" style="position:absolute;width:1px;height:1px;opacity:0">
							<span class="ch-bb-imei-file-label" style="pointer-events:none">${__("Click to upload")}</span>
						</label>
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

		const is_mobile_app = (data.source || "") === "Mobile App";
		const can_start = status === "Submitted" || status === "Quote Generated";
		const inspection_in_progress = status === "Inspection Created" && !!data.buyback_inspection;
		const kyc_done = _kyc_done(data);

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
			// Mobile App flow — KYC is no longer enforced at ASSESS stage.
			// Customer self-assessed via app → straight to physical inspection.
			// KYC will be required after Inspect, before order creation.
			action_btn = `
				<button class="btn btn-primary btn-lg ch-bb-act ch-bb-start-inspection"
					data-name="${data.name}"
					style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
					<i class="fa fa-search-plus"></i> ${__("Inspect Device")}
				</button>`;

		} else if (can_start && !is_mobile_app) {

			
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
				<div style="margin-bottom:14px;padding:10px 12px;border:1px solid var(--border-color);border-radius:var(--pos-radius,8px);background:var(--subtle-fg)">
					<label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer;font-weight:600;margin-bottom:6px">
						<input type="checkbox" class="ch-bb-walkin-lock-cleared" style="margin-top:3px;width:16px;height:16px">
						<span>${__("FRP / iCloud Lock Cleared (Factory Reset Confirmed)")}</span>
					</label>
					<div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">
						${__("Walk-in skips a separate inspection step — confirm this here before creating the order.")}
					</div>
					<textarea class="form-control form-control-sm ch-bb-walkin-lock-notes" rows="1"
						style="border-radius:6px;font-size:12px"
						placeholder="${__("e.g. customer signed out in-store")}"></textarea>
				</div>
				<button class="btn btn-primary btn-lg ch-bb-act ch-bb-create-walkin-order"
					data-name="${data.name}"
					style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
					<i class="fa fa-check-circle"></i> ${__("Create Order & Send for Approval")}
				</button>`;

			// Walk-in flow — KYC blocks order creation (not inspection)
			if (!kyc_done) {
				action_btn = `
					<div class="ch-bb-info-note" style="background:#fef3c7;border-color:#f59e0b;color:#92400e">
						<i class="fa fa-exclamation-triangle"></i>
						${__("KYC pending — Aadhaar must be uploaded before creating buyback order")}
					</div>
					<div class="ch-bb-info-note">
						<i class="fa fa-info-circle"></i>
						${__("Store walk-in: upload KYC, set final buyback price and create order.")}
					</div>
					<button class="btn btn-warning btn-lg ch-bb-act ch-bb-upload-kyc"
						data-name="${data.name}"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px;margin-bottom:10px;background:#f59e0b;border-color:#f59e0b;color:#fff">
						<i class="fa fa-id-card"></i> ${__("Step 1: Upload Customer KYC (Aadhaar)")}
					</button>
					<div style="margin-bottom:10px">
						<label class="ch-bb-field-label">${__("Final Buyback Price (₹)")}</label>
						<input type="number" class="form-control ch-bb-walkin-price"
							value="${data.quoted_price || data.estimated_price}" min="0" step="1"
							style="font-size:20px;font-weight:700;text-align:right;padding:10px;border-radius:var(--pos-radius,8px)">
					</div>
					<button class="btn btn-primary btn-lg ch-bb-act ch-bb-create-walkin-order"
						data-name="${data.name}"
						disabled
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px;opacity:0.5;cursor:not-allowed">
						<i class="fa fa-lock"></i> ${__("Upload KYC First")}
					</button>`;
			} else {
				action_btn = `
					<div class="ch-bb-info-note" style="background:#d1fae5;border-color:#10b981;color:#065f46">
						<i class="fa fa-check-circle"></i>
						${__("KYC verified ✓")} ${data.kyc_id_number ? ` — ${frappe.utils.escape_html(data.kyc_id_type || "")}: <strong>${frappe.utils.escape_html(data.kyc_id_number)}</strong>` : ""}
					</div>
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
					<div style="margin-bottom:14px;padding:10px 12px;border:1px solid var(--border-color);border-radius:var(--pos-radius,8px);background:var(--subtle-fg)">
						<label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer;font-weight:600;margin-bottom:6px">
							<input type="checkbox" class="ch-bb-walkin-lock-cleared" style="margin-top:3px;width:16px;height:16px">
							<span>${__("FRP / iCloud Lock Cleared (Factory Reset Confirmed)")}</span>
						</label>
						<div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">
							${__("Walk-in skips a separate inspection step — confirm this here before creating the order.")}
						</div>
						<textarea class="form-control form-control-sm ch-bb-walkin-lock-notes" rows="1"
							style="border-radius:6px;font-size:12px"
							placeholder="${__("e.g. customer signed out in-store")}"></textarea>
					</div>
					<button class="btn btn-primary btn-lg ch-bb-act ch-bb-create-walkin-order"
						data-name="${data.name}"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
						<i class="fa fa-check-circle"></i> ${__("Create Order & Send for Approval")}
					</button>`;
			}

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

	_html_inspect_inline(ins, data) {
		const quoted = ins.quoted_price || (data && data.quoted_price) || 0;
		const revised = ins.revised_price || quoted;
		const is_completed = ins.status === "Completed";
		const is_mobile = data && data.source === "Mobile App";

		const grade_options = (ins.grades || []).map(g =>
			`<option value="${frappe.utils.escape_html(g.name)}"
				${(ins.post_inspection_grade || ins.condition_grade || ins.pre_inspection_grade) === g.name ? "selected" : ""}>
				${frappe.utils.escape_html(g.label)} (${g.name})
			</option>`
		).join("");

		const responses = ins.responses || [];
		let comparison_html = "";
		if (responses.length) {
			const rows = responses.map((r, i) => {
				const cust_label = r.assessment_answer_label || r.assessment_answer || "—";
				const cust_impact = r.assessment_impact || 0;
				const insp_answer = r.inspector_answer || "";
				const insp_impact = r.inspector_impact || 0;
				const has_mismatch = insp_answer && insp_answer !== r.assessment_answer;
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
							${has_mismatch ? '<i class="fa fa-exclamation-triangle" style="color:#f97316"></i>'
								: (insp_answer ? '<i class="fa fa-check" style="color:#22c55e"></i>' : '<i class="fa fa-arrow-right" style="opacity:0.3"></i>')}
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
					<span class="ch-pos-badge badge-${assess === "Pass" ? "success" : assess === "Fail" ? "danger" : "muted"}">${__(assess)}</span>
					<i class="fa fa-arrow-right" style="opacity:0.3;font-size:10px"></i>
					<span class="ch-pos-badge badge-${insp === "Pass" ? "success" : insp === "Fail" ? "danger" : "muted"}">${insp ? __(insp) : "—"}</span>
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

		return `
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
			<div class="ch-ins-eval-panel">
				<div class="ch-ins-section-header" style="margin-bottom:10px">
					<i class="fa fa-user-md"></i>
					<span>${__("Inspector Decision")}</span>
				</div>
				<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
					<div>
						<label class="ch-ins-field-label">
							${__("Final Condition Grade")} <span style="color:#e53e3e">*</span>
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
						placeholder="${__("Required if price differs from quoted")}"
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

	_html_inspect_awaiting(data) {
		const order = data.order;
		const price = order ? order.final_price : (data.quoted_price || data.estimated_price);
		const order_name = order ? frappe.utils.escape_html(order.name) : "";
		return `
			<div class="ch-bb-valuation-banner" style="background:#fef3c7;border-color:#f59e0b">
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

	_html_approve(data) {
		const order = data.order;
		const ins = data.inspection;
		const kyc_done = _kyc_done(data);

		// Inspection completed but order not yet created — KYC gate applies here
		if (!order && ins && ins.status === "Completed") {
			const price = ins.revised_price || ins.quoted_price || data.quoted_price || data.estimated_price;
			const grade = ins.post_inspection_grade || ins.condition_grade || ins.pre_inspection_grade || "";

			if (!kyc_done) {
				// KYC blocks order creation — show upload panel + locked button
				return `
					<div class="ch-bb-valuation-banner"
						style="background:#d1fae5;border-color:#10b981">
						<div class="ch-bb-val-label" style="color:#065f46">
							<i class="fa fa-check-circle"></i> ${__("Inspection Complete")}
						</div>
						<div class="ch-bb-val-amount" style="color:#065f46">₹${format_number(price)}</div>
						<div class="ch-bb-val-sub">
							${grade ? __("Grade") + " " + frappe.utils.escape_html(grade) + " · " : ""}
							${frappe.utils.escape_html(data.customer_name || data.mobile_no || "—")}
						</div>
					</div>
					<div class="ch-bb-info-note" style="margin-top:12px;background:#fef3c7;border-color:#f59e0b;color:#92400e">
						<i class="fa fa-exclamation-triangle"></i>
						${__("KYC pending — Aadhaar must be uploaded before creating the order")}
					</div>
					<div class="ch-bb-actions" style="margin-top:14px;flex-direction:column;gap:10px">
						<button class="btn btn-warning btn-lg ch-bb-act ch-bb-upload-kyc"
							data-name="${frappe.utils.escape_html(data.name)}"
							style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px;background:#f59e0b;border-color:#f59e0b;color:#fff">
							<i class="fa fa-id-card"></i> ${__("Upload Customer KYC (Aadhaar)")}
						</button>
						<button class="btn btn-primary btn-lg ch-bb-act ch-bb-create-order-from-inspection"
							data-name="${frappe.utils.escape_html(data.name)}"
							data-inspection="${frappe.utils.escape_html(ins.name)}"
							data-price="${price}"
							data-grade="${frappe.utils.escape_html(grade)}"
							disabled
							style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px;opacity:0.5;cursor:not-allowed">
							<i class="fa fa-lock"></i> ${__("Upload KYC First")}
						</button>
					</div>`;
			}

			// KYC done — clean order-creation flow
			return `
				<div class="ch-bb-valuation-banner"
					style="background:#d1fae5;border-color:#10b981">
					<div class="ch-bb-val-label" style="color:#065f46">
						<i class="fa fa-check-circle"></i> ${__("Inspection Complete")}
					</div>
					<div class="ch-bb-val-amount" style="color:#065f46">₹${format_number(price)}</div>
					<div class="ch-bb-val-sub">
						${grade ? __("Grade") + " " + frappe.utils.escape_html(grade) + " · " : ""}
						${frappe.utils.escape_html(data.customer_name || data.mobile_no || "—")}
					</div>
				</div>
				<div class="ch-bb-info-note" style="margin-top:12px;background:#d1fae5;border-color:#10b981;color:#065f46">
					<i class="fa fa-check-circle"></i>
					${__("KYC verified ✓")} ${data.kyc_id_number ? `— ${frappe.utils.escape_html(data.kyc_id_type || "")}: <strong>${frappe.utils.escape_html(data.kyc_id_number)}</strong>` : ""}
				</div>
				<div class="ch-bb-actions" style="margin-top:14px;flex-direction:column;gap:10px">
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
						: __("Ask the customer for the OTP they received and resume the approval wizard.")}
				</div>
				<div class="ch-bb-actions" style="margin-top:14px;flex-direction:column;gap:8px">
					<button class="btn btn-primary btn-lg ch-bb-act ch-bb-approve-instor"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:48px">
						<i class="fa fa-arrow-right"></i> ${__("Continue Customer Approval")}
					</button>
				</div>`;
		}

		const price_banner = `
			<div class="ch-bb-valuation-banner"
				style="background:#d1fae5;border-color:#10b981">
				<div class="ch-bb-val-label" style="color:#065f46">
					${is_waiting
						? `<i class="fa fa-clock-o"></i> ${__("Awaiting Customer Approval")}`
						: __("Inspection Complete — Get Customer Approval")}
				</div>
				<div class="ch-bb-val-amount" style="color:#065f46">₹${format_number(price)}</div>
				${is_waiting
					? `<div class="ch-bb-val-sub">${__("Link sent to")} <b>${masked}</b></div>`
					: `<div class="ch-bb-val-sub">${__("Customer:")} ${frappe.utils.escape_html(data.customer_name || mobile || "—")}</div>`}
			</div>`;

		if (is_waiting) {
			return `${price_banner}
				<div class="ch-bb-info-note" style="margin-top:12px;background:#fef9c3;border-color:#facc15;color:#713f12">
					<i class="fa fa-hourglass-half"></i>
					${__("The approval link has been sent. Waiting for the customer to tap and approve.")}
					${approval_url ? `<br><small style="word-break:break-all;opacity:0.7">${frappe.utils.escape_html(approval_url)}</small>` : ""}
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

		return `${price_banner}
			<div class="ch-bb-info-note" style="margin-top:12px">
				<i class="fa fa-info-circle"></i>
				${__("Share the approval link with the customer.")}
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
				<button class="btn btn-outline-warning ch-bb-act ch-bb-raise-price-exc"
					data-order="${order ? frappe.utils.escape_html(order.name) : ""}"
					data-price="${price}"
					style="width:100%;border-radius:var(--pos-radius,8px)">
					<i class="fa fa-gavel"></i> ${__("Raise Price Exception")}
				</button>
			</div>`;
	}

	_esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	// Show price exceptions raised against this Buyback Order, with live status,
	// at the top of the stage body — so POS reflects them like other exceptions.
	_render_order_exceptions(el, order_name) {
		frappe.xcall("frappe.client.get_list", {
			doctype: "CH Exception Request",
			filters: { reference_doctype: "Buyback Order", reference_name: order_name },
			fields: ["name", "exception_type", "status", "requested_value", "resolution_value"],
			order_by: "creation desc",
			limit_page_length: 5,
		}).then((rows) => {
			if (!rows || !rows.length) return;
			const color = {
				Pending: "#b45309", Escalated: "#b45309", Approved: "#065f46",
				"Auto-Approved": "#065f46", Rejected: "#b91c1c", Expired: "#6b7280",
			};
			const items = rows.map((r) => {
				const c = color[r.status] || "#6b7280";
				const amt = flt(r.resolution_value) || flt(r.requested_value);
				return `<div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;padding:3px 0">
						<a class="ch-bb-open-exc" data-name="${frappe.utils.escape_html(r.name)}" href="#" style="color:inherit">
							<i class="fa fa-gavel"></i> ${frappe.utils.escape_html(r.exception_type)} — ₹${format_number(amt)}
						</a>
						<span style="font-weight:700;color:${c}">${frappe.utils.escape_html(r.status)}</span>
					</div>`;
			}).join("");
			const banner = `<div class="ch-bb-info-note" style="margin-bottom:12px;background:#fffbeb;border-color:#f59e0b">
					<div style="font-weight:700;margin-bottom:4px;color:#92400e">
						<i class="fa fa-gavel"></i> ${__("Price Exceptions")}
					</div>${items}</div>`;
			const body = el.find(".ch-bb-stage-body");
			body.prepend(banner);
			body.find(".ch-bb-open-exc").on("click", (e) => {
				e.preventDefault();
				frappe.set_route("Form", "CH Exception Request", $(e.currentTarget).data("name"));
			});
		}).catch(() => {});
	}

	_selected(actual, expected) {
		return actual === expected ? "selected" : "";
	}

	_html_order_summary(order) {
		if (!order) return "";
		const rows = [];
		if (order.customer_approval_method) rows.push([__("Approval"), order.customer_approval_method]);
		if (order.approval_date) rows.push([__("Approved At"), order.approval_date]);
		if (order.approval_remarks) rows.push([__("Approval Notes"), order.approval_remarks]);
		if (order.settlement_type) rows.push([__("Settlement"), order.settlement_type]);
		if (order.customer_payout_mode) rows.push([__("Payout Mode"), order.customer_payout_mode]);
		if (order.customer_payout_mode === "Cash" && order.customer_cash_receiver_name) {
			rows.push([__("Cash Receiver"), order.customer_cash_receiver_name]);
		}
		if (order.customer_payout_mode === "UPI" && order.customer_upi_id) {
			rows.push([__("UPI ID"), order.customer_upi_id]);
		}
		if (order.customer_payout_mode === "Bank Transfer") {
			if (order.customer_bank_account_holder) rows.push([__("Account Holder"), order.customer_bank_account_holder]);
			if (order.customer_bank_account_number) {
				const acct = String(order.customer_bank_account_number);
				rows.push([__("Account"), acct.length > 4 ? `**** ${acct.slice(-4)}` : acct]);
			}
			if (order.customer_bank_ifsc) rows.push([__("IFSC"), order.customer_bank_ifsc]);
			if (order.customer_bank_name) rows.push([__("Bank"), order.customer_bank_name]);
		}
		if (order.customer_payout_updated_at) rows.push([__("Payout Updated"), order.customer_payout_updated_at]);
		if (order.customer_payout_updated_by) rows.push([__("Updated By"), order.customer_payout_updated_by]);
		if (order.kyc_verified) rows.push([__("KYC"), order.kyc_verified_at ? __("Verified at {0}", [order.kyc_verified_at]) : __("Verified")]);
		if (!rows.length) return "";

		return `<div class="ch-bb-info-note" style="margin-top:12px;background:#f8fafc;border-color:#cbd5e1;color:#334155">
			<div style="font-weight:700;margin-bottom:8px">
				<i class="fa fa-info-circle"></i> ${__("Saved Customer Selection")}
			</div>
			<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 14px;font-size:12px;line-height:1.35">
				${rows.map(([label, value]) => `
					<div>
						<div style="color:#64748b;font-weight:600">${this._esc(label)}</div>
						<div style="font-weight:700;color:#0f172a;word-break:break-word">${this._esc(value)}</div>
					</div>
				`).join("")}
			</div>
		</div>`;
	}

	_html_settle(data) {
		const order = data.order;
		const price = order ? order.final_price : (data.quoted_price || data.estimated_price);
		const saved_settlement = order && order.settlement_type ? order.settlement_type : "";
		const indemnity_ok = !!(order && order.indemnity_signed);
		const order_name = order ? order.name : "";
		const summary = this._html_order_summary(order);

		const indemnity_banner = !indemnity_ok ? `
			<div class="ch-bb-info-note" style="background:#fffbeb;border-color:#fbbf24;color:#92400e;margin-top:12px;display:flex;align-items:center;justify-content:space-between;gap:10px">
				<div>
					<i class="fa fa-exclamation-triangle"></i>
					<strong>${__("Indemnity / NOC required before settlement")}</strong><br>
					<span style="font-size:12px">${__("Capture the customer's signed declaration of ownership first.")}</span>
				</div>
				<button class="btn btn-warning ch-bb-record-indemnity"
					data-order="${frappe.utils.escape_html(order_name)}"
					style="border-radius:var(--pos-radius,8px);white-space:nowrap;min-height:36px;font-size:12px;font-weight:700">
					<i class="fa fa-file-signature"></i> ${__("Record Indemnity")}
				</button>
			</div>` : `
			<div class="ch-bb-info-note" style="background:#f0fdf4;border-color:#86efac;color:#166534;margin-top:12px">
				<i class="fa fa-check-circle"></i> <strong>${__("Indemnity / NOC captured")}</strong>
			</div>`;

		return `
			<div class="ch-bb-valuation-banner" style="background:#f0f9ff;border-color:#0ea5e9">
				<div class="ch-bb-val-label" style="color:#0284c7">
					${__("Customer Approved ✓ — Choose Settlement")}
				</div>
				<div class="ch-bb-val-amount" style="color:#0284c7">₹${format_number(price)}</div>
				${saved_settlement
					? `<div class="ch-bb-val-sub">${__("Customer selected")} <b>${this._esc(saved_settlement)}</b></div>`
					: ""}
			</div>
			${summary}
			${indemnity_banner}
			<div class="ch-bb-section-label" style="margin-top:14px">
				${__("How does the customer want to receive value?")}
			</div>
			<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px">
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
							style="border-radius:var(--pos-radius,8px)"
							${!indemnity_ok ? "disabled" : ""}>
							<option value="Cash">${__("Cash")}</option>
							<option value="UPI">${__("UPI")}</option>
							<option value="Bank Transfer">${__("Bank Transfer")}</option>
						</select>
					</div>
					<button class="btn btn-warning ch-bb-act ch-bb-cashback"
						style="width:100%;border-radius:var(--pos-radius,8px);font-weight:700;min-height:44px"
						data-price="${price}"
						${!indemnity_ok ? "disabled title='" + __("Record Indemnity first") + "'" : ""}>
						<i class="fa fa-money"></i> ${__("Settle as Cashback")}
					</button>
				</div>
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
						data-grade="${data.estimated_grade || ""}"
						${!indemnity_ok ? "disabled title='" + __("Record Indemnity first") + "'" : ""}>
						<i class="fa fa-exchange"></i> ${__("Add to Cart & Sell")}
					</button>
				</div>
			</div>`;
	}

	// ─────────────────────────────── stage: CLOSED (order rejected / assessment cancelled) ──
	// Shown when the Buyback Order has been auto-rejected (e.g. Sanchar Saathi
	// IMEI check returned Blacklisted / Duplicate IMEI / Already In Use, which
	// triggers BuybackOrder.submit_imei_validation → status = "Rejected"), or
	// when the Buyback Assessment itself was cancelled / expired. The previous
	// behaviour had no terminal-state branch in _determine_stage, so a rejected
	// order whose assessment was already "Inspection Created" was routed back
	// to the INSPECT panel — the UI appeared to send staff into the inspection
	// form even though the order was closed.
	_html_closed(data) {
		const order = data.order;
		const is_order_terminal = !!order && ["Rejected", "Cancelled"].includes(order.status || "");
		const terminal_status = is_order_terminal ? order.status : (data.status || "Cancelled");
		const is_rejected = terminal_status === "Rejected";

		// Build the human-readable reason — prefer the explicit approval/IMEI
		// remarks; fall back to a generic "no reason recorded" line so the
		// panel never looks empty.
		const imei_status = (order && order.imei_validation_status) || data.imei_validation_status || "";
		const imei_remarks = (order && order.imei_validation_remarks) || data.imei_validation_remarks || "";
		const approval_remarks = (order && order.approval_remarks) || "";
		const reason_parts = [];
		if (approval_remarks) reason_parts.push(frappe.utils.escape_html(approval_remarks));
		if (imei_status && imei_status !== "Pending" && imei_status !== "Verified Clean") {
			reason_parts.push(__("Sanchar Saathi IMEI check") + ": <strong>" + frappe.utils.escape_html(imei_status) + "</strong>");
		}
		if (imei_remarks && !approval_remarks.includes(imei_remarks)) {
			reason_parts.push(frappe.utils.escape_html(imei_remarks));
		}
		const reason_html = reason_parts.length
			? reason_parts.map(r => `<li>${r}</li>`).join("")
			: `<li>${__("No additional reason recorded.")}</li>`;

		const title = is_rejected ? __("Buyback Order Rejected") : __("Buyback Closed");
		const sub = is_rejected
			? __("This order has been rejected and cannot proceed to inspection, KYC, OTP or settlement.")
			: __("This buyback was cancelled. Start a new assessment if the customer wants to retry.");

		const desk_link = order
			? `<a href="#" class="ch-bb-open-order-desk text-muted" data-name="${frappe.utils.escape_html(order.name)}" style="font-size:12px"><i class="fa fa-external-link"></i> ${__("Open Buyback Order")}</a>`
			: `<a href="#" class="ch-bb-open-desk text-muted" data-name="${frappe.utils.escape_html(data.name)}" style="font-size:12px"><i class="fa fa-external-link"></i> ${__("Open Assessment")}</a>`;

		return `
			<div class="ch-bb-valuation-banner" style="background:#fee2e2;border-color:#ef4444">
				<div class="ch-bb-val-label" style="color:#7f1d1d">
					<i class="fa fa-times-circle"></i> ${title}
				</div>
				<div class="ch-bb-val-amount" style="color:#7f1d1d;font-size:18px">
					${frappe.utils.escape_html(terminal_status)}
				</div>
				<div class="ch-bb-val-sub" style="color:#7f1d1d">${sub}</div>
			</div>
			<div class="ch-bb-info-note" style="background:#fef2f2;border-color:#fecaca;color:#7f1d1d;margin-top:12px">
				<div style="font-weight:700;margin-bottom:6px">
					<i class="fa fa-info-circle"></i> ${__("Reason")}
				</div>
				<ul style="margin:0;padding-left:20px">${reason_html}</ul>
			</div>
			<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
				${desk_link}
			</div>
		`;
	}

	_html_done(data) {
		const order = data.order;
		const price = order ? order.final_price : 0;
		const order_name = order ? order.name : "";

		// Phase B — Compliance surface
		// Show indemnity + data-wipe + pickup state so the store agent knows
		// whether the order is still open on the back-office side (Cashify /
		// Samsung / Best Buy show this on their POS after payout too).
		const indemnity_ok = !!(order && order.indemnity_signed);
		const wipe_ok = !!(order && order.data_wipe_certificate);
		const pickup_name = order && order.latest_pickup_appointment;
		const pickup_attempts = order ? (order.pickup_attempts_count || 0) : 0;

		const chip = (ok, label_ok, label_pending) =>
			`<span class="ch-pos-badge ${ok ? "badge-success" : "badge-warning"}"
				style="font-size:10px">
				<i class="fa fa-${ok ? "check-circle" : "exclamation-triangle"}"></i>
				${ok ? __(label_ok) : __(label_pending)}
			</span>`;

		const compliance_chips = order ? `
			<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin-top:10px">
				${chip(indemnity_ok, "Indemnity Captured", "Indemnity Pending")}
				${chip(wipe_ok, "Data Wiped", "Data Wipe Pending")}
				${pickup_name
					? `<span class="ch-pos-badge badge-primary" style="font-size:10px">
						<i class="fa fa-truck"></i>
						${__("Pickup")} ${frappe.utils.escape_html(pickup_name)}
						${pickup_attempts > 1 ? ` (${pickup_attempts})` : ""}
					</span>`
					: `<span class="ch-pos-badge badge-muted" style="font-size:10px">
						<i class="fa fa-truck"></i> ${__("No Pickup Scheduled")}
					</span>`}
			</div>` : "";

		const compliance_actions = order ? `
			<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;justify-content:center">
				${!indemnity_ok ? `
					<button class="btn btn-outline-primary ch-bb-record-indemnity"
						data-order="${frappe.utils.escape_html(order_name)}"
						style="border-radius:var(--pos-radius,8px);min-height:36px;font-size:12px">
						<i class="fa fa-file-signature"></i> ${__("Record Indemnity")}
					</button>` : ""}
				${!pickup_name && pickup_attempts < 3 ? `
					<button class="btn btn-outline-primary ch-bb-schedule-pickup"
						data-order="${frappe.utils.escape_html(order_name)}"
						style="border-radius:var(--pos-radius,8px);min-height:36px;font-size:12px">
						<i class="fa fa-truck"></i> ${__("Schedule Pickup")}
					</button>` : ""}
				${!wipe_ok ? `
					<button class="btn btn-outline-primary ch-bb-record-wipe"
						data-order="${frappe.utils.escape_html(order_name)}"
						style="border-radius:var(--pos-radius,8px);min-height:36px;font-size:12px">
						<i class="fa fa-eraser"></i> ${__("Record Data Wipe")}
					</button>` : ""}
			</div>` : "";

		return `
			<div class="ch-bb-valuation-banner" style="background:#d1fae5;border-color:#10b981">
				<div class="ch-bb-val-label" style="color:#065f46">
					<i class="fa fa-check-circle"></i> ${__("Buyback Complete")}
				</div>
				<div class="ch-bb-val-amount" style="color:#065f46">₹${format_number(price)}</div>
				<div class="ch-bb-val-sub">
					${__("Order")} ${order_name} · ${order ? __(order.status) : ""}
				</div>
				${compliance_chips}
			</div>
			${compliance_actions}
			<div style="text-align:center;margin-top:16px;display:flex;justify-content:center;gap:10px;flex-wrap:wrap">
				<button class="btn btn-primary ch-bb-print-receipt"
					data-order="${order_name}"
					style="border-radius:var(--pos-radius,8px);font-weight:600;min-height:40px">
					<i class="fa fa-print"></i> ${__("Print Receipt")}
				</button>
				<button class="btn btn-outline-secondary ch-bb-open-desk" data-name="${data.name}"
					style="border-radius:var(--pos-radius,8px);min-height:40px">
					<i class="fa fa-external-link"></i> ${__("View in Desk")}
				</button>
			</div>`;
	}

	_bind_stage_actions(el, data, stage) {
		el.off(".bbstage");

		// ── IMEI / Sanchar Saathi check card (ASSESS stage) ─────────
		let imei_card_upload_url = null;
		el.find(".ch-bb-imei-file-drop").each(function () {
			const $drop = $(this);
			const $input = $drop.find('input[type="file"]');
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
					btn.prop("disabled", false).html(`<i class="fa fa-check"></i> ${__("Submit Assessment")}`);
				});
		});

		el.on("click.bbstage", ".ch-bb-open-assessment", (e) => {
			frappe.set_route("Form", "Buyback Assessment", $(e.currentTarget).data("name"));
		});

		el.on("click.bbstage", ".ch-bb-upload-kyc", () => {
			this._show_kyc_upload_dialog(data);
		});

		el.on("click.bbstage", ".ch-bb-start-inspection", (e) => {
			const btn = $(e.currentTarget);
			if (btn.prop("disabled")) {
				frappe.show_alert({ message: __("Action blocked"), indicator: "orange" });
				return;
			}
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Loading...")}`);

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
				btn.prop("disabled", false).html(`<i class="fa fa-search-plus"></i> ${__("Inspect Device")}`);
			});
		});

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
			const $lock_cb = el.find(".ch-bb-ins-lock-cleared");
			if (!$lock_cb.length) {
				// Defensive: the FRP/iCloud confirmation control is mandatory for
				// inspection completion. If it has been removed from the rendered
				// stage UI by an upstream change, fail loudly instead of silently
				// blocking the user with a generic "unchecked" message.
				console.error("[buyback] FRP/iCloud lock control (.ch-bb-ins-lock-cleared) missing from inspection stage UI");
				frappe.msgprint({
					title: __("UI configuration error"),
					indicator: "red",
					message: __("FRP / iCloud Lock confirmation control is missing from this stage. Please reload the POS and contact IT if it persists."),
				});
				return;
			}
			const lock_cleared = $lock_cb.is(":checked");
			if (!lock_cleared) {
				frappe.show_alert({ message: __("Confirm FRP / iCloud Lock Cleared before completing inspection"), indicator: "orange" });
				return;
			}
			const lock_notes = el.find(".ch-bb-ins-lock-notes").val() || "";

			const btn = $(e.currentTarget);
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Completing...")}`);
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
				btn.prop("disabled", false).html(`<i class="fa fa-check-circle"></i> ${__("Complete Inspection & Create Order")}`);
			});
		});

		el.on("click.bbstage", ".ch-bb-inspection-to-desk, .ch-bb-open-inspection-form", (e) => {
			frappe.set_route("Form", "Buyback Inspection", $(e.currentTarget).data("inspection"));
		});

		// Create order from completed inspection — strict KYC enforcement
		el.on("click.bbstage", ".ch-bb-create-order-from-inspection", (e) => {
			const btn = $(e.currentTarget);
			if (btn.prop("disabled")) {
				frappe.show_alert({
					message: __("Please upload KYC (Aadhaar) first"),
					indicator: "orange",
				});
				return;
			}
			if (!_kyc_done(data)) {
				frappe.show_alert({
					message: __("KYC Aadhaar image is mandatory — upload now"),
					indicator: "red",
				});
				this._show_kyc_upload_dialog(data);
				return;
			}
			const price = parseFloat(btn.data("price")) || 0;
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Creating Order...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_start_buyback_order", {
				assessment_name: data.name,
				pos_profile: PosState.pos_profile || "",
				final_price: price,
			}).then(() => {
				frappe.show_alert({ message: __("Order created — send for approval"), indicator: "green" });
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false).html(`<i class="fa fa-check-circle"></i> ${__("Create Order & Send for Approval")}`);
			});
		});

		// Walk-in order — strict KYC enforcement
		el.on("click.bbstage", ".ch-bb-create-walkin-order", (e) => {
			const btn = $(e.currentTarget);
			if (btn.prop("disabled")) {
				frappe.show_alert({
					message: __("Please upload KYC (Aadhaar) first"),
					indicator: "orange",
				});
				return;
			}
			if (!_kyc_done(data)) {
				frappe.show_alert({
					message: __("KYC Aadhaar image is mandatory — upload now"),
					indicator: "red",
				});
				this._show_kyc_upload_dialog(data);
				return;
			}
			const price = parseFloat(el.find(".ch-bb-walkin-price").val()) || 0;
			if (price <= 0) {
				frappe.show_alert({ message: __("Enter a valid buyback price"), indicator: "orange" });
				return;
			}
			const $lock_cb = el.find(".ch-bb-walkin-lock-cleared");
			if (!$lock_cb.length) {
				// Defensive: walk-in skips a separate Buyback Inspection record,
				// so this control is the ONLY place lock-clearance can be captured
				// before the server-side gate (BuybackOrder._validate_lock_clearance_before_kyc).
				// If a UI regression removes it, surface a clear error instead of
				// blocking the cashier with the generic "unchecked" message.
				console.error("[buyback] FRP/iCloud lock control (.ch-bb-walkin-lock-cleared) missing from walk-in stage UI");
				frappe.msgprint({
					title: __("UI configuration error"),
					indicator: "red",
					message: __("FRP / iCloud Lock confirmation control is missing from this stage. Please reload the POS and contact IT if it persists."),
				});
				return;
			}
			const lock_cleared = $lock_cb.is(":checked");
			if (!lock_cleared) {
				frappe.show_alert({ message: __("Confirm FRP / iCloud Lock Cleared before creating the order"), indicator: "orange" });
				return;
			}
			const lock_notes = el.find(".ch-bb-walkin-lock-notes").val() || "";
			btn.prop("disabled", true)
				.html(`<i class="fa fa-spinner fa-spin"></i> ${__("Creating Order...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_start_buyback_order", {
				assessment_name: data.name,
				pos_profile: PosState.pos_profile || "",
				final_price: price,
				account_lock_cleared: 1,
				account_lock_check_notes: lock_notes,
			}).then(() => {
				frappe.show_alert({ message: __("Order created"), indicator: "green" });
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false).html(`<i class="fa fa-check-circle"></i> ${__("Create Order & Send for Approval")}`);
			});
		});

		el.on("click.bbstage", ".ch-bb-manager-approve", (e) => {
			const order_name = $(e.currentTarget).data("order");
			if (!order_name) return;
			frappe.confirm(
				__("Confirm manager approval for order {0}?", [order_name]),
				() => {
					frappe.xcall("buyback.api.approve_order", {
						order_name, remarks: "Approved in-store via POS",
					}).then(() => {
						frappe.show_alert({ message: __("Order approved"), indicator: "green" });
						this._reload();
					});
				}
			);
		});

		el.on("click.bbstage", ".ch-bb-refresh-stage", () => { this._reload(); });

		el.on("click.bbstage", ".ch-bb-send-link, .ch-bb-resend-link", (e) => {
			const btn = $(e.currentTarget);
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Sending...")}`);
			frappe.xcall("ch_pos.api.pos_api.pos_send_approval_link", {
				order_name: data.order.name,
			}).then((res) => {
				frappe.show_alert({
					message: __("Approval link sent to {0}", [res.mobile_masked]),
					indicator: "green",
				});
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false).html(`<i class="fa fa-paper-plane"></i> ${__("Send Approval Link")}`);
			});
		});

		el.on("click.bbstage", ".ch-bb-approve-instor", () => {
			if (!data.order || !data.order.name) {
				frappe.show_alert({ message: __("No Buyback Order found — complete inspection first"), indicator: "orange" });
				return;
			}
			this._show_instore_approval_dialog(data);
		});

		// Store-initiated price negotiation → routes to Buyback Manager, who
		// approves it; on approval the order's payout is updated (close-the-loop).
		el.on("click.bbstage", ".ch-bb-raise-price-exc", (e) => {
			const order_name = $(e.currentTarget).data("order") || (data.order && data.order.name);
			const current = flt($(e.currentTarget).data("price"));
			if (!order_name) {
				frappe.show_alert({ message: __("No Buyback Order found — complete inspection first"), indicator: "orange" });
				return;
			}
			const d = new frappe.ui.Dialog({
				title: __("Raise Price Exception"),
				fields: [
					{ fieldname: "current", fieldtype: "Currency", label: __("Current Price"),
					  default: current, read_only: 1 },
					{ fieldname: "requested_price", fieldtype: "Currency", label: __("Requested Price"),
					  reqd: 1, default: current },
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1,
					  description: __("Sent to the Buyback Manager for approval.") },
				],
				primary_action_label: __("Submit to Manager"),
				primary_action: (v) => {
					if (!v.reason || !flt(v.requested_price)) {
						frappe.show_alert({ message: __("Enter a price and a reason"), indicator: "orange" });
						return;
					}
					d.hide();
					frappe.xcall("buyback.api.raise_buyback_exception", {
						order: order_name,
						requested_price: flt(v.requested_price),
						reason: v.reason,
					}).then((res) => {
						frappe.show_alert({
							message: __("Price exception {0} sent to Buyback Manager.", [res.name]),
							indicator: "orange",
						});
						this._reload();
					});
				},
			});
			d.show();
		});

		el.on("click.bbstage", ".ch-bb-cashback", (e) => {
			const price = flt($(e.currentTarget).data("price"));
			const payment_method = el.find(".ch-bb-cashback-mode").val();
			const btn = el.find(".ch-bb-cashback");
			btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i>`);
			frappe.xcall("ch_pos.api.pos_api.pos_settle_buyback_cashback", {
				order_name: data.order.name, payment_method,
			}).then(() => {
				frappe.show_alert({
					message: __("Cashback ₹{0} recorded via {1}", [format_number(price), payment_method]),
					indicator: "green",
				});
				this._reload();
			}).catch(() => {
				btn.prop("disabled", false).html(`<i class="fa fa-money"></i> ${__("Settle as Cashback")}`);
			});
		});

		el.on("click.bbstage", ".ch-bb-exchange", (e) => {
			const btn = $(e.currentTarget);
			PosState.exchange_assessment = btn.data("name");
			PosState.exchange_order = btn.data("order-name");
			PosState.exchange_amount = flt(btn.data("amount"));
			EventBus.emit("exchange:applied", {
				assessment: btn.data("name"),
				buyback_amount: flt(btn.data("amount")),
				item_name: btn.data("item-name"),
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

		// ── Phase B — Indemnity capture from POS ────────────────────
		el.on("click.bbstage", ".ch-bb-record-indemnity", (e) => {
			const order_name = $(e.currentTarget).data("order");
			if (!order_name) return;
			const cust_name = (data.order && data.order.customer_name)
				|| data.customer_name || "";
			const d = new frappe.ui.Dialog({
				title: __("Record Customer Indemnity / NOC"),
				fields: [
					{
						fieldname: "signed_by_name", fieldtype: "Data",
						label: __("Signed By (Customer Name)"),
						reqd: 1, default: cust_name,
					},
					{
						fieldname: "signature_type", fieldtype: "Select",
						label: __("Signature Type"),
						options: [
							"E-Signature (Kiosk)",
							"Wet Signature Scanned",
							"Aadhaar OTP Consent",
							"Digilocker eSign",
						].join("\n"),
						reqd: 1, default: "E-Signature (Kiosk)",
					},
					{
						fieldname: "attachment", fieldtype: "Attach",
						label: __("Signed Document (Optional)"),
					},
					{
						fieldname: "notes", fieldtype: "Small Text",
						label: __("Notes"),
					},
				],
				primary_action_label: __("Record"),
				primary_action: (v) => {
					d.hide();
					frappe.call({
						method: "buyback.lifecycle_api.record_indemnity",
						args: {
							order_name: order_name,
							signed_by_name: v.signed_by_name,
							signature_type: v.signature_type,
							attachment: v.attachment,
							notes: v.notes,
						},
						freeze: true,
						freeze_message: __("Recording indemnity…"),
						callback: () => {
							frappe.show_alert({
								message: __("Indemnity captured."),
								indicator: "green",
							});
							this._reload();
						},
					});
				},
			});
			d.show();
		});

		// ── Phase B — Schedule Pickup from POS ──────────────────────
		el.on("click.bbstage", ".ch-bb-schedule-pickup", (e) => {
			const order_name = $(e.currentTarget).data("order");
			if (!order_name) return;
			const mobile = (data.order && data.order.mobile_no)
				|| data.mobile_no || "";
			const d = new frappe.ui.Dialog({
				title: __("Schedule Pickup Appointment"),
				fields: [
					{
						fieldname: "appointment_date", fieldtype: "Date",
						label: __("Appointment Date"), reqd: 1,
						default: frappe.datetime.add_days(
							frappe.datetime.get_today(), 1),
					},
					{
						fieldname: "appointment_slot", fieldtype: "Select",
						label: __("Slot"),
						options: [
							"",
							"09:00 - 12:00",
							"12:00 - 15:00",
							"15:00 - 18:00",
							"18:00 - 21:00",
						].join("\n"),
					},
					{
						fieldname: "pickup_address", fieldtype: "Small Text",
						label: __("Pickup Address"),
						description: __(
							"Leave blank to auto-fill from customer's primary address."),
					},
					{
						fieldname: "contact_phone", fieldtype: "Data",
						label: __("Contact Phone"), default: mobile,
					},
					{
						fieldname: "vendor_partner", fieldtype: "Data",
						label: __("Vendor Partner"),
						description: __(
							"e.g. Delhivery, Shadowfax, Porter, Own Fleet"),
					},
					{
						fieldname: "remarks", fieldtype: "Small Text",
						label: __("Remarks"),
					},
				],
				primary_action_label: __("Schedule"),
				primary_action: (v) => {
					d.hide();
					frappe.call({
						method: "buyback.lifecycle_api.schedule_pickup",
						args: Object.assign({ order_name: order_name }, v),
						freeze: true,
						freeze_message: __("Scheduling pickup…"),
						callback: (r) => {
							if (r.message && r.message.appointment) {
								frappe.show_alert({
									message: __(
										"Pickup Appointment {0} created.",
										[r.message.appointment]),
									indicator: "green",
								});
								this._reload();
							}
						},
					});
				},
			});
			d.show();
		});

		// ── Phase B — Data-Wipe Certificate from POS ────────────────
		el.on("click.bbstage", ".ch-bb-record-wipe", (e) => {
			const order_name = $(e.currentTarget).data("order");
			if (!order_name) return;
			const d = new frappe.ui.Dialog({
				title: __("Record Data-Wipe Certificate"),
				fields: [
					{
						fieldname: "wipe_method", fieldtype: "Select",
						label: __("Wipe Method"), reqd: 1,
						options: [
							"Factory Reset",
							"Encrypted Erase",
							"Overwrite (Single Pass)",
							"Overwrite (Multi Pass)",
							"Cryptographic Erase",
							"Physical Destruction",
						].join("\n"),
						default: "Factory Reset",
					},
					{
						fieldname: "wipe_standard", fieldtype: "Select",
						label: __("Wipe Standard"),
						options: [
							"",
							"DoD 5220.22-M",
							"NIST SP 800-88 Clear",
							"NIST SP 800-88 Purge",
							"NIST SP 800-88 Destroy",
							"Gutmann",
							"Vendor Default",
						].join("\n"),
					},
					{
						fieldname: "wipe_tool", fieldtype: "Data",
						label: __("Wipe Tool"),
					},
					{
						fieldname: "wipe_duration_minutes", fieldtype: "Int",
						label: __("Duration (minutes)"),
					},
					{
						fieldname: "evidence_attachment", fieldtype: "Attach",
						label: __("Evidence Report (PDF/Log)"),
					},
					{
						fieldname: "remarks", fieldtype: "Small Text",
						label: __("Remarks"),
					},
				],
				primary_action_label: __("Submit Certificate"),
				primary_action: (v) => {
					d.hide();
					frappe.call({
						method: "buyback.lifecycle_api.record_data_wipe",
						args: Object.assign(
							{ order_name: order_name, submit: 1 }, v),
						freeze: true,
						freeze_message: __("Submitting Data-Wipe Certificate…"),
						callback: (r) => {
							if (r.message && r.message.certificate) {
								frappe.show_alert({
									message: __(
										"Data-Wipe Certificate {0} submitted.",
										[r.message.certificate]),
									indicator: "green",
								});
								this._reload();
							}
						},
					});
				},
			});
			d.show();
		});
	}

	// ─────────────────────────────── KYC Upload Dialog ──
	_show_kyc_upload_dialog(data) {
		const self = this;
		const uploads = {
			customer_id_front: data.customer_id_front || null,
			customer_id_back: data.customer_id_back || null,
			customer_photo: data.customer_photo || null,
		};

		const dlg = new frappe.ui.Dialog({
			title: __("Customer KYC — Aadhaar Verification"),
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "header_html" },
				{ fieldtype: "Section Break", label: __("ID Details") },
				{
					fieldname: "kyc_id_type", fieldtype: "Select", label: __("ID Type"),
					options: "Aadhar Card\nPAN Card\nPassport\nDriving License\nVoter ID",
					default: data.kyc_id_type || "Aadhar Card", reqd: 1,
				},
				{
					fieldname: "kyc_id_number", fieldtype: "Data", label: __("ID Number"),
					default: data.kyc_id_number || "", reqd: 1,
					description: __("Enter 12-digit Proof number"),
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "kyc_name", fieldtype: "Data", label: __("Name on ID"),
					default: data.kyc_name || "",
				},
				{ fieldtype: "Section Break", label: __("Proof / ID Attachment") },
				{ fieldtype: "HTML", fieldname: "upload_html" },
			],
			primary_action_label: __("Save KYC & Continue"),
			primary_action: (values) => {
				if (!uploads.customer_id_front) {
					frappe.show_alert({
						message: __("Proof front image is mandatory — please attach"),
						indicator: "red",
					});
					return;
				}
				if (!values.kyc_id_type || !values.kyc_id_number) {
					frappe.show_alert({
						message: __("ID Type and ID Number are required"),
						indicator: "orange",
					});
					return;
				}
				if (!validate_id_number(values.kyc_id_type, values.kyc_id_number)) {
					return;
				}

				dlg.disable_primary_action();
				dlg.get_primary_btn().html(`<i class="fa fa-spinner fa-spin"></i> ${__("Saving...")}`);

				frappe.xcall("frappe.client.set_value", {
					doctype: "Buyback Assessment",
					name: data.name,
					fieldname: {
						kyc_id_type: values.kyc_id_type,
						kyc_id_number: values.kyc_id_number,
						kyc_name: values.kyc_name || "",
						customer_id_front: uploads.customer_id_front,
						customer_id_back: uploads.customer_id_back || null,
						customer_photo: uploads.customer_photo || null,
					},
				}).then(() => {
					self._kyc_cache[data.name] = {
						customer_id_front: uploads.customer_id_front,
						customer_id_back: uploads.customer_id_back,
						customer_photo: uploads.customer_photo,
						kyc_id_type: values.kyc_id_type,
						kyc_id_number: values.kyc_id_number,
						kyc_name: values.kyc_name || "",
					};

					if (self._current_data && self._current_data.name === data.name) {
						self._current_data.customer_id_front = uploads.customer_id_front;
						self._current_data.customer_id_back = uploads.customer_id_back;
						self._current_data.customer_photo = uploads.customer_photo;
						self._current_data.kyc_id_type = values.kyc_id_type;
						self._current_data.kyc_id_number = values.kyc_id_number;
						self._current_data.kyc_name = values.kyc_name || "";
						self._current_data._kyc_uploaded_local = true;
					}
					data.customer_id_front = uploads.customer_id_front;
					data.customer_id_back = uploads.customer_id_back;
					data.customer_photo = uploads.customer_photo;
					data.kyc_id_type = values.kyc_id_type;
					data.kyc_id_number = values.kyc_id_number;
					data.kyc_name = values.kyc_name || "";
					data._kyc_uploaded_local = true;

					dlg.hide();
					frappe.show_alert({
						message: __("✓ KYC saved — you can now create the order"),
						indicator: "green",
					});

					self._rerender_with_current_data();
				}).catch((e) => {
					dlg.enable_primary_action();
					dlg.get_primary_btn().html(`<i class="fa fa-check"></i> ${__("Save KYC & Continue")}`);
					frappe.show_alert({
						message: _api_error_message(e, __("Failed to save KYC")),
						indicator: "red",
					});
				});
			},
		});

		const device_label = frappe.utils.escape_html(data.item_name || "");
		const mobile = data.mobile_no || "";
		const price = data.quoted_price || data.estimated_price || 0;
		dlg.fields_dict.header_html.$wrapper.html(`
			<div style="background:linear-gradient(135deg,#eef2ff,#f5f3ff);border:1px solid #c7d2fe;border-radius:8px;padding:14px 18px;margin-bottom:6px">
				<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
					<div>
						<div style="font-size:11px;text-transform:uppercase;color:#6366f1;font-weight:700;letter-spacing:.06em">
							${__("Buyback Amount")}
						</div>
						<div style="font-size:24px;font-weight:800;color:#4338ca">₹${format_number(price)}</div>
					</div>
					<div style="text-align:right">
						<div style="font-size:13px;font-weight:600;color:#1f2937">${device_label}</div>
						<div style="font-size:12px;color:#6b7280">${frappe.utils.escape_html(mobile)} · ${data.name}</div>
					</div>
				</div>
				<div style="margin-top:10px;padding:8px 12px;background:#fef3c7;border-radius:6px;font-size:12px;color:#92400e">
					<i class="fa fa-info-circle"></i>
					${__("Proof front image is mandatory before creating the buyback order.")}
				</div>
			</div>
		`);

		const _existing_preview = (url) => url
			? `<img src="${url}" alt="preview" />
				<div class="file-name"><i class="fa fa-check-circle"></i> ${__("Already uploaded")}</div>
				<div class="file-status">${__("Click to replace")}</div>`
			: "";

		dlg.fields_dict.upload_html.$wrapper.html(`
			<style>
				.ch-kyc-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:8px}
				@media(max-width:768px){.ch-kyc-grid{grid-template-columns:1fr}}
				.ch-kyc-box{border:2px dashed #cbd5e1;border-radius:8px;padding:14px;text-align:center;cursor:pointer;
					transition:all .15s;background:#fff;position:relative;min-height:160px;display:flex;
					flex-direction:column;align-items:center;justify-content:center}
				.ch-kyc-box:hover{border-color:#6366f1;background:#f5f3ff}
				.ch-kyc-box.has-file{border-style:solid;border-color:#22c55e;background:#f0fdf4}
				.ch-kyc-box.required{border-color:#f59e0b}
				.ch-kyc-box.required.has-file{border-color:#22c55e}
				.ch-kyc-label{font-size:12px;font-weight:700;color:#374151;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em}
				.ch-kyc-required-tag{display:inline-block;background:#fee2e2;color:#b91c1c;font-size:9px;padding:1px 6px;border-radius:4px;font-weight:700;margin-left:4px;vertical-align:middle}
				.ch-kyc-icon{font-size:26px;color:#94a3b8;margin-bottom:6px}
				.ch-kyc-text{font-size:11px;color:#64748b}
				.ch-kyc-preview{width:100%}
				.ch-kyc-preview img{max-width:100%;max-height:90px;border-radius:4px;margin-bottom:4px;object-fit:cover}
				.ch-kyc-preview .file-name{font-size:11px;color:#15803d;font-weight:600;word-break:break-all}
				.ch-kyc-preview .file-status{font-size:10px;color:#15803d;margin-top:2px}
				.ch-kyc-label,.ch-kyc-default,.ch-kyc-preview{pointer-events:none;user-select:none}
				.ch-kyc-remove{position:absolute;top:6px;right:6px;background:#fff;border:1px solid #e5e7eb;
					border-radius:50%;width:22px;height:22px;display:none;align-items:center;justify-content:center;
					cursor:pointer;color:#ef4444;font-size:11px;z-index:2}
				.ch-kyc-box.has-file .ch-kyc-remove{display:flex}
				.ch-kyc-box.has-file .ch-kyc-default{display:none}
			</style>
			<div class="ch-kyc-grid">
				<div class="ch-kyc-box required ${uploads.customer_id_front ? "has-file" : ""}" data-upload="customer_id_front">
					<div class="ch-kyc-remove" title="${__("Remove")}"><i class="fa fa-times"></i></div>
					<div class="ch-kyc-label">${__("Proof Front")}<span class="ch-kyc-required-tag">${__("REQUIRED")}</span></div>
					<div class="ch-kyc-default">
						<div class="ch-kyc-icon"><i class="fa fa-id-card-o"></i></div>
						<div class="ch-kyc-text">${__("Click or drag image")}</div>
					</div>
					<div class="ch-kyc-preview">${_existing_preview(uploads.customer_id_front)}</div>
					<input type="file" accept="image/*" style="position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;z-index:1" />
				</div>
				<div class="ch-kyc-box ${uploads.customer_id_back ? "has-file" : ""}" data-upload="customer_id_back">
					<div class="ch-kyc-remove" title="${__("Remove")}"><i class="fa fa-times"></i></div>
					<div class="ch-kyc-label">${__("Proof Back")}</div>
					<div class="ch-kyc-default">
						<div class="ch-kyc-icon"><i class="fa fa-id-card"></i></div>
						<div class="ch-kyc-text">${__("Optional")}</div>
					</div>
					<div class="ch-kyc-preview">${_existing_preview(uploads.customer_id_back)}</div>
					<input type="file" accept="image/*" style="position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;z-index:1" />
				</div>
				<div class="ch-kyc-box ${uploads.customer_photo ? "has-file" : ""}" data-upload="customer_photo">
					<div class="ch-kyc-remove" title="${__("Remove")}"><i class="fa fa-times"></i></div>
					<div class="ch-kyc-label">${__("Customer Photo")}</div>
					<div class="ch-kyc-default">
						<div class="ch-kyc-icon"><i class="fa fa-user-circle-o"></i></div>
						<div class="ch-kyc-text">${__("Optional selfie")}</div>
					</div>
					<div class="ch-kyc-preview">${_existing_preview(uploads.customer_photo)}</div>
					<input type="file" accept="image/*" style="position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;z-index:1" />
				</div>
			</div>
		`);

		const $kyc_root = dlg.fields_dict.upload_html.$wrapper;

		$kyc_root.find(".ch-kyc-box").each(function () {
			const $box = $(this);
			const $input = $box.find('input[type="file"]');
			const name = $box.data("upload");

			$box.on("dragover", (e) => {
				e.preventDefault();
				$box.css("border-color", "#6366f1");
			});
			$box.on("dragleave", () => $box.css("border-color", ""));
			$box.on("drop", (e) => {
				e.preventDefault();
				$box.css("border-color", "");
				if (e.originalEvent.dataTransfer.files.length) {
					_handle_kyc_file(name, $box, e.originalEvent.dataTransfer.files[0]);
				}
			});

			$input.on("change", function () {
				if (this.files.length) {
					_handle_kyc_file(name, $box, this.files[0]);
				}
			});

			$box.find(".ch-kyc-remove").on("click", function (e) {
				e.preventDefault();
				e.stopPropagation();
				uploads[name] = null;
				$box.removeClass("has-file");
				$box.find(".ch-kyc-default").show();
				$box.find(".ch-kyc-preview").html("");
				$input.val("");
			});
		});

		function _handle_kyc_file(name, $box, file) {
			if (!file.type.startsWith("image/")) {
				frappe.show_alert({ message: __("Please select an image file"), indicator: "orange" });
				return;
			}
			if (file.size > 5 * 1024 * 1024) {
				frappe.show_alert({ message: __("File size must be less than 5MB"), indicator: "orange" });
				return;
			}

			$box.find(".ch-kyc-default").hide();
			$box.find(".ch-kyc-preview").html(
				`<div style="padding:20px"><i class="fa fa-spinner fa-spin fa-2x" style="color:#6366f1"></i><div style="font-size:11px;margin-top:6px;color:#6366f1">${__("Uploading...")}</div></div>`
			);

			const form_data = new FormData();
			form_data.append("file", file, file.name);
			form_data.append("doctype", "Buyback Assessment");
			form_data.append("docname", data.name);
			form_data.append("fieldname", name);
			form_data.append("is_private", 1);

			const reader = new FileReader();
			reader.onload = (e) => {
				$.ajax({
					url: "/api/method/upload_file",
					type: "POST",
					data: form_data,
					processData: false,
					contentType: false,
					headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
				}).done((r) => {
					const file_url = r.message.file_url;
					uploads[name] = file_url;
					$box.addClass("has-file");
					$box.find(".ch-kyc-default").hide();
					$box.find(".ch-kyc-preview").html(`
						<img src="${e.target.result}" alt="preview" />
						<div class="file-name"><i class="fa fa-check-circle"></i> ${frappe.utils.escape_html(file.name)}</div>
						<div class="file-status">${__("Attached successfully")}</div>
					`);
					const label_map = {
						customer_id_front: __("Aadhaar front"),
						customer_id_back: __("Aadhaar back"),
						customer_photo: __("Customer photo"),
					};
					frappe.show_alert({
						message: __("{0} uploaded", [label_map[name] || name]),
						indicator: "green",
					});
				}).fail(() => {
					uploads[name] = null;
					$box.removeClass("has-file");
					$box.find(".ch-kyc-preview").html(
						`<div style="color:#ef4444;font-size:11px"><i class="fa fa-times-circle"></i> ${__("Upload failed")}</div>`
					);
					setTimeout(() => {
						$box.find(".ch-kyc-default").show();
						$box.find(".ch-kyc-preview").html("");
					}, 2000);
				});
			};
			reader.readAsDataURL(file);
		}

		dlg.show();
	}

	// ─────────────────────────────── In-Store Approval (OTP + KYC) ──
	_show_instore_approval_dialog(data) {
		const order = data.order;
		const price = order.final_price || 0;
		const mobile = data.mobile_no || order.mobile_no || "";
		const masked = mobile ? mobile.slice(0, 2) + "****" + mobile.slice(-2) : "—";
		const device_label = frappe.utils.escape_html(data.item_name || "");
		const self = this;

		const otp_already_sent = order.status === "Awaiting OTP";
		const otp_already_verified = ["OTP Verified", "Ready to Pay", "Paid", "Closed"].includes(order.status);
		const otp_required = !otp_already_verified;
		let otp_sent_in_session = otp_already_sent;
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
			otp_code: "",
			kyc_id_type: order.customer_id_type || data.kyc_id_type || "",
			kyc_id_number: order.customer_id_number || data.kyc_id_number || "",
			settlement_type: order.settlement_type || "",
			payout_mode: order.customer_payout_mode || "",
			upi_id: order.customer_upi_id || "",
			bank_account_holder: order.customer_bank_account_holder || "",
			bank_account_number: order.customer_bank_account_number || "",
			bank_ifsc: order.customer_bank_ifsc || "",
			bank_name: order.customer_bank_name || "",
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
			customer_id_front: data.customer_id_front || null,
			customer_id_back: data.customer_id_back || null,
			customer_photo: data.customer_photo || null,
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
			const existing = uploads[name];
			const initial_html = existing
				? `<img src="${existing}" style="max-width:100%;max-height:60px;object-fit:cover;border-radius:4px"/>
				   <div style="font-size:11px;color:#15803d;font-weight:600;margin-top:4px"><i class="fa fa-check-circle"></i> ${__("Already uploaded")}</div>`
				: "";
			return `<div class="ch-wz-file-box" data-upload="${name}">
				<label class="ch-wz-label">${label}</label>
				<label class="ch-wz-file-drop ${existing ? "has-file" : ""}">
					${!existing ? `<div class="ch-wz-file-icon"><i class="fa fa-cloud-upload"></i></div>
					<div class="ch-wz-file-text">${__("Click or drag to upload")}</div>` : ""}
					<input class="ch-wz-file-input" type="file" accept="image/*" />
					<div class="ch-wz-file-preview" ${existing ? "" : 'style="display:none"'}>${initial_html}</div>
				</label>
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
					<i class="fa fa-info-circle"></i> ${hint}
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
					.ch-wz-hint{background:var(--blue-50,#e3f2fd);border-radius:8px;padding:12px 16px;font-size:13px;color:var(--blue-700,#1565c0);margin-bottom:16px}
					.ch-wz-hint i{margin-right:6px}
					.ch-wz-alert{display:flex;gap:12px;background:var(--yellow-50,#fffde7);border:1px solid var(--yellow-200,#fff9c4);border-radius:8px;padding:14px 16px;margin-bottom:18px;font-size:13px}
					.ch-wz-alert i{font-size:18px;color:var(--yellow-700);margin-top:2px}
					.ch-wz-alert span{color:var(--text-muted);font-size:12px}
					.ch-wz-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
					.ch-wz-row-3{grid-template-columns:1fr 1fr 1fr}
					.ch-wz-field{margin-bottom:14px}
					.ch-wz-label{display:block;font-size:12px;font-weight:600;color:var(--heading-color);margin-bottom:6px}
					.ch-wz-input,.ch-wz-select{width:100%;padding:8px 12px;border:1px solid var(--gray-300);border-radius:6px;font-size:14px;background:#fff;color:var(--text-color);outline:none;transition:border-color .15s}
					.ch-wz-input:focus,.ch-wz-select:focus{border-color:var(--primary)}
					.ch-wz-desc{font-size:11px;color:var(--text-muted);margin-top:4px}
					.ch-wz-divider{border-top:1px solid var(--border-color);margin:18px 0}
					.ch-wz-section-title{font-size:14px;font-weight:700;margin-bottom:14px;color:var(--heading-color)}
					.ch-wz-payout-confirm{display:flex;padding:12px 0}
					.ch-wz-check{display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;font-weight:500;color:var(--heading-color)}
					.ch-wz-check input{width:18px;height:18px;accent-color:var(--primary)}
					.ch-wz-file-box{margin-bottom:14px}
					.ch-wz-file-drop{border:2px dashed var(--gray-300);border-radius:8px;padding:16px;text-align:center;cursor:pointer;transition:border-color .15s;position:relative;min-height:80px;display:flex;flex-direction:column;align-items:center;justify-content:center;user-select:none}
					.ch-wz-file-drop:hover{border-color:var(--primary)}
					.ch-wz-file-drop.has-file{border-style:solid;border-color:var(--green-400);background:var(--green-50)}
					/* Input must remain interactive (no pointer-events:none) so the native
					   label→input association opens the OS file picker on click. The 1×1 px
					   absolute positioning + opacity:0 keeps it invisible. */
					.ch-wz-file-input{position:absolute;width:1px;height:1px;opacity:0}
					.ch-wz-file-icon{font-size:22px;color:var(--gray-400);margin-bottom:4px}
					.ch-wz-file-text{font-size:12px;color:var(--text-muted)}
					.ch-wz-file-preview{font-size:12px;color:var(--green-700);font-weight:600;width:100%}
					.ch-wz-file-icon,.ch-wz-file-text,.ch-wz-file-preview{pointer-events:none}
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
			dlg.get_primary_btn().closest(".modal-footer").hide();
			_bind_events();
		}

		function _sync_state() {
			$body.find(".ch-wz-input, .ch-wz-select").each(function () {
				const name = $(this).data("name");
				if (name) state[name] = $(this).val();
			});
			$body.find('input[type="checkbox"][data-name]').each(function () {
				state[$(this).data("name")] = $(this).is(":checked");
			});
		}

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
						order_name: order.name, method: "OTP", otp_code: state.otp_code,
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
				if (_validate()) { step++; _render(); }
			});
			$body.find(".ch-wz-submit").on("click", () => { _sync_state(); _submit(); });

			$body.find('[data-name="settlement_type"]').on("change", function () {
				state.settlement_type = $(this).val();
				$body.find(".ch-wz-payout-section").toggle(state.settlement_type === "Buyback");
			});
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

			$body.find(".ch-wz-file-drop").each(function () {
				const $drop = $(this);
				const $input = $drop.find('input[type="file"]');
				const name = $drop.closest("[data-upload]").data("upload");
				$drop.on("dragover", (e) => { e.preventDefault(); $drop.css("border-color", "var(--primary)"); });
				$drop.on("dragleave drop", () => $drop.css("border-color", ""));
				$drop.on("drop", (e) => { e.preventDefault(); if (e.originalEvent.dataTransfer.files.length) _handle_file(name, $drop, e.originalEvent.dataTransfer.files[0]); });
				// Click handling is intentionally left to the browser's native
				// <label>→<input> association. Do NOT add a synthetic click handler
				// here — calling input.click() from inside a label click handler
				// causes the synthesised click to bubble back up to the label,
				// recurse, and the browser blocks the file picker because the
				// activation can no longer be traced to a single user gesture.
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
				url: "/api/method/upload_file", type: "POST", data: form_data,
				processData: false, contentType: false,
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
				if (ownership_proof_required) {
					if (!state.ownership_proof_type) {
						frappe.show_alert({ message: __("Ownership/purchase proof type is required for this amount"), indicator: "orange" }); return false;
					}
					if (state.ownership_proof_type === "Not Available" && !(state.ownership_proof_remarks || "").trim()) {
						frappe.show_alert({ message: __("Please explain why ownership proof is not available"), indicator: "orange" }); return false;
					}
					if (state.ownership_proof_type !== "Not Available" && !uploads.ownership_proof_document) {
						frappe.show_alert({ message: __("Please upload the ownership proof document"), indicator: "orange" }); return false;
					}
				}
			}
			return true;
		}

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

			if (ownership_proof_required) {
				if (!state.ownership_proof_type) {
					frappe.show_alert({ message: __("Ownership/purchase proof type is required for this amount"), indicator: "orange" }); return;
				}
				if (state.ownership_proof_type === "Not Available" && !(state.ownership_proof_remarks || "").trim()) {
					frappe.show_alert({ message: __("Please explain why ownership proof is not available"), indicator: "orange" }); return;
				}
				if (state.ownership_proof_type !== "Not Available" && !uploads.ownership_proof_document) {
					frappe.show_alert({ message: __("Please upload the ownership proof document"), indicator: "orange" }); return;
				}
			}


			$body.find(".ch-wz-submit").prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Verifying...")}`);
			const send_method = (otp_required && !otp_verified_in_session) ? "OTP" : "In-Store Signature";
			const send_otp_code = (otp_required && !otp_verified_in_session) ? state.otp_code : null;
			frappe.xcall("ch_pos.api.pos_api.pos_approve_customer_buyback", {
				order_name: order.name,
				method: send_method, otp_code: send_otp_code,
				kyc_id_type: state.kyc_id_type, kyc_id_number: state.kyc_id_number,
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
				ownership_proof_type: state.ownership_proof_type || null,
				ownership_proof_document: uploads.ownership_proof_document || null,
				ownership_proof_remarks: state.ownership_proof_remarks || null,
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

	_status_cls(status) {
		if (["Submitted", "Approved", "Complete", "Inspection Created",
			"Customer Approved", "Paid", "Closed"].includes(status)) return "success";
		if (["Draft", "Awaiting Approval", "Awaiting Customer Approval",
			"Ready to Pay"].includes(status)) return "warning";
		if (["Rejected", "Cancelled"].includes(status)) return "danger";
		return "muted";
	}
}
