import jwt from "jsonwebtoken";
import config from "../../config";

const Utilities = {
    CreateJWT: () => {
        const claims = {
            'iss': Cypress.env('ADMIN_USERNAME'),
            'iat': Math.round(Date.now() / 1000)
        }

        var token = jwt.sign(claims, Cypress.env('ADMIN_SECRET'));

        return token;
    },
};
const Admin = {
    SendOneOff: ({to, template_id}) => {

        var token = Utilities.CreateJWT();
        return cy.request({
            url: `/service/${config.Services.Cypress}/send-notification`,
            method: 'POST',
            headers: {
                Authorization: `Bearer ${token}`,
            },
            body: {
                'to': to,
                'template_id': template_id,
                'created_by': Cypress.env('NOTIFY_USER_ID'),
            }
        });
    }
}

const API = {
    SendEmail: ({ api_key, to, template_id, personalisation, failOnStatusCode = true, email_reply_to_id }) => {
        return cy.request({
            failOnStatusCode: failOnStatusCode,
            url: '/v2/notifications/email',
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: 'ApiKey-v1 ' + api_key,
            },
            body: {
                "email_address": to,
                "template_id": template_id,
                "personalisation": personalisation,
                ...(email_reply_to_id) && { email_reply_to_id: email_reply_to_id } // only add email_reply_to_id if it's defined
            }
        });
    },
    SendBulkEmail: ({ api_key, to, bulk_name, template_id, personalisation, failOnStatusCode = true, scheduled_for}) => {
        return cy.request({
            failOnStatusCode: failOnStatusCode,
            url: '/v2/notifications/bulk',
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: 'ApiKey-v1 ' + api_key,
            },
            body: {
                "name": bulk_name,
                "template_id": template_id,
                "rows": [
                    ["email address"],
                    ...to
                ],
                ...(scheduled_for) && { scheduled_for: scheduled_for } // only add scheduled_for if it's defined
            }
        });
    },
    SendSMS: ({ api_key, to, template_id, personalisation, failOnStatusCode = true }) => {
        return cy.request({
            failOnStatusCode: failOnStatusCode,
            url: '/v2/notifications/sms',
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: 'ApiKey-v1 ' + api_key,
            },
            body: {
                "phone_number": to,
                "template_id": template_id,
                "personalisation": personalisation,
            }
        });
    },
    SendBulkSMS: ({ api_key, to, bulk_name, template_id, personalisation, failOnStatusCode = true }) => {
        return cy.request({
            failOnStatusCode: failOnStatusCode,
            url: '/v2/notifications/bulk',
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: 'ApiKey-v1 ' + api_key,
            },
            body: {
                "name": bulk_name,
                "template_id": template_id,
                "rows": [
                    ["phone number"],
                    ...to
                ],
            }
        });
    },
    GetAPIStatus: () => {
        return cy.request({
            url: '/',
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            },
        });
    }
}

export default { API, Utilities, Admin };
