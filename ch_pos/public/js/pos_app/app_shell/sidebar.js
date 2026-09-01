/**
 * CH POS — Premium Sidebar
 *
 * Retail-grade mode switcher with icon pills, grouped sections,
 * operator/store identity, and network status.
 *
 * Access control: the server resolves allowed_modes per company
 * (based on CH Store capability flags or company type).
 * The sidebar simply renders what the server says — no client-side heuristics.
 */
import { PosState, EventBus } from "../state.js";
import { validate_india_phone, wh_label } from "../shared/helpers.js";

const MODE_SECTIONS = [
	{
		label: __("Sales"),
		modes: [
			{ key: "sell",    icon: "fa-shopping-bag", label: __("Sell") },
			{ key: "queue",   icon: "fa-ticket",       label: __("Queue") },
			{ key: "returns", icon: "fa-undo",         label: __("Returns") },
			{ key: "prebook", icon: "fa-bookmark",     label: __("Pre-Book / Proforma") },
			{ key: "pickup",  icon: "fa-cube",         label: __("Pickup / Bill") },
		],
	},
	{
		label: __("Services"),
		modes: [
			{ key: "buyback", icon: "fa-exchange",  label: __("Buyback") },
			{ key: "repair",  icon: "fa-wrench",    label: __("Repair") },
			{ key: "service", icon: "fa-cogs",      label: __("Service") },
			{ key: "claims",  icon: "fa-shield",    label: __("Claims") },
			{ key: "exceptions", icon: "fa-exclamation-triangle", label: __("Bill Exceptions") },
		],
	},
	{
		label: __("Inventory"),
		modes: [
			{ key: "material_request", icon: "fa-clipboard",    label: __("Request Stock") },
			{ key: "inbound_receive",  icon: "fa-inbox",        label: __("Inbound Receive") },
			{ key: "stock_transfer",   icon: "fa-truck",        label: __("Transfers") },
			{ key: "bin_manager",      icon: "fa-th-large",     label: __("Bin Manager") },
			{ key: "stock_audit",      icon: "fa-balance-scale",label: __("Stock Audit") },
		],
	},
	{
		label: __("Sales Tools"),
		modes: [
			{ key: "guided", icon: "fa-compass", label: __("Guided") },
			{ key: "model_compare", icon: "fa-columns", label: __("Compare") },
		],
	},
	{
		label: __("Lookup"),
		modes: [
			{ key: "imei",        icon: "fa-barcode",     label: __("IMEI") },
			{ key: "customer360", icon: "fa-user-circle", label: __("Customers") },
		],
	},
	{
		label: __("Insights"),
		modes: [
			{ key: "reports", icon: "fa-bar-chart", label: __("Dashboard") },
		],
	},
];

export class Sidebar {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.collapsed = localStorage.getItem("ch_pos_sidebar_collapsed") === "1";
		this._allowed_modes = null; // computed after profile loads
		this.render();
		this.bind();
		this._auto_responsive();
		if (this.collapsed) this._apply_collapsed(true);
	}

	/** Auto-collapse sidebar on narrow screens */
	_auto_responsive() {
		if (!window.matchMedia) return;
		const mql = window.matchMedia("(max-width: 1024px)");
		const handler = (e) => {
			if (e.matches && !this.collapsed) {
				this.collapsed = true;
				this._apply_collapsed(true);
			} else if (!e.matches && this.collapsed &&
				localStorage.getItem("ch_pos_sidebar_collapsed") !== "1") {
				this.collapsed = false;
				this._apply_collapsed(false);
			}
		};
		mql.addEventListener("change", handler);
		if (mql.matches && !this.collapsed) {
			this.collapsed = true;
			this._apply_collapsed(true);
		}
	}

	/** Compute which modes the current user can access (server-driven). */
	_compute_allowed_modes() {
		const access = PosState.executive_access;
		const active = PosState.active_company;

		if (access && access.companies && access.companies.length) {
			// Find modes for the active company, or union all if none selected
			const match = active
				? access.companies.find(c => c.company === active)
				: null;

			// NB: an EMPTY allowed_modes array means "unconfigured", not
			// "deny everything" — treating [] as a restriction blanked the
			// whole sidebar for every non-privileged cashier.
			if (match && match.allowed_modes && match.allowed_modes.length) {
				this._allowed_modes = new Set(match.allowed_modes);
				return;
			}

			// Union of all accessible companies' modes
			const union = new Set();
			for (const cr of access.companies) {
				(cr.allowed_modes || []).forEach(m => union.add(m));
			}
			if (union.size) {
				this._allowed_modes = union;
				return;
			}
		}

		// No executive data — show all (admin fallback)
		this._allowed_modes = null;
	}

	_is_mode_allowed(modeKey) {
		if (!this._allowed_modes) return true;
		if (modeKey === "bin_manager" && this._allowed_modes.has("stock_transfer")) {
			return true;
		}
		return this._allowed_modes.has(modeKey);
	}

	render() {
		this._compute_allowed_modes();

		let html = `
			<button class="ch-pos-sidebar-toggle" title="${__("Toggle sidebar")}">
				<i class="fa fa-bars"></i>
			</button>`;

		for (const section of MODE_SECTIONS) {
			// Filter modes in this section by access
			const visible_modes = section.modes.filter((m) => this._is_mode_allowed(m.key));
			if (!visible_modes.length) continue; // hide entire section

			html += `<div class="ch-pos-sidebar-section">${section.label}</div>`;
			for (const mode of visible_modes) {
				const active = mode.key === PosState.active_mode ? " active" : "";
				html += `
					<button class="ch-pos-sidebar-item${active}"
						data-mode="${mode.key}"
						title="${mode.label}">
						<span class="sidebar-icon"><i class="fa ${mode.icon}"></i></span>
						<span class="sidebar-label">${mode.label}</span>
					</button>`;
			}
		}

		// Bottom: Store identity + operator + executive badge + network
		const access_data = PosState.executive_access;
		const exec = (access_data?.own_by_company || {})[PosState.active_company]
			|| access_data?.own_executive;
		const exec_name = exec ? exec.executive_name : (frappe.session.user_fullname || frappe.session.user);
		const initials = (exec_name || "U").split(" ").map(w => w[0]).join("").substring(0, 2).toUpperCase();
		// Store identity, not bin identity — the cashier only ever sells from
		// the Sellable bin, so "GG-KELLYS-Sellable - BM" reads as "Gogizmo - Kellys".
		// wh_label() returns escaped markup, hence no second escape below.
		const store = wh_label(PosState.warehouse)
			|| frappe.utils.escape_html(PosState.pos_profile || "");
		const role_badge = exec ? `<span class="sidebar-role-badge sidebar-role-${(exec.role || "").toLowerCase().replace(/\s+/g, "-")}">${frappe.utils.escape_html(exec.role)}</span>` : "";
		const company_badge = PosState.active_company
			? `<span class="sidebar-company-badge">${frappe.utils.escape_html(PosState.active_company)}</span>`
			: "";

		html += `
			<div class="ch-pos-sidebar-bottom">
				<div class="store-info">
					<div class="store-avatar">${frappe.utils.escape_html(initials)}</div>
					<div class="store-detail">
						<span class="store-name">${store}</span>
						<span class="store-operator">${frappe.utils.escape_html(exec_name)} ${role_badge}</span>
						${company_badge}
					</div>
				</div>
				<div class="ch-pos-sidebar-status">
					<span class="ch-pos-online-dot${navigator.onLine ? "" : " offline"}"></span>
					<span class="ch-pos-online-label">${navigator.onLine ? __("Online") : __("Offline")}</span>
				</div>
			</div>`;

		this.wrapper.html(html);

		// Hook point for session controls (rendered by SessionControls externally)
		EventBus.emit("sidebar:rendered", this.wrapper);
	}

	/** Re-render store info after profile loads */
	update_store_info() {
		// Full re-render to pick up executive access + module filtering
		this.render();
		if (this.collapsed) this._apply_collapsed(true);

		// The active mode may have been restored from localStorage before
		// real permissions were known (see get_remembered_mode) — if it
		// turns out this operator can't access it here, fall back rather
		// than leaving the workspace panel stuck on a now-hidden mode.
		if (!this._is_mode_allowed(PosState.active_mode)) {
			const first_valid = this.wrapper.find(".ch-pos-sidebar-item").first().data("mode");
			if (first_valid && first_valid !== PosState.active_mode) {
				PosState.active_mode = first_valid;
				Sidebar._remember_mode(first_valid);
				EventBus.emit("mode:switch", first_valid);
			}
		}
	}

	_show_walkin_dialog() {
		// Raw CH Model link value for whatever device_model currently has
		// selected — kept so the Item search below can filter by it. The
		// human-readable model_name is resolved once at submit time instead
		// of being tracked live (see primary_action below).
		let selected_model_value = "";
		const d = new frappe.ui.Dialog({
			title: __("Log Walk-in"),
			fields: [
				{
					label: __("Purpose"),
					fieldname: "visit_purpose",
					fieldtype: "Select",
					options: "Enquiry\nRepair\nSales\nBuyback\nOther",
					default: "Enquiry",
					reqd: 1,
				},
				{
					label: __("Customer Name"),
					fieldname: "customer_name",
					fieldtype: "Data",
					placeholder: __("Optional"),
				},
				{
					label: __("Phone"),
					fieldname: "customer_phone",
					fieldtype: "Data",
					placeholder: __("Optional — 10 digits starting with 6-9"),
					description: __("Indian mobile number (optional)"),
				},
				{
					fieldtype: "Section Break",
					label: __("What are they looking for?"),
					collapsible: 0,
				},
				{
					// Category leads the cascade: it is the one thing the
					// counter always knows ("they asked about laptops") even
					// when the customer never names a brand or model.
					label: __("Category"),
					fieldname: "ch_category",
					fieldtype: "Link",
					options: "CH Category",
					placeholder: __("e.g. Mobile, Laptop, Accessories"),
					get_query: () => ({
						query: "ch_pos.api.item_search.search_categories",
					}),
					onchange: () => {
						// Everything downstream is scoped by category, so a
						// change invalidates the picks made under the old one
						// rather than leaving a brand that sells nothing here.
						d.set_value("device_brand", "");
						d.set_value("device_model", "");
						d.set_value("item_code", "");
						selected_model_value = "";
					},
				},
				{ fieldtype: "Column Break" },
				{
					label: __("Brand"),
					fieldname: "device_brand",
					fieldtype: "Link",
					options: "Brand",
					placeholder: __("e.g. Apple, Samsung, OnePlus"),
					// Default Link search doesn't return results inside this
					// SPA dialog context — same fix as item_code below, an
					// explicit custom query against a whitelisted function.
					get_query: () => ({
						query: "ch_pos.api.item_search.search_brands",
						filters: d.get_value("ch_category")
							? { ch_category: d.get_value("ch_category") }
							: {},
					}),
					onchange: () => {
						// Model is scoped to the chosen brand — clear any
						// stale pick from a different brand instead of
						// leaving a mismatched model selected.
						d.set_value("device_model", "");
						selected_model_value = "";
					},
				},
				{ fieldtype: "Column Break" },
				{
					label: __("Model"),
					fieldname: "device_model",
					// Autocomplete, not Link: CH Model's own name is an internal
					// compound key, and CH Model has show_title_field_in_link on
					// so a Link field's own display should have shown model_name
					// instead — but that native title write races against
					// selection and left the field blank after picking a model.
					// Autocomplete keeps {value, label} pairs client-side and
					// looks the display label up locally on every render, so
					// there's no async round trip left to race.
					fieldtype: "Autocomplete",
					placeholder: __("e.g. iPhone 15, Galaxy S24"),
					get_query: () => {
						const params = {};
						const brand = d.get_value("device_brand");
						const category = d.get_value("ch_category");
						if (brand) params.brand = brand;
						if (category) params.ch_category = category;
						return {
							query: "ch_pos.api.item_search.autocomplete_ch_models",
							params,
						};
					},
					onchange: () => {
						selected_model_value = d.get_value("device_model") || "";
					},
				},
			{
                label: __("Item / Product Interest"),
                fieldname: "item_code",
                fieldtype: "Link",
                options: "Item",
                placeholder: __("Optional — pick a catalogue item"),

                get_query: () => {
                    // Narrowed by whatever the counter has given us so far.
                    // Never narrowed by stock: an item we cannot supply is the
                    // demand this form exists to record.
                    const filters = {};
                    if (selected_model_value) filters.ch_model = selected_model_value;
                    const category = d.get_value("ch_category");
                    if (category) filters.ch_category = category;
                    return {
                        query: "ch_pos.api.item_search.search_items_by_name",
                        filters,
                    };
                },

                // After selection: real item_code is stored,
                // but we replace the input's visible text with item_name
                onchange: function () {
                    const item_code = d.get_value("item_code");
                    if (!item_code) return;

                    frappe.db.get_value("Item", item_code, "item_name", (r) => {
                        if (r && r.item_name) {
                            const field = d.get_field("item_code");
                            // Replace visible input text with item_name
                            // (the underlying value remains item_code)
                            if (field && field.$input) {
                                field.$input.val(r.item_name);
                            }
                        }
                    });
                },
            },


				
				{
					label: __("Remarks"),
					fieldname: "remarks",
					fieldtype: "Small Text",
					placeholder: __("Optional — what did the customer need?"),
				},
			],
			primary_action_label: __("Log Walk-in"),
			primary_action: (values) => {
				// Final guard — live validation already prevents most bad input
				if (values.customer_phone && !validate_india_phone(values.customer_phone)) {
					this._mark_phone_invalid(d, __("Enter a valid Indian phone number (10 digits starting with 6-9)"));
					frappe.show_alert({
						message: __("Please fix the phone number before continuing"),
						indicator: "red",
					});
					return;
				}

				d.hide();
				const log_walkin = (device_model_name) => {
					frappe.call({
						method: "ch_pos.api.token_api.log_counter_walkin",
						args: {
							pos_profile: PosState.pos_profile,
							visit_purpose: values.visit_purpose,
							customer_name: values.customer_name || "",
							customer_phone: values.customer_phone || "",
							remarks: values.remarks || "",
							ch_category: values.ch_category || "",
							device_brand: values.device_brand || "",
							device_model: device_model_name || values.device_model || "",
							item_code: values.item_code || "",
							// Captured at intake so the token is linked from birth;
							// the server re-checks it and falls back to the phone.
							linked_customer: d._linked_customer || "",
						},
						callback: (r) => {
							const res = r.message || {};
							if (res.status === "ok") {
								frappe.show_alert({
									message: __("Walk-in logged: {0} ({1})", [res.token, res.visit_purpose]),
									indicator: "green",
								});
								EventBus.emit("walkin:logged", res);
							} else {
								frappe.show_alert({ message: __("Could not log walk-in"), indicator: "orange" });
							}
						},
					});
				};

				// Resolve the readable model_name once, here, instead of
				// tracking it live through the dialog — CH Model's own name
				// is the ugly compound key, so values.device_model alone
				// isn't what should be logged.
				if (values.device_model) {
					frappe.db.get_value("CH Model", values.device_model, "model_name", (r) => {
						log_walkin((r && r.model_name) || values.device_model);
					});
				} else {
					log_walkin("");
				}
			},
		});
		d.show();

		// Attach live phone validation after the dialog DOM is ready
		this._attach_phone_live_validation(d, "customer_phone");
		// ...then identify the customer behind that number.
		this._attach_walkin_customer_lookup(d);
	}

	/**
	 * Recognise a returning customer from the phone number as it is typed.
	 *
	 * The counter already has the number in hand; asking the system who it
	 * belongs to at THAT moment is what lets the token carry `linked_customer`
	 * from birth. Without it the walk-in is just a name string, and the repair
	 * / buyback / bill downstream each re-guess the customer -- which is how
	 * duplicate Customer records get made.
	 */
	_attach_walkin_customer_lookup(dialog) {
		const field = dialog.get_field("customer_phone");
		if (!field || !field.$input) return;

		const $panel = $(`<div class="ch-walkin-cust" style="
			margin-top:6px; font-size:12px; line-height:1.5; display:none;
			padding:6px 8px; border-radius:4px;
		"></div>`);
		field.$wrapper.append($panel);

		dialog._linked_customer = null;
		let last_query = "";
		let timer = null;

		const clear = () => {
			dialog._linked_customer = null;
			$panel.hide().empty();
		};

		const render = (res) => {
			const esc = frappe.utils.escape_html;
			if (!res || !res.found) {
				dialog._linked_customer = null;
				$panel.css({ background: "var(--bg-light-gray, #f4f5f6)", color: "var(--text-muted, #8d99a6)" })
					.text(__("No existing customer with this number — a new one will be created at billing."))
					.show();
				return;
			}
			if (res.restricted) {
				// Deliberately no name, no history: the number is known to the
				// group but this store has never transacted with them.
				dialog._linked_customer = null;
				$panel.css({ background: "#fff8e6", color: "#8a6d1f" })
					.html(`<b>${__("Existing customer at another store.")}</b> ${
						__("A manager can link it — do not create a duplicate.")}`)
					.show();
				return;
			}

			dialog._linked_customer = res.customer;
			const bits = [];
			if (res.visits) {
				bits.push(res.visits === 1 ? __("1 visit") : __("{0} visits", [res.visits]));
			}
			if (res.last_visit) bits.push(__("last {0}", [frappe.datetime.str_to_user(res.last_visit)]));
			if (res.email_id) bits.push(esc(res.email_id));

			$panel.css({ background: "#eaf5ee", color: "#1f7a3d" }).html(
				`<b>✓ ${__("Existing customer")}</b> — ${esc(res.customer_name || res.customer)}` +
				(bits.length ? `<div style="opacity:.85">${bits.join(" · ")}</div>` : "") +
				`<div style="opacity:.7">${__("This walk-in will be linked to {0}.", [esc(res.customer)])}</div>`
			).show();

			// Fill the name only if the operator has not typed one — never
			// overwrite what a human entered.
			const name_field = dialog.get_field("customer_name");
			if (name_field && name_field.$input && !(dialog.get_value("customer_name") || "").trim()) {
				dialog.set_value("customer_name", res.customer_name || "");
			}
		};

		const lookup = () => {
			const digits = (field.$input.val() || "").replace(/\D/g, "").slice(-10);
			if (!/^[6-9]\d{9}$/.test(digits)) {
				last_query = "";
				clear();
				return;
			}
			if (digits === last_query) return;
			last_query = digits;

			$panel.css({ background: "var(--bg-light-gray, #f4f5f6)", color: "var(--text-muted, #8d99a6)" })
				.text(__("Checking…")).show();

			frappe.xcall("ch_pos.api.token_api.lookup_walkin_customer", {
				phone: digits,
				pos_profile: PosState.pos_profile,
			}).then((res) => {
				// The operator may have typed on since this call left.
				const now = (field.$input.val() || "").replace(/\D/g, "").slice(-10);
				if (now === digits) render(res);
			}).catch(() => {
				// Identification is a convenience; never block logging a walk-in.
				clear();
			});
		};

		const debounced = () => {
			clearTimeout(timer);
			timer = setTimeout(lookup, 350);
		};

		field.$input.on("input", debounced);
		field.$input.on("blur", lookup);
		field.$input.on("paste", () => setTimeout(lookup, 0));
	}

	_attach_phone_live_validation(dialog, fieldname) {
		const field = dialog.get_field(fieldname);
		if (!field || !field.$input) return;

		const $input = field.$input;

		// Inline error element (created once)
		const $err = $(`<div class="ch-phone-error" style="
			color:#d9534f; font-size:12px; margin-top:4px; display:none;
		"></div>`);
		field.$wrapper.append($err);

		$input.attr({
			maxlength: 15, 
			inputmode: "tel",
			autocomplete: "tel",
		});

		const $primary = dialog.get_primary_btn();
		const normalize = (raw) => {
			if (!raw) return "";
			let s = raw.toString().trim();

			s = s.replace(/[\s\-().]/g, "");

			if (/^\+91/.test(s))    s = s.slice(3);
			else if (/^0091/.test(s)) s = s.slice(4);
			else if (/^0\d{10}$/.test(s)) s = s.slice(1);

			s = s.replace(/\D/g, "");
			return s.slice(0, 10);
		};

		const INDIAN_MOBILE = /^[6-9]\d{9}$/;

		const run_validation = () => {
			const raw = $input.val() || "";
			const cleaned = normalize(raw);

			if (cleaned !== raw) {
				$input.val(cleaned);
			}

			// field → empty is valid
			if (!cleaned) {
				$err.hide().text("");
				$input.removeClass("ch-input-invalid").css("border-color", "");
				$primary.prop("disabled", false);
				return true;
			}

			if (cleaned.length < 10) {
				$err.text(__("Enter a valid Indian phone number (10 digits starting with 6-9)")).show();
				$input.addClass("ch-input-invalid").css("border-color", "#d9534f");
				$primary.prop("disabled", true);
				return false;
			}

			if (!INDIAN_MOBILE.test(cleaned)) {
				$err.text(__("Enter a valid Indian phone number (10 digits starting with 6-9)")).show();
				$input.addClass("ch-input-invalid").css("border-color", "#d9534f");
				$primary.prop("disabled", true);
				return false;
			}

			$err.hide().text("");
			$input.removeClass("ch-input-invalid").css("border-color", "");
			$primary.prop("disabled", false);
			return true;
		};

		$input.on("input", run_validation);
		$input.on("blur",  run_validation);
		$input.on("paste", () => setTimeout(run_validation, 0));

		field._validate_phone = run_validation;
	
	}

	bind() {
		const sidebar = this.wrapper;

		// Walk-in Log button — also triggered from the top-right profile area
		sidebar.on("click", ".ch-pos-walkin-btn", () => this._show_walkin_dialog());
		EventBus.on("walkin:open", () => this._show_walkin_dialog());

		// Collapse toggle
		sidebar.on("click", ".ch-pos-sidebar-toggle", () => {
			this.collapsed = !this.collapsed;
			localStorage.setItem("ch_pos_sidebar_collapsed", this.collapsed ? "1" : "0");
			this._apply_collapsed(this.collapsed);
		});

		// Mode navigation
		sidebar.on("click", ".ch-pos-sidebar-item", function () {
			const mode = $(this).data("mode");
			if (mode === PosState.active_mode) return;
			sidebar.find(".ch-pos-sidebar-item").removeClass("active");
			$(this).addClass("active");
			PosState.active_mode = mode;
			Sidebar._remember_mode(mode);
			EventBus.emit("mode:switch", mode);
			// Explicit navigation (unlike the many internal mode:set/
			// mode:switch calls elsewhere that auto-return to Sell after
			// completing a task) — push a real browser history entry so
			// Back steps through recently-visited POS menus instead of
			// leaving the app entirely. ch_pos_app.js's refresh() reacts to
			// the resulting route change on Back/Forward.
			frappe.route_flags.replace_route = false;
			frappe.set_route("ch-pos-app", mode);
		});

		// Browser Back/Forward landed on a different mode's route — mirror
		// the sidebar click handler's UI update, but without pushing
		// another route (the URL already reflects this state).
		EventBus.on("mode:route_change", (mode) => {
			if (mode === PosState.active_mode) return;
			sidebar.find(".ch-pos-sidebar-item").removeClass("active");
			sidebar.find(`.ch-pos-sidebar-item[data-mode="${mode}"]`).addClass("active");
			PosState.active_mode = mode;
			Sidebar._remember_mode(mode);
			EventBus.emit("mode:switch", mode);
		});

		// Programmatic mode set
		EventBus.on("mode:set", (mode) => {
			sidebar.find(".ch-pos-sidebar-item").removeClass("active");
			sidebar.find(`.ch-pos-sidebar-item[data-mode="${mode}"]`).addClass("active");
			PosState.active_mode = mode;
			Sidebar._remember_mode(mode);
		});

		// Network updates
		EventBus.on("network:status", (online) => {
			const dot = sidebar.find(".ch-pos-online-dot");
			const label = sidebar.find(".ch-pos-online-label");
			dot.toggleClass("offline", !online);
			label.text(online ? __("Online") : __("Offline"));
		});

		// Profile loaded → update store info
		EventBus.on("profile:loaded", () => this.update_store_info());

		// Company switched → re-render sidebar to show/hide modules
		EventBus.on("company:switched", () => {
			this.update_store_info();
			// If current mode is no longer allowed, switch to first valid mode
			if (!this._is_mode_allowed(PosState.active_mode)) {
				const first_valid = this.wrapper.find(".ch-pos-sidebar-item").first().data("mode");
				if (first_valid) {
					PosState.active_mode = first_valid;
					Sidebar._remember_mode(first_valid);
					EventBus.emit("mode:switch", first_valid);
				}
			}
		});
	}

	_apply_collapsed(collapsed) {
		const container = this.wrapper.closest(".ch-pos-container");
		if (collapsed) {
			this.wrapper.addClass("collapsed");
			container.addClass("sidebar-collapsed");
			this.wrapper.find(".ch-pos-sidebar-toggle i").removeClass("fa-chevron-left").addClass("fa-chevron-right");
		} else {
			this.wrapper.removeClass("collapsed");
			container.removeClass("sidebar-collapsed");
			this.wrapper.find(".ch-pos-sidebar-toggle i").removeClass("fa-chevron-right").addClass("fa-chevron-left");
		}
	}

	static get ALL_MODES() {
		return MODE_SECTIONS.flatMap((s) => s.modes.map((m) => m.key));
	}
	static get TRANSACTIONAL_MODES() {
		// "service" keeps the cart visible so a completed repair can be
		// added to the bill directly from the Service Tracker.
		return ["sell", "returns", "buyback", "repair", "service"];
	}
	static get NON_TRANSACTIONAL_MODES() {
		return ["imei", "customer360", "reports", "material_request", "inbound_receive", "stock_transfer", "bin_manager", "stock_audit", "guided", "model_compare", "claims", "exceptions", "queue"];
	}

	static get ALL_MODE_KEYS() {
		return MODE_SECTIONS.flatMap((section) => section.modes.map((m) => m.key));
	}

	/** Persist the active mode so a browser refresh lands back on it instead
	 * of always resetting to Sell — mirrors the existing sidebar-collapsed
	 * localStorage pattern just above. */
	static _remember_mode(mode) {
		try {
			localStorage.setItem("ch_pos_active_mode", mode);
		} catch (e) { /* ignore storage issues in private mode/restricted browsers */ }
	}

	/** Read back the last active mode on boot, validated against the known
	 * mode keys — an unrecognised/stale value (e.g. after an app update
	 * renamed a mode) falls back to Sell rather than breaking the boot. */
	static get_remembered_mode() {
		try {
			const mode = localStorage.getItem("ch_pos_active_mode");
			return mode && Sidebar.ALL_MODE_KEYS.includes(mode) ? mode : "sell";
		} catch (e) {
			return "sell";
		}
	}
}
