/// <reference types="cypress" />

import config from '../../../config';
import Notify from "../../Notify/NotifyAPI";

describe(`Sanity check [${config.CONFIG_NAME}]`, () => {
  it("Can connect to API", () => {
    Notify.API.GetAPIStatus().as('apiStatus');

    cy.get('@apiStatus').then(resp => {
      expect(resp.status).to.eq(200);
    });
  });
});

