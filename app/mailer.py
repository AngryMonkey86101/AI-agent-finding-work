"""Отправка сопроводительного письма с резюме на email работодателя."""
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path


class MailError(RuntimeError):
    pass


def build_message(vacancy: dict, settings: dict, resume: dict | None) -> EmailMessage:
    sender = settings.get("smtp_user") or ""
    if not sender:
        raise MailError("Не указан адрес почты в настройках.")
    to = vacancy.get("contact_email") or ""
    if not to:
        raise MailError("В вакансии нет email.")

    msg = EmailMessage()
    msg["From"] = formataddr((settings.get("email_from_name") or "", sender))
    msg["To"] = to
    template = settings.get("email_subject") or "Отклик на вакансию «{vacancy}»"
    msg["Subject"] = template.replace("{vacancy}", vacancy.get("title", ""))
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])

    body = (vacancy.get("letter") or "").strip()
    link = (settings.get("hh_resume_url") or "").strip()
    if link and link not in body:
        body += f"\n\nРезюме на hh.ru: {link}"
    body += f"\n\nВакансия: {vacancy.get('url', '')}"
    msg.set_content(body)

    if resume and resume.get("stored_path") and Path(resume["stored_path"]).exists():
        data = Path(resume["stored_path"]).read_bytes()
        ctype, _ = mimetypes.guess_type(resume["filename"])
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=resume["filename"])
    return msg


def send(msg: EmailMessage, settings: dict, smtp_factory=None) -> None:
    host = settings.get("smtp_host") or ""
    port = int(settings.get("smtp_port") or 465)
    user = settings.get("smtp_user") or ""
    password = settings.get("smtp_password") or ""
    if not (host and user and password):
        raise MailError("Заполните SMTP-сервер, адрес и пароль приложения в панели.")
    context = ssl.create_default_context()
    try:
        if smtp_factory:
            server = smtp_factory(host, port)
        elif port == 465:
            server = smtplib.SMTP_SSL(host, port, context=context, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            if port != 465 and not smtp_factory:
                server.starttls(context=context)
            server.login(user, password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Почта не отправлена: {exc}") from exc
