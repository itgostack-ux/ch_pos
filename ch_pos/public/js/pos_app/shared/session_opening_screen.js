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
	}

	/**
	 * Show opening screen. Resolves when session is ready.
	 * @param {Array} open_entries - ERPNext open entries (for resume detection)
	 * @returns {Promise<{session_name, business_date, store}>}
	 */
	show(open_entries) {
		return new Promise((resolve, reject) => {
			this._resolve = resolve;
			// Always check POS context first (handles admin store picker)
			frappe.call({
				method: "ch_pos.api.isolation_api.get_pos_context",
				callback: (r) => {
					const ctx = r.message || {};
					if (ctx.status === "select_store") {
						this._show_store_picker(ctx.stores || [], resolve, ctx.default_store);
					} else if (open_entries && open_entries.length === 1) {
						this._check_existing_session(open_entries[0], resolve);
					} else {
						this._show_profile_and_opening(open_entries, resolve);
					}
				},
				error: () => {
					// Fallback if isolation API fails
					if (open_entries && open_entries.length === 1) {
						this._check_existing_session(open_entries[0], resolve);
					} else {
						this._show_profile_and_opening(open_entries, resolve);
					}
				},
			});
		});
	}

	_check_existing_session(entry, resolve) {
		// First try get_pos_context for full isolation-aware context
		frappe.call({
			method: "ch_pos.api.isolation_api.get_pos_context",
			callback: (r) => {
				const ctx = r.message || {};
				if (ctx.status === "no_allocation") {
					frappe.msgprint({
						title: __("POS Setup Required"),
						indicator: "red",
						message: ctx.message || __("You are not allocated to any store for POS operations."),
					});
				} else if (ctx.status === "select_store") {
					// Admin / System Manager — show store picker first
					this._show_store_picker(ctx.stores || [], resolve, ctx.default_store);
				} else if (ctx.existing_session) {
					// Active session exists — auto resume
					resolve({
						session_name: ctx.existing_session,
						business_date: ctx.business_date,
						store: ctx.store,
						company: ctx.company,
						device: ctx.device,
						pos_profile: entry.pos_profile,
						opening_entry: entry,
					});
				} else if (ctx.day_closed) {
				this._show_day_closed_message({ store: ctx.store, business_date: ctx.business_date, message: __("Store day is already closed for {0}. Advance business date before opening a new session.", [ctx.business_date]) });
				} else if (ctx.error) {
					frappe.msgprint({
						title: __("POS Setup Required"),
						indicator: "red",
						message: ctx.error,
					});
				} else {
					// No session — fall back to regular status check for unclosed session detection
					this._check_status_and_open(entry, resolve, ctx);
				}
			},
			error: () => {
				// Fallback to legacy flow if isolation API not available
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
					this._show_opening_form(entry.pos_profile, entry.company, resolve);
				}
			},
		});
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
				label: __("Close Session"),
				action: () => {
					// Switch to closing dashboard for the unclosed session
					PosState._unclosed_session = data.unclosed_session;
					EventBus.emit("session:force_close", data.unclosed_session);
				},
			},
		});
	}

	_show_store_picker(stores, resolve, defaultStore) {
		stores = stores || [];
		const storeOptions = stores.map(
			s => `${s.name} — ${s.store_name || s.name}`
		);
		const defaultOption = defaultStore
			? storeOptions.find(o => o.startsWith(defaultStore + " — ")) || ""
			: "";

		const dlg = new frappe.ui.Dialog({
			title: __("Select Store"),
			fields: [
				{
					fieldname: "info",
					fieldtype: "HTML",
					options: `<div class="alert alert-info" style="margin-bottom:10px">
						${__("You have System Manager access. Select a store to continue.")}
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
			],
			primary_action_label: __("Continue"),
			primary_action: (values) => {
				const selectedLabel = values.store;
				const storeName = selectedLabel.split(" — ")[0];
				dlg.disable_primary_action();
				frappe.call({
					method: "ch_pos.api.isolation_api.get_pos_context_for_store",
					args: { store: storeName },
					callback: (r) => {
						const ctx = r.message || {};
						dlg.hide();
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
						// Check if session already exists for this store
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
									this._show_opening_form(profile, ctx.company, resolve, ctx);
								}
							},
						});
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

	_show_profile_and_opening(open_entries, resolve) {
		open_entries = open_entries || [];
		const open_map = {};
		open_entries.forEach((e) => { open_map[e.pos_profile] = e; });

		const fields = [];

		if (open_entries.length) {
			const names = open_entries.map((e) => `<b>${e.pos_profile}</b>`).join(", ");
			fields.push({
				fieldname: "open_info",
				fieldtype: "HTML",
				options: `<div class="alert alert-info" style="margin-bottom:10px">
					${__("Open ERPNext sessions")}: ${names}
				</div>`,
			});
		}

		fields.push(
			{
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
				default: 0,
				description: __("Count the cash in the drawer and enter the total"),
			},
			{ fieldtype: "Section Break", label: __("Manager Approval") },
			{
				fieldname: "manager_pin",
				fieldtype: "Password",
				label: __("Manager PIN"),
				description: __("4-6 digit PIN for opening approval"),
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

	_show_day_closed_message(data) {
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
				{ fieldtype: "Section Break", label: __("Manager Authorization") },
				{
					fieldname: "manager_pin",
					fieldtype: "Password",
					label: __("Manager PIN"),
					reqd: 1,
					description: __("Manager PIN required to advance business date"),
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
						manager_pin: values.manager_pin,
					},
					callback: (r) => {
						if (r.message) {
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
		const context_info = ctx ? `
			<div class="text-muted" style="margin-bottom:12px">
				${__("Profile")}: <b>${pos_profile}</b><br>
				${ctx.company ? `${__("Company")}: <b>${frappe.utils.escape_html(ctx.company)}</b><br>` : ""}
				${ctx.store ? `${__("Store")}: <b>${frappe.utils.escape_html(ctx.store)}</b><br>` : ""}
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
				reqd: 1,
				default: 0,
				description: __("Count cash in drawer before starting"),
			},
			{ fieldtype: "Section Break", label: __("Manager Approval") },
			{
				fieldname: "manager_pin",
				fieldtype: "Password",
				label: __("Manager PIN"),
				description: __("4-6 digit PIN for opening approval"),
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
		dlg.disable_primary_action();
		const args = {
			pos_profile: pos_profile,
			opening_cash: values.opening_cash || 0,
			manager_pin: values.manager_pin || null,
		};
		// Pass device from context if available
		if (ctx && ctx.device) {
			args.device = ctx.device;
		}
		frappe.call({
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
			error: () => {
				dlg.enable_primary_action();
			},
		});
	}

	destroy() {
		if (this._dialog) {
			this._dialog.hide();
			this._dialog = null;
		}
	}
}
