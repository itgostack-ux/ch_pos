/**
 * CH POS — Guided Selling Workspace
 *
 * Assisted consultation flow:
 * 1) Pick category/sub-category
 * 2) Ask discovery questions
 * 3) Show ranked recommendations
 * 4) Persist POS Guided Session and optionally add to cart
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class GuidedWorkspace {
	constructor() {
		this.catalog = { categories: [], sub_categories: [] };
		this.questions = [];
		this.recommendations = [];
		this.selected_category = "";
		this.selected_sub_category = "";

		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "guided") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		this.panel = panel;
		this.questions = [];
		this.recommendations = [];

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#e0f2fe;color:#0369a1">
							<i class="fa fa-compass"></i>
						</span>
						${__("Guided Selling")}
					</h4>
					<span class="ch-mode-hint">${__("Capture customer needs and get ranked recommendations")}</span>
				</div>

				<div class="ch-guided-card" style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin-bottom:12px;">
					<div style="font-weight:600;margin-bottom:8px">${__("Step 1: Select Product Scope")}</div>
					<div class="row" style="margin:0 -6px;">
						<div class="col-sm-4" style="padding:0 6px;">
							<label style="font-size:12px;color:#6b7280">${__("Category")}</label>
							<select class="form-control ch-guided-category">
								<option value="">${__("Select Category")}</option>
							</select>
						</div>
						<div class="col-sm-4" style="padding:0 6px;">
							<label style="font-size:12px;color:#6b7280">${__("Sub Category")}</label>
							<select class="form-control ch-guided-sub-category">
								<option value="">${__("Select Sub Category")}</option>
							</select>
						</div>
						<div class="col-sm-4" style="padding:0 6px;display:flex;align-items:flex-end;gap:8px;">
							<button class="btn btn-primary ch-guided-load" style="flex:1">
								<i class="fa fa-list-alt"></i> ${__("Load Questions")}
							</button>
						</div>
					</div>
				</div>

				<div class="ch-guided-questions"></div>
				<div class="ch-guided-results"></div>
			</div>
		`);

		this._bind(panel);
		this._load_catalog();
	}

	_bind(panel) {
		panel.on("change", ".ch-guided-category", () => {
			this.selected_category = panel.find(".ch-guided-category").val() || "";
			this._populate_sub_categories();
		});

		panel.on("change", ".ch-guided-sub-category", () => {
			this.selected_sub_category = panel.find(".ch-guided-sub-category").val() || "";
		});

		panel.on("click", ".ch-guided-load", () => this._load_questions());
		panel.on("click", ".ch-guided-run", () => this._run_recommendations());
		panel.on("click", ".ch-guided-reset", () => this.render(panel));

		panel.on("click", ".ch-guided-add", (e) => {
			const idx = Number($(e.currentTarget).data("idx") || 0);
			const rec = this.recommendations[idx];
			if (!rec) return;
			EventBus.emit("cart:add_item", {
				item_code: rec.item_code,
				item_name: rec.item_name,
				selling_price: rec.price || 0,
				mrp: rec.price || 0,
				stock_qty: rec.stock_qty || 0,
				has_serial_no: rec.has_serial_no || 0,
				stock_uom: rec.stock_uom || "Nos",
				must_be_whole_number: rec.must_be_whole_number || 0,
				offers: [],
			});
			frappe.show_alert({
				message: __("Added {0} to cart", [rec.item_name || rec.item_code]),
				indicator: "green",
			});
		});

		panel.on("click", ".ch-guided-go-sell", () => {
			PosState.active_mode = "sell";
			EventBus.emit("mode:switch", "sell");
			EventBus.emit("mode:set", "sell");
		});
	}

	_load_catalog() {
		frappe.xcall("ch_pos.api.guided.get_guided_catalog")
			.then((data) => {
				this.catalog = data || { categories: [], sub_categories: [] };
				this._populate_categories();
			})
			.catch(() => {
				frappe.show_alert({ message: __("Could not load guided catalog"), indicator: "orange" });
			});
	}

	_populate_categories() {
		const sel = this.panel.find(".ch-guided-category");
		const options = (this.catalog.categories || []).map((c) => {
			const v = frappe.utils.escape_html(c.name);
			const l = frappe.utils.escape_html(c.category_name || c.name);
			return `<option value="${v}">${l}</option>`;
		}).join("");
		sel.append(options);
	}

	_populate_sub_categories() {
		const sel = this.panel.find(".ch-guided-sub-category");
		sel.html(`<option value="">${__("Select Sub Category")}</option>`);
		this.selected_sub_category = "";

		if (!this.selected_category) return;
		const rows = (this.catalog.sub_categories || []).filter((s) => s.category === this.selected_category);
		for (const s of rows) {
			const name = frappe.utils.escape_html(s.name);
			const label = frappe.utils.escape_html(s.sub_category_name || s.name);
			sel.append(`<option value="${name}">${label}</option>`);
		}
	}

	_load_questions() {
		if (!this.selected_sub_category) {
			frappe.show_alert({ message: __("Select a sub category first"), indicator: "orange" });
			return;
		}

		const qWrap = this.panel.find(".ch-guided-questions");
		qWrap.html(`
			<div class="ch-guided-card" style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin-bottom:12px;">
				<div style="text-align:center;padding:18px;color:#6b7280"><i class="fa fa-spinner fa-spin"></i> ${__("Loading questions...")}</div>
			</div>
		`);

		frappe.xcall("ch_pos.api.guided.get_guided_questions", {
			sub_category: this.selected_sub_category,
		}).then((questions) => {
			this.questions = questions || [];
			this._render_questions();
		}).catch(() => {
			qWrap.html("");
			frappe.show_alert({ message: __("Could not load guided questions"), indicator: "red" });
		});
	}

	_render_questions() {
		const qWrap = this.panel.find(".ch-guided-questions");
		if (!this.questions.length) {
			qWrap.html("");
			frappe.show_alert({ message: __("No guided questions configured for this sub category"), indicator: "orange" });
			return;
		}

		const fields = this.questions.map((q, i) => {
			const key = frappe.utils.escape_html(q.key || `q_${i}`);
			const label = frappe.utils.escape_html(q.question || key);

			if (q.type === "range") {
				const min = Number(q.options?.min ?? 0);
				const max = Number(q.options?.max ?? 200000);
				const step = Number(q.options?.step ?? 1000);
				return `
					<div style="margin-bottom:12px;">
						<label style="font-size:12px;color:#6b7280">${label}</label>
						<input type="number" class="form-control ch-guided-answer" data-key="${key}" data-type="range"
							min="${min}" max="${max}" step="${step}" placeholder="${__("Enter amount")}">
					</div>
				`;
			}

			if (q.type === "multi") {
				const options = (q.options || []).map((opt) => {
					const esc = frappe.utils.escape_html(opt);
					return `<label style="display:inline-flex;align-items:center;margin-right:12px;margin-bottom:4px;font-weight:400">
						<input type="checkbox" class="ch-guided-answer-multi" data-key="${key}" value="${esc}" style="margin-right:6px"> ${esc}
					</label>`;
				}).join("");
				return `
					<div style="margin-bottom:12px;">
						<label style="font-size:12px;color:#6b7280;display:block">${label}</label>
						<div>${options}</div>
					</div>
				`;
			}

			const options = ["<option value=''>" + __("Select") + "</option>"]
				.concat((q.options || []).map((opt) => {
					const esc = frappe.utils.escape_html(opt);
					return `<option value="${esc}">${esc}</option>`;
				}))
				.join("");
			return `
				<div style="margin-bottom:12px;">
					<label style="font-size:12px;color:#6b7280">${label}</label>
					<select class="form-control ch-guided-answer" data-key="${key}" data-type="choice">${options}</select>
				</div>
			`;
		}).join("");

		qWrap.html(`
			<div class="ch-guided-card" style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin-bottom:12px;">
				<div style="font-weight:600;margin-bottom:8px">${__("Step 2: Discovery Questions")}</div>
				${fields}
				<div style="display:flex;gap:8px;">
					<button class="btn btn-primary ch-guided-run"><i class="fa fa-magic"></i> ${__("Get Recommendations")}</button>
					<button class="btn btn-default ch-guided-reset">${__("Reset")}</button>
				</div>
			</div>
		`);
	}

	_collect_responses() {
		const responses = [];
		this.panel.find(".ch-guided-answer").each((_, el) => {
			const $el = $(el);
			const key = $el.data("key");
			const answer = ($el.val() || "").toString().trim();
			if (!answer) return;
			responses.push({ key, answer });
		});

		const multiByKey = {};
		this.panel.find(".ch-guided-answer-multi:checked").each((_, el) => {
			const $el = $(el);
			const key = $el.data("key");
			const value = ($el.val() || "").toString();
			if (!multiByKey[key]) multiByKey[key] = [];
			multiByKey[key].push(value);
		});
		for (const key of Object.keys(multiByKey)) {
			responses.push({ key, answer: multiByKey[key].join(", ") });
		}

		return responses;
	}

	_run_recommendations() {
		const responses = this._collect_responses();
		if (!responses.length) {
			frappe.show_alert({ message: __("Please answer at least one question"), indicator: "orange" });
			return;
		}

		const resWrap = this.panel.find(".ch-guided-results");
		resWrap.html(`
			<div class="ch-guided-card" style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;">
				<div style="text-align:center;padding:18px;color:#6b7280"><i class="fa fa-spinner fa-spin"></i> ${__("Building recommendations...")}</div>
			</div>
		`);

		frappe.xcall("ch_pos.api.guided.get_guided_recommendations", {
			sub_category: this.selected_sub_category,
			responses,
			warehouse: PosState.warehouse,
			limit: 8,
		}).then((rows) => {
			this.recommendations = rows || [];
			return frappe.xcall("ch_pos.api.guided.save_guided_session", {
				session_name: PosState.guided_session || null,
				pos_profile: PosState.pos_profile,
				category: this.selected_category,
				sub_category: this.selected_sub_category,
				kiosk_token: PosState.kiosk_token || null,
				responses,
				recommendations: this.recommendations,
				status: "Completed",
			});
		}).then((saved) => {
			if (saved && saved.name) {
				PosState.guided_session = saved.name;
			}
			this._render_results();
		}).catch((e) => {
			console.error("Guided recommendations failed", e);
			resWrap.html("");
			frappe.show_alert({ message: __("Could not generate recommendations"), indicator: "red" });
		});
	}

	_render_results() {
		const resWrap = this.panel.find(".ch-guided-results");
		if (!this.recommendations.length) {
			resWrap.html(`
				<div class="ch-guided-card" style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;">
					<div class="text-muted" style="text-align:center;padding:14px">${__("No matching items found")}</div>
				</div>
			`);
			return;
		}

		const cards = this.recommendations.map((r, idx) => {
			const score = Number(r.match_score || 0);
			const price = Number(r.price || 0);
			const stock = Number(r.stock_qty || 0);
			const inStock = stock > 0;
			return `
				<div style="border:1px solid #e5e7eb;border-radius:10px;padding:12px;background:#fff;display:flex;gap:12px;align-items:flex-start;margin-bottom:8px;">
					<div style="width:54px;height:54px;border-radius:8px;background:#f9fafb;border:1px solid #f1f5f9;display:flex;align-items:center;justify-content:center;overflow:hidden;flex-shrink:0;">
						${r.image ? `<img src="${r.image}" alt="" style="max-width:100%;max-height:100%;object-fit:cover">` : `<i class="fa fa-mobile" style="font-size:22px;color:#64748b"></i>`}
					</div>
					<div style="flex:1;min-width:0;">
						<div style="font-weight:600;line-height:1.2;">${frappe.utils.escape_html(r.item_name || r.item_code)}</div>
						<div style="font-size:12px;color:#6b7280;margin-top:2px;">${frappe.utils.escape_html(r.brand || "")}</div>
						<div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">
							<span style="font-size:11px;background:#ecfeff;color:#155e75;padding:2px 8px;border-radius:999px;">${__("Score")} ${score}%</span>
							<span style="font-size:11px;background:${inStock ? "#dcfce7" : "#fee2e2"};color:${inStock ? "#166534" : "#991b1b"};padding:2px 8px;border-radius:999px;">
								${inStock ? __("In stock") : __("Out of stock")}
							</span>
						</div>
						${r.reason ? `<div style="font-size:12px;color:#475569;margin-top:6px">${frappe.utils.escape_html(r.reason)}</div>` : ""}
					</div>
					<div style="text-align:right;min-width:130px;">
						<div style="font-weight:700;color:#0f172a;">₹${format_number(price)}</div>
						<button class="btn btn-sm btn-primary ch-guided-add" data-idx="${idx}" style="margin-top:8px" ${inStock ? "" : "disabled"}>
							<i class="fa fa-cart-plus"></i> ${__("Add to Cart")}
						</button>
					</div>
				</div>
			`;
		}).join("");

		const sessionBadge = PosState.guided_session
			? `<span style="font-size:11px;background:#f1f5f9;color:#334155;padding:3px 8px;border-radius:999px">${__("Session")}: ${frappe.utils.escape_html(PosState.guided_session)}</span>`
			: "";

		resWrap.html(`
			<div class="ch-guided-card" style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;">
				<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px;">
					<div style="font-weight:600">${__("Step 3: Recommendations")}</div>
					${sessionBadge}
				</div>
				${cards}
				<div style="display:flex;justify-content:flex-end;margin-top:10px;">
					<button class="btn btn-default ch-guided-go-sell"><i class="fa fa-shopping-bag"></i> ${__("Go to Sell")}</button>
				</div>
			</div>
		`);
	}
}
