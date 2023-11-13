/// <reference types="cypress" />

import config from '../../../config';
import Notify from "../../Notify/NotifyAPI";

describe(`Email notifications test[${config.CONFIG_NAME}]`, () => {
  var keys = {
    LIVE: Cypress.env(config.CONFIG_NAME).API_KEY_LIVE,
    // TEAM: Cypress.env(config.CONFIG_NAME).API_KEY_TEAM,
    // TEST: Cypress.env(config.CONFIG_NAME).API_KEY_TEST,
  };


  for (const api_key in keys) {
    context(`With ${api_key} api key`, () => {
      it('can send email notification without personalisation', () => {
        Notify.API.SendEmail({
          api_key: keys[api_key],
          to: api_key === 'TEAM' ? config.Users.Team[0] : config.Users.Simulated[1],
          template_id: config.Templates.SIMPLE_EMAIL_TEMPLATE_ID,
          personalisation: {},
        }).as('emailRequest');

        cy.get('@emailRequest').then(resp => {
          expect(resp.status).to.eq(201);
        });
      });

      it('can send email notification with personalisation', () => {
        Notify.API.SendEmail({
          api_key: keys[api_key],
          to: api_key === 'TEAM' ? config.Users.Team[0] : config.Users.Simulated[1],
          template_id: config.Templates.VARIABLES_EMAIL_TEMPLATE_ID,
          personalisation: {
            name: 'Alex',
            has_stuff: true
          },
        }).as('emailRequest');

        cy.get('@emailRequest').then(resp => {
          expect(resp.status).to.eq(201);
        });

      });

      it('can send email to smoke test addresses', () => {
        if (api_key !== 'TEAM') {
          for (const email of config.Users.Simulated) {
            Notify.API.SendEmail({
              api_key: keys[api_key],
              to: email,
              template_id: config.Templates.SIMPLE_EMAIL_TEMPLATE_ID,
              personalisation: {},
            }).as('emailRequest');

            cy.get('@emailRequest').then(resp => {
              expect(resp.status).to.eq(201);
            });
          }
        }
      });

      it('can use a non-default replyTo', () => {
        Notify.API.SendEmail({
          api_key: keys[api_key],
          to: config.Users.Simulated[0],
          template_id: config.Templates.SIMPLE_EMAIL_TEMPLATE_ID,
          personalisation: {},
          email_reply_to_id: config.ReplyTos.Second
        }).as('emailRequest');

        cy.get('@emailRequest').then(resp => {
          expect(resp.status).to.eq(201);
        });
      });

      it('can use a default replyTo', () => {
        Notify.API.SendEmail({
          api_key: keys[api_key],
          to: config.Users.Simulated[0],
          template_id: config.Templates.SIMPLE_EMAIL_TEMPLATE_ID,
          personalisation: {},
          email_reply_to_id: config.ReplyTos.Default
        }).as('emailRequest');

        cy.get('@emailRequest').then(resp => {
          expect(resp.status).to.eq(201);
        });
      });

      it('can use no replyTo', () => {
        Notify.API.SendEmail({
          api_key: keys[api_key],
          to: config.Users.Simulated[0],
          template_id: config.Templates.SIMPLE_EMAIL_TEMPLATE_ID,
          personalisation: {}
        }).as('emailRequest');

        cy.get('@emailRequest').then(resp => {
          expect(resp.status).to.eq(201);
        });
      });

      it('Can scheduled a bulk email send', () => {
        // Schedule 20 seconds from now
        var secheduled_for = new Date(); 
        secheduled_for.setSeconds(secheduled_for.getSeconds()+20);

        // send an email using the Notify API
        Notify.API.SendBulkEmail({
            api_key: Cypress.env(config.CONFIG_NAME).API_KEY_LIVE,
            to: [[config.Users.Simulated[0]],[config.Users.Simulated[0]],[config.Users.Simulated[0]],[config.Users.Simulated[0]],[config.Users.Simulated[0]]],
            bulk_name: "Smoke Test",
            template_id: config.Templates.SIMPLE_EMAIL_TEMPLATE_ID,
            personalisation: {},
            scheduled_for: secheduled_for.toISOString(),
        }).as('emailRequest');

        // ensure API returns a 201
        cy.get('@emailRequest').then(resp => {
            expect(resp.status).to.eq(201);
        });
      });
      // Additional tests for TEAM keys
      if (api_key === 'TEAM') {
        it('can send to team address', () => {
          Notify.API.SendEmail({
            api_key: keys[api_key],
            to: config.Users.Team[0],
            template_id: config.Templates.SIMPLE_EMAIL_TEMPLATE_ID,
            personalisation: {},
          }).as('emailRequest');

          cy.get('@emailRequest').then(resp => {
            expect(resp.status).to.eq(201);
          });
        });

        it('cannot send to non-team address', () => {
          Notify.API.SendEmail({
            api_key: keys[api_key],
            to: config.Users.NonTeam[0],
            template_id: config.Templates.SIMPLE_EMAIL_TEMPLATE_ID,
            personalisation: {},
            failOnStatusCode: false
          }).as('emailRequest');

          cy.get('@emailRequest').then(resp => {
            expect(resp.status).to.eq(400);
          });
        });
      }
    });
  }
});

