#!/usr/bin/env python3
"""
Ia din ENTSO-E Transparency Platform date despre productia regenerabila
(eolian/solar) actuala vs prognozata si indisponibilitati noi de productie
(UMM) pentru Romania, si scrie un rezumat compact in entsoe-live.md.

Ruleaza in GitHub Actions; ENTSOE_TOKEN vine din secrets.
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET

import requests

TOKEN = os.environ.get("ENTSOE_TOKEN", "").strip()
DOMAIN = "10YRO-TEL------P"
BASE = "https://web-api.tp.entsoe.eu/api"

PSR_LABELS = {
    "B16": "Solar",
    "B19": "Eolian",
}

OUT_FILE = "entsoe-live.md"


def local(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def fmt_period(dt):
    return dt.strftime("%Y%m%d%H%M")


def entsoe_get(params, label):
    """Face request-ul si returneaza (text, eroare)."""
    if not TOKEN:
        return None, "token invalid sau expirat (ENTSOE_TOKEN lipseste din environment)"

    q = dict(params)
    q["securityToken"] = TOKEN
    try:
        r = requests.get(BASE, params=q, timeout=30)
    except requests.RequestException as e:
        return None, f"eroare retea la {label}: {e}"

    text = r.text or ""

    if r.status_code in (401, 403):
        return None, "token invalid sau expirat"

    if r.status_code != 200:
        return None, f"eroare HTTP {r.status_code} la {label}"

    if "Acknowledgement_MarketDocument" in text:
        m = re.search(r"<text>(.*?)</text>", text, re.S)
        msg = m.group(1).strip() if m else "eroare necunoscuta de la API"
        low = msg.lower()
        if "security token" in low or "unauthorized" in low or "invalid" in low and "token" in low:
            return None, "token invalid sau expirat"
        return None, f"API eroare la {label}: {msg}"

    return text, None


def split_documents(xml_text):
    """Raspunsurile bulk (ex. UMM) pot contine mai multe documente XML
    concatenate fara un root comun. Le separam dupa declaratia <?xml."""
    parts = re.split(r"(?=<\?xml)", xml_text)
    docs = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            docs.append(ET.fromstring(p))
        except ET.ParseError:
            continue
    return docs


def parse_latest_values(xml_text):
    """Pentru A75 (actual) si A69/A18 (prognoza): ultimul punct per psrType."""
    out = {}
    for root in split_documents(xml_text):
        for ts in root:
            if local(ts.tag) != "TimeSeries":
                continue
            psr = None
            for el in ts.iter():
                if local(el.tag) == "psrType":
                    psr = el.text
                    break
            if psr not in PSR_LABELS:
                continue
            for period in ts:
                if local(period.tag) != "Period":
                    continue
                points = []
                for point in period:
                    if local(point.tag) != "Point":
                        continue
                    pos = qty = None
                    for c in point:
                        if local(c.tag) == "position":
                            pos = int(c.text)
                        elif local(c.tag) == "quantity":
                            qty = float(c.text)
                    if pos is not None and qty is not None:
                        points.append((pos, qty))
                if points:
                    points.sort()
                    out[psr] = points[-1][1]
    return out


def parse_umm(xml_text, since_minutes=90, window_hours=48):
    """Extrage indisponibilitati noi/curente din documente A80/A77."""
    now = datetime.now(timezone.utc)
    cutoff_created = now - timedelta(minutes=since_minutes)
    window_end = now + timedelta(hours=window_hours)

    items = []
    for root in split_documents(xml_text):
        created = None
        for el in root:
            if local(el.tag) == "createdDateTime":
                try:
                    created = datetime.strptime(el.text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    created = None

        resource_name = None
        avail_qty = None
        start = end = None

        for el in root.iter():
            tag = local(el.tag)
            if tag == "name" and resource_name is None:
                resource_name = el.text
            elif tag == "start" and start is None:
                try:
                    start = datetime.strptime(el.text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            elif tag == "end" and end is None:
                try:
                    end = datetime.strptime(el.text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            elif tag == "quantity" and avail_qty is None:
                try:
                    avail_qty = float(el.text)
                except (TypeError, ValueError):
                    pass

        overlaps_now = start is not None and end is not None and start <= now <= end
        is_recent = created is not None and created >= cutoff_created
        in_window = start is not None and start <= window_end

        if (is_recent or overlaps_now) and in_window:
            items.append({
                "resource": resource_name or "necunoscut",
                "qty": avail_qty,
                "start": start,
                "end": end,
                "created": created,
            })
    return items


def deviation_note(actual, forecast):
    if actual is None or forecast is None or forecast == 0:
        return None, None
    dev_pct = round((actual - forecast) / forecast * 100, 1)
    if dev_pct <= -12:
        signal = "presiune de creștere preț (actual sub prognoză)"
    elif dev_pct >= 12:
        signal = "presiune de scădere preț (actual peste prognoză)"
    else:
        signal = "neutru"
    return dev_pct, signal


def main():
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = now.replace(hour=23, minute=59, second=0, microsecond=0)
    umm_end = now + timedelta(hours=48)

    status_notes = []

    # Pasul 2: productie actuala (A75/A16)
    actual_xml, err = entsoe_get(
        {
            "documentType": "A75",
            "processType": "A16",
            "in_Domain": DOMAIN,
            "periodStart": fmt_period(day_start),
            "periodEnd": fmt_period(day_end),
        },
        "productie actuala (A75)",
    )
    actual_vals = parse_latest_values(actual_xml) if actual_xml else {}
    if err:
        status_notes.append(f"pas 2 (actual): {err}")

    # Pasul 3: prognoza (A69/A01), fallback A18
    forecast_xml, ferr = entsoe_get(
        {
            "documentType": "A69",
            "processType": "A01",
            "in_Domain": DOMAIN,
            "periodStart": fmt_period(day_start),
            "periodEnd": fmt_period(day_end),
        },
        "prognoza (A69/A01)",
    )
    if ferr and "token invalid" not in ferr:
        forecast_xml, ferr2 = entsoe_get(
            {
                "documentType": "A69",
                "processType": "A18",
                "in_Domain": DOMAIN,
                "periodStart": fmt_period(day_start),
                "periodEnd": fmt_period(day_end),
            },
            "prognoza (A69/A18)",
        )
        if ferr2:
            status_notes.append(f"pas 3 (prognoza): {ferr}; fallback A18: {ferr2}")
        else:
            ferr = None
    elif ferr:
        status_notes.append(f"pas 3 (prognoza): {ferr}")

    forecast_vals = parse_latest_values(forecast_xml) if forecast_xml else {}

    # Pasul 5: UMM (A80/A16), fallback A77
    umm_xml, uerr = entsoe_get(
        {
            "documentType": "A80",
            "processType": "A16",
            "in_Domain": DOMAIN,
            "periodStart": fmt_period(day_start),
            "periodEnd": fmt_period(umm_end),
        },
        "UMM (A80)",
    )
    if uerr and "token invalid" not in uerr:
        umm_xml, uerr2 = entsoe_get(
            {
                "documentType": "A77",
                "in_Domain": DOMAIN,
                "periodStart": fmt_period(day_start),
                "periodEnd": fmt_period(umm_end),
            },
            "UMM (A77)",
        )
        if uerr2:
            status_notes.append(f"pas 5 (UMM): {uerr}; fallback A77: {uerr2}")
        else:
            uerr = None
    elif uerr:
        status_notes.append(f"pas 5 (UMM): {uerr}")

    umm_items = parse_umm(umm_xml) if umm_xml else []

    # token invalid oriunde -> semnaleaza explicit
    token_invalid = any("token invalid" in n for n in status_notes)

    ts_utc = now.strftime("%Y-%m-%d %H:%M UTC")
    ts_local = (now + timedelta(hours=3)).strftime("%H:%M ora României")

    lines = [f"# ENTSO-E date live — actualizat: {ts_utc} ({ts_local})", ""]

    for code, label in (("B19", "Eolian"), ("B16", "Solar")):
        actual = actual_vals.get(code)
        forecast = forecast_vals.get(code)
        if actual is None and forecast is None:
            lines.append(f"{label}: date indisponibile")
            continue
        dev_pct, signal = deviation_note(actual, forecast)
        a_txt = f"{actual:.0f} MW" if actual is not None else "N/A"
        f_txt = f"{forecast:.0f} MW" if forecast is not None else "N/A"
        if dev_pct is not None:
            lines.append(f"{label}: actual {a_txt} vs prognoză {f_txt} (deviație {dev_pct:+.1f}%, {signal})")
        else:
            lines.append(f"{label}: actual {a_txt} vs prognoză {f_txt}")

    lines.append("")

    if umm_items:
        lines.append("UMM nou (ultimele ~90 min sau active acum):")
        for it in umm_items:
            qty_txt = f"{it['qty']:.0f} MW indisponibili" if it["qty"] is not None else "MW necunoscut"
            per_txt = ""
            if it["start"] and it["end"]:
                per_txt = f" ({it['start'].strftime('%d.%m %H:%M')}–{it['end'].strftime('%d.%m %H:%M')} UTC)"
            lines.append(f"- {it['resource']}: {qty_txt}{per_txt}")
    else:
        lines.append("UMM nou (ultimele ~90 min): niciunul nou")

    lines.append("")

    if token_invalid:
        status = "token invalid sau expirat"
    elif status_notes:
        status = "eroare parțială — " + "; ".join(status_notes)
    else:
        status = "OK"
    lines.append(f"Status: {status}")
    lines.append("")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\n".join(lines))


if __name__ == "__main__":
    main()
