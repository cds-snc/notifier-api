/// <reference types="cypress" />

import config from "../../../config";
import { LoginPage } from "../../Notify/Admin/Pages/all";


const ADMIN_COOKIE = 'notify_admin_session';
describe('Basic login', () => {

    // Login to notify before the test suite starts
    before(() => {
        Cypress.config('baseUrl', config.Admin.HostName); // use hostname for this environment
        cy.clearCookie(ADMIN_COOKIE); // clear auth cookie
        cy.task('deleteAllEmails'); // purge email inbox to make getting the 2fa code easier

        LoginPage.Login(Cypress.env('UI_TEST_USER'), Cypress.env('ADMIN_USER_PASSWORD'));

        // ensure we logged in correctly
        cy.contains('h1', 'Sign-in history').should('be.visible');
    });

    // Before each test, persist the auth cookie so we don't have to login again
    beforeEach(() => {
        // stop the recurring dashboard fetch requests
        cy.intercept('GET', '**/dashboard.json', {});
    });

    it('succeeds and ADMIN displays accounts page', () => {
        cy.visit("/accounts");

        cy.injectAxe();
        cy.checkA11y();
        cy.contains('h1', 'Your services').should('be.visible');
    });

    it('displays notify service page', () => {
        cy.visit(`/services/${config.Services.Notify}`);
        cy.contains('h1', 'Dashboard').should('be.visible');
    });

    it('has a qualtrics survey', () => {
        cy.get('#QSIFeedbackButton-btn').should('be.visible'); // qualtrics survey button
        cy.get('#QSIFeedbackButton-btn').click(); // click the button
        cy.get('#QSIFeedbackButton-survey-iframe').should('be.visible'); // 
    });
});