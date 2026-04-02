# mailer.py — Envoi d'emails HTML via Office 365

import smtplib
import config
import database as db
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from jinja2 import Environment, FileSystemLoader, select_autoescape
import os

# Environnement Jinja2 pour les templates emails
_jinja_env = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates", "emails")),
    autoescape=select_autoescape(["html"])
)


def _render_template(template_name, context):
    """Rendu d'un template Jinja2 email."""
    tpl = _jinja_env.get_template(template_name)
    return tpl.render(**context)


def _send_mail(to_email, subject, html_body):
    """Envoi SMTP avec authentification Office 365 (STARTTLS)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{config.SMTP_FROM_NAME} <{config.SMTP_USER}>"
    msg["To"] = to_email

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.sendmail(config.SMTP_USER, to_email, msg.as_string())


def send_event_notification(user, event_id, etype, points, score_avant,
                             score_apres, niveau_avant, niveau_apres,
                             raison, annulation=False):
    """
    Envoie un email de notification d'événement au collaborateur.
    Lève une exception en cas d'échec (loggée par models.py).
    """
    montee_niveau = (not annulation) and (niveau_apres != niveau_avant)
    descente_niveau = annulation and (niveau_apres != niveau_avant)

    context = {
        "company_name": db.get_setting("company_name", config.COMPANY_NAME),
        "user": user,
        "etype": etype,
        "points": points,
        "score_avant": score_avant,
        "score_apres": score_apres,
        "niveau_avant": niveau_avant,
        "niveau_apres": niveau_apres,
        "raison": raison,
        "annulation": annulation,
        "montee_niveau": montee_niveau,
        "descente_niveau": descente_niveau,
        "leaderboard_url": config.LEADERBOARD_URL,
        "support_email": db.get_setting("support_email", config.SUPPORT_EMAIL),
        "is_bonus": etype["direction"] == "+",
    }

    if annulation:
        subject = f"CyberScore — Annulation : {etype['label']}"
    else:
        subject = etype.get("email_subject") or f"CyberScore — {etype['label']}"

    html = _render_template(etype.get("email_template", "event_notification.html"), context)
    _send_mail(user["email"], subject, html)


def send_quiz_invitation(user, quiz, token):
    """Envoie l'email d'invitation quiz avec le lien unique."""
    company     = db.get_setting("company_name", config.COMPANY_NAME)
    support     = db.get_setting("support_email", config.SUPPORT_EMAIL)
    quiz_url    = f"{config.LEADERBOARD_URL.rsplit('/leaderboard', 1)[0]}/quiz/{token}"
    points_quiz = 30  # Points QUIZ_REUSSI par défaut

    # Cherche la valeur réelle de l'event type QUIZ_REUSSI
    try:
        import database as _db
        et = _db.get_event_type_by_code("QUIZ_REUSSI")
        if et:
            points_quiz = et["points"]
    except Exception:
        pass

    context = {
        "company_name": company,
        "support_email": support,
        "user": user,
        "quiz": quiz,
        "quiz_url": quiz_url,
        "points_quiz": points_quiz,
    }
    subject = f"[CyberScore] Quiz : {quiz['title']}"
    html    = _render_template("quiz_invitation.html", context)
    _send_mail(user["email"], subject, html)


def send_test_email(to_email):
    """Envoie un email de test pour vérifier la config SMTP."""
    company = db.get_setting("company_name", config.COMPANY_NAME)
    subject = f"[{company}] Test CyberScore — configuration SMTP OK"
    html = f"""
    <html><body style="font-family:Arial,sans-serif;padding:20px;background:#f5f5f5;">
      <div style="max-width:600px;margin:auto;background:#fff;padding:30px;border-radius:8px;">
        <h2 style="color:#1E3A5F;">&#10003; Configuration SMTP opérationnelle</h2>
        <p>Cet email de test confirme que <strong>CyberScore</strong> peut envoyer des emails
        via <code>{config.SMTP_SERVER}:{config.SMTP_PORT}</code>.</p>
        <p style="color:#666;font-size:12px;">— {company} / DSI</p>
      </div>
    </body></html>
    """
    _send_mail(to_email, subject, html)
