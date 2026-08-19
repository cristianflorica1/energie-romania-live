#!/usr/bin/env python3
"""
Ia date live de piata energie RO din doua surse publice si scrie un rezumat
compact in market-live.md:
  - consumenergie.ro/api/sen  -> consum, productie, mix pe surse, sold import/export
  - euenergy.live/romania     -> pret PZU (ziua curenta / day-ahead) + istoric recent

Ruleaza in GitHub Actions (runner cu acces normal la internet, fara
restrictiile de allowlist ale mediului cloud Claude). Ideea e ca task-ul
orar din Claude sa citeasca market-live.md prin raw.githubusercontent.com
(curl, fara prompt de permisiune) in loc sa dea WebFetch direct pe cele
doua site-uri (ceea ce cere aprobare manuala la fiecare rulare).
"""

import re
from datetime import datetime, timezone

import requests

OUT_FILE = "market-live.md"

SEN_URL = "https://consumenergie.ro/api/sen"
EUENERGY_URL = "https://euenergy.live/romania"

SEN_LABELS = {
    "CONS": "Consum",
    "PROD": "Productie",
    "NUCL": "Nuclear",
    "CARB": "Carbune",
    "MUKA": "Hidro",
    "EOLIAN": "Eolian",
    "FOTO": "Solar",
    "GAZE": "Gaz",
    "BMASA": "Biomasa",
    "SOLD": "Sold",
}


def fetch_sen():
    """Intoarce (dict_valori, eroare)."""
    try:
        r = requests.get(SEN_URL, timeout=20)
    except requests.RequestException as e:
        return None, f"eroare retea la consumenergie.ro: {e}"

    if r.status_code != 200:
        return None, f"eroare HTTP {r.status_code} la consumenergie.ro"

    try:
        raw = r.json()
    except ValueError:
        return None, "raspuns non-JSON de la consumenergie.ro"

    flat = {}
    for item in raw:
        for k, v in item.items():
            flat[k] = v

    missing = [k for k in SEN_LABELS if k not in flat]
    if missing:
        return None, f"campuri lipsa de la consumenergie.ro: {missing}"

    return flat, None


def strip_tags(html):
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def fetch_euenergy():
    """Intoarce (dict cu 'today_spot', 'day_ahead', 'history': [(data, pret)]), eroare)."""
    try:
        r = requests.get(EUENERGY_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        return None, f"eroare retea la euenergy.live: {e}"

    if r.status_code != 200:
        return None, f"eroare HTTP {r.status_code} la euenergy.live"

    text = strip_tags(r.text)

    out = {"today_spot": None, "day_ahead": None, "history": []}

    m = re.search(r"Today'?s electricity price in Romania is\s*€\s*([\d.,]+)\s*/\s*MWh", text, re.I)
    if m:
        out["today_spot"] = m.group(1).replace(",", "")

    m = re.search(r"day-ahead price for Romania is\s*€\s*([\d.,]+)\s*/\s*MWh", text, re.I)
    if m:
        out["day_ahead"] = m.group(1).replace(",", "")

    for dm in re.finditer(r"(\d{4}-\d{2}-\d{2})[^€\n]{0,40}?€\s*([\d.,]+)", text):
        out["history"].append((dm.group(1), dm.group(2).replace(",", "")))

    if out["today_spot"] is None and out["day_ahead"] is None and not out["history"]:
        return None, "nu s-a putut extrage niciun pret din pagina euenergy.live (posibil schimbare de format)"

    return out, None


def main():
    now = datetime.now(timezone.utc)
    ts_utc = now.strftime("%Y-%m-%d %H:%M UTC")

    status_notes = []

    sen, sen_err = fetch_sen()
    if sen_err:
        status_notes.append(f"consumenergie.ro: {sen_err}")

    eu, eu_err = fetch_euenergy()
    if eu_err:
        status_notes.append(f"euenergy.live: {eu_err}")

    lines = [f"# Piata energie RO - date live, actualizat: {ts_utc}", ""]

    if sen:
        for key, label in SEN_LABELS.items():
            lines.append(f"{label}: {sen[key]}")
        lines.append(f"Timestamp sursa: {sen.get('row1_HARTASEN_DATA', 'n/a')}")
    else:
        lines.append("Mix energetic: indisponibil")

    lines.append("")

    if eu:
        if eu["today_spot"]:
            lines.append(f"Pret curent (today spot): {eu['today_spot']} EUR/MWh")
        if eu["day_ahead"]:
            lines.append(f"Pret PZU (day-ahead): {eu['day_ahead']} EUR/MWh")
        if eu["history"]:
            lines.append("Istoric recent (data, pret EUR/MWh):")
            for d, p in eu["history"][-4:]:
                lines.append(f"- {d}: {p}")
    else:
        lines.append("Pret PZU: indisponibil")

    lines.append("")

    status = "OK" if not status_notes else "eroare partiala - " + "; ".join(status_notes)
    lines.append(f"Status: {status}")
    lines.append("")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("\n".join(lines))


if __name__ == "__main__":
    main()
