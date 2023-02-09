"""

Revision ID: 0425_update_system_templates
Revises: 0424_sms_templates_in_redacted
Create Date: 2022-09-21 00:00:00

"""
from datetime import datetime

from alembic import op
from flask import current_app

revision = "0425_update_system_templates"
down_revision = "0424_sms_templates_in_redacted"

templates = [
    {
        "id": current_app.config["ALREADY_REGISTERED_EMAIL_TEMPLATE_ID"],
        "name": "Your Notify account",
        "template_type": "email",
        "content": """[[en]]\r\nYou already have a GC Notify account with this email address.\r\n\r\n[Sign in](((signin_url)) ""Sign in"")\r\n\r\nIf you’ve forgotten your password, you can reset it here: [Password reset](((forgot_password_url)) ""Password reset"")\r\n\r\nIf you didn’t try to register for an account recently, please contact us: ((feedback_url))\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\nVous avez déjà un compte Notification GC avec cette adresse courriel.\r\n\r\n[Connectez-vous](((signin_url)) ""Connectez-vous"")\r\n\r\nSi vous avez oublié votre mot de passe, vous pouvez le réinitialiser ici: [Réinitialisation du mot de passe](((Forgot_password_url)) ""Réinitialisation du mot de passe"")\r\n\r\nSi vous n''avez pas essayé de vous connecter à un compte récemment, veuillez communiquer avec nous: ((feedback_url))\r\n[[/fr]]""",
        "subject": "Your account | Votre compte",
        "process_type": "priority",
    },
    {
        "id": current_app.config["ORGANISATION_INVITATION_EMAIL_TEMPLATE_ID"],
        "name": "Notify organisation invitation email",
        "template_type": "email",
        "content": """[[en]]\r\n((user_name)) has invited you to collaborate on ((organisation_name)) on GC Notify.\r\n\r\nGC Notify makes it easy to keep people updated by helping you send emails and text messages.\r\n\r\nTo create an account on GC Notify, use this link:\r\n((url))\r\n\r\nThis invitation will stop working at midnight tomorrow. This is to keep ((organisation_name)) secure.\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\n((user_name)) vous a invité à collaborer sure ((organisation_name)) dans Notification GC.\r\n\r\nNotification GC facilite la mise à jour des personnes en vous aidant à envoyer des courriels et messages texte.\r\n\r\nUtilisez ce lien pour créer un compte sur Notification GC:\r\n((url))\r\n\r\nCette invitation cessera de fonctionner à minuit demain. Ceci est de garder ((organisation_name)) sécurisé.\r\n[[/fr]]""",
        "subject": "((user_name)) has invited you to collaborate | ((user_name)) vous a invité à collaborer",
        "process_type": "priority",
    },
    {
        "id": current_app.config["EMAIL_2FA_TEMPLATE_ID"],
        "name": "Notify email verify code",
        "template_type": "email",
        "content": """[[en]]\r\nHi ((name)),\r\n\r\n((verify_code)) is your security code to log in to GC Notify.\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\nBonjour ((name)),\r\n\r\n((verify_code)) est votre code de sécurité pour vous connecter à Notification GC.\r\n[[/fr]]""",
        "subject": "Sign in | Connectez-vous",
        "process_type": "priority",
    },
    {
        "id": current_app.config["SMS_CODE_TEMPLATE_ID"],
        "name": "Notify SMS verify code",
        "template_type": "sms",
        "content": """((verify_code)) is your GC Notify authentication code | ((verify_code)) est votre code d''authentification de Notification GC""",
        "subject": "None",
        "process_type": "priority",
    },
    {
        "id": current_app.config["PASSWORD_RESET_TEMPLATE_ID"],
        "name": "Notify password reset email",
        "template_type": "email",
        "content": """[[en]]\r\nHi ((user_name)),\r\n\r\nWe received a request to reset your password on GC Notify.\r\n\r\nIf you didn''t request this email, you can ignore it – your password has not been changed.\r\n\r\nTo reset your password, click this link:\r\n[Password reset](((url)) ""Password reset"")\r\n[[/en]]\r\n\r\n___\r\n\r\n[[fr]]\r\nBonjour ((user_name)),\r\n\r\nNous avons reçu une demande de réinitialisation de votre mot de passe dans Notification GC.\r\n\r\nSi vous n''avez pas demandé ce courriel, vous pouvez l''ignorer - votre mot de passe n''a pas été changé.\r\n\r\nPour réinitialiser votre mot de passe, cliquez sur ce lien:\r\n[Réinitialisation du mot de passe](((url)) ""Réinitialisation du mot de passe"")\r\n[[/fr]]""",
        "subject": "Reset your password | Réinitialiser votre mot de passe",
        "process_type": "priority",
    },
    {
        "id": current_app.config["INVITATION_EMAIL_TEMPLATE_ID"],
        "name": "Notify invitation email",
        "template_type": "email",
        "content": """[[en]]\r\n((user_name)) has invited you to collaborate on ((service_name)) on GC Notify.\r\n\r\nGC Notify makes it easy to keep people updated by helping you send emails and text messages.\r\n\r\nTo accept the invitation, use this link:\r\n((url))\r\n\r\nThis invitation will stop working at midnight tomorrow. This is to keep ((service_name)) secure.\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\n((user_name)) vous a invité à collaborer sur ((service_name)) dans Notification GC.\r\n\r\nNotification GC facilite la mise à jour des personnes en vous aidant à envoyer des courriels et des messages texte.\r\n\r\nUtilisez ce lien pour accepter l''invitation :\r\n((url))\r\n\r\nCette invitation cessera de fonctionner à minuit demain. C''est pour garder ((service_name)) sécurisé.\r\n[[/fr]]""",
        "subject": "((user_name)) invited you to collaborate | ((user_name)) vous a invité à collaborer",
        "process_type": "priority",
    },
    {
        "id": current_app.config["MOU_SIGNER_RECEIPT_TEMPLATE_ID"],
        "name": "MOU Signed By Receipt (not in use)",
        "template_type": "email",
        "content": """[[en]]\r\nHi ((signed_by_name)),\r\n\r\n((org_name)) has accepted the GC Notify data sharing and financial agreement. \r\n\r\nIf you need another copy of the agreement you can download it here: ((mou_link))\r\n\r\nThanks,\r\nThe GC Notify team\r\nhttps://notification.canada.ca\r\n[[/en]]""",
        "subject": "You’ve accepted the GC Notify data sharing and financial agreement",
        "process_type": "priority",
    },
    {
        "id": current_app.config["MOU_SIGNED_ON_BEHALF_ON_BEHALF_RECEIPT_TEMPLATE_ID"],
        "name": "MOU Signed On Behalf Of Receipt - On Behalf Of (not in use)",
        "template_type": "email",
        "content": """[[en]]\r\nHi ((on_behalf_of_name)),\r\n\r\n((signed_by_name)) has accepted the GC Notify data sharing and financial agreement on your behalf, for ((org_name)).\r\n\r\nGC Notify lets teams in the public sector send emails and text messages. It’s built and run by a team at the Canadian Digital Service.\r\n\r\nIf you need another copy of the agreement you can download it here: ((mou_link))\r\n\r\nThanks,\r\nThe GC Notify team\r\nhttps://notification.canada.ca \r\n[[/en]]""",
        "subject": "((org_name)) has accepted the GC Notify data sharing and financial agreement",
        "process_type": "priority",
    },
    {
        "id": current_app.config["ACCOUNT_CHANGE_TEMPLATE_ID"],
        "name": "Account update",
        "template_type": "email",
        "content": """[[en]]\r\nYour GC Notify user account information was changed on ((base_url)).\r\n\r\nUpdated information: ((change_type_en))\r\n\r\nIf you did not make this change, [contact us](((contact_us_url)) ""contact us"") immediately.\r\n[[/en]]\r\n\r\n___\r\n\r\n[[fr]]\r\nLes renseignements de votre compte d''utilisateur ont été modifiées sur ((base_url)).\r\n\r\nRenseignements mis à jour : ((change_type_fr))\r\n\r\nSi vous n''avez pas effectué ce changement, [communiquez avec nous](((contact_us_url)) ""communiquez avec nous"") immédiatement.\r\n[[/fr]]""",
        "subject": "Account information changed | Renseignements de compte modifiés",
        "process_type": "priority",
    },
    {
        "id": current_app.config["NEAR_DAILY_LIMIT_TEMPLATE_ID"],
        "name": "Near combined daily limit",
        "template_type": "email",
        "content": """Hello ((name)),\r\n\r\n((service_name)) just reached 80% of its daily limit of ((message_limit_en)) messages. Your service will be blocked from sending if you go above the daily limit by the end of the day.\r\n\r\nYou can request a limit increase by [contacting us](((contact_url))).\r\n\r\nThe GC Notify team\r\n\r\n___\r\n\r\nBonjour ((name)),\r\n\r\n((service_name)) vient d’atteindre 80% de sa limite quotidienne de ((message_limit_fr)) messages. Votre service ne pourra plus envoyer de messages si vous allez au-delà de votre limite d’ici la fin de journée.\r\n\r\nVous pouvez demander à augmenter cette limite en [nous contactant](((contact_url))).\r\n\r\nL’équipe Notification GC""",
        "subject": "Action required: 80% of daily sending limit reached for ((service_name)) | Action requise: 80% de la limite d’envoi quotidienne atteinte pour ((service_name))",
        "process_type": "normal",
    },
    {
        "id": current_app.config["SERVICE_NOW_LIVE_TEMPLATE_ID"],
        "name": """Automated "You''re now live" message""",
        "template_type": "email",
        "content": """[[en]]\r\nHello ((name)),\r\n\r\n\r\n((service_name)) is now live on GC Notify.\r\n\r\nYou’re all set to send notifications outside your team.\r\n\r\n\r\nYou can send up to ((message_limit_en)) messages per day.\r\n\r\nIf you ever need to send more messages, [contact us](((contact_us_url)) ""contact us"").\r\n\r\n\r\n[Sign in to GC Notify](((signin_url)) ""Sign in to GC Notify"")\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\nBonjour ((name)),\r\n\r\n\r\n((service_name)) est maintenant activé sur Notification GC.\r\n\r\nVous êtes prêts à envoyer des notifications en dehors de votre équipe.\r\n\r\n\r\nVous pouvez envoyer jusqu’à ((message_limit_fr)) messages par jour.\r\n\r\nSi jamais vous avez besoin d’envoyer plus de messages, [communiquez avec nous](((contact_us_url)) ""communiquez avec nous"").\r\n\r\n\r\n[Connectez-vous à Notification GC](((signin_url)) ""Connectez-vous à Notification GC"")\r\n[[/fr]]""",
        "subject": "Your service is now live | Votre service est maintenant activé",
        "process_type": "priority",
    },
    {
        "id": current_app.config["DAILY_SMS_LIMIT_UPDATED_TEMPLATE_ID"],
        "name": "Daily SMS limit updated",
        "template_type": "email",
        "content": """(la version française suit)\r\n\r\nHello ((name)),\r\n\r\nYou can now send ((message_limit_en)) text fragments per day. \r\n\r\nThe GC Notify Team\r\n\r\n___\r\n\r\nBonjour ((name)),\r\n\r\nVous pouvez désormais envoyer ((message_limit_fr)) fragments de message texte par jour. \r\n\r\nL’équipe Notification GC""",
        "subject": "We’ve updated the daily limit for ((service_name)) | Limite quotidienne d’envoi mise à jour pour ((service_name)).",
        "process_type": "priority",
    },
    {
        "id": current_app.config["BRANDING_REQUEST_TEMPLATE_ID"],
        "name": "Support - Branding Request",
        "template_type": "email",
        "content": """[[en]]\r\nA new logo has been uploaded by ((email)) for the following service:\r\n\r\nService id: ((service_id))\r\nService name: ((service_name))\r\n\r\nLogo filename: ((url))\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\nUn nouveau logo a été téléchargé par ((email)) pour le service suivant :\r\n\r\nIdentifiant du service : ((service_id))\r\nNom du service : ((service_name))\r\n\r\nNom du fichier du logo : ((url))\r\n[[/fr]]""",
        "subject": "Branding change request for ((service_name)) | Demande de changement d''image de marque pour ((service_name))",
        "process_type": "priority",
    },
    {
        "id": current_app.config["NO_REPLY_TEMPLATE_ID"],
        "name": "No Reply",
        "template_type": "email",
        "content": """[[en]]\r\nYour message was not delivered.\r\n\r\nThe email address ((sending_email_address)) is not able to receive messages since this feature has not been set by the sender.\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\nVotre message n’a pas été livré.\r\n\r\nL’adresse courriel ((sending_email_address)) ne peut pas recevoir de messages car cette fonction n’a pas été définie par l’expéditeur.\r\n[[/fr]]""",
        "subject": "Message not delivered | Message non livré",
        "process_type": "normal",
    },
    {
        "id": current_app.config["TEAM_MEMBER_EDIT_MOBILE_TEMPLATE_ID"],
        "name": "Phone number changed by service manager",
        "template_type": "sms",
        "content": """Your mobile number was changed by ((servicemanagername)). Next time you sign in, your GC Notify authentication code will be sent to this phone. | Votre numéro de téléphone mobile a été modifié par ((servicemanagername)). Lors de votre prochaine connexion, votre code d''authentification de Notification GC sera envoyé à ce téléphone.""",
        "subject": "None",
        "process_type": "priority",
    },
    {
        "id": current_app.config["REPLY_TO_EMAIL_ADDRESS_VERIFICATION_TEMPLATE_ID"],
        "name": "Verify email reply-to address for a service",
        "template_type": "email",
        "content": """[[en]]\r\nHi,\r\n\r\nThis address has been provided as a reply-to email address for a  GC Notify account.\r\n\r\nAny replies from users to emails they receive through GC Notify will come back to this email address.\r\n\r\nThis is just a quick check to make sure the address is valid.\r\n\r\nNo need to reply.\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\nBonjour,\r\n\r\nCette adresse a été fournie comme adresse courriel de réponse pour un compte Notification GC.\r\n\r\nToute réponse des utilisateurs aux courriel qu''ils reçoivent via Notification GC reviendra à cette adresse courriel.\r\n\r\nCeci est juste une vérification rapide pour vous assurer que cette adresse courriel est valide.\r\n\r\nPas besoin de répondre.\r\n[[/fr]]""",
        "subject": "Your reply-to email address | Votre adresse courriel de réponse",
        "process_type": "priority",
    },
    {
        "id": current_app.config["REACHED_DAILY_SMS_LIMIT_TEMPLATE_ID"],
        "name": "Daily SMS limit reached",
        "template_type": "email",
        "content": """(la version française suit)\r\n\r\nHello ((name)),\r\n\r\n((service_name)) has sent ((message_limit_en)) text message fragments today. \r\n\r\nIf a text message is long, it travels in fragments. The fragments assemble into 1 message for the recipient. Each fragment counts towards your daily limit.\r\n\r\nThe number of fragments may be higher than the number of recipients. Complex factors determine how messages split into fragments. These factors include character count and type of characters used. \r\n\r\nYou can send more messages tomorrow. \r\n\r\nTo request a limit increase, [contact us](((contact_url))). We’ll respond within 1 business day.\r\n\r\nThe GC Notify team\r\n\r\n___\r\n\r\nBonjour ((name)),\r\n\r\nAujourd’hui, ((service_name)) a envoyé ((message_limit_fr)) fragments de message texte. \r\n\r\nLorsqu’un message texte est long, il se fragmente lors de la transmission. Tous les fragments sont rassemblés pour former un message unique pour le destinataire. Chaque fragment compte dans votre limite quotidienne.\r\n\r\nLe nombre de fragments peut être supérieur au nombre de destinataires. La division des messages en fragments dépend de facteurs complexes, dont le nombre de caractères et le type de caractères utilisés. \r\n\r\nVous pourrez à nouveau envoyer des messages dès demain. \r\n\r\nVeuillez [nous contacter](((contact_url))) si vous désirez augmenter votre limite d’envoi. Nous vous répondrons en un jour ouvrable.\r\n\r\nL’équipe Notification GC""",
        "subject": "((service_name)) has reached its daily limit for text fragments | Limite quotidienne d’envoi de fragments de message texte atteinte pour ((service_name)).",
        "process_type": "normal",
    },
    {
        "id": current_app.config["NEAR_DAILY_SMS_LIMIT_TEMPLATE_ID"],
        "name": "Near daily SMS limit",
        "template_type": "email",
        "content": """(la version française suit)\r\n\r\n\r\nHello ((name)),\r\n\r\nIf a text message is long, it travels in fragments. The fragments assemble into 1 message for the recipient. Each fragment counts towards your daily limit.\r\n\r\nThe number of fragments may be higher than the number of recipients. Complex factors determine how messages split into fragments. These factors include character count and type of characters used.\r\n\r\n((service_name)) can send ((message_limit_en)) text fragments per day. You’ll be blocked from sending if you exceed that limit before the end of the day. \r\n\r\nTo request a limit increase, [contact us](((contact_url))). We’ll respond within 1 business day.\r\n\r\nThe GC Notify team\r\n\r\n___\r\n\r\nBonjour ((name)),\r\n\r\nLorsqu’un message texte est long, il se fragmente lors de la transmission. Tous les fragments sont rassemblés pour former un message unique pour le destinataire. Chaque fragment compte dans votre limite quotidienne.\r\n\r\nLe nombre de fragments peut être supérieur au nombre de destinataires. La division des messages en fragments dépend de facteurs complexes, dont le nombre de caractères et le type de caractères utilisés.\r\n\r\n((service_name)) peut envoyer ((message_limit_fr)) fragments de message texte par jour. Si vous atteignez cette limite avant la fin de la journée, vous ne pourrez plus envoyer de messages texte. \r\n\r\nVeuillez [nous contacter](((contact_url))) si vous désirez augmenter votre limite d’envoi. Nous vous répondrons en un jour ouvrable.\r\n\r\nL’équipe Notification GC""",
        "subject": "((service_name)) is near its daily limit for text fragments. | La limite quotidienne d’envoi de fragments de message texte est presque atteinte pour ((service_name)).",
        "process_type": "normal",
    },
    {
        "id": current_app.config["DAILY_LIMIT_UPDATED_TEMPLATE_ID"],
        "name": "Combined daily limit updated",
        "template_type": "email",
        "content": """Hello ((name)),\r\n\r\nThe daily limit of ((service_name)) has just been updated. You can now send ((message_limit_en)) messages per day. This new limit is effective now.\r\n\r\nThe GC Notify team\r\n\r\n___\r\n\r\nBonjour ((name)),\r\n\r\nLa limite quotidienne de ((service_name)) a été mise à jour. Vous pouvez désormais envoyer ((message_limit_fr)) messages par jour. Ce changement est effectif dès maintenant.\r\n\r\nL’équipe Notification GC""",
        "subject": "Daily sending limit updated for ((service_name)) | Limite d’envoi quotidienne mise à jour pour ((service_name))",
        "process_type": "normal",
    },
    {
        "id": current_app.config["MOU_SIGNED_ON_BEHALF_SIGNER_RECEIPT_TEMPLATE_ID"],
        "name": "MOU Signed On Behalf Of Receipt - Signed by (not in use)",
        "template_type": "email",
        "content": """[[en]]\r\nHi ((signed_by_name)),\r\n\r\n((org_name)) has accepted the GC Notify data sharing and financial agreement. We’ve emailed ((on_behalf_of_name)) to let them know.\r\n\r\nIf you need another copy of the agreement you can download it here: ((mou_link))\r\n\r\nThanks,\r\nThe GC Notify team\r\nhttps://notification.canada.ca\r\n[[/en]]""",
        "subject": "You’ve accepted the GC Notify data sharing and financial agreement",
        "process_type": "priority",
    },
    {
        "id": current_app.config["TEAM_MEMBER_EDIT_EMAIL_TEMPLATE_ID"],
        "name": "Email address changed by service manager",
        "template_type": "email",
        "content": """[[en]]\r\nHi ((name)),\r\n\r\n((servicemanagername)) changed your GC Notify account email address to:\r\n\r\n((email address))\r\n\r\nYou’ll need to use this email address next time you sign in.\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\nBonjour ((name)),\r\n\r\n((servicemanagername)) a modifié l''adresse courriel de votre compte Notification GC :\r\n\r\n((email address))\r\n\r\nVous devrez utiliser cette adresse courriel lors de votre prochaine connexion.\r\n[[/fr]]""",
        "subject": "Your email address has changed | Votre adresse courriel a changé",
        "process_type": "priority",
    },
    {
        "id": current_app.config["MOU_NOTIFY_TEAM_ALERT_TEMPLATE_ID"],
        "name": "MOU Signed Notify Team Alert (not in use)",
        "template_type": "email",
        "content": """[[en]]\r\n((signed_by_name)) accepted the data sharing and financial agreement for ((org_name)).\r\n\r\nSee how ((org_name)) is using GC Notify here: ((org_dashboard_link))\r\n[[/en]]""",
        "subject": "Someone signed an MOU for an org on GC Notify",
        "process_type": "priority",
    },
    {
        "id": current_app.config["FORCED_PASSWORD_RESET_TEMPLATE_ID"],
        "name": "Notify forced-password reset email",
        "template_type": "email",
        "content": """Hi ((user_name)),\r\n\r\nTo reset your password, click this link:\r\n\r\n[Password reset](((url))?lang=en)\r\n\r\nThis is your unique link. Do not share this link with anyone.\r\n\r\nIf you didn’t request this email, please [contact us](https://notification.canada.ca/contact?lang=en).\r\n\r\n___\r\n\r\n\r\nBonjour ((user_name)),\r\n\r\nPour réinitialiser votre mot de passe, veuillez cliquer sur le lien suivant :\r\n\r\n[Réinitialisation de votre mot de passe](((url))?lang=fr)\r\n\r\nCe lien est unique. Ne le transmettez à personne. \r\n\r\nSi vous n’avez pas demandé ce courriel, veuillez [nous contacter](https://notification.canada.ca/contact?lang=fr).""",
        "subject": "Reset your password | Réinitialiser votre mot de passe",
        "process_type": "normal",
    },
    {
        "id": current_app.config["CHANGE_EMAIL_CONFIRMATION_TEMPLATE_ID"],
        "name": "Confirm new email address",
        "template_type": "email",
        "content": """[[en]]\r\nHi ((name)),\r\n\r\nClick this link to confirm the new email address for your GC Notify account:\r\n((url))\r\n        \r\nIf you did not try to change your email address, [contact us](\r\n((feedback_url)) ""contact us"").\r\n[[/en]]\r\n___\r\n\r\n[[fr]]\r\nBonjour ((name)),\r\n\r\nCliquez sur ce lien pour confirmer la nouvelle adresse courriel de voter compete Notification GC :\r\n((url))\r\n        \r\nSi vous n''avez pas essayé de changer votre adresse courriel, [communiquez avec nous](\r\n((feedback_url)) ""communiquez avec nous"").\r\n[[/fr]]""",
        "subject": "Confirm new email address | Confirmer votre nouvelle adresse courriel",
        "process_type": "priority",
    },
    {
        "id": current_app.config["NEW_USER_EMAIL_VERIFICATION_TEMPLATE_ID"],
        "name": "Notify email verification code",
        "template_type": "email",
        "content": """[[en]]\r\nHi ((name)),\r\n\r\nTo complete your registration for GC Notify, please click the link:\r\n((url))\r\n[[/en]]\r\n\r\n___\r\n\r\n[[fr]]\r\nBonjour ((name)),\r\n\r\nPour compléter votre inscription à Notification GC, veuillez cliquer sur le lien :\r\n((url))\r\n[[/fr]]""",
        "subject": "Confirm your registration | Confirmer votre inscription",
        "process_type": "priority",
    },
    {
        "id": current_app.config["REACHED_DAILY_LIMIT_TEMPLATE_ID"],
        "name": "Combined daily limit reached",
        "template_type": "email",
        "content": """Hello ((name)),\r\n\r\n((service_name)) has reached its daily limit of ((message_limit_en)) messages. Your service has been blocked from sending messages until tomorrow.\r\n\r\nYou can request a limit increase by [contacting us](((contact_url))).\r\n\r\nThe GC Notify team\r\n\r\n___\r\n\r\nBonjour ((name)),\r\n\r\n((service_name)) vient d’atteindre sa limite quotidienne de ((message_limit_fr)) messages. Votre service ne peut plus envoyer de messages jusqu’à demain.\r\n\r\nVous pouvez demander à augmenter cette limite en [nous contactant](((contact_url))).\r\n\r\nL’équipe Notification GC""",
        "subject": "Action required: Daily sending limit reached for ((service_name)) | Action requise: Limite d’envoi quotidienne atteinte pour ((service_name)) )",
        "process_type": "normal",
    },
]


def upgrade():
    conn = op.get_bind()

    for template in templates:
        current_version = conn.execute("select version from templates where id='{}'".format(template["id"])).fetchone()
        template["version"] = current_version[0] + 1

    template_update = """
        UPDATE templates SET content = '{}', subject = '{}', version = '{}', updated_at = '{}'
        WHERE id = '{}'
    """
    template_history_insert = """
        INSERT INTO templates_history (id, name, template_type, created_at, content, archived, service_id, subject,
        created_by_id, version, process_type, hidden)
        VALUES ('{}', '{}', '{}', '{}', '{}', False, '{}', '{}', '{}', {}, '{}', false)
    """

    for template in templates:
        op.execute(
            template_update.format(
                template["content"],
                template["subject"],
                template["version"],
                datetime.utcnow(),
                template["id"],
            )
        )

        op.execute(
            template_history_insert.format(
                template["id"],
                template["name"],
                template["template_type"],
                datetime.utcnow(),
                template["content"],
                current_app.config["NOTIFY_SERVICE_ID"],
                template["subject"],
                current_app.config["NOTIFY_USER_ID"],
                template["version"],
                template["process_type"],
            )
        )


def downgrade():
    pass
