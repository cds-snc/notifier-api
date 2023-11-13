let STAGING = {
    CONFIG_NAME: "STAGING",
    Hostnames: {
        API: 'https://api.staging.notification.cdssandbox.xyz',
        Admin: 'https://staging.notification.cdssandbox.xyz',
        DDAPI: 'https://api.document.staging.notification.cdssandbox.xyz',
    },
    Services: {
        Notify: 'd6aa2c68-a2d9-4437-ab19-3ae8eb202553',
        Cypress: '5c8a0501-2aa8-433a-ba51-cefb8063ab93'
    },
    Templates: {
        'FILE_ATTACH_TEMPLATE_ID': '7246c71e-3d60-458b-96af-af17a5b07659',
        'SIMPLE_EMAIL_TEMPLATE_ID': '939dafde-1b60-47f0-a6d5-c9080d92a4a8',
        'VARIABLES_EMAIL_TEMPLATE_ID': '1101a00a-11b7-4036-865c-add43fcff7c9',
        'SMOKE_TEST_EMAIL': '5e26fae6-3565-44d5-bfed-b18680b6bd39',
        'SMOKE_TEST_EMAIL_BULK': '04145882-0f21-4d57-940d-69883fc23e77',
        'SMOKE_TEST_EMAIL_ATTACH': 'bf85def8-01b4-4c72-98a8-86f2bc10f2a4',
        'SMOKE_TEST_EMAIL_LINK': '37924e87-038d-48b8-b122-f6dddefd56d5',
        'SMOKE_TEST_SMS': '16cae0b3-1d44-47ad-a537-fd12cc0646b6'
    },
    Users: {
        Team: ['andrew.leith+bannertest@cds-snc.ca'],
        NonTeam: ['person@example.com'],
        Simulated: ['simulate-delivered-2@notification.canada.ca', 'simulate-delivered-3@notification.canada.ca', 'success@simulator.amazonses.com'],
        SimulatedPhone: ['+16132532222', '+16132532223', '+16132532224']
    },
    ReplyTos: {
        Default: '24e5288d-8bfa-4ad4-93aa-592c11a694cd',
        Second: '797865c4-788b-4184-91ae-8e45eb07e40b'
    },
    viewports: [320,375,640,768]
};

let LOCAL = {
    CONFIG_NAME: "LOCAL",
    Hostnames: {
        API: 'http://localhost:6011',
        Admin: 'http://localhost:6012',
        DDAPI: 'http://localhost:7000',
    },
    Services: {
        Notify: 'd6aa2c68-a2d9-4437-ab19-3ae8eb202553',
        Cypress: '4049c2d0-0cab-455c-8f4c-f356dff51810'
    },
    Templates: {
        'FILE_ATTACH_TEMPLATE_ID': '7246c71e-3d60-458b-96af-af17a5b07659',
        'SIMPLE_EMAIL_TEMPLATE_ID': 'b4692883-4182-4a23-b1b9-7b9df66a66e8',
        'VARIABLES_EMAIL_TEMPLATE_ID': '258d8617-da88-4faa-ad28-46cc69f5a458',
        'SMOKE_TEST_EMAIL': '136e951e-05c8-4db4-bc50-fe122d72fcaa',
        'SMOKE_TEST_EMAIL_BULK': '48207d93-144d-4ebb-92c5-99ff1f1baead',
        'SMOKE_TEST_EMAIL_ATTACH': '58db03d6-a9d8-4482-8621-26f473f3980a',
        'SMOKE_TEST_EMAIL_LINK': '2d52d997-42d3-4ac0-a597-7afc94d4339a',
        'SMOKE_TEST_SMS': '5945e2f0-3e37-4813-9a60-e0665e02e9c8'
    },
    Users: {
        Team: ['notify-ui-tests@cds-snc.ca'],
        NonTeam: ['person@example.com'],
        Simulated: ['simulate-delivered-2@notification.canada.ca', 'simulate-delivered-3@notification.canada.ca', 'success@simulator.amazonses.com'],
        SimulatedPhone: ['+16132532222', '+16132532223', '+16132532224']
    },
    ReplyTos: {
        Default: '1bc45a34-f4de-4635-b36f-7da2e2d248ed',
        Second: 'aaa58593-fc0a-46b0-82b8-b303ae662a41'
    },
    viewports: [320,375,640,768]
};

const config = {
    STAGING,
    LOCAL,
};

// choose which config to use here
const ConfigToUse = config.LOCAL;

module.exports = ConfigToUse;
