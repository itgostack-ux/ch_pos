/**
 * CH POS — Buyback Guided Intake
 *
 * Why this exists: creating a trade-in used to mean frappe.new_doc("Buyback
 * Assessment") — a 61-field desk form with 63 questions spread over four child
 * tables. That is a back-office document, not a counter tool, and store
 * executives were abandoning it half-filled.
 *
 * This walks the same inputs one section at a time. The step list is built
 * AFTER the device is chosen, because the question set is per-item (resolved
 * server-side from the Buyback Item Question Map), so it is never a fixed
 * wizard — a foldable and a feature phone get different screens.
 *
 * Sections are chunked into screens of CHUNK questions so each screen is
 * answerable without scrolling on a counter tablet.
 *
 * The client never computes the quote. It posts answers to
 * buyback.api.create_assessment_from_intake, which re-derives the question set
 * and runs the pricing engine, so a stale tab cannot invent a question or an
 * option impact.
 */
import { format_number, validate_india_phone } from "../../shared/helpers.js";

const CHUNK = 8;

//: The four stable phases. Question screens are per-item and only known after
//: the device is chosen, so they all roll up under "Assessment" rather than
//: producing a step count that lies early and jumps later.
const PHASES = [__("Customer"), __("Device"), __("Assessment"), __("Quote")];

const WARRANTY_OPTIONS = ["In Warranty", "Out of Warranty"];
const AGE_OPTIONS = ["0-3 Months", "4-6 Months", "7-11 Months", "12+ Months"];

const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

function _chunk(list, size) {
	const out = [];
	for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
	return out;
}

export class BuybackIntake {
	constructor({ store, on_created, on_cancel }) {
		this.store = store || "";
		this.on_created = on_created || (() => {});
		this.on_cancel = on_cancel || (() => {});
		this.idx = 0;
		this.steps = [];
		this.busy = false;
		this.data = {
			mobile_no: "",
			customer: null,
			customer_name: "",
			item: null,
			item_name: "",
			item_brand: "",
			item_model: "",
			imei_info: null,
			quotable: null,   // {quotable, reason} once the device is checked
			imei_serial: "",
			warranty_status: "",
			device_age_months: "",
			is_phone_dead: 0,
			remarks: "",
			answers: {},      // {question_bank_name: option_value}
			diagnostics: {},  // {question_bank_name: option_value}
			quote_ready: false,
			quote_error: "",
			form_error: "",
		};
	}

	// ── lifecycle ────────────────────────────────────────────────────
	open($host) {
		this.$host = $host;
		this.steps = this._base_steps();
		this.idx = 0;
		this._bind_nav();
		this._render();
	}

	close() {
		this.on_cancel();
	}

	_base_steps() {
		return [
			{ key: "customer", label: __("Customer"),
			  title: __("Who is trading in?"), hint: __("We only need a mobile number to start.") },
			{ key: "device", label: __("Device"),
			  title: __("Which device?"), hint: __("The questions asked next depend on the model.") },
		];
	}

	/** Rebuild the tail of the step list once the item's questions are known. */
	_build_steps(tests, questions) {
		const steps = this._base_steps();
		if (!this.data.is_phone_dead) {
			_chunk(tests, CHUNK).forEach((rows, i, all) =>
				steps.push({
					key: `diag_${i}`, label: __("Tests"), kind: "diagnostics", rows,
					title: __("Device tests"),
					hint: all.length > 1 ? __("Screen {0} of {1}", [i + 1, all.length]) : "",
				}));
			const by_purpose = { Grading: [], Deduction: [], Eligibility: [] };
			questions.forEach((q) => (by_purpose[q.question_purpose] || by_purpose.Deduction).push(q));
			[
				["Grading", __("Condition")],
				["Deduction", __("Faults")],
				["Eligibility", __("Eligibility")],
			].forEach(([purpose, label]) => {
				_chunk(by_purpose[purpose] || [], CHUNK).forEach((rows, i, all) =>
					steps.push({
						key: `${purpose}_${i}`, label, kind: "questions", rows,
						title: label,
						hint: all.length > 1 ? __("Screen {0} of {1}", [i + 1, all.length]) : "",
					}));
			});
		}
		steps.push({ key: "review", label: __("Quote"),
			title: __("Review and confirm"), hint: __("Check the details before creating the assessment.") });
		this.steps = steps;
	}

	// ── rendering ────────────────────────────────────────────────────
	//
	// Phases, not raw step numbers. The question screens are per-item and only
	// known after the device is picked, so a running "Step 3 of 14" either lies
	// early or jumps around later. Four stable phases with a sub-label inside
	// the assessment phase reads honestly the whole way through.
	_phase_of(step) {
		if (step.key === "customer") return 0;
		if (step.key === "device") return 1;
		if (step.key === "review") return 3;
		return 2;
	}

	_render() {
		const step = this.steps[this.idx];
		this.$host.html(`
			<div class="ch-bbi-wrap">
				<div class="ch-bbi-card">
					${this._html_head(step)}
					${this._html_form_error()}
					<div class="ch-bbi-body ${step.kind ? "ch-bbi-body-questions" : ""}">${this._html_step(step)}</div>
					${this._html_footer(step)}
				</div>
			</div>
			${this._css()}
		`);
		this._bind(step);
	}

	_html_form_error() {
		if (!this.data.form_error) return "";
		return `<div class="ch-bbi-form-error" role="alert" tabindex="-1">
			<i class="fa fa-exclamation-circle"></i>
			<span>${esc(this.data.form_error)}</span>
		</div>`;
	}

	_show_form_error(raw) {
		const message = frappe.utils.strip_html(String(raw || __("Please retry."))).trim();
		this.data.form_error = message;
		let $error = this.$host.find(".ch-bbi-form-error");
		if (!$error.length) {
			this.$host.find(".ch-bbi-head").after(this._html_form_error());
			$error = this.$host.find(".ch-bbi-form-error");
		} else {
			$error.find("span").text(message);
		}
		$error.trigger("focus");
		$error[0]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
	}

	_clear_form_error() {
		this.data.form_error = "";
		this.$host.find(".ch-bbi-form-error").remove();
	}

	/** Keep server failures owned by this full-screen form. Without silent mode,
	 * Frappe opens its global error dialog before our inline catch handler. */
	_xcall(method, args) {
		return frappe.xcall(method, args, undefined, { silent: true });
	}

	_html_head(step) {
		const phase = this._phase_of(step);
		const dots = PHASES.map((p, i) => `
			<div class="ch-bbi-ph ${i === phase ? "on" : ""} ${i < phase ? "done" : ""}">
				<span class="ch-bbi-ph-dot">${i < phase ? '<i class="fa fa-check"></i>' : i + 1}</span>
				<span class="ch-bbi-ph-label">${esc(p)}</span>
			</div>`).join('<div class="ch-bbi-ph-bar"></div>');

		return `
			<div class="ch-bbi-head">
				<div class="ch-bbi-head-top">
					<div class="ch-bbi-phases">${dots}</div>
					<button class="btn ch-bbi-cancel" title="${__("Cancel")}" aria-label="${__("Cancel")}">
						<i class="fa fa-times"></i>
					</button>
				</div>
				<div class="ch-bbi-title">${esc(step.title || step.label)}</div>
				${step.hint ? `<div class="ch-bbi-sub">${esc(step.hint)}</div>` : ""}
			</div>`;
	}

	_html_step(step) {
		if (step.key === "customer") return this._html_customer();
		if (step.key === "device") return this._html_device();
		if (step.key === "review") return this._html_review();
		if (step.kind === "diagnostics") return this._html_rows(step.rows, "diagnostics");
		return this._html_rows(step.rows, "answers");
	}

	_field(label, inner, hint, req) {
		return `
			<div class="ch-bbi-f">
				<label class="ch-bbi-lbl">${label}${req ? '<span class="ch-bbi-req">*</span>' : ""}</label>
				${inner}
				${hint ? `<div class="ch-bbi-hint">${hint}</div>` : ""}
			</div>`;
	}

	_html_customer() {
		const d = this.data;
		return `
			${this._field(__("Mobile number"),
				`<input type="tel" inputmode="numeric" maxlength="10" class="ch-bbi-in ch-bbi-mobile ch-bbi-in-lg"
					value="${esc(d.mobile_no)}" placeholder="00000 00000">`,
				__("The customer's quote is looked up by this number."), true)}
			<div class="ch-bbi-cust-hit"></div>
			${this._field(__("Customer name"),
				`<input type="text" class="ch-bbi-in ch-bbi-cname" value="${esc(d.customer_name)}"
					placeholder="${__("Optional")}">`)}`;
	}

	_html_device() {
		const d = this.data;
		const chip = (v, cur, cls) =>
			`<button class="ch-bbi-chip ${cur === v ? "on" : ""} ${cls}" data-v="${esc(v)}">${esc(v)}</button>`;

		const picked = d.item
			? `<div class="ch-bbi-picked">
					<div>
						<div class="ch-bbi-picked-name">${esc(d.item_name)}</div>
						<div class="ch-bbi-picked-code">${esc([d.item_brand, d.item_model].filter(Boolean).join(" · ") || d.item)}</div>
					</div>
					<button class="ch-bbi-clear-item">${__("Change")}</button>
				</div>`
			: `<input type="text" class="ch-bbi-in ch-bbi-item-q" placeholder="${__("Search the device master…")}">
			   <div class="ch-bbi-item-results"></div>`;

		return `
			${this._field(__("IMEI / serial"),
				`<input type="text" class="ch-bbi-in ch-bbi-imei" value="${esc(d.imei_serial)}"
					placeholder="${__("Scan or type — identifies the device if we sold it")}">`)}
			<div class="ch-bbi-imei-note">${this._html_imei_note()}</div>
			${this._field(__("Device"), picked, "", true)}
			<div class="ch-bbi-quotable">${this._html_quotable()}</div>
			${this._field(__("Warranty"),
				`<div class="ch-bbi-chips">${WARRANTY_OPTIONS.map(o => chip(o, d.warranty_status, "ch-bbi-warr")).join("")}</div>`,
				"", true)}
			${this._field(__("Age of device"),
				`<div class="ch-bbi-chips">${AGE_OPTIONS.map(o => chip(o, d.device_age_months, "ch-bbi-age")).join("")}</div>`,
				__("Warranty and age decide the price band."), true)}
			<label class="ch-bbi-dead-row">
				<input type="checkbox" class="ch-bbi-dead" ${d.is_phone_dead ? "checked" : ""}>
				<span>
					<span class="ch-bbi-dead-t">${__("Phone does not switch on")}</span>
					<span class="ch-bbi-dead-s">${__("Skips the condition questions and quotes salvage value")}</span>
				</span>
			</label>`;
	}

	/** Provenance banner — "ours" carries the sale date, which is the honest
	 *  input for the age band; "external" is a normal answer, not an error. */
	_html_imei_note() {
		const o = this.data.imei_info;
		if (!o) return "";
		if (o.origin === "external") {
			return `<div class="ch-bbi-note ext">
				<i class="fa fa-info-circle"></i>
				<span>${__("Not sold by us — external device.")}</span></div>`;
		}
		if (o.origin !== "ours") return "";
		const bits = [];
		if (o.last_sold_on) {
			bits.push(__("Sold by us on {0}", [frappe.datetime.str_to_user(o.last_sold_on).split(" ")[0]]));
			if (o.last_sold_voucher) bits.push(esc(o.last_sold_voucher));
		} else {
			bits.push(__("In our records — no sale recorded yet"));
		}
		const prior = (o.buyback_count || 0) > 0
			? `<div class="ch-bbi-note warn"><i class="fa fa-repeat"></i>
				<span>${__("Traded in before ({0}x) — status {1}", [o.buyback_count, o.buyback_status || "—"])}</span></div>`
			: "";
		return `<div class="ch-bbi-note ours"><i class="fa fa-check-circle"></i>
			<span>${bits.join(" · ")}</span></div>${prior}`;
	}

	/** A device with no Buyback Price Master cannot be quoted at all. The engine
	 *  only says so on save, which is 13 screens too late — say it here. */
	_html_quotable() {
		const q = this.data.quotable;
		if (!q || !this.data.item) return "";
		if (q.quotable) {
			return q.market_price
				? `<div class="ch-bbi-note ours"><i class="fa fa-tag"></i>
					<span>${__("Market reference ₹{0}", [format_number(q.market_price)])}</span></div>`
				: "";
		}
		return `<div class="ch-bbi-note stop"><i class="fa fa-ban"></i>
			<span>${esc(q.reason || __("This device cannot be quoted."))}</span></div>`;
	}

	_check_quotable() {
		const item = this.data.item;
		if (!item) { this.data.quotable = null; return; }
		const request_key = [item, this.data.warranty_status, this.data.device_age_months].join("|");
		this._quotable_request_key = request_key;
		this.data.quotable = null;
		this._xcall("buyback.api.check_device_quotable", {
			item_code: item,
			warranty_status: this.data.warranty_status || null,
			device_age_months: this.data.device_age_months || null,
		})
			.then((q) => {
				if (this._quotable_request_key !== request_key) return;
				this.data.quotable = q || null;
				this.$host.find(".ch-bbi-quotable").html(this._html_quotable());
			})
			.catch((e) => this._show_form_error(
				(e && e.message) || __("Could not check the device price configuration. Please retry.")));
	}

	_html_rows(rows, bucket) {
		const chosen = this.data[bucket];
		return rows.map((r) => {
			const key = r.name;
			const label = bucket === "diagnostics" ? r.test_name : r.question_text;
			const opts = r.options || [];
			const done = !!chosen[key];
			return `
				<div class="ch-bbi-q ${done ? "done" : ""}">
					<div class="ch-bbi-q-t">${esc(label)}</div>
					<div class="ch-bbi-chips">
						${opts.map(o => `
							<button class="ch-bbi-chip ${chosen[key] === o.value ? "on" : ""} ch-bbi-opt"
								data-bucket="${bucket}" data-key="${esc(key)}" data-v="${esc(o.value)}">
								${esc(o.label || o.value)}
							</button>`).join("")}
					</div>
				</div>`;
		}).join("");
	}

	_html_review() {
		const d = this.data;
		const row = (k, v) => `<div class="ch-bbi-sum"><span>${k}</span><b>${esc(v || "—")}</b></div>`;
		return `
			<div class="ch-bbi-quote">
				<div class="ch-bbi-quote-l">${__("Estimated offer")}</div>
				<div class="ch-bbi-price">${__("Calculating…")}</div>
				<div class="ch-bbi-grade"></div>
			</div>
			${row(__("Mobile"), d.mobile_no)}
			${row(__("Device"), d.item_name)}
			${row(__("IMEI"), d.imei_serial)}
			${row(__("Warranty"), d.warranty_status)}
			${row(__("Age"), d.device_age_months)}
			${d.is_phone_dead
				? row(__("Condition"), __("Does not switch on"))
				: row(__("Checks completed"),
					`${Object.keys(d.answers).length + Object.keys(d.diagnostics).length}`)}
			${this._field(__("Remarks"),
				`<textarea class="ch-bbi-in ch-bbi-remarks" rows="2"
					placeholder="${__("Anything the inspector should know")}">${esc(d.remarks)}</textarea>`)}`;
	}

	_html_footer(step) {
		const last = step.key === "review";
		const disabled = last && !this.data.quote_ready ? "disabled" : "";
		return `
			<div class="ch-bbi-foot">
				${this.idx > 0
					? `<button class="ch-bbi-back">${__("Back")}</button>`
					: `<span></span>`}
				<button class="ch-bbi-next" ${disabled}>
					${last ? __("Create assessment") : __("Continue")}
				</button>
			</div>`;
	}

	_css() {
		return `<style>
		/* The intake is a workspace view, not a modal. Its host is .ch-bb-split,
		   so fill that area without covering or dimming the surrounding POS. */
		.ch-bbi-wrap{position:relative;display:flex;flex:1;width:100%;min-width:0;min-height:0;
			align-items:stretch;background:transparent}
		.ch-bbi-card{width:100%;min-width:0;height:100%;display:flex;
			flex-direction:column;background:var(--card-bg,#fff);
			border:1px solid var(--border-color,#e8ecf1);border-radius:12px;
			box-shadow:0 1px 2px rgba(16,24,40,.04);overflow:hidden}
		.ch-bbi-head{padding:18px 22px 14px;border-bottom:1px solid var(--border-color,#eef1f5)}
		.ch-bbi-head-top{display:flex;align-items:center;justify-content:space-between;gap:12px}
		.ch-bbi-phases{display:flex;align-items:center;gap:6px;flex:1;min-width:0}
		.ch-bbi-ph{display:flex;align-items:center;gap:6px;min-width:0}
		.ch-bbi-ph-dot{width:22px;height:22px;border-radius:50%;flex:none;display:flex;align-items:center;
			justify-content:center;font-size:11px;font-weight:700;background:var(--bg-light-gray,#eef1f5);
			color:var(--text-muted,#8a94a6)}
		.ch-bbi-ph.on .ch-bbi-ph-dot{background:var(--primary,#4f46e5);color:#fff}
		.ch-bbi-ph.done .ch-bbi-ph-dot{background:#dcfce7;color:#16a34a}
		.ch-bbi-ph-label{font-size:12px;font-weight:600;color:var(--text-muted,#8a94a6);
			white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
		.ch-bbi-ph.on .ch-bbi-ph-label{color:var(--text-color,#1f272e)}
		.ch-bbi-ph-bar{height:1px;background:var(--border-color,#e3e8ef);flex:1;min-width:8px}
		.ch-bbi-cancel{border:0;background:transparent;color:var(--text-muted,#8a94a6);
			width:30px;height:30px;border-radius:8px;padding:0;flex:none}
		.ch-bbi-cancel:hover{background:var(--bg-light-gray,#f2f4f7);color:var(--text-color,#1f272e)}
		.ch-bbi-title{font-size:19px;font-weight:750;margin-top:14px;letter-spacing:-.01em}
		.ch-bbi-sub{font-size:13px;color:var(--text-muted,#8a94a6);margin-top:2px}
		.ch-bbi-form-error{display:flex;align-items:flex-start;gap:9px;margin:12px 22px 0;
			padding:10px 12px;border:1px solid #fecaca;border-radius:10px;background:#fef2f2;
			color:#b91c1c;font-size:13px;font-weight:600;line-height:1.4;outline:none}
		.ch-bbi-form-error i{margin-top:2px;flex:none}
		.ch-bbi-body{padding:20px 22px;flex:1;min-height:0;overflow-y:auto}
		.ch-bbi-body-questions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
			align-content:start;gap:12px}
		.ch-bbi-f{margin-bottom:18px}
		.ch-bbi-lbl{display:block;font-size:13px;font-weight:650;margin-bottom:6px}
		.ch-bbi-req{color:#dc2626;margin-left:3px}
		.ch-bbi-hint{font-size:12px;color:var(--text-muted,#8a94a6);margin-top:6px}
		.ch-bbi-in{width:100%;border:1px solid var(--border-color,#dfe3e8);border-radius:10px;
			padding:10px 13px;font-size:14px;background:var(--control-bg,#fff);outline:none;transition:border-color .15s,box-shadow .15s}
		.ch-bbi-in:focus{border-color:var(--primary,#4f46e5);box-shadow:0 0 0 3px rgba(79,70,229,.12)}
		.ch-bbi-in-lg{font-size:20px;letter-spacing:2px;padding:12px 14px;font-weight:600}
		.ch-bbi-chips{display:flex;flex-wrap:wrap;gap:8px}
		.ch-bbi-chip{border:1px solid var(--border-color,#dfe3e8);background:var(--card-bg,#fff);
			border-radius:9px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;
			color:var(--text-color,#1f272e);transition:all .12s}
		.ch-bbi-chip:hover{border-color:var(--primary,#4f46e5)}
		.ch-bbi-chip.on{background:var(--primary,#4f46e5);border-color:var(--primary,#4f46e5);color:#fff}
		.ch-bbi-q{padding:13px 14px;border:1px solid var(--border-color,#e8ecf1);border-radius:12px;margin-bottom:10px}
		.ch-bbi-body-questions .ch-bbi-q{margin-bottom:0}
		.ch-bbi-q.done{border-color:#c7f0d8;background:#fbfefc}
		.ch-bbi-q-t{font-weight:600;font-size:14px;margin-bottom:9px;line-height:1.35}
		.ch-bbi-picked{display:flex;align-items:center;justify-content:space-between;gap:10px;
			border:1px solid var(--border-color,#dfe3e8);border-radius:10px;padding:10px 13px}
		.ch-bbi-picked-name{font-weight:650;font-size:14px}
		.ch-bbi-picked-code{font-size:12px;color:var(--text-muted,#8a94a6)}
		.ch-bbi-clear-item{border:0;background:transparent;color:var(--primary,#4f46e5);font-weight:650;font-size:13px;cursor:pointer}
		.ch-bbi-item-results{margin-top:6px}
		.ch-bbi-hit-t{font-weight:650;font-size:14px}
		.ch-bbi-hit-s{font-size:12px;color:var(--text-muted,#8a94a6);margin-top:1px}
		.ch-bbi-imei-note:empty{display:none}
		.ch-bbi-note{display:flex;gap:8px;align-items:flex-start;font-size:13px;font-weight:600;
			border-radius:10px;padding:9px 12px;margin:-8px 0 18px}
		.ch-bbi-note.ours{background:#f0fdf4;border:1px solid #bbf7d0;color:#15803d}
		.ch-bbi-note.ext{background:var(--bg-light-gray,#f6f8fa);border:1px solid var(--border-color,#e3e8ef);
			color:var(--text-muted,#6b7480)}
		.ch-bbi-quotable:empty{display:none}
		.ch-bbi-note.stop{background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;margin-top:-8px}
		.ch-bbi-note.warn{background:#fffbeb;border:1px solid #fde68a;color:#b45309;margin-top:8px}
		.ch-bbi-hit{padding:10px 13px;border:1px solid var(--border-color,#e8ecf1);border-radius:10px;
			margin-top:6px;cursor:pointer;font-weight:600;font-size:14px}
		.ch-bbi-hit:hover{border-color:var(--primary,#4f46e5);background:var(--bg-light-gray,#f8fafc)}
		.ch-bbi-dead-row{display:flex;gap:10px;align-items:flex-start;padding:12px 14px;
			border:1px solid var(--border-color,#e8ecf1);border-radius:12px;cursor:pointer;margin:0}
		.ch-bbi-dead-t{display:block;font-weight:650;font-size:14px}
		.ch-bbi-dead-s{display:block;font-size:12px;color:var(--text-muted,#8a94a6);margin-top:1px}
		.ch-bbi-quote{border-radius:12px;padding:16px 18px;margin-bottom:16px;
			background:linear-gradient(135deg,#eef2ff,#f5f3ff);border:1px solid #dfe3fb}
		.ch-bbi-quote-l{font-size:11px;text-transform:uppercase;letter-spacing:.6px;
			font-weight:700;color:#6366f1}
		.ch-bbi-price{font-size:30px;font-weight:800;letter-spacing:-.02em;margin-top:2px}
		.ch-bbi-grade{font-size:13px;color:var(--text-muted,#8a94a6)}
		.ch-bbi-sum{display:flex;justify-content:space-between;gap:12px;padding:8px 0;
			border-bottom:1px solid var(--border-color,#f1f4f8);font-size:13px}
		.ch-bbi-sum span{color:var(--text-muted,#8a94a6)}
		.ch-bbi-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;
			padding:14px 22px;border-top:1px solid var(--border-color,#eef1f5);background:var(--bg-light-gray,#fbfcfd)}
		.ch-bbi-back{border:0;background:transparent;font-weight:650;font-size:14px;
			color:var(--text-muted,#8a94a6);cursor:pointer;padding:10px 4px}
		.ch-bbi-back:hover{color:var(--text-color,#1f272e)}
		.ch-bbi-next{border:0;background:var(--primary,#4f46e5);color:#fff;font-weight:700;
			font-size:14px;border-radius:10px;padding:11px 26px;cursor:pointer;min-width:170px}
		.ch-bbi-next:hover{filter:brightness(1.06)}
		.ch-bbi-next:disabled{opacity:.5;cursor:not-allowed;filter:none}
		.ch-bbi-quote.error{background:#fef2f2;border-color:#fecaca}
		.ch-bbi-quote.error .ch-bbi-price,.ch-bbi-quote.error .ch-bbi-grade{color:#b91c1c}
		@media(max-width:800px){
			.ch-bbi-card{border-radius:8px}
			.ch-bbi-body-questions{grid-template-columns:1fr}
			.ch-bbi-ph-label{display:none}
		}
		</style>`;
	}

	// ── behaviour ────────────────────────────────────────────────────
	/** Bound ONCE on the host, not per render. Delegated handlers survive the
	 *  DOM being replaced, so navigation cannot be orphaned by a repaint that
	 *  happens between mousedown and click. */
	_bind_nav() {
		this.$host
			.off(".bbi")
			.on("click.bbi", ".ch-bbi-cancel", () => this.close())
			.on("click.bbi", ".ch-bbi-back", () => { if (this.idx > 0) { this.idx--; this._render(); } })
			.on("click.bbi", ".ch-bbi-next", () => {
				// A throw inside the step logic used to die in the console and the
				// button just looked dead. Surface it instead.
				try {
					this._next();
				} catch (e) {
					console.error("Buyback intake step failed", e);
					this._show_form_error((e && e.message) || String(e));
				}
			});
	}

	_bind(step) {
		const $h = this.$host;

		$h.find(".ch-bbi-opt").on("click", (e) => {
			this._clear_form_error();
			const $b = $(e.currentTarget);
			this.data[$b.data("bucket")][$b.data("key")] = $b.data("v");
			// Toggle the SAME class the renderer uses. This used to flip the old
			// Bootstrap btn-primary/btn-outline-secondary pair, which no longer
			// exists after the redesign — so the answer was stored but never
			// looked selected, and the only dark chip on screen was whichever
			// button happened to hold focus. It read as "my last answer got
			// cleared" every time the user moved on.
			const $card = $b.closest(".ch-bbi-q");
			$card.find(".ch-bbi-opt").removeClass("on");
			$b.addClass("on");
			$card.addClass("done");
			$b.trigger("blur");
		});

		if (step.key === "customer") {
			const $m = $h.find(".ch-bbi-mobile");
			$m.on("input", () => { this.data.mobile_no = $m.val().replace(/\D/g, "").slice(-10); });
			$m.on("blur", () => this._lookup_customer());
			$h.find(".ch-bbi-cname").on("input", (e) => { this.data.customer_name = e.target.value; });
			if (this.data.mobile_no) this._lookup_customer();
		}

		if (step.key === "device") {
			if (this.data.item && !this.data.quotable) this._check_quotable();
			$h.find(".ch-bbi-clear-item").on("click", () => {
				// An IMEI we sold RESOLVED this device, so the two are one fact —
				// changing the device would leave our serial claiming to be a
				// model it is not. Clear it and let them re-scan.
				// An external IMEI is just a number the customer's handset carries;
				// it is not tied to anything in our books, so keep it while they
				// correct the model.
				const from_our_serial = (this.data.imei_info || {}).origin === "ours";
				if (from_our_serial) {
					this.data.imei_serial = "";
					this.data.imei_info = null;
					this._imei_looked_up = "";
					frappe.show_alert({
						message: __("IMEI cleared — it belonged to the device we fetched. Scan the correct one."),
						indicator: "blue",
					});
				}
				this.data.item = null;
				this.data.item_name = "";
				this.data.item_brand = "";
				this.data.item_model = "";
				this.data.quotable = null;
				this._render();
			});
			const $q = $h.find(".ch-bbi-item-q");
			let t = null;
			$q.on("input", () => {
				clearTimeout(t);
				const term = $q.val().trim();
				t = setTimeout(() => this._search_items(term), 250);
			});
			const $imei = $h.find(".ch-bbi-imei");
			$imei.on("input", (e) => { this.data.imei_serial = e.target.value.trim(); });
			$imei.on("blur", () => this._lookup_imei());
			$imei.on("keypress", (e) => { if (e.which === 13) $imei.trigger("blur"); });
			$h.find(".ch-bbi-warr").on("click", (e) => {
				this.data.warranty_status = $(e.currentTarget).data("v"); this._render(); this._check_quotable();
			});
			$h.find(".ch-bbi-age").on("click", (e) => {
				this.data.device_age_months = $(e.currentTarget).data("v"); this._render(); this._check_quotable();
			});
			$h.find(".ch-bbi-dead").on("change", (e) => { this.data.is_phone_dead = e.target.checked ? 1 : 0; });
		}

		if (step.key === "review") {
			$h.find(".ch-bbi-remarks").on("input", (e) => { this.data.remarks = e.target.value; });
			this._preview_quote();
		}
	}

	_lookup_customer() {
		const m = this.data.mobile_no;
		const $hit = this.$host.find(".ch-bbi-cust-hit");
		if (!m || m.length !== 10) return $hit.empty();
		frappe.call({
			method: "frappe.client.get_list",
			silent: true,
			args: { doctype: "Customer", filters: { mobile_no: m }, fields: ["name", "customer_name"], limit_page_length: 1 },
			callback: (r) => {
				const hit = (r.message || [])[0];
				this.data.customer = hit ? hit.name : null;
				if (!hit) {
					return $hit.html(`<div style="font-size:12px;opacity:.7">
						<i class="fa fa-user-plus"></i> ${__("New customer — will be linked by mobile")}</div>`);
				}
				this.data.customer_name = this.data.customer_name || hit.customer_name;
				$hit.html(`<div style="font-size:13px;font-weight:650;color:var(--green-600,#16a34a)">
					<i class="fa fa-check-circle"></i> ${esc(hit.customer_name)}</div>`);
			},
		});
	}

	/** Repaint ONLY the provenance banner.
	 *
	 *  This runs on blur, and blur fires before the click that caused it. A full
	 *  _render() here replaced the card while the mouse was still going down on
	 *  Continue, so the click landed on a detached button and the wizard looked
	 *  frozen. Never re-render the whole step from a blur handler.
	 */
	_paint_imei_note() {
		this.$host.find(".ch-bbi-imei-note").html(this._html_imei_note());
	}

	_lookup_imei() {
		const imei = (this.data.imei_serial || "").trim();
		if (!imei) {
			this._imei_looked_up = "";
			if (this.data.imei_info) { this.data.imei_info = null; this._paint_imei_note(); }
			return;
		}
		if (this._imei_looked_up === imei) return;
		this._imei_looked_up = imei;
		this._xcall("buyback.api.lookup_imei_for_intake", { imei }).then((info) => {
			this.data.imei_info = info || null;
			// A device we sold identifies itself — never make the executive search
			// for a model we already know. An explicit pick still wins.
			const filled = info && info.origin === "ours" && info.item_code && !this.data.item;
			if (filled) {
				this.data.item = info.item_code;
				this.data.item_name = info.item_name || info.item_code;
				this.data.item_brand = info.brand || "";
			}
			// Only the auto-fill changes a field outside the banner, and that
			// arrives a network round-trip later, well clear of the click.
			if (filled) { this._render(); this._check_quotable(); }
			else this._paint_imei_note();
		}).catch(() => { /* lookup is an aid, never a gate */ });
	}

	_search_items(term) {
		const $res = this.$host.find(".ch-bbi-item-results");
		if (!term || term.length < 2) return $res.empty();
		frappe.call({
			method: "buyback.api.search_items",
			silent: true,
			args: { search_text: term, limit: 8 },
			callback: (r) => {
				const items = r.message || [];
				if (!items.length) {
					return $res.html(`<div style="font-size:12px;opacity:.6;padding:6px">${__("No matching device")}</div>`);
				}
				$res.html(items.map(it => {
					const label = it.ch_display_name || it.item_name || it.item_code;
					const sub = [it.brand, it.ch_model, it.ch_sub_category].filter(Boolean).join(" · ");
					return `
						<div class="ch-bbi-hit" data-code="${esc(it.item_code || it.name)}"
							data-label="${esc(label)}" data-brand="${esc(it.brand || "")}"
							data-model="${esc(it.ch_model || "")}">
							<div class="ch-bbi-hit-t">${esc(label)}</div>
							${sub ? `<div class="ch-bbi-hit-s">${esc(sub)}</div>` : ""}
						</div>`;
				}).join(""));
				$res.find(".ch-bbi-hit").on("click", (e) => {
					const $t = $(e.currentTarget);
					this.data.item = $t.data("code");
					this.data.item_name = $t.data("label");
					this.data.item_brand = $t.data("brand") || "";
					this.data.item_model = $t.data("model") || "";
					this.data.quotable = null;
					this._render();
					this._check_quotable();
				});
			},
		});
	}

	_next() {
		const step = this.steps[this.idx];
		const d = this.data;
		this._clear_form_error();

		if (step.key === "customer") {
			if (!validate_india_phone(d.mobile_no)) {
				return this._show_form_error(__("Enter a valid 10-digit mobile number"));
			}
		}

		if (step.key === "device") {
			if (!d.item) return this._show_form_error(__("Select the device"));
			if (!d.quotable) {
				this._check_quotable();
				return this._show_form_error(__("Checking the selected price band…"));
			}
			if (d.quotable && d.quotable.quotable === false) {
				const $note = this.$host.find(".ch-bbi-note.stop");
				$note.attr("tabindex", "-1").trigger("focus");
				$note[0]?.scrollIntoView({ behavior: "smooth", block: "center" });
				return;
			}
			if (!d.warranty_status || !d.device_age_months) {
				return this._show_form_error(__("Warranty and device age are required to quote"));
			}
			return this._load_questions();   // step list depends on the item
		}

		if (step.kind === "questions" || step.kind === "diagnostics") {
			const bucket = step.kind === "diagnostics" ? "diagnostics" : "answers";
			const missing = step.rows.filter(r => !this.data[bucket][r.name]).length;
			if (missing) {
				return this._show_form_error(__("{0} unanswered on this screen", [missing]));
			}
		}

		if (step.key === "review") return this._create();

		this.idx++;
		this._render();
	}

	_load_questions() {
		if (this.data.is_phone_dead) {
			this._build_steps([], []);
			this.idx = this.steps.length - 1;
			return this._render();
		}
		frappe.dom.freeze(__("Loading questions…"));
		Promise.all([
			this._xcall("buyback.api.get_diagnostic_tests_for_item", { item_code: this.data.item }),
			this._xcall("buyback.api.get_customer_questions_for_item", { item_code: this.data.item }),
		]).then(([tests, questions]) => {
			frappe.dom.unfreeze();
			this._build_steps(tests || [], questions || []);
			this.idx = 2;
			this._render();
		}).catch((e) => {
			frappe.dom.unfreeze();
			console.error("Buyback intake: question load failed", e);
			this._show_form_error((e && e.message) || __("Could not load questions. Please retry."));
		});
	}

	_preview_quote() {
		const d = this.data;
		const $p = this.$host.find(".ch-bbi-price");
		const $g = this.$host.find(".ch-bbi-grade");
		const $quote = this.$host.find(".ch-bbi-quote");
		const $create = this.$host.find(".ch-bbi-next");
		d.quote_ready = false;
		d.quote_error = "";
		$create.prop("disabled", true);
		$quote.removeClass("error");
		const responses = Object.entries(d.answers).map(([q, v]) => ({ question: q, answer_value: v }));
		const diagnostic_tests = Object.entries(d.diagnostics).map(([t, v]) => ({ test: t, result: v }));
		this._xcall("buyback.api.calculate_live_estimate", {
			item_code: d.item,
			warranty_status: d.warranty_status || "",
			device_age_months: d.device_age_months || "",
			diagnostic_tests: JSON.stringify(diagnostic_tests),
			responses: JSON.stringify(responses),
			is_phone_dead: d.is_phone_dead ? 1 : 0,
		}).then((est) => {
			$p.text("₹" + format_number((est && est.estimated_price) || 0));
			$g.text(est && est.grade ? __("Grade {0}", [est.grade]) : "");
			d.quote_ready = true;
			$create.prop("disabled", false);
		}).catch((e) => {
			const raw = (e && (e.message || e.exc || e.exc_type))
				|| __("Pricing configuration is incomplete.");
			this._show_quote_error(raw);
		});
	}

	_show_quote_error(raw) {
		const message = frappe.utils.strip_html(String(raw || __("Pricing configuration is incomplete."))).trim();
		this.data.quote_ready = false;
		this.data.quote_error = message;
		this.$host.find(".ch-bbi-price").text(__("Cannot quote"));
		this.$host.find(".ch-bbi-grade").text(message);
		this.$host.find(".ch-bbi-quote").addClass("error")[0]
			?.scrollIntoView({ behavior: "smooth", block: "center" });
		this.$host.find(".ch-bbi-next").prop("disabled", true);
	}

	_create() {
		if (this.busy) return;
		if (!this.data.quote_ready) {
			this._show_quote_error(
				this.data.quote_error || __("Wait for the quote calculation to finish."));
			return;
		}
		this.busy = true;
		const d = this.data;
		frappe.dom.freeze(__("Creating assessment…"));
		this._xcall("buyback.api.create_assessment_from_intake", {
			mobile_no: d.mobile_no,
			item_code: d.item,
			store: this.store,
			customer: d.customer || null,
			imei_serial: d.imei_serial || null,
			warranty_status: d.warranty_status || null,
			device_age_months: d.device_age_months || null,
			is_phone_dead: d.is_phone_dead ? 1 : 0,
			diagnostics: JSON.stringify(Object.entries(d.diagnostics).map(([t, v]) => ({ test: t, result: v }))),
			answers: JSON.stringify(Object.entries(d.answers).map(([q, v]) => ({ question: q, answer: v }))),
			remarks: d.remarks || null,
		}).then((res) => {
			frappe.dom.unfreeze();
			this.busy = false;
			if (!res || !res.name) return;
			const dropped = (res.submitted_answers - res.accepted_answers)
				+ (res.submitted_diagnostics - res.accepted_diagnostics);
			if (dropped > 0) {
				// Never silent: the server rejects answers whose question or option
				// is not in the item's current catalogue.
				frappe.show_alert({
					message: __("{0} answer(s) were not accepted — the question set changed. Review the assessment.", [dropped]),
					indicator: "orange",
				}, 10);
			}
			frappe.show_alert({
				message: __("{0} created — ₹{1}", [res.name, format_number(res.estimated_price || 0)]),
				indicator: "green",
			});
			this.on_created(res.name);
		}).catch((e) => {
			frappe.dom.unfreeze();
			this.busy = false;
			this._show_quote_error((e && (e.message || e.exc || e.exc_type)) || __("Please retry."));
		});
	}
}
