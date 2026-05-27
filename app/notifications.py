import os
import smtplib
from email.message import EmailMessage
from datetime import datetime
from .db import get_connection


def _smtp_configured():
    return all(os.environ.get(k) for k in ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"])


def send_email(to_email: str, subject: str, body: str):
    """Send an email if SMTP settings exist. Otherwise use safe demo mode.

    Demo mode keeps the notification in SQLite and prints to terminal instead of sending real mail.
    This lets the project work during laboratory checking without external accounts.
    """
    if not _smtp_configured():
        print("\n--- DEMO EMAIL NOTIFICATION ---")
        print(f"To: {to_email}")
        print(f"Subject: {subject}")
        print(body)
        print("--- END EMAIL ---\n")
        return True, "demo"

    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        server.send_message(msg)
    return True, None


def notify_user(user_id: int, subject: str, message: str, booking_id=None):
    conn = get_connection()
    user = conn.execute("SELECT id, email FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return

    conn.execute("""
        INSERT INTO notifications(user_id, booking_id, subject, message, channel, email_to)
        VALUES (?, ?, ?, ?, 'email', ?)
    """, (user_id, booking_id, subject, message, user["email"]))
    notification_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    try:
        ok, mode = send_email(user["email"], subject, message)
        conn.execute("""
            UPDATE notifications
            SET is_sent=?, sent_at=?, error=?
            WHERE id=?
        """, (1 if ok else 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mode, notification_id))
    except Exception as exc:
        conn.execute("UPDATE notifications SET is_sent=0, error=? WHERE id=?", (str(exc), notification_id))
    conn.commit()
    conn.close()
