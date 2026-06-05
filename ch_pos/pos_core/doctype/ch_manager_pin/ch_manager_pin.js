frappe.ui.form.on("CH Manager PIN", {
    refresh(frm) {
        frm.set_intro(
            __("Saved PIN values are intentionally masked for security. Use manager-approval flows to verify a PIN."),
            "blue"
        );
    },
});
