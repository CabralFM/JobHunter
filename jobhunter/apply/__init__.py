"""Envio de candidaturas.

Modos (settings.yaml -> apply.mode):
- "queue"  (padrão): gera tudo, NÃO envia nada. Você revisa no relatório diário e usa
            `python -m jobhunter approve` para abrir o link, enviar e marcar como enviada.
- "email":  envia automaticamente por e-mail (SMTP) SOMENTE quando o anúncio publica um e-mail
            de candidatura E o auditor anti-invenção aprovou o CV. Os demais vão para a fila.

Por que não há envio automático em portais/LinkedIn por padrão: ver README, seção
"Por que a candidatura não é 100% automática".
"""

from __future__ import annotations

import logging
import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)


def send_email(*, cfg: dict, sender_name: str, to: str, subject: str, body: str,
               attachments: list[Path], dry_run: bool) -> bool:
    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{cfg['from']}>"
    msg["To"] = to
    msg["Subject"] = subject
    if cfg.get("bcc_self", True):
        msg["Bcc"] = cfg["from"]
    msg.set_content(body)
    for p in attachments:
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)

    if dry_run:
        log.info("[dry-run] e-mail para %s: %s (%d anexos)", to, subject, len(attachments))
        return False
    with smtplib.SMTP(cfg["host"], cfg.get("port", 587)) as s:
        s.starttls()
        s.login(cfg["from"], os.environ[cfg.get("password_env", "SMTP_PASSWORD")])
        s.send_message(msg)
    log.info("e-mail enviado para %s", to)
    return True
