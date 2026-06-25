"""Transactional email for RunCore — sends the certification result + badge.

Configuration is entirely env-driven so the platform runs fine with email
disabled (the default). When SMTP is not configured every call is a safe no-op
that just logs, so certification never fails because email is unavailable.

Env vars
--------
  RUNCORE_SMTP_HOST     SMTP server host (enables sending when set)
  RUNCORE_SMTP_PORT     default 587
  RUNCORE_SMTP_USER     login user
  RUNCORE_SMTP_PASS     login password / app password
  RUNCORE_SMTP_FROM     From address (defaults to RUNCORE_SMTP_USER)
  RUNCORE_SMTP_TLS      "1" (default) STARTTLS, "ssl" for implicit TLS, "0" none
  RUNCORE_PUBLIC_URL    public base URL for links/badges (e.g. https://runcore.onrender.com)
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("runcore.email")


def is_configured() -> bool:
    return bool(os.environ.get("RUNCORE_SMTP_HOST"))


def public_url() -> str:
    return os.environ.get("RUNCORE_PUBLIC_URL", "https://runcore.onrender.com").rstrip("/")


def _grade_color(grade: str) -> str:
    return {
        "A+": "#22c55e", "A": "#22c55e", "B+": "#3b82f6",
        "B": "#3b82f6", "C": "#f59e0b", "F": "#ef4444",
    }.get(grade, "#6b7280")


def _build_html(company: str, label: str, score: dict) -> str:
    grade = score.get("grade", "?")
    overall = score.get("overall", 0.0)
    certified = score.get("certified")
    color = _grade_color(grade)
    base = public_url()
    badge = f"{base}/badge/{grade.replace('+', 'plus')}.svg"
    status = ("✓ Certified" if certified else "Not certified — score below the bar")
    status_color = "#22c55e" if certified else "#f59e0b"
    dims = ""
    for d in score.get("dimensions", []):
        ok = "✓" if d.get("passed") else "—"
        dims += (f'<tr><td style="padding:6px 0;color:#475569">{d.get("name")}</td>'
                 f'<td style="padding:6px 0;text-align:right;font-weight:600">{d.get("score",0):.0f}/100 {ok}</td></tr>')
    return f"""<!DOCTYPE html><html><body style="margin:0;background:#0b1220;font-family:Arial,Helvetica,sans-serif">
<div style="max-width:560px;margin:0 auto;padding:32px 24px;color:#e6edf3">
  <div style="font-size:20px;font-weight:700;color:#fff;margin-bottom:24px">RunCore</div>
  <div style="background:#111a2e;border:1px solid #1f2a44;border-radius:14px;padding:32px;text-align:center">
    <div style="font-size:13px;color:#94a3b8;text-transform:uppercase;letter-spacing:.08em">{label}</div>
    <div style="display:inline-block;background:{color};color:#06101f;font-weight:800;font-size:28px;padding:8px 20px;border-radius:10px;margin:16px 0">{grade}</div>
    <div style="font-size:40px;font-weight:800;color:#fff">{overall:.1f}<span style="font-size:18px;color:#64748b">/100</span></div>
    <div style="color:{status_color};font-weight:600;margin-top:8px">{status}</div>
  </div>
  <table style="width:100%;margin-top:24px;font-size:14px;border-collapse:collapse">{dims}</table>
  <div style="margin-top:28px;text-align:center">
    <img src="{badge}" alt="RunCore badge" style="height:24px">
  </div>
  <div style="margin-top:24px;background:#0d1117;border-radius:8px;padding:14px 16px;font-family:monospace;font-size:12px;color:#94a3b8;word-break:break-all">
    [![RunCore Certified]({badge})]({base}/leaderboard)
  </div>
  <p style="color:#64748b;font-size:13px;margin-top:24px">The full report is attached to this email. View your dashboard at {base}/app/dashboard</p>
  <p style="color:#475569;font-size:12px;margin-top:24px">RunCore — independent efficiency certification for AI agents</p>
</div></body></html>"""


def send_certification_email(
    to_email: str,
    company: str,
    score: dict,
    report_html: str | None = None,
    label: str | None = None,
) -> bool:
    """Send the certification result. Returns True if actually sent.

    Never raises — failures are logged and reported via the return value so a
    certification run is never blocked by email problems.
    """
    if not to_email:
        return False
    if not is_configured():
        log.info("Email not configured (RUNCORE_SMTP_HOST unset) — skipping send to %s", to_email)
        return False

    host = os.environ["RUNCORE_SMTP_HOST"]
    port = int(os.environ.get("RUNCORE_SMTP_PORT", "587"))
    user = os.environ.get("RUNCORE_SMTP_USER", "")
    password = os.environ.get("RUNCORE_SMTP_PASS", "")
    sender = os.environ.get("RUNCORE_SMTP_FROM") or user or "no-reply@runcore.dev"
    tls_mode = os.environ.get("RUNCORE_SMTP_TLS", "1").lower()

    grade = score.get("grade", "?")
    overall = score.get("overall", 0.0)
    label = label or (score.get("product_name") or f"{score.get('provider','')} / {score.get('model','')}")

    msg = EmailMessage()
    msg["Subject"] = f"RunCore Certification — {label}: {grade} ({overall:.0f}/100)"
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(
        f"Your RunCore certification for {label} is complete.\n\n"
        f"Grade: {grade}\nScore: {overall:.1f}/100\n"
        f"Certified: {'yes' if score.get('certified') else 'no'}\n\n"
        f"View your dashboard: {public_url()}/app/dashboard\n"
    )
    msg.add_alternative(_build_html(company, label, score), subtype="html")

    if report_html:
        msg.add_attachment(
            report_html.encode("utf-8"),
            maintype="text", subtype="html",
            filename=f"runcore_certificate_{grade.replace('+','plus')}.html",
        )

    try:
        if tls_mode == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if tls_mode in ("1", "true", "starttls"):
                server.starttls()
            if user:
                server.login(user, password)
            server.send_message(msg)
        log.info("Sent certification email to %s (%s %s)", to_email, label, grade)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to send certification email to %s: %s", to_email, exc)
        return False
