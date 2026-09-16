/**
 * CH POS — Session Opening Screen
 *
 * Shown before POS loads. Cashier must:
 * 1. Select POS Profile (if not auto-resumed)
 * 2. Enter opening cash amount
 * 3. Get manager PIN approval
 *
 * Only after successful session creation does the POS app load.
 */
import { PosState, EventBus } from "../state.js";

const INDIAN_DENOMINATIONS = [2000, 500, 200, 100, 50, 20, 10, 5, 2, 1];

export class SessionOpeningScreen {
	constructor() {
		this._dialog = null;
		this._pending_promise = null;
		this._bind_restart();
	}

	/** Dismiss any open dialog so a new one can take focus. */
	_dismiss_dialog() {
		if (this._dialog) {
			try { this._dialog.hide(); } catch (e) { /* ignore */ }
			this._dialog = null;
		}
	}

	_bind_restart() {
		EventBus.on("session:restart_flow", () => {
			// Clear any stale pending promise so show() can re-enter
			this._pending_promise = null;
			this._dismiss_dialog();
			// Re-trigger the full session opening flow
			this.show([]).then((session_data) => {
				PosState.session_name = session_data.session_name;
				PosState.business_date = session_data.business_date;
				PosState.store = session_data.store;
				EventBus.emit("session:loaded", session_data);

				const entry = session_data.opening_entry || {
					pos_profile: session_data.pos_profile,
					company: session_data.company,
				};
				// Trigger profile load via PosApp — emit event for it
				EventBus.emit("session:profile_reload", entry);
			});
		});
	}

	_get_saved_store() {
		try {
			return window.sessionStorage?.getItem("ch_pos_selected_store") || "";
		} catch (e) {
			return "";
		}
	}

	_remember_store(store) {
		if (!store) return;
		try {
			window.sessionStorage?.setItem("ch_pos_selected_store", store);
		} catch (e) {
			// Ignore storage issues in private mode/restricted browsers.
		}
	}

	_queue_store_resume(store) {
		if (!store) return;
		this._remember_store(store);
		try {
			window.sessionStorage?.setItem("ch_pos_resume_store_once", store);
		} catch (e) {
			// Ignore storage issues in private mode/restricted browsers.
		}
	}

	_consume_store_resume() {
		try {
			const store = window.sessionStorage?.getItem("ch_pos_resume_store_once") || "";
			if (store) {
				window.sessionStorage?.removeItem("ch_pos_resume_store_once");
			}
			return store;
		} catch (e) {
			return "";
		}
	}

	_continue_with_store(storeName, resolve, dlg = null) {
		this._remember_store(storeName);
		if (dlg) {
			dlg.disable_primary_action();
		}
		frappe.call({
			method: "ch_pos.api.isolation_api.get_pos_context_for_store",
			args: { store: storeName },
			callback: (r) => {
				const ctx = r.message || {};
				if (dlg) {
					dlg.hide();
				}
				if (ctx.day_closed) {
					this._show_day_closed_message({
						store: storeName,
						business_date: ctx.business_date,
						message: __("Store day is already closed for {0}. Advance business date before opening a new session.", [ctx.business_date]),
					});
					return;
				}
				const profile = ctx.pos_profile;
				if (!profile) {
					frappe.msgprint({
						title: __("No POS Profile"),
						indicator: "red",
						message: __("No POS Profile found for store {0}. Configure a POS Profile Extension.", [storeName]),
					});
					return;
				}
				frappe.call({
					method: "ch_pos.api.session_api.get_session_status",
					args: { pos_profile: profile },
					callback: (r2) => {
						const data = r2.message || {};
						if (data.has_session) {
							resolve({
								session_name: data.session_name,
								business_date: data.business_date,
								store: storeName,
								company: ctx.company,
								device: ctx.device,
								pos_profile: profile,
								opening_entry: { pos_profile: profile, company: ctx.company },
							});
						} else if (data.day_closed) {
							this._show_day_closed_message(data);
						} else if (data.unclosed_session) {
							this._show_must_close(data, profile, resolve);
						} else {
							if (data.warning_unclosed_session) this._show_stale_session_warning(data);
							this._show_opening_form(profile, ctx.company, resolve, ctx);
						}
					},
				});
			},
			error: () => {
				if (dlg) {
					dlg.enable_primary_action();
				}
			},
		});
	}

	/**
	 * Show opening screen. Resolves when session is ready.
	 * @param {Array} open_entries - ERPNext open entries (for resume detection)
	 * @returns {Promise<{session_name, business_date, store}>}
	 */
	show(open_entries) {
		// Guard: prevent double-invocation while a flow is already pending
		if (this._pending_promise) {
			return this._pending_promise;
		}
		this._pending_promise = new Promise((resolve, reject) => {
			const _done = (v) => { this._pending_promise = null; resolve(v); };
			this._resolve = _done;
			// Always check POS context first (handles admin store picker)
			frappe.call({
				method: "ch_pos.api.isolation_api.get_pos_context",
				callback: (r) => {
					const ctx = r.message || {};
					// Consume the one-shot store request before branching. It used
					// to be read only inside the select_store branch, which a user
					// with an open session never reaches -- they are auto-resumed
					// below -- so "Switch Store" reloaded straight back into the
					// store they were trying to leave. An explicit request has to
					// outrank auto-resume, or it is not a switch at all. Scope is
					// still enforced server-side by get_pos_context_for_store.
					const resumeStore = this._consume_store_resume();
					if (resumeStore && resumeStore !== ctx.store) {
						this._continue_with_store(resumeStore, _done);
						return;
					}
					if (ctx.status === "select_store") {
						const stores = ctx.stores || [];
						if (resumeStore && stores.some((s) => s.name === resumeStore)) {
							this._continue_with_store(resumeStore, _done);
							return;
						}
						// Unlike resumeStore (one-shot, cleared the instant it's read),
						// the saved store persists for the whole browser tab session —
						// so a user who already picked a store once should not have to
						// pick it again on every subsequent refresh before their
						// session is actually created. Only used as a dropdown default
						// until now; auto-resuming it here is what actually skips the
						// picker.
						const savedStore = this._get_saved_store();
						if (savedStore && stores.some((s) => s.name === savedStore)) {
							this._continue_with_store(savedStore, _done);
							return;
						}
						this._show_store_picker(stores, _done, savedStore || ctx.default_store, ctx);
					} else if (ctx.day_closed) {
						// Day closed — no access until date is advanced (market standard)
						this._show_day_closed_message({ store: ctx.store, business_date: ctx.business_date, message: __("Store day is already closed for {0}. Advance business date before opening a new session.", [ctx.business_date]) });
					} else if (ctx.existing_session) {
						// Active session — auto-resume using context from get_pos_context
						const es = ctx.existing_session;
						const entry = (open_entries && open_entries.length === 1) ? open_entries[0] : { pos_profile: es.pos_profile, company: ctx.company };
						_done({
							session_name: es.name,
							business_date: ctx.business_date,
							store: ctx.store,
							company: ctx.company,
							device: ctx.device,
							pos_profile: es.pos_profile,
							opening_entry: entry,
						});
					} else if (open_entries && open_entries.length === 1) {
						// No existing CH session but ERPNext entry exists — check status
						this._check_existing_session(open_entries[0], _done, ctx);
					} else {
						this._show_profile_and_opening(open_entries, _done);
					}
				},
				error: () => {
					// Fallback if isolation API fails
					this._pending_promise = null;
					if (open_entries && open_entries.length === 1) {
						this._check_existing_session(open_entries[0], _done);
					} else {
						this._show_profile_and_opening(open_entries, _done);
					}
				},
			});
		});
		return this._pending_promise;
	}

	_check_existing_session(entry, resolve, ctx) {
		// Use context already fetched by show() when available — avoids redundant API call
		if (ctx && ctx.status) {
			this._check_status_and_open(entry, resolve, ctx);
			return;
		}
		// Fallback: fetch context if not passed (e.g. error path)
		frappe.call({
			method: "ch_pos.api.isolation_api.get_pos_context",
			callback: (r) => {
				const freshCtx = r.message || {};
				if (freshCtx.status === "no_allocation") {
					frappe.msgprint({
						title: __("POS Setup Required"),
						indicator: "red",
						message: freshCtx.message || __("You are not allocated to any store for POS operations."),
					});
				} else if (freshCtx.status === "select_store") {
					const resumeStore = this._consume_store_resume();
					const stores = freshCtx.stores || [];
					if (resumeStore && stores.some((s) => s.name === resumeStore)) {
						this._continue_with_store(resumeStore, resolve);
						return;
					}
					const savedStore = this._get_saved_store();
					if (savedStore && stores.some((s) => s.name === savedStore)) {
						this._continue_with_store(savedStore, resolve);
						return;
					}
					this._show_store_picker(stores, resolve, savedStore || freshCtx.default_store, freshCtx);
				} else if (freshCtx.day_closed) {
					this._show_day_closed_message({ store: freshCtx.store, business_date: freshCtx.business_date, message: __("Store day is already closed for {0}. Advance business date before opening a new session.", [freshCtx.business_date]) });
				} else {
					this._check_status_and_open(entry, resolve, freshCtx);
				}
			},
			error: () => {
				this._check_status_legacy(entry, resolve);
			},
		});
	}

	_check_status_and_open(entry, resolve, ctx) {
		frappe.call({
			method: "ch_pos.api.session_api.get_session_status",
			args: { pos_profile: entry.pos_profile },
			callback: (r) => {
				const data = r.message || {};
				if (data.has_session) {
					resolve({
						session_name: data.session_name,
						business_date: data.business_date,
						store: data.store,
						company: data.company || ctx.company,
						device: data.device || ctx.device,
						pos_profile: entry.pos_profile,
						opening_entry: entry,
					});
				} else if (data.day_closed) {
					this._show_day_closed_message(data);
				} else if (data.unclosed_session) {
					this._show_must_close(data, entry.pos_profile, resolve);
				} else {
					if (data.warning_unclosed_session) this._show_stale_session_warning(data);
					this._show_opening_form(entry.pos_profile, ctx.company || entry.company, resolve, ctx);
				}
			},
		});
	}

	_check_status_legacy(entry, resolve) {
		frappe.call({
			method: "ch_pos.api.session_api.get_session_status",
			args: { pos_profile: entry.pos_profile },
			callback: (r) => {
				const data = r.message || {};
				if (data.has_session) {
					resolve({
						session_name: data.session_name,
						business_date: data.business_date,
						store: data.store,
						company: data.company,
						device: data.device,
						pos_profile: entry.pos_profile,
						opening_entry: entry,
					});
				} else if (data.day_closed) {
					this._show_day_closed_message(data);
				} else if (data.unclosed_session) {
					this._show_must_close(data, entry.pos_profile, resolve);
				} else {
					if (data.warning_unclosed_session) this._show_stale_session_warning(data);
					this._show_opening_form(entry.pos_profile, entry.company, resolve);
				}
			},
		});
	}

	_show_stale_session_warning(data) {
		// Non-blocking, one-time nudge at login only — the operator can
		// keep working at this new store.
		frappe.show_alert({
			message: __("You still have an unsettled session {0} open at {1} (since {2}). Settle and close it when you get a chance.", [
				`<b>${data.warning_unclosed_session}</b>`,
				data.warning_unclosed_store || "",
				data.warning_unclosed_date,
			]),
			indicator: "orange",
		}, 10);
	}

	_show_must_close(data, pos_profile, resolve) {
		frappe.msgprint({
			title: __("Unclosed Session"),
			message: __("Session {0} from {1} (cashier: {2}) is still open. It must be closed before a new session can start.", [
				`<b>${data.unclosed_session}</b>`,
				data.unclosed_date,
				data.unclosed_user,
			]),
			indicator: "orange",
			primary_action: {
				label: __("Settlement"),
				action: () => {
					// Settlement first (close_session hard-fails without it) —
					// session:force_settle chains straight into Close Session
					// automatically once Settlement succeeds.
					PosState._unclosed_session = data.unclosed_session;
					EventBus.emit("session:force_settle", data.unclosed_session);
				},
			},
		});
	}

	_show_store_picker(stores, resolve, defaultStore, ctx) {
		this._dismiss_dialog();
		stores = stores || [];
		ctx = ctx || {};
		const storeOptions = stores.map(
			s => `${s.name} — ${s.store_name || s.name}`
		);
		const defaultOption = defaultStore
			? storeOptions.find(o => o.startsWith(defaultStore + " — ")) || ""
			: "";

		// The list is scoped to the active company. Offer an explicit escape
		// rather than silently hiding the other companies' stores.
		const canWiden = !!ctx.active_company && !ctx.showing_all_companies;
		const fields = [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: `<div class="alert alert-info" style="margin-bottom:10px">
					${frappe.utils.escape_html(ctx.message || __("You have administrative access. Select a store to continue."))}
				</div>`,
			},
			{
				fieldname: "store",
				fieldtype: "Select",
				label: __("Store"),
				options: ["", ...storeOptions],
				reqd: 1,
				default: defaultOption,
			},
		];
		if (canWiden) {
			fields.push({
				fieldname: "all_companies",
				fieldtype: "Check",
				label: __("Show stores from all companies"),
				default: 0,
				description: __("Leave unticked to stay within {0}.", [ctx.active_company]),
				change: () => {
					if (!dlg.get_value("all_companies")) return;
					dlg.hide();
					this._reload_store_picker(resolve, defaultStore);
				},
			});
		}

		const dlg = new frappe.ui.Dialog({
			title: ctx.active_company && !ctx.showing_all_companies
				? __("Select Store — {0}", [ctx.active_company])
				: __("Select Store"),
			fields,
			primary_action_label: __("Continue"),
			primary_action: (values) => {
				const selectedLabel = values.store || "";
				const storeName = selectedLabel.split(" — ")[0];
				if (!storeName) {
					frappe.msgprint(__("Please select a store to continue."));
					return;
				}
				this._continue_with_store(storeName, resolve, dlg);
			},
		});
		dlg.show();
		this._dialog = dlg;
	}

	/** Re-fetch the picker unscoped after the admin opts into all companies. */
	_reload_store_picker(resolve, defaultStore) {
		frappe.call({
			method: "ch_pos.api.isolation_api.get_pos_context",
			args: { all_companies: 1 },
			callback: (r) => {
				const ctx = r.message || {};
				this._show_store_picker(ctx.stores || [], resolve, defaultStore, ctx);
			},
			error: () => {
				this._pending_promise = null;
			},
		});
	}

	/**
	 * Ask which till, then how much cash is in it.
	 *
	 * The profile list is fetched rather than left to a Link field for two
	 * reasons: a Link searches every POS Profile the user can read, which is
	 * wider than the set they are actually entitled to open, and it shows the
	 * profile code — `POS - STO-GSPL-CHENNA-0005` — which nobody at a counter
	 * recognises. `get_pos_profiles` returns the scoped set already labelled
	 * with the shop name.
	 */
	_show_profile_and_opening(open_entries, resolve) {
		frappe.xcall("ch_pos.api.token_api.get_pos_profiles")
			.then((profiles) => this._render_profile_and_opening(
				open_entries, resolve, profiles || []))
			// Never strand the opener on a list that failed to load: fall back
			// to the unlabelled picker. The server re-checks entitlement on
			// open either way, so this loses the nicety, not the gate.
			.catch(() => this._render_profile_and_opening(open_entries, resolve, null));
	}

	_render_profile_and_opening(open_entries, resolve, profiles) {
		this._dismiss_dialog();
		open_entries = open_entries || [];
		const open_map = {};
		open_entries.forEach((e) => { open_map[e.pos_profile] = e; });

		const by_profile = {};
		(profiles || []).forEach((p) => { by_profile[p.name] = p; });
		const label_for = (name) => (by_profile[name] && by_profile[name].label) || name;

		const fields = [];

		if (open_entries.length) {
			const names = open_entries
				.map((e) => `<b>${frappe.utils.escape_html(label_for(e.pos_profile))}</b>`)
				.join(", ");
			fields.push({
				fieldname: "open_info",
				fieldtype: "HTML",
				options: `<div class="alert alert-info" style="margin-bottom:10px">
					${__("Open ERPNext sessions")}: ${names}
				</div>`,
			});
		}

		fields.push(
			profiles
				? {
					fieldname: "pos_profile",
					fieldtype: "Select",
					label: __("Store / Till"),
					// {label, value} pairs: the cashier reads the shop name,
					// the server still receives the profile code.
					options: profiles.map((p) => ({ value: p.name, label: p.label || p.name })),
					reqd: 1,
					default: open_entries.length
						? open_entries[0].pos_profile
						: (profiles.length === 1 ? profiles[0].name : undefined),
				}
				: {
					fieldname: "pos_profile",
					fieldtype: "Link",
					label: __("POS Profile"),
					options: "POS Profile",
					reqd: 1,
					default: open_entries.length ? open_entries[0].pos_profile : undefined,
				},
			{ fieldtype: "Column Break" },
			{
				fieldname: "opening_cash",
				fieldtype: "Currency",
				label: __("Opening Cash (₹)"),
				reqd: 1,
				description: __("Count the cash in the drawer and enter the total"),
			},
			{ fieldtype: "Section Break", label: __("Verify It Is You") },
			{
				fieldname: "send_otp",
				fieldtype: "Button",
				label: __("Email me a code"),
				// frappe/form/controls/button.js dispatches a dialog button
				// through df.click. Binding to $input instead only works if the
				// control happens to be rendered already, and fails silently
				// when it is not — which is why no code was being sent.
				click: () => this._send_session_otp(this._dialog),
			},
			{
				fieldname: "otp",
				fieldtype: "Data",
				label: __("6-digit code"),
				reqd: 1,
				description: __("We email the code to you, so the till records who opened it"),
			},
		);

		const dlg = new frappe.ui.Dialog({
			title: __("Open POS Session"),
			fields,
			size: "large",
			primary_action_label: __("Open Session"),
			primary_action: (values) => {
				const profile = values.pos_profile;
				// First check if profile has active CH session
				frappe.call({
					method: "ch_pos.api.session_api.get_session_status",
					args: { pos_profile: profile },
					callback: (r) => {
						const data = r.message || {};
						if (data.has_session) {
							dlg.hide();
							resolve({
								session_name: data.session_name,
								business_date: data.business_date,
								store: data.store,
								pos_profile: profile,
								company: open_map[profile]?.company,
								opening_entry: open_map[profile],
							});
						} else if (data.day_closed) {
							this._show_day_closed_message(data);
						} else if (data.unclosed_session) {
							frappe.msgprint(__("Close session {0} first", [data.unclosed_session]));
						} else {
							this._create_session(dlg, profile, values, open_map, resolve);
						}
					},
				});
			},
		});

		// Show expected float hint when profile changes
		dlg.fields_dict.pos_profile.$input.on("change", () => {
			const pp = dlg.get_value("pos_profile");
			if (pp) {
				frappe.xcall("ch_pos.api.session_api.get_session_status", { pos_profile: pp })
					.then((data) => {
						if (data.has_session) {
							dlg.set_df_property("opening_cash", "description",
								__("Session already exists — will auto-resume"));
						}
					});
			}
		});

		dlg.show();
		this._dialog = dlg;
	}

	/**
	 * "Email me a code" — sends a 6-digit code to the signed-in user's own
	 * inbox. The till then records who opened it, which a shared manager PIN
	 * could never establish.
	 */
	/**
	 * Request the emailed code. Called from the Button field's `click`, which
	 * is what frappe.ui.form.ControlButton.onclick() invokes for a dialog.
	 *
	 * `pos_profile` may come from a picker (the profile dialog) or be fixed by
	 * the caller (the store-context dialog), so it is resolved from whichever
	 * is available rather than assumed.
	 */
	_send_session_otp(dlg, fixed_profile) {
		if (!dlg) return;
		const profile = fixed_profile || dlg.get_value("pos_profile");
		if (!profile) {
			frappe.show_alert({ message: __("Pick a POS Profile first"), indicator: "orange" });
			return;
		}
		const btn = dlg.fields_dict.send_otp && dlg.fields_dict.send_otp.$input;
		if (btn) btn.prop("disabled", true).text(__("Sending…"));
		frappe.xcall("ch_pos.api.session_api.request_session_open_otp", { pos_profile: profile })
			.then((r) => {
				dlg.set_df_property("otp", "description", r.message || __("Code sent."));
				frappe.show_alert({ message: r.message, indicator: "green" });
				dlg.fields_dict.otp && dlg.fields_dict.otp.$input &&
					dlg.fields_dict.otp.$input.focus();
			})
			.catch(() => {
				// The server has already explained why; let them retry.
			})
			.finally(() => {
				if (btn) btn.prop("disabled", false).text(__("Email me a code"));
			});
	}

	/**
	 * Request a code for a PIN-gated action, then exchange it for a grant as
	 * soon as the operator types it. Keyed by action so the remaining PIN
	 * popups can reuse it.
	 */
	_send_action_otp(kind, store) {
		const dlg = this._dialog;
		if (!dlg) return;
		const btn = dlg.fields_dict.send_otp && dlg.fields_dict.send_otp.$input;
		if (btn) btn.prop("disabled", true).text(__("Sending…"));
		frappe.xcall("ch_pos.api.session_api.request_action_otp", { kind, store })
			.then((r) => {
				dlg.set_df_property("otp", "description", r.message || __("Code sent."));
				frappe.show_alert({ message: r.message, indicator: "green" });
				const otp_field = dlg.fields_dict.otp && dlg.fields_dict.otp.$input;
				if (otp_field) {
					otp_field.focus();
					otp_field.off("input.chotp").on("input.chotp", () => {
						const code = (otp_field.val() || "").trim();
						if (code.length !== 6) return;
						frappe.xcall("ch_pos.api.session_api.verify_action_otp",
							{ kind, store, otp: code })
							.then((v) => {
								this._action_grant = v.action_grant;
								frappe.show_alert({ message: v.message, indicator: "green" });
							})
							.catch(() => { this._action_grant = null; });
					});
				}
			})
			.catch(() => {})
			.finally(() => {
				if (btn) btn.prop("disabled", false).text(__("Email me a code"));
			});
	}

	_show_day_closed_message(data) {
		this._dismiss_dialog();
		const store = data.store;
		const business_date = data.business_date;
		const today = frappe.datetime.get_today();
		const next_date = business_date
			? frappe.datetime.add_days(business_date, 1)
			: today;
		const suggested_date = next_date > today ? next_date : today;

		const dlg = new frappe.ui.Dialog({
			title: __("Business Date Closed"),
			fields: [
				{
					fieldname: "info",
					fieldtype: "HTML",
					options: `<div class="alert alert-warning" style="margin-bottom:12px">
						<i class="fa fa-exclamation-triangle"></i>
						${data.message || __("Store day is already closed. Advance business date to start a new session.")}
					</div>`,
				},
				{
					fieldname: "new_date",
					fieldtype: "Date",
					label: __("New Business Date"),
					reqd: 1,
					default: suggested_date,
					description: __("Typically the next operating day"),
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason"),
					default: __("Advance to next business day"),
				},
				{ fieldtype: "Section Break", label: __("Verify It Is You") },
				{
					fieldname: "send_otp",
					fieldtype: "Button",
					label: __("Email me a code"),
					// An executive holds no approver role by design, so a manager
					// PIN can only ever answer "Invalid PIN" and the store is
					// stranded. A code to their own inbox proves who rolled the day.
					click: () => this._send_action_otp("business_date_override", store),
				},
				{
					fieldname: "otp",
					fieldtype: "Data",
					label: __("6-digit code"),
					description: __("We email the code to you; the day roll records who did it"),
				},
				{
					fieldname: "manager_pin",
					fieldtype: "Password",
					label: __("Manager PIN (alternative)"),
					description: __("Only if a manager is approving instead"),
				},
			],
			primary_action_label: __("Advance Date & Start New Day"),
			primary_action: (values) => {
				if (!store) {
					frappe.msgprint(__("Store information not available. Please reload and try again."));
					return;
				}
				dlg.disable_primary_action();
				frappe.call({
					method: "ch_pos.api.session_api.override_business_date",
					args: {
						store: store,
						new_date: values.new_date,
						reason: values.reason || "Advance to next business day",
						manager_pin: values.manager_pin || null,
						action_grant: this._action_grant || null,
					},
					callback: (r) => {
						if (r.message) {
							this._queue_store_resume(store);
							dlg.hide();
							frappe.show_alert({
								message: __("Business date advanced to {0}. Reloading…", [r.message.business_date]),
								indicator: "green",
							});
							setTimeout(() => window.location.reload(), 1200);
						}
					},
					error: () => {
						dlg.enable_primary_action();
					},
				});
			},
		});
		dlg.show();
		this._dialog = dlg;
	}

	_show_opening_form(pos_profile, company, resolve, ctx) {
		this._dismiss_dialog();
		const store_label = ctx && ctx.store
			? (ctx.store_name ? `${ctx.store_name} · ${ctx.store}` : ctx.store)
			: "";
		const context_info = ctx ? `
			<div class="text-muted" style="margin-bottom:12px">
				${store_label ? `${__("Store")}: <b>${frappe.utils.escape_html(store_label)}</b><br>` : ""}
				${__("Till")}: <b>${frappe.utils.escape_html(pos_profile)}</b><br>
				${ctx.company ? `${__("Company")}: <b>${frappe.utils.escape_html(ctx.company)}</b><br>` : ""}
				${ctx.device ? `${__("Device")}: <b>${frappe.utils.escape_html(ctx.device)}</b><br>` : ""}
				${ctx.business_date ? `${__("Business Date")}: <b>${ctx.business_date}</b>` : ""}
			</div>` : `
			<div class="text-muted" style="margin-bottom:12px">
				${__("Profile")}: <b>${pos_profile}</b>
			</div>`;

		const fields = [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: context_info,
			},
			{
				fieldname: "opening_cash",
				fieldtype: "Currency",
				label: __("Opening Cash (₹)"),
				default: 0,
				description: __("Count cash in drawer before starting"),
			},
			{ fieldtype: "Section Break", label: __("Verify It Is You") },
			{
				fieldname: "send_otp",
				fieldtype: "Button",
				label: __("Email me a code"),
				// see _send_session_otp: dialog buttons dispatch via df.click
				click: () => this._send_session_otp(this._dialog, pos_profile),
			},
			{
				fieldname: "otp",
				fieldtype: "Data",
				label: __("6-digit code"),
				reqd: 1,
				description: __("We email the code to you, so the till records who opened it"),
			},
			{
				fieldname: "error_area",
				fieldtype: "HTML",
				options: "",
			},
		];

		const dlg = new frappe.ui.Dialog({
			title: __("Open POS Session"),
			fields,
			primary_action_label: __("Start Session"),
			primary_action: (values) => {
				this._create_session(dlg, pos_profile, values, {}, resolve, company, ctx);
			},
		});
		dlg.show();
		this._dialog = dlg;
	}

	_create_session(dlg, pos_profile, values, open_map, resolve, company, ctx) {
		// Clear previous error
		if (dlg.fields_dict.error_area) {
			dlg.fields_dict.error_area.$wrapper.html("");
		}
		dlg.disable_primary_action();

		const show_error = (msg) => {
			dlg.enable_primary_action();
			if (dlg.fields_dict.error_area) {
				dlg.fields_dict.error_area.$wrapper.html(
					`<div class="alert alert-danger" style="margin-top:10px">
						<i class="fa fa-exclamation-circle"></i> ${msg}
					</div>`
				);
			}
		};

		if (!values.otp) {
			show_error(__("Enter the 6-digit code we emailed you."));
			return;
		}

		// Exchange the emailed code for a single-use grant, then open with it.
		// The grant is what proves to the server which person is at the till.
		frappe.xcall("ch_pos.api.session_api.verify_session_open_otp", {
			pos_profile,
			otp: values.otp,
		}).then((v) => {
			const args = {
				pos_profile: pos_profile,
				opening_cash: values.opening_cash || 0,
				session_grant: v.session_grant,
			};
			// Pass device from context if available
			if (ctx && ctx.device) {
				args.device = ctx.device;
			}
			return this._open_with_grant(dlg, pos_profile, args, open_map, resolve, company, ctx);
		}).catch(() => {
			// The server explains why the code failed; re-enable so they retry.
			dlg.enable_primary_action();
		});
	}

	_open_with_grant(dlg, pos_profile, args, open_map, resolve, company, ctx) {
		return frappe.call({
			method: "ch_pos.api.session_api.open_session",
			args: args,
			callback: (r) => {
				if (r.message) {
					dlg.hide();
					frappe.show_alert({
						message: __("Session opened — Business Date: {0}", [r.message.business_date]),
						indicator: "green",
					});
					resolve({
						session_name: r.message.session_name,
						business_date: r.message.business_date,
						store: r.message.store,
						company: r.message.company || company || open_map[pos_profile]?.company,
						device: r.message.device || (ctx && ctx.device) || null,
						pos_profile: pos_profile,
						opening_entry: { pos_profile, company: r.message.company || company || open_map[pos_profile]?.company },
					});
				}
			},
			error: (r) => {
				dlg.enable_primary_action();
				// Show error inside the dialog so user can see it
				const msg = (r && r.exc_type)
					? (r._server_messages
						? JSON.parse(r._server_messages).map(m => {
							try { return JSON.parse(m).message || m; } catch(e) { return m; }
						}).join("<br>")
						: __("Session could not be opened. Check the error and try again."))
					: __("Session could not be opened. Check the error and try again.");
				if (dlg.fields_dict.error_area) {
					dlg.fields_dict.error_area.$wrapper.html(
						`<div class="alert alert-danger" style="margin-top:10px">
							<i class="fa fa-exclamation-circle"></i> ${msg}
						</div>`
					);
				}
			},
		});
	}

	destroy() {
		this._dismiss_dialog();
		this._pending_promise = null;
	}
}
