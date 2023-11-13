# import os
import sqlalchemy as sa

SQLALCHEMY_DATABASE_URI = 'postgresql://postgres:chummy@localhost:5432/notification_api'  # os.getenv("SQLALCHEMY_DATABASE_URI")
print(SQLALCHEMY_DATABASE_URI)

# set connection URI here ↓
engine = sa.create_engine(SQLALCHEMY_DATABASE_URI)

with engine.connect() as conn:
    with conn.begin():
        insert_user = """
        INSERT INTO "public"."users"("id","name","email_address","created_at","updated_at","_password","mobile_number","password_changed_at","logged_in_at","failed_login_count","state","platform_admin","current_session_id","auth_type","blocked","additional_information","password_expired")
            VALUES
            (E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',E'Notify UI Tests',E'notify-ui-tests@cds-snc.ca',E'2023-05-24 13:41:48.022737',E'2023-08-01 13:20:08.017647',E'$2b$10$jwj45gXRLUIteNBwhRgEV.4P7uHGH11O2PhNiDK6SY6oRPSdKhg2u',E'6139863526',E'2023-05-24 13:41:48.017676',E'2023-08-01 13:20:08.014524',0,E'active',FALSE,E'8f6e79df-782d-4620-b86c-adf1ea933934',E'email_auth',FALSE,E'{}',FALSE);
        """

        insert_org = """
            INSERT INTO "public"."organisation"("id","name","active","created_at","updated_at","email_branding_id","letter_branding_id","agreement_signed","agreement_signed_at","agreement_signed_by_id","agreement_signed_version","crown","organisation_type","request_to_go_live_notes","agreement_signed_on_behalf_of_email_address","agreement_signed_on_behalf_of_name","default_branding_is_french")
            VALUES
            (E'93413f91-227f-4704-b229-b8210d1ecc0a',E'GOC',TRUE,E'2023-05-16 15:08:29.552525',NULL,NULL,NULL,FALSE,NULL,NULL,NULL,TRUE,E'central',NULL,NULL,NULL,FALSE);
        """

        insert_service = """
            INSERT INTO "public"."services"("id","name","created_at","updated_at","active","message_limit","restricted","email_from","created_by_id","version","research_mode","organisation_type","prefix_sms","crown","rate_limit","contact_link","consent_to_research","volume_email","volume_letter","volume_sms","count_as_live","go_live_at","go_live_user_id","organisation_id","sending_domain","default_branding_is_french","sms_daily_limit","organisation_notes")
            VALUES
            (E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'Cypress2',E'2023-05-16 15:02:43.663183',E'2023-05-24 14:01:51.335072',TRUE,10000,FALSE,E'bouncey',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',5,FALSE,E'central',TRUE,TRUE,1000,NULL,NULL,NULL,NULL,NULL,FALSE,E'2023-05-16 15:08:46.692247',NULL,E'93413f91-227f-4704-b229-b8210d1ecc0a',NULL,FALSE,1000,NULL);
        """

        insert_s_permissions = """
            INSERT INTO "public"."service_permissions"("service_id","permission","created_at")
            VALUES
            (E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'email',E'2023-05-16 15:02:43.706537'),
            (E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'international_sms',E'2023-05-16 15:02:43.706543'),
            (E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'sms',E'2023-05-16 15:02:43.706528'),
            (E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'upload_document',E'2023-05-24 14:01:51.321006');
        """
        insert_permissions = """
            INSERT INTO "public"."permissions"("id","service_id","user_id","permission","created_at")
            VALUES
            (E'074d892e-4608-45e7-b24f-53203ad75482',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',E'view_activity',E'2023-05-24 13:41:57.755493'),
            (E'1e59292c-ff05-466b-ac11-d77378d94aac',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',E'manage_users',E'2023-05-24 13:41:57.755452'),
            (E'20a1fb31-0e64-43a7-8d5e-fdd5ae97b006',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',E'manage_api_keys',E'2023-05-24 13:41:57.755534'),
            (E'544cbafa-a979-43aa-8727-45082af5c901',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',E'manage_templates',E'2023-05-24 13:41:57.755554'),
            (E'5ec04af6-4842-4c94-9294-b53cdaaf2166',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',E'send_emails',E'2023-05-24 13:41:57.75558'),
            (E'7b3a128a-45ea-4ec7-b897-c5bb1ce515d8',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',E'send_letters',E'2023-05-24 13:41:57.755425');
        """
        insert_user_to_service = """
            INSERT INTO "public"."user_to_service"("user_id","service_id")
            VALUES
            (E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',E'4049c2d0-0cab-455c-8f4c-f356dff51810');
        """

        insert_templates = """
            INSERT INTO "public"."templates"("id","name","template_type","created_at","updated_at","content","service_id","subject","created_by_id","version","archived","process_type","service_letter_contact_id","hidden","postage")
            VALUES
            (E'136e951e-05c8-4db4-bc50-fe122d72fcaa',E'SMOKE_TEST_EMAIL',E'email',E'2023-05-24 13:31:40.907915',NULL,E'# This is a smoke test from Cypress\n\nSMOKE_TEST_EMAIL',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'SMOKE_TEST_EMAIL',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',1,FALSE,E'bulk',NULL,FALSE,NULL),
            (E'258d8617-da88-4faa-ad28-46cc69f5a458',E'VARIABLES_EMAIL_TEMPLATE_ID',E'email',E'2023-07-10 16:38:43.913184',NULL,E'Hi ((name))\n\n((has_stuff??You have stuff!))',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'TESTING',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',1,FALSE,E'bulk',NULL,FALSE,NULL),
            (E'2d52d997-42d3-4ac0-a597-7afc94d4339a',E'SMOKE_TEST_EMAIL_LINK',E'email',E'2023-05-24 13:32:49.286647',NULL,E'# This is a smoke test from Cypress\n\nSMOKE_TEST_EMAIL_LINK\n\n((link_to_file))',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'SMOKE_TEST_EMAIL_LINK',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',1,FALSE,E'bulk',NULL,FALSE,NULL),
            (E'48207d93-144d-4ebb-92c5-99ff1f1baead',E'SMOKE_TEST_EMAIL_BULK',E'email',E'2023-05-24 13:32:24.912515',NULL,E'# This is a smoke test from Cypress\n\nSMOKE_TEST_EMAIL_BULK',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'SMOKE_TEST_EMAIL_BULK',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',1,FALSE,E'bulk',NULL,FALSE,NULL),
            (E'58db03d6-a9d8-4482-8621-26f473f3980a',E'SMOKE_TEST_EMAIL_ATTACH',E'email',E'2023-05-24 13:32:03.754124',NULL,E'# This is a smoke test from Cypress\n\nSMOKE_TEST_EMAIL_ATTACH',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'SMOKE_TEST_EMAIL_ATTACH',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',1,FALSE,E'bulk',NULL,FALSE,NULL),
            (E'5945e2f0-3e37-4813-9a60-e0665e02e9c8',E'SMOKE_TEST_SMS',E'sms',E'2023-05-24 13:33:20.936811',NULL,E'SMOKE_TEST_SMS',E'4049c2d0-0cab-455c-8f4c-f356dff51810',NULL,E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',1,FALSE,E'bulk',NULL,FALSE,NULL),
            (E'b4692883-4182-4a23-b1b9-7b9df66a66e8',E'SIMPLE_EMAIL_TEMPLATE_ID',E'email',E'2023-07-10 16:33:10.288618',NULL,E'TESTING',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'TESTING',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',1,FALSE,E'bulk',NULL,FALSE,NULL);
        """

        insert_api_keys = """
            INSERT INTO "public"."api_keys"("id","name","secret","service_id","expiry_date","created_at","created_by_id","updated_at","version","key_type")
            VALUES
            (E'076fc5ba-8578-44d6-8b69-122ac3a7b206',E'CYPRESS_TEST_KEY',E'IjY3YTViZmU4LWMxNjktNDQ0NS04OGVjLWRkNWExZDA3NjFmYSI.wLt-AQOYG3vgab_qP1p0Djw_r-8',E'4049c2d0-0cab-455c-8f4c-f356dff51810',NULL,E'2023-07-10 16:44:13.025984',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',NULL,1,E'test'),
            (E'61a3f8e1-516d-446c-bc3a-c2dac33c9474',E'CYPRESS',E'IjhmYmE3MzUwLTIwN2ItNDRhYi1iNmYwLTVkZjk3ZjAzNjZlMyI.xiQ94d2oAedkohXNhx83B6cWr_0',E'4049c2d0-0cab-455c-8f4c-f356dff51810',NULL,E'2023-05-24 13:48:23.93561',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',NULL,1,E'normal'),
            (E'74a06881-7742-4d4a-85ff-b91ae71e1bcd',E'CYPRESS_TEAM_KEY',E'IjYxZjhhYzU3LTNlZGEtNDE3MS1iMDcyLTJjNTY0OWEzODI4ZSI.p6K5JNnYp6Pcn3OEQg1kDM1UHAY',E'4049c2d0-0cab-455c-8f4c-f356dff51810',NULL,E'2023-07-10 16:43:19.425995',E'3f478896-6d3f-4ef3-aa5a-530fea1206bb',NULL,1,E'team');
        """
        insert_reply_tos = """
            INSERT INTO "public"."service_email_reply_to"("id","service_id","email_address","is_default","created_at","updated_at","archived")
            VALUES
            (E'1bc45a34-f4de-4635-b36f-7da2e2d248ed',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'andrew.leith+testing@cds-snc.ca',TRUE,E'2023-07-10 16:41:25.562693',NULL,FALSE),
            (E'aaa58593-fc0a-46b0-82b8-b303ae662a41',E'4049c2d0-0cab-455c-8f4c-f356dff51810',E'andrew.leith+testing_second_reply@cds-snc.ca',FALSE,E'2023-07-10 16:42:30.376411',NULL,FALSE);
        """
        conn.execute(sa.text(insert_user))
        conn.execute(sa.text(insert_org))
        conn.execute(sa.text(insert_service))
        conn.execute(sa.text(insert_s_permissions))
        conn.execute(sa.text(insert_permissions))
        conn.execute(sa.text(insert_user_to_service))
        conn.execute(sa.text(insert_templates))
        conn.execute(sa.text(insert_api_keys))
        conn.execute(sa.text(insert_reply_tos))
