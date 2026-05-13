/**
 * CH Shared New Customer Dialog
 *
 * Loaded on every page via app_include_js.
 * Exposes window.ch_open_new_customer_dialog({company, on_success, on_use_existing})
 *
 * on_success(customer_name, mobile_no)       — new customer created
 * on_use_existing(customer_id, customer_name, mobile_no) — user clicked "Use it" on existing
 */
(function () {
	"use strict";

	// ── Phone validation (inline, no ES-module dependency) ──────────────────
	function validate_india_phone(val) {
		const clean = (val || "").replace(/[\s\-().]/g, "");
		const stripped = clean
			.replace(/^\+91/, "")
			.replace(/^0091/, "")
			.replace(/^0(?=[6-9])/, "")
			.replace(/^0(?=\d{9,10}$)/, "");
		if (/^[6-9]\d{9}$/.test(stripped)) return true;
		const withZero = clean.replace(/^\+91|^0091/, "");
		if (/^0[1-9]\d{8,9}$/.test(withZero)) return true;
		if (/^[1-9][1-9]\d{6,8}$/.test(stripped)) return true;
		return false;
	}

	function assert_india_phone(input, val) {
		const $el = $(input);
		if (!val) { $el.removeClass("ch-phone-invalid"); return true; }
		if (validate_india_phone(val)) {
			$el.removeClass("ch-phone-invalid").attr("title", "");
			return true;
		}
		$el.addClass("ch-phone-invalid").attr("title", __("Enter a valid Indian phone number"));
		frappe.show_alert({ message: __("Enter a valid Indian phone number (mobile or landline)"), indicator: "orange" });
		return false;
	}

	// ── Main exported function ────────────────────────────────────────────────
	window.ch_open_new_customer_dialog = function (options) {
		const opts = options || {};
		const company = opts.company || (typeof frappe !== "undefined" && frappe.defaults && frappe.defaults.get_default("company")) || "";

		let otp_verified_number = "";
		let last_auto_synced_mobile = "";
		let duplicate_check_timer = null;
		let last_duplicate_phone_checked = "";
		let whatsapp_manually_edited = false;
		let syncing_whatsapp = false;
		let $mobile_status_div = $();
		let autofill_watch_timer = null;
		let last_otp_trigger_at = 0;
		let otp_request_in_flight = false;

		const status_html = (message, color = "#6b7280") =>
			`<div style="font-size:12px;color:${color};padding-top:4px">${frappe.utils.escape_html(message || "")}</div>`;

		const dialog_alert = (message, indicator = "red") => {
			const alert_class = indicator === "red" ? "danger" : indicator === "orange" ? "warning" : "info";
			if (d?.set_alert) d.set_alert(frappe.utils.escape_html(message), alert_class);
			frappe.show_alert({ message, indicator });
		};

		const input_value = (fieldname) => {
			const f = d.fields_dict[fieldname];
			let v = (f && f.$input && f.$input.val()) || "";
			if (!v) {
				const $i = d.$wrapper.find(`[data-fieldname="${fieldname}"] input`).first();
				v = ($i.length ? $i.val() : "") || "";
			}
			if (!v) v = d.get_value(fieldname) || "";
			return String(v).trim();
		};

		const input_el = (fieldname) => {
			const f = d.fields_dict[fieldname];
			if (f && f.$input && f.$input[0]) return f.$input[0];
			const $i = d.$wrapper.find(`[data-fieldname="${fieldname}"] input`).first();
			return $i[0] || null;
		};

		const validate_email_input = () => {
			const email = input_value("email_id");
			if (!email) return true;
			const valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
			if (!valid) {
				frappe.show_alert({ message: __("Enter a valid Email address"), indicator: "red" });
				d.fields_dict.email_id.$input.focus();
			}
			return valid;
		};

		const otp_error_message = (err, fallback) => {
			// Try to extract Frappe server-side message (from frappe.throw)
			let msg = "";
			try {
				const sm = (err && err._server_messages)
					|| (frappe.last_response && frappe.last_response._server_messages);
				if (sm) {
					const parsed = JSON.parse(sm);
					if (parsed && parsed.length) {
						const first = typeof parsed[0] === "string" ? JSON.parse(parsed[0]) : parsed[0];
						msg = (first && (first.message || first)) || "";
						// strip HTML tags
						msg = String(msg).replace(/<[^>]+>/g, "").trim();
					}
				}
			} catch (e) { /* ignore parse errors */ }

			if (!msg) msg = (err && (err.message || err.exc)) || "";
			if (String(msg).includes("Purpose cannot be")) {
				return __("OTP setup is not configured for customer verification. Please contact administrator.");
			}
			return msg || fallback;
		};

		const check_existing_customer = (phone_no) => {
			return frappe.xcall("ch_pos.api.pos_api.find_existing_customer_by_phone", { phone_no })
				.then((res) => res || { exists: false })
				.catch((err) => {
					console.error("Customer duplicate check failed", err);
					return { exists: false, error: err };
				});
		};

		const paint_whatsapp_input = (mobile) => {
			const ctl = d.fields_dict.whatsapp_number;
			const selectors = [
				'[data-fieldname="whatsapp_number"] input',
				'[data-fieldname="whatsapp_number"] textarea',
			];
			selectors.forEach((selector) => {
				d.$wrapper.find(selector).each((_, el) => {
					el.value = mobile;
					el.setAttribute("value", mobile);
				});
			});
			if (ctl?.$input?.length) {
				ctl.$input.val(mobile).attr("value", mobile);
			}
			if (ctl) {
				ctl.value = mobile;
				ctl.last_value = mobile;
				ctl.set_input?.(mobile);
			}
		};

		const force_set_whatsapp = (mobile) => {
			syncing_whatsapp = true;
			paint_whatsapp_input(mobile);
			const ctl = d.fields_dict.whatsapp_number;
			if (ctl) { ctl.value = mobile; ctl.last_value = mobile; }
			(d.doc || (d.doc = {})).whatsapp_number = mobile;
			last_auto_synced_mobile = mobile;
			whatsapp_manually_edited = false;
			otp_verified_number = "";
			d.set_value("whatsapp_number", mobile)
				.then(() => paint_whatsapp_input(mobile))
				.catch(() => paint_whatsapp_input(mobile));
			requestAnimationFrame(() => paint_whatsapp_input(mobile));
			setTimeout(() => paint_whatsapp_input(mobile), 25);
			setTimeout(() => { syncing_whatsapp = false; }, 100);
		};

		const sync_whatsapp_from_mobile = () => {
			const mobile = input_value("mobile_no");
			(d.doc || (d.doc = {})).mobile_no = mobile;
			if (!mobile) return;

			const mobile_digits = mobile.replace(/\D/g, "");
			if (mobile_digits.length < 10) {
				if (input_value("whatsapp_number") === last_auto_synced_mobile) {
					force_set_whatsapp("");
				}
				if (typeof $mobile_status_div !== "undefined") $mobile_status_div.empty();
				return;
			}

			const whatsapp = input_value("whatsapp_number");
			const whatsapp_digits = whatsapp.replace(/\D/g, "");
			const whatsapp_is_auto_prefix = whatsapp && mobile_digits.startsWith(whatsapp_digits) && whatsapp_digits.length < 10;
			if (!whatsapp_manually_edited || !whatsapp || whatsapp === last_auto_synced_mobile || whatsapp_is_auto_prefix) {
				force_set_whatsapp(mobile);
			}

			const mobile_suffix = mobile_digits.slice(-10);
			clearTimeout(duplicate_check_timer);
			duplicate_check_timer = setTimeout(async () => {
				if (last_duplicate_phone_checked === mobile_suffix) return;
				last_duplicate_phone_checked = mobile_suffix;
				const hit = await check_existing_customer(mobile);
				if (typeof $mobile_status_div === "undefined") return;
				if (hit && hit.exists) {
					show_existing_banner(hit);
				} else {
					$mobile_status_div.empty();
				}
			}, 350);
		};

		const show_existing_banner = (hit) => {
			if (!hit || !hit.exists || !hit.customer) { $mobile_status_div.empty(); return; }
			const esc_name = frappe.utils.escape_html(hit.customer_name || hit.customer);
			const esc_id = frappe.utils.escape_html(hit.customer);
			$mobile_status_div.html(
				`<div style="font-size:12px;color:#b91c1c;padding-top:4px;display:flex;align-items:center">` +
				`<span>${frappe.utils.escape_html(__("Customer already exists: {0} ({1})", [hit.customer_name || hit.customer, hit.customer]))}</span>` +
				`<button class="btn btn-xs btn-warning ch-use-existing-btn" style="margin-left:8px" data-customer="${esc_id}" data-customer-name="${esc_name}">${__("Use it")}</button>` +
				`</div>`
			);
		};

		const offer_use_existing_customer = (hit) => {
			if (!hit || !hit.exists || !hit.customer) return false;
			show_existing_banner(hit);
			const message = __("Customer already exists: {0}. Click 'Use it' to select.", [hit.customer_name || hit.customer]);
			d.fields_dict.otp_status?.$wrapper.html(status_html(message, "#b45309"));
			dialog_alert(message, "orange");
			return true;
		};

		const d = new frappe.ui.Dialog({
			title: __("New Customer"),
			fields: [
				// ── Basic Info ──
				{ fieldname: "customer_name", fieldtype: "Data", label: __("Customer Name"), reqd: 1 },
				{ fieldname: "mobile_no", fieldtype: "Data", label: __("Mobile Number"), reqd: 1,
				  onchange: () => sync_whatsapp_from_mobile() },
				{ fieldtype: "Column Break" },
				{ fieldname: "email_id", fieldtype: "Data", label: __("Email"), options: "Email" },
				{ fieldname: "customer_group", fieldtype: "Link", label: __("Customer Group"),
				  options: "Customer Group", default: "Individual" },

				// ── Additional Contact ──
				{ fieldtype: "Section Break", label: __("Additional Contact") },
				{ fieldname: "alternate_phone", fieldtype: "Data", label: __("Alternate Number") },
				{ fieldtype: "Column Break" },
				{ fieldname: "whatsapp_number", fieldtype: "Data", label: __("WhatsApp Number"), reqd: 1 },

				{ fieldtype: "Section Break", label: __("WhatsApp Verification") },
				{ fieldname: "otp_code", fieldtype: "Data", label: __("OTP Code") },
				{ fieldtype: "Column Break" },
				{ fieldname: "otp_actions", fieldtype: "HTML", options: `
					<div class="ch-customer-otp-actions" style="display:flex;flex-direction:column;align-items:flex-start;gap:8px;padding-top:2px">
						<button type="button" class="btn btn-default btn-xs ch-send-customer-otp">${__("Send OTP")}</button>
						<button type="button" class="btn btn-default btn-xs ch-verify-customer-otp">${__("Verify OTP")}</button>
					</div>` },
				{ fieldname: "otp_status", fieldtype: "HTML", options: "" },

				// ── Address ──
				{ fieldtype: "Section Break", label: __("Address") },
				{ fieldname: "address_line1", fieldtype: "Data", label: __("Address Line 1") },
				{ fieldname: "address_line2", fieldtype: "Data", label: __("Address Line 2") },
				{ fieldtype: "Column Break" },
				{ fieldname: "city", fieldtype: "Link", options: "CH City", label: __("City"),
				  get_query: () => {
					const filters = { disabled: 0 };
					if (company) filters.company = company;
					return { filters };
				  } },
				{ fieldname: "state", fieldtype: "Link", options: "CH State", label: __("State"), reqd: 1,
				  get_query: () => ({ filters: { disabled: 0 } }) },
				{ fieldtype: "Section Break" },
				{ fieldname: "pincode", fieldtype: "Data", label: __("Pincode") },
				{ fieldname: "area", fieldtype: "Data", label: __("Area / Locality") },
				{ fieldtype: "Column Break" },
				{ fieldname: "gstin", fieldtype: "Data", label: __("GSTIN") },

				// ── Billing / Shipping ──
				{ fieldtype: "Section Break", label: __("Shipping") },
				{ fieldname: "same_as_billing", fieldtype: "Check", label: __("Ship to same as billing address"), default: 1 },
				{ fieldname: "shipping_address_line1", fieldtype: "Data", label: __("Shipping Address Line 1"),
				  depends_on: "eval:!doc.same_as_billing" },
				{ fieldname: "shipping_city", fieldtype: "Data", label: __("Shipping City"),
				  depends_on: "eval:!doc.same_as_billing" },
				{ fieldtype: "Column Break" },
				{ fieldname: "shipping_state", fieldtype: "Link", options: "CH State", label: __("Shipping State"),
				  depends_on: "eval:!doc.same_as_billing", get_query: () => ({ filters: { disabled: 0 } }) },
				{ fieldname: "shipping_pincode", fieldtype: "Data", label: __("Shipping Pincode"),
				  depends_on: "eval:!doc.same_as_billing" },
			],
			size: "large",
			primary_action_label: __("Create"),
			primary_action: async (values) => {
				const customer_name = input_value("customer_name");
				const phone = input_value("mobile_no");
				if (!phone) {
					frappe.show_alert({ message: __("Mobile Number is mandatory"), indicator: "red" });
					return;
				}
				if (!assert_india_phone(input_el("mobile_no"), phone)) return;
				const whatsapp = input_value("whatsapp_number");
				if (!whatsapp || !assert_india_phone(input_el("whatsapp_number"), whatsapp)) return;
				if (!validate_email_input()) return;
				const email = input_value("email_id");

				const existing_by_mobile = phone ? await check_existing_customer(phone) : { exists: false };
				if (offer_use_existing_customer(existing_by_mobile)) return;

				const existing_by_whatsapp = await check_existing_customer(whatsapp);
				if (offer_use_existing_customer(existing_by_whatsapp)) return;

				if (otp_verified_number !== whatsapp) {
					frappe.show_alert({ message: __("Verify WhatsApp OTP before creating customer"), indicator: "red" });
					return;
				}
				frappe.xcall("ch_pos.api.pos_api.quick_create_customer", {
					customer_name,
					mobile_no: phone,
					email_id: email,
					customer_group: values.customer_group || "Individual",
					company: company,
					alternate_phone: values.alternate_phone || "",
					whatsapp_number: whatsapp,
					address_line1: values.address_line1 || "",
					address_line2: values.address_line2 || "",
					city: values.city || "",
					state: values.state || "",
					pincode: values.pincode || "",
					area: values.area || "",
					gstin: values.gstin || "",
					same_as_billing: values.same_as_billing ? 1 : 0,
					shipping_address_line1: values.shipping_address_line1 || "",
					shipping_city: values.shipping_city || "",
					shipping_state: values.shipping_state || "",
					shipping_pincode: values.shipping_pincode || "",
				}).then((name) => {
					d.hide();
					frappe.show_alert({ message: __("Customer {0} created", [name]), indicator: "green" });
					if (opts.on_success) opts.on_success(name, phone);
				});
			},
		});

		d.doc = d.doc || {};
		d.show();
		d.fields_dict.otp_status.$wrapper.html(status_html(__("OTP not verified")));

		// Status div below mobile_no
		$mobile_status_div = $('<div class="ch-mobile-status" style="margin-top:4px"></div>');
		d.$wrapper.find('[data-fieldname="mobile_no"]').after($mobile_status_div);
		$mobile_status_div.on("click", ".ch-use-existing-btn", (e) => {
			const $btn = $(e.currentTarget);
			const customer = $btn.data("customer");
			const cname = $btn.data("customer-name");
			if (!customer) return;
			d.hide();
			frappe.show_alert({ message: __("Selected existing customer {0}", [cname || customer]), indicator: "green" });
			if (opts.on_use_existing) opts.on_use_existing(customer, cname, "");
			else if (opts.on_success) opts.on_success(customer, "");
		});

		// Responsive sizing
		d.$wrapper.find(".modal-dialog").css({ "max-width": "900px" });
		d.$wrapper.find(".modal-body").css({
			"max-height": "calc(100vh - 180px)",
			"overflow-y": "auto",
		});

		// Prevent Enter key from auto-submitting
		d.$wrapper.find("form").on("keydown", (e) => {
			if (e.key === "Enter") e.preventDefault();
		});

		const $body = d.$wrapper.find(".modal-body");

		$body.on("input keyup paste change", '[data-fieldname="mobile_no"] input', () => {
			sync_whatsapp_from_mobile();
		});
		$body.on("blur", '[data-fieldname="mobile_no"] input', () => {
			whatsapp_manually_edited = false;
			sync_whatsapp_from_mobile();
		});
		$body.on("input", '[data-fieldname="whatsapp_number"] input', () => {
			if (syncing_whatsapp) return;
			const val = input_value("whatsapp_number");
			(d.doc || (d.doc = {})).whatsapp_number = val;
			whatsapp_manually_edited = val !== last_auto_synced_mobile;
			otp_verified_number = "";
			d.fields_dict.otp_status.$wrapper.html(status_html(__("OTP not verified")));
		});

		$body.on("awesomplete-selectcomplete change blur", '[data-fieldname="city"] input', () => {
			const city = input_value("city");
			if (!city) return;
			frappe.xcall("frappe.client.get_value", {
				doctype: "CH City",
				filters: { name: city },
				fieldname: ["city_name", "state"],
			}).then((row) => {
				if (!row || !row.state) return;
				frappe.xcall("ch_item_master.ch_item_master.ch_core.doctype.ch_state.ch_state.ensure_state", {
					state_name: row.state,
				}).then((state_name) => {
					const value = state_name || row.state;
					d.set_value("state", value);
					if (d.fields_dict.state.$input) d.fields_dict.state.$input.val(value);
					(d.doc || (d.doc = {})).state = value;
				}).catch(() => {
					d.set_value("state", row.state);
				});
			});
		});

		$body.on("change blur", '[data-fieldname="email_id"] input', validate_email_input);

		const send_otp_handler = async () => {
			if (otp_request_in_flight) return;
			const now = Date.now();
			if (now - last_otp_trigger_at < 350) return;
			last_otp_trigger_at = now;
			sync_whatsapp_from_mobile();
			const phone = input_value("mobile_no");
			const mobile_digits = phone.replace(/\D/g, "");
			if (mobile_digits.length >= 10) force_set_whatsapp(phone);

			d.fields_dict.otp_status.$wrapper.html(status_html(__("Checking customer..."), "#2563eb"));
			if (!phone) { dialog_alert(__("Mobile Number is mandatory"), "red"); return; }
			if (!assert_india_phone(input_el("mobile_no"), phone)) {
				dialog_alert(__("Enter a valid Indian mobile number"), "orange");
				return;
			}

			const whatsapp = input_value("whatsapp_number");
			if (!whatsapp) {
				dialog_alert(__("WhatsApp Number is required to send OTP"), "red");
				d.fields_dict.otp_status.$wrapper.html(status_html(__("WhatsApp Number is required to send OTP"), "#b91c1c"));
				return;
			}
			if (!assert_india_phone(input_el("whatsapp_number"), whatsapp)) {
				dialog_alert(__("Enter a valid WhatsApp Number before sending OTP"), "orange");
				d.fields_dict.otp_status.$wrapper.html(status_html(__("Enter a valid WhatsApp Number before sending OTP"), "#b91c1c"));
				return;
			}
			if (!validate_email_input()) return;

			const send_btn = d.$wrapper.find(".ch-send-customer-otp").get(0);
			otp_request_in_flight = true;
			if (send_btn) send_btn.disabled = true;
			try {
				const existing_by_mobile = await check_existing_customer(phone);
				if (existing_by_mobile.error) {
					dialog_alert(__("Could not check existing customer. Please try again."), "red");
					d.fields_dict.otp_status.$wrapper.html(status_html(__("Could not check existing customer"), "#b91c1c"));
					return;
				}
				if (offer_use_existing_customer(existing_by_mobile)) return;

				if (whatsapp !== phone) {
					const existing_by_whatsapp = await check_existing_customer(whatsapp);
					if (existing_by_whatsapp.error) {
						dialog_alert(__("Could not check existing customer. Please try again."), "red");
						d.fields_dict.otp_status.$wrapper.html(status_html(__("Could not check existing customer"), "#b91c1c"));
						return;
					}
					if (offer_use_existing_customer(existing_by_whatsapp)) return;
				}

				d.fields_dict.otp_status.$wrapper.html(status_html(__("Sending OTP..."), "#2563eb"));
				frappe.dom.freeze(__("Sending OTP..."));
				const res = await frappe.xcall("ch_pos.api.pos_api.request_customer_whatsapp_otp", {
					mobile_no: whatsapp,
					customer_name: input_value("customer_name") || "Customer",
					email_id: input_value("email_id"),
				});
				otp_verified_number = "";
				const channels = [];
				if (res && res.sent_whatsapp) channels.push(__("WhatsApp"));
				if (res && res.sent_email) channels.push(__("Email"));
				const channel_text = channels.length ? channels.join(" + ") : __("OTP log");
				d.fields_dict.otp_status.$wrapper.html(status_html(__("OTP generated via {0}", [channel_text]), "#15803d"));
				frappe.show_alert({ message: __("OTP generated via {0}", [channel_text]), indicator: "green" });
			} catch (err) {
				const message = otp_error_message(err, __("Failed to send OTP"));
				d.fields_dict.otp_status.$wrapper.html(status_html(message, "#b91c1c"));
				dialog_alert(message, "red");
			} finally {
				otp_request_in_flight = false;
				frappe.dom.unfreeze();
				if (send_btn) send_btn.disabled = false;
			}
		};

		const verify_otp_handler = () => {
			const whatsapp = input_value("whatsapp_number");
			const otp_code = input_value("otp_code");
			if (!whatsapp || !assert_india_phone(input_el("whatsapp_number"), whatsapp)) return;
			if (!otp_code) {
				frappe.show_alert({ message: __("Enter OTP code"), indicator: "orange" });
				return;
			}
			frappe.xcall("ch_pos.api.pos_api.verify_customer_whatsapp_otp", {
				mobile_no: whatsapp,
				otp_code,
			}).then((res) => {
				if (res && res.valid) {
					otp_verified_number = whatsapp;
					d.fields_dict.otp_status.$wrapper.html(status_html(__("WhatsApp verified"), "#15803d"));
					frappe.show_alert({ message: __("WhatsApp verified"), indicator: "green" });
				} else {
					otp_verified_number = "";
					d.fields_dict.otp_status.$wrapper.html(status_html((res && res.message) || __("Invalid OTP"), "#b91c1c"));
				}
			}).catch((err) => {
				otp_verified_number = "";
				d.fields_dict.otp_status.$wrapper.html(status_html(otp_error_message(err, __("OTP verification failed")), "#b91c1c"));
			});
		};

		const bind_send_otp_button = () => {
			const button = d.$wrapper.find(".ch-send-customer-otp").get(0);
			if (!button || button._ch_send_otp_bound) return;
			button._ch_send_otp_bound = true;
			const run = (event) => {
				event?.preventDefault?.();
				event?.stopImmediatePropagation?.();
				send_otp_handler();
				return false;
			};
			button.onclick = run;
			button.onmousedown = run;
			button.onpointerdown = run;
		};

		const bind_verify_otp_button = () => {
			const button = d.$wrapper.find(".ch-verify-customer-otp").get(0);
			if (!button || button._ch_verify_otp_bound) return;
			button._ch_verify_otp_bound = true;
			const run = (event) => {
				event?.preventDefault?.();
				event?.stopImmediatePropagation?.();
				verify_otp_handler();
				return false;
			};
			button.onclick = run;
			button.onmousedown = run;
			button.onpointerdown = run;
		};

		const bind_native_inputs = () => {
			const m_el = d.$wrapper.find('[data-fieldname="mobile_no"] input').get(0);
			const w_el = d.$wrapper.find('[data-fieldname="whatsapp_number"] input').get(0);
			if (m_el && !m_el._ch_bound) {
				m_el._ch_bound = true;
				m_el.addEventListener("input", () => sync_whatsapp_from_mobile());
				m_el.addEventListener("keyup", () => sync_whatsapp_from_mobile());
				m_el.addEventListener("change", () => sync_whatsapp_from_mobile());
				m_el.addEventListener("blur", () => { whatsapp_manually_edited = false; sync_whatsapp_from_mobile(); });
			}
			if (w_el && !w_el._ch_bound) {
				w_el._ch_bound = true;
				w_el.addEventListener("input", () => {
					if (syncing_whatsapp) return;
					const val = (w_el.value || "").trim();
					(d.doc || (d.doc = {})).whatsapp_number = val;
					whatsapp_manually_edited = val !== last_auto_synced_mobile;
					otp_verified_number = "";
					d.fields_dict.otp_status.$wrapper.html(status_html(__("OTP not verified")));
				});
			}
		};

		setTimeout(bind_native_inputs, 50);
		d.$wrapper.one("shown.bs.modal", bind_native_inputs);
		setTimeout(bind_send_otp_button, 50);
		setTimeout(bind_verify_otp_button, 50);
		d.$wrapper.one("shown.bs.modal", () => {
			bind_send_otp_button();
			bind_verify_otp_button();
		});

		// Autofill watch
		autofill_watch_timer = setInterval(() => {
			if (!d.display) return;
			const mobile = input_value("mobile_no");
			const whatsapp = input_value("whatsapp_number");
			const mobile_digits = mobile.replace(/\D/g, "");
			const whatsapp_digits = whatsapp.replace(/\D/g, "");
			const whatsapp_is_auto_prefix = whatsapp && mobile_digits.startsWith(whatsapp_digits) && whatsapp_digits.length < 10;
			if (mobile_digits.length >= 10 && (!whatsapp || whatsapp === last_auto_synced_mobile || whatsapp_is_auto_prefix || !whatsapp_manually_edited)) {
				force_set_whatsapp(mobile);
			}
		}, 250);
		d.$wrapper.one("hidden.bs.modal", () => {
			if (autofill_watch_timer) clearInterval(autofill_watch_timer);
		});

		bind_send_otp_button();
		bind_verify_otp_button();

		$(document)
			.off("click.ch_customer_dialog_otp mousedown.ch_customer_dialog_otp pointerdown.ch_customer_dialog_otp")
			.on("pointerdown.ch_customer_dialog_otp mousedown.ch_customer_dialog_otp click.ch_customer_dialog_otp", ".ch-send-customer-otp", (event) => {
				if (!$.contains(d.$wrapper.get(0), event.currentTarget)) return;
				event.preventDefault();
				event.stopImmediatePropagation();
				d.fields_dict.otp_status.$wrapper.html(status_html(__("Checking customer..."), "#2563eb"));
				send_otp_handler();
				return false;
			})
			.on("pointerdown.ch_customer_dialog_otp mousedown.ch_customer_dialog_otp click.ch_customer_dialog_otp", ".ch-verify-customer-otp", (event) => {
				if (!$.contains(d.$wrapper.get(0), event.currentTarget)) return;
				event.preventDefault();
				event.stopImmediatePropagation();
				verify_otp_handler();
				return false;
			});
		d.$wrapper.one("hidden.bs.modal", () => {
			$(document).off("click.ch_customer_dialog_otp mousedown.ch_customer_dialog_otp pointerdown.ch_customer_dialog_otp");
		});
	};

	// ── Global override: any "+" button on a Customer Link field, any form ──
	// Wait until frappe.ui.form is ready, then patch make_quick_entry.
	function _install_customer_quick_entry_override() {
		if (typeof frappe === "undefined" || !frappe.ui || !frappe.ui.form) {
			return setTimeout(_install_customer_quick_entry_override, 200);
		}
		if (frappe.ui.form._ch_customer_qe_patched) return;
		frappe.ui.form._ch_customer_qe_patched = true;

		const _orig_make_quick_entry = frappe.ui.form.make_quick_entry;
		frappe.ui.form.make_quick_entry = function (doctype, after_insert, init_callback, doc, force) {
			if (doctype === "Customer") {
				window.ch_open_new_customer_dialog({
					on_success: (name) => {
						if (typeof after_insert === "function") {
							after_insert({ name: name, doctype: "Customer", customer_name: name });
						}
					},
					on_use_existing: (customer) => {
						if (typeof after_insert === "function") {
							after_insert({ name: customer, doctype: "Customer", customer_name: customer });
						}
					},
				});
				return;
			}
			return _orig_make_quick_entry.apply(this, arguments);
		};

		// Also patch QuickEntryForm class if present (some Frappe paths instantiate directly)
		if (frappe.ui.form.QuickEntryForm && frappe.ui.form.CustomerQuickEntryForm) {
			const _orig_qef_show = frappe.ui.form.QuickEntryForm.prototype.show;
			frappe.ui.form.QuickEntryForm.prototype.show = function () {
				if (this.doctype === "Customer") {
					window.ch_open_new_customer_dialog({
						on_success: (name) => {
							if (typeof this.after_insert === "function") {
								this.after_insert({ name: name, doctype: "Customer", customer_name: name });
							}
						},
						on_use_existing: (customer) => {
							if (typeof this.after_insert === "function") {
								this.after_insert({ name: customer, doctype: "Customer", customer_name: customer });
							}
						},
					});
					return;
				}
				return _orig_qef_show.apply(this, arguments);
			};
		}
	}
	_install_customer_quick_entry_override();
})();
