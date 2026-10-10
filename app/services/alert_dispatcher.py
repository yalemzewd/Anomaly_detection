"""Multi-channel alert dispatcher: Teams, PagerDuty, email, 511.

- Dispatch only HIGH/CRITICAL Waze-corroborated incidents to 511.
- Alert delivery log persisted to anomaly_alerts table by the caller.
- 30-second dispatch cadence driven by Celery beat (run_alert_dispatch).
"""

import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

import aiohttp

from app.core.config import settings


async def send_teams(webhook_url: str, text: str, title: str = "Traffic Anomaly") -> bool:
    if not webhook_url:
        return False
    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": title,
        "themeColor": "FF0000",
        "title": title,
        "text": text,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                return r.status in (200, 201, 202)
    except Exception:
        return False


async def send_pagerduty(routing_key: str, summary: str, severity: str = "critical") -> bool:
    if not routing_key:
        return False
    payload = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {"summary": summary, "severity": severity, "source": "fhwa-agent-003"},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return r.status in (200, 201, 202)
    except Exception:
        return False


def send_email(to_addr: str, subject: str, body: str) -> bool:
    try:
        msg = EmailMessage()
        msg["From"] = "fhwa-agent-003@fhwa.dot.gov"
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(settings.SMTP_HOST, 25, timeout=10) as smtp:
            smtp.send_message(msg)
        return True
    except Exception:
        return False


async def send_fiveoneone(message: Dict[str, Any]) -> bool:
    url = getattr(settings, "FIVEONEONE_API_URL", "")
    key = getattr(settings, "FIVEONEONE_API_KEY", "")
    if not url:
        return False
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=message, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                return r.status in (200, 201, 202)
    except Exception:
        return False


def should_push_511(anomaly: Dict[str, Any]) -> bool:
    """511 push rule: HIGH/CRITICAL severity AND Waze-corroborated."""
    return bool(
        anomaly.get("severity") in ("high", "critical")
        and anomaly.get("waze_corroborated")
    )


def format_anomaly_message(anomaly: Dict[str, Any]) -> str:
    return (
        f"[{anomaly.get('severity', 'low').upper()}] {anomaly.get('incident_type', 'unknown')} "
        f"detected on sensor {anomaly.get('sensor_id')} "
        f"(TMC {anomaly.get('tmc_code', 'n/a')}) "
        f"score={anomaly.get('anomaly_score', 0):.2f} "
        f"waze={'yes' if anomaly.get('waze_corroborated') else 'no'}"
    )


async def dispatch_anomaly(anomaly: Dict[str, Any]) -> Dict[str, bool]:
    """Dispatch one anomaly across configured channels."""
    text = format_anomaly_message(anomaly)
    results: Dict[str, bool] = {}
    results["teams"] = await send_teams(settings.TEAMS_WEBHOOK_URL, text)
    results["pagerduty"] = await send_pagerduty(
        settings.PAGERDUTY_ROUTING_KEY, text,
        severity="critical" if anomaly.get("severity") == "critical" else "warning",
    )
    results["email"] = send_email(settings.ALERT_TO_EMAIL, "FHWA Traffic Anomaly", text)
    if should_push_511(anomaly):
        results["fiveoneone"] = await send_fiveoneone({
            "event_type": anomaly.get("incident_type"),
            "severity": anomaly.get("severity"),
            "tmc": anomaly.get("tmc_code"),
            "message": text,
        })
    else:
        results["fiveoneone"] = False
    return results
