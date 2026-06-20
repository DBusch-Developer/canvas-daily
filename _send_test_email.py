"""One-off: send the email mockup to a couple inboxes to check rendering.
Reads SMTP creds from the environment (GitHub Actions secrets). Temporary.
"""
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

try:  # local convenience; in CI the env comes from workflow secrets
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

RECIPIENTS = ["buschdiana007@gmail.com", "dbusch2@yc.edu"]
html = Path("docs/email-mockup.html").read_text(encoding="utf-8")

host = os.environ["SMTP_HOST"]
port = int(os.environ.get("SMTP_PORT", "587"))
user = os.environ["SMTP_USERNAME"]
password = os.environ["SMTP_PASSWORD"]
sender = os.environ.get("SMTP_FROM", "daily@canvas-daily.org")

smtp = smtplib.SMTP(host, port)
smtp.starttls()
smtp.login(user, password)
try:
    for to in RECIPIENTS:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = "Canvas Daily — your daily report (design test)"
        msg.set_content("Your Canvas Daily report. Open in an HTML email client to see the full version.")
        msg.add_alternative(html, subtype="html")
        smtp.send_message(msg)
        print("sent to", to)
finally:
    smtp.quit()
print("done")
