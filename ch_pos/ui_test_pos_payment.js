const parseCurrency = (text) => Number(String(text || "").replace(/[^0-9.]/g, "")) || 0;

function openPosWithFreshCart(testData) {
	cy.visit(testData.route, {
		onBeforeLoad(win) {
			win.localStorage.removeItem("ch_pos_active_cart");
		},
	});

	cy.window({ timeout: 30000 }).should((win) => {
		expect(win.cur_pos).to.exist;
	});
	cy.get(".ch-pos-search", { timeout: 30000 }).should("be.visible");
	cy.get(".ch-pos-btn-pay").should("be.visible");
}

function seedCartFromUi(testData) {
	cy.window().then((win) => {
		win.cur_pos.cart_panel._commit_customer(testData.customer);
	});
	cy.get(".ch-pos-customer-tag", { timeout: 10000 }).contains(testData.customer);

	cy.get(".ch-pos-search").clear().type(testData.item_code);
	cy.get(`[data-item-code="${testData.item_code}"]`, { timeout: 20000 }).should("be.visible");

	Cypress._.times(testData.item_qty, () => {
		cy.get(`[data-item-code="${testData.item_code}"] .ch-pos-item-add-btn`).first().click();
	});

	cy.get(".ch-pos-cart-line", { timeout: 20000 }).should("have.length.at.least", 1);
	cy.get(".ch-pos-btn-pay").click();
	cy.get("#ch-pos-payment-overlay", { timeout: 20000 }).should("be.visible");
}

context("CH POS Payment UI", () => {
	let testData;

	before(() => {
		cy.login("Administrator");
		cy.visit("/app");
		cy.call("ch_pos.api.ui_test_api.prepare_pos_payment_ui_test").then((response) => {
			testData = response.message;
		});
	});

	beforeEach(() => {
		openPosWithFreshCart(testData);
		seedCartFromUi(testData);
	});

	it("shows store offer deduction in payment totals", () => {
		cy.get(".ch-pay-total-grand").find("span").last().invoke("text").then((beforeText) => {
			const beforeGrand = parseCurrency(beforeText);

			cy.get("#ch-pay-bank-offers", { timeout: 20000 }).contains(testData.store_offer_name).click();
			cy.get(".ch-pay-totals-block").contains("Bank Offer");
			cy.get(".ch-pay-totals-block").contains(`-₹${testData.store_offer_discount}`);

			cy.get(".ch-pay-total-grand").find("span").last().invoke("text").then((afterText) => {
				const afterGrand = parseCurrency(afterText);
				expect(beforeGrand - afterGrand).to.eq(testData.store_offer_discount);
			});
		});
	});

	it("rebalances financed amount after down payment", () => {
		cy.get("#ch-pay-bank-offers", { timeout: 20000 }).contains(testData.store_offer_name).click();
		cy.get(`.ch-pay-saletype-btn[data-type="${testData.finance_sale_type}"]`, { timeout: 20000 }).click();

		cy.get("#ch-pay-sale-sub-row", { timeout: 10000 }).should("be.visible");
		cy.get("#ch-pay-sale-sub-select").select(testData.finance_partner);
		cy.get("#ch-pay-sale-fin-tenure").should("be.visible").select(testData.finance_tenure);
		cy.get("#ch-pay-sale-ref-input").should("be.visible").clear().type(testData.approval_id);

		cy.get(".ch-pay-row-finance", { timeout: 10000 }).should("be.visible");
		cy.get(".ch-pay-total-grand").find("span").last().invoke("text").then((grandText) => {
			const grand = parseCurrency(grandText);
			const expectedFinance = grand - testData.down_payment;

			cy.get(".ch-pay-row-fin-down").clear().type(String(testData.down_payment));
			cy.get(".ch-pay-row-finance .ch-pay-row-amount", { timeout: 10000 })
				.invoke("val")
				.then((financeText) => {
					expect(parseCurrency(financeText)).to.eq(expectedFinance);
				});

			cy.get("#ch-pay-balance-label").should("contain", "Financed Amount");
			cy.get("#ch-pay-balance-due").invoke("text").then((balanceText) => {
				expect(parseCurrency(balanceText)).to.eq(expectedFinance);
			});
			cy.get("#ch-pay-submit-label").invoke("text").then((labelText) => {
				expect(labelText).to.contain("Confirm Finance Sale");
				expect(parseCurrency(labelText)).to.eq(expectedFinance);
			});
		});
	});
});