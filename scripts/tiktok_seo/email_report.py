#!/usr/bin/env python3
import os
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT_MD = ROOT / "data" / "tiktok" / "latest_report.md"
REPORT_CSV = ROOT / "data" / "tiktok" / "latest_keywords.csv"

GMAIL_USERNAME = os.environ["GMAIL_USERNAME"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT = "bullytheboard@gmail.com"


def main():
    if not REPORT_MD.exists():
        raise FileNotFoundError(f"Missing report: {REPORT_MD}")
    if not REPORT_CSV.exists():
        raise FileNotFoundError(f"Missing report: {REPORT_CSV}")

    today = date.today().strftime("%B %d, %Y")
    report_text = REPORT_MD.read_text(encoding="utf-8")

    msg = EmailMessage()
    msg["Subject"] = f"BTB TikTok Search Report — {today}"
    msg["From"] = GMAIL_USERNAME
    msg["To"] = RECIPIENT

    msg.set_content(
        f"""BTB Analytics Weekly TikTok Search Report
{today}

The weekly TikTok keyword/search report is below and attached.

{report_text}

Attachments:
- BTB_TikTok_Search_Report.md
- BTB_TikTok_Keywords.csv
"""
    )

    with REPORT_MD.open("rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="text",
            subtype="markdown",
            filename="BTB_TikTok_Search_Report.md",
        )

    with REPORT_CSV.open("rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="text",
            subtype="csv",
            filename="BTB_TikTok_Keywords.csv",
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USERNAME, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)

    print(f"Report emailed successfully to {RECIPIENT}")


if __name__ == "__main__":
    main()
