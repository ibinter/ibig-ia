"""Connecteurs mail (section 9) : API Gmail d'un côté, IMAP/SMTP LWS de l'autre.

Option A du cahier des charges (raccordement direct) : un connecteur par boîte.
Les messages sont lus sans être marqués comme lus (BODY.PEEK) pour ne rien changer
aux habitudes de l'équipe.
"""

from __future__ import annotations

import base64
import email
import email.header
import html
import imaplib
import json
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage, Message
from email.utils import getaddresses, make_msgid, parsedate_to_datetime
from typing import Protocol

from ..config import Mailbox


@dataclass
class MailMessage:
    mailbox: str
    message_id: str
    ref: str  # UID IMAP ou identifiant Gmail, pour étiqueter
    sender: str
    sender_name: str
    subject: str
    body: str
    received_at: datetime | None = None
    thread_id: str = ""
    references: str = ""


class MailConnector(Protocol):
    mailbox: Mailbox

    def fetch_recent(self, days: int = 3) -> list[MailMessage]: ...

    def send(self, to: str, subject: str, body: str, in_reply_to: str = "",
             references: str = "", thread_id: str = "") -> dict: ...

    def label(self, ref: str, labels: list[str]) -> None: ...


# ---------------------------------------------------------------- utilitaires
def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    return str(email.header.make_header(email.header.decode_header(value)))


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def extract_body(msg: Message, limit: int = 20000) -> str:
    plain, rich = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            plain.append(text)
        elif part.get_content_type() == "text/html":
            rich.append(_html_to_text(text))
    body = "\n".join(plain) if plain else "\n".join(rich)
    return re.sub(r"\n{3,}", "\n\n", body).strip()[:limit]


def parse_message(raw: bytes, mailbox: str, ref: str) -> MailMessage:
    msg = email.message_from_bytes(raw)
    (name, addr), = getaddresses([msg.get("From", "")]) or [("", "")]
    try:
        received = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
    except (TypeError, ValueError):
        received = None
    return MailMessage(
        mailbox=mailbox,
        message_id=(msg.get("Message-ID") or f"<{ref}@{mailbox}>").strip(),
        ref=ref,
        sender=addr.lower(),
        sender_name=_decode_header(name),
        subject=_decode_header(msg.get("Subject")),
        body=extract_body(msg),
        received_at=received,
        references=(msg.get("References") or "").strip(),
    )


def build_reply(mailbox: Mailbox, to: str, subject: str, body: str, in_reply_to: str = "",
                references: str = "") -> EmailMessage:
    out = EmailMessage()
    out["From"] = mailbox.adresse
    out["To"] = to
    is_reply = in_reply_to and not subject.lower().startswith(("re:", "tr:", "fwd:"))
    out["Subject"] = f"Re: {subject}" if is_reply else subject
    out["Message-ID"] = make_msgid(domain=mailbox.adresse.split("@")[-1])
    if in_reply_to:
        out["In-Reply-To"] = in_reply_to
        out["References"] = f"{references} {in_reply_to}".strip()
    out.set_content(body)
    return out


def _keyword(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", label)[:40]


# ---------------------------------------------------------------- LWS (IMAP/SMTP)
class ImapSmtpConnector:
    def __init__(self, mailbox: Mailbox) -> None:
        self.mailbox = mailbox

    def _imap(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self.mailbox.imap_host, self.mailbox.imap_port)
        conn.login(self.mailbox.adresse, self.mailbox.secret())
        conn.select("INBOX")
        return conn

    def fetch_recent(self, days: int = 3) -> list[MailMessage]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
        conn = self._imap()
        try:
            status, data = conn.uid("SEARCH", None, f"(SINCE {since})")
            if status != "OK":
                raise RuntimeError(f"IMAP SEARCH a échoué sur {self.mailbox.adresse}")
            messages = []
            for uid in data[0].split():
                status, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                if status == "OK" and parts and isinstance(parts[0], tuple):
                    messages.append(parse_message(parts[0][1], self.mailbox.adresse, uid.decode()))
            return messages
        finally:
            conn.logout()

    def send(self, to: str, subject: str, body: str, in_reply_to: str = "",
             references: str = "", thread_id: str = "") -> dict:
        msg = build_reply(self.mailbox, to, subject, body, in_reply_to, references)
        with smtplib.SMTP_SSL(self.mailbox.smtp_host, self.mailbox.smtp_port) as smtp:
            smtp.login(self.mailbox.adresse, self.mailbox.secret())
            smtp.send_message(msg)
        return {"message_id": msg["Message-ID"]}

    def label(self, ref: str, labels: list[str]) -> None:
        conn = self._imap()
        try:
            flags = " ".join(_keyword(lb) for lb in labels)
            conn.uid("STORE", ref, "+FLAGS", f"({flags})")
        finally:
            conn.logout()


# ---------------------------------------------------------------- Gmail (API)
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailConnector:
    """Nécessite l'extra `gmail` (pip install ibig-agent[gmail]).

    Le secret est le JSON d'un jeton OAuth « authorized user » du compte dédié à l'agent.
    """

    def __init__(self, mailbox: Mailbox) -> None:
        self.mailbox = mailbox
        self._service = None
        self._labels: dict[str, str] = {}

    def _svc(self):
        if self._service is None:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_info(
                json.loads(self.mailbox.secret()), GMAIL_SCOPES
            )
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def fetch_recent(self, days: int = 3) -> list[MailMessage]:
        users = self._svc().users()
        ids, token = [], None
        while True:
            resp = users.messages().list(
                userId="me", q=f"in:inbox newer_than:{days}d", pageToken=token
            ).execute()
            ids += [m["id"] for m in resp.get("messages", [])]
            token = resp.get("nextPageToken")
            if not token:
                break
        messages = []
        for mid in ids:
            raw = users.messages().get(userId="me", id=mid, format="raw").execute()
            msg = parse_message(base64.urlsafe_b64decode(raw["raw"]), self.mailbox.adresse, mid)
            msg.thread_id = raw.get("threadId", "")
            messages.append(msg)
        return messages

    def send(self, to: str, subject: str, body: str, in_reply_to: str = "",
             references: str = "", thread_id: str = "") -> dict:
        msg = build_reply(self.mailbox, to, subject, body, in_reply_to, references)
        payload: dict = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        if thread_id:
            payload["threadId"] = thread_id
        sent = self._svc().users().messages().send(userId="me", body=payload).execute()
        return {"message_id": msg["Message-ID"], "gmail_id": sent.get("id")}

    def _label_id(self, name: str) -> str:
        if not self._labels:
            resp = self._svc().users().labels().list(userId="me").execute()
            self._labels = {lb["name"]: lb["id"] for lb in resp.get("labels", [])}
        if name not in self._labels:
            created = self._svc().users().labels().create(
                userId="me", body={"name": name}
            ).execute()
            self._labels[name] = created["id"]
        return self._labels[name]

    def label(self, ref: str, labels: list[str]) -> None:
        ids = [self._label_id(lb) for lb in labels]
        self._svc().users().messages().modify(
            userId="me", id=ref, body={"addLabelIds": ids}
        ).execute()


def connector_for(mailbox: Mailbox) -> MailConnector:
    if mailbox.hebergeur == "gmail":
        return GmailConnector(mailbox)
    if mailbox.hebergeur == "lws":
        return ImapSmtpConnector(mailbox)
    raise ValueError(f"Hébergeur inconnu pour {mailbox.adresse} : {mailbox.hebergeur}")
