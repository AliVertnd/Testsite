#!/usr/bin/env python3
"""Пересчёт себестоимости и цен Impro из research/*.md → catalog/."""
from __future__ import annotations

import csv
import json
import re
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "research"
OUT = ROOT / "catalog"

SALARY_COST = {
    "SEO": 1700, "SEO-JR": 1000, "SEO-SR": 2600,
    "PPC": 2000, "PPC-SR": 3000, "SMM": 1800,
    "CONTENT": 1500, "DESIGN": 2000, "UI": 2100,
    "UX": 2600, "MOTION": 2300, "AD": 3500,
    "FE": 3500, "BE": 3800, "FS": 3800, "BITRIX": 2600, "MOBILE": 4000,
    "QA": 2700, "DEVOPS": 4600, "ANALYTICS": 3000, "BI": 3500,
    "PM": 2300, "STRATEGIST": 2900, "BA": 3500, "BRAND": 3400,
    "SERM": 1700, "MP": 1700, "PRODUCER": 2500,
}
CLIENT_RATE = {
    "SEO": 2500, "SEO-JR": 1600, "SEO-SR": 3500,
    "PPC": 2800, "PPC-SR": 4000, "SMM": 2600,
    "CONTENT": 2000, "DESIGN": 2600, "UI": 2800,
    "UX": 3500, "MOTION": 3200, "AD": 5000,
    "FE": 3000, "BE": 3200, "FS": 3200, "BITRIX": 2700, "MOBILE": 3000,
    "QA": 2200, "DEVOPS": 3500, "ANALYTICS": 3200, "BI": 3500,
    "PM": 2800, "STRATEGIST": 4200, "BA": 4500, "BRAND": 5000,
    "SERM": 2400, "MP": 2200, "PRODUCER": 3500,
}
DELIVERY = {
    "SEO", "PPC", "SMM", "CONTENT", "DESIGN", "FE", "BE", "BITRIX",
    "QA", "SERM", "MP", "ANALYTICS", "FS", "MOBILE",
}


def impro_cost_rate(role: str) -> int:
    sal = SALARY_COST[role]
    cli = CLIENT_RATE.get(role, int(sal * 1.5))
    if role in DELIVERY:
        blended = 0.35 * (sal * 0.55) + 0.65 * sal
    else:
        blended = sal
    return int(round(min(blended, cli * 0.70)))


IMPRO_RATES = {k: impro_cost_rate(k) for k in SALARY_COST}

ROLE_PATTERNS = [
    (r"арт[\-\s]?директор|art director", "AD", 0.15),
    (r"бренд[\-\s]?стратег", "BRAND", 0.5),
    (r"маркетинг[\-\s]?стратег|стратег", "STRATEGIST", 0.55),
    (r"бизнес[\-\s]?консульт|консульт", "BA", 0.55),
    (r"ux[\-\s]?research|ux[\-\s]?исследоват|исследователь", "UX", 0.5),
    (r"devops|девопс", "DEVOPS", 0.7),
    (r"frontend|фронтенд|вёрст|верст", "FE", 0.7),
    (r"backend|бэкенд|бэкэнд", "BE", 0.7),
    (r"fullstack|фуллстек", "FS", 0.7),
    (r"bitrix|битрикс[\-\s]?разраб", "BITRIX", 0.7),
    (r"mobile|мобильн", "MOBILE", 0.7),
    (r"\bqa\b|тестиров", "QA", 0.5),
    (r"web[\-\s]?аналит|веб[\-\s]?аналит|аналитик(?! бизнес)", "ANALYTICS", 0.6),
    (r"\bbi\b|data[\-\s]?anal|дашборд", "BI", 0.5),
    (r"контекст|ppc|директ|трафик[\-\s]?менедж", "PPC", 0.7),
    (r"\bseo\b|сео", "SEO", 0.7),
    (r"\bsmm\b|смм", "SMM", 0.7),
    (r"serm|серм|репутац", "SERM", 0.7),
    (r"маркетплейс|wildberries|\bozon\b", "MP", 0.7),
    (r"motion|моушн|продюсер", "PRODUCER", 0.4),
    (r"копирайт|контент|редактор|copy", "CONTENT", 0.7),
    (r"дизайн|designer|ui[\-\s]?диз", "DESIGN", 0.7),
    (r"pm\b|project|проджект|аккаунт|account|менеджер проект", "PM", 0.15),
]


def map_roles(text: str, sid: str):
    text = (text or "").lower()
    found = []
    for pat, key, share in ROLE_PATTERNS:
        if re.search(pat, text, re.I):
            found.append((key, share))
    seen, roles = set(), []
    for k, s in found:
        if k not in seen:
            seen.add(k)
            roles.append((k, s))
    if not roles or (len(roles) == 1 and roles[0][0] == "PM"):
        if sid.startswith("M-"):
            roles = [("SEO", 0.7), ("PM", 0.15)]
        elif sid.startswith("IT-"):
            roles = [("FE", 0.5), ("PM", 0.15)]
        else:
            roles = [("BA", 0.7), ("PM", 0.15)]
    prod = [(k, s) for k, s in roles if k != "PM"]
    pm = sum(s for k, s in roles if k == "PM") or 0.10
    if not prod:
        prod = [("SEO", 1.0)]
    ssum = sum(s for _, s in prod) or 1
    prod = [(k, s / ssum) for k, s in prod]
    return prod, min(pm, 0.15)


def parse_file(path: Path, prefix: str):
    text = path.read_text(encoding="utf-8")
    parts = re.split(rf"\n### ({prefix}-\d+)\.\s+", text)
    out = []
    for i in range(1, len(parts), 2):
        sid, body = parts[i], parts[i + 1]
        name = body.split("\n", 1)[0].strip()
        m = re.search(r"\|\s*\*\*Название\*\*\s*\|\s*(.+?)\s*\|", body)
        if m:
            name = m.group(1).strip()
        direction = sub = typ = ""
        m = re.search(r"\|\s*\*\*Направление\*\*\s*\|\s*(.+?)\s*\|", body)
        if m:
            direction = m.group(1).strip()
        m = re.search(r"\|\s*\*\*Поднаправление\*\*\s*\|\s*(.+?)\s*\|", body)
        if m:
            sub = m.group(1).strip()
        m = re.search(r"\|\s*\*\*Тип\*\*\s*\|\s*(.+?)\s*\|", body)
        if m:
            typ = m.group(1).strip()
        hours = (8, 16, 40)
        m = re.search(r"Трудозатраты[^\n]*?:\s*([0-9]+)\s*/\s*([0-9]+)\s*/\s*([0-9]+)", body)
        if not m:
            m = re.search(
                r"\*\*Трудозатраты[^\n]*?\*\*[^\n]*?([0-9]+)\s*/\s*([0-9]+)\s*/\s*([0-9]+)",
                body,
            )
        if m:
            hours = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        price = (0, 0, 0)
        m = re.search(
            r"Рыночная цена[^\n]*?:\s*([0-9][0-9\s]*)\s*/\s*([0-9][0-9\s]*)\s*/\s*([0-9][0-9\s]*)",
            body,
        )
        if not m:
            m = re.search(
                r"\*\*Рыночная цена[^\n]*?\*\*[^\n]*?([0-9][0-9\s]*)\s*/\s*([0-9][0-9\s]*)\s*/\s*([0-9][0-9\s]*)",
                body,
            )
        if m:
            n = lambda x: int(re.sub(r"\s+", "", x))
            price = (n(m.group(1)), n(m.group(2)), n(m.group(3)))
        specialists = ""
        m = re.search(r"\*\*Специалисты:\*\*\s*(.+)", body)
        if m:
            specialists = m.group(1).strip()
        m = re.search(r"\|\s*\*\*Специалисты\*\*\s*\|\s*(.+?)\s*\|", body)
        if m:
            specialists = m.group(1).strip()

        prod, pm_share = map_roles(specialists, sid)
        blend = sum(IMPRO_RATES.get(k, 2000) * s for k, s in prod)
        eff = 0.85

        def cost(h: int) -> int:
            prod_cost = h * blend * eff
            return int(round(prod_cost * (1 + pm_share)))

        hmin, havg, hmax = hours
        cmin, cavg, cmax = cost(hmin), cost(havg), cost(hmax)
        pmin, pavg, pmax = price

        def sell(c, mkt, tier):
            margin = {"min": 0.30, "avg": 0.38, "prem": 0.45}[tier]
            from_cost = int(round(c / (1 - margin)))
            if mkt <= 0:
                return from_cost
            if tier == "min":
                return max(from_cost, int(mkt * 0.9)) if from_cost < mkt * 1.3 else from_cost
            if tier == "avg":
                target = max(from_cost, int(mkt * 1.05))
                if pmax:
                    target = min(target, max(pmax, from_cost))
                return target
            return max(from_cost, int(mkt * 1.25), int(pmax * 0.95) if pmax else from_cost)

        rmin = sell(cmin, pmin, "min")
        ravg = sell(cavg, pavg, "avg")
        rprem = sell(cmax, pmax, "prem")
        ravg = max(ravg, rmin)
        rprem = max(rprem, ravg)

        domain = (
            "marketing"
            if sid.startswith("M")
            else ("it" if sid.startswith("IT") else "consulting")
        )
        out.append(
            {
                "id": sid,
                "name": name,
                "domain": domain,
                "direction": direction,
                "subdirection": sub,
                "type": typ,
                "hours_min": hmin,
                "hours_avg": havg,
                "hours_max": hmax,
                "market_min": pmin,
                "market_avg": pavg,
                "market_max": pmax,
                "specialists_raw": specialists[:200],
                "roles_model": "+".join(f"{k}({s:.0%})" for k, s in prod)
                + f"+PM_cost({pm_share:.0%})",
                "blend_rate_cost": int(round(blend * eff)),
                "primary_role": prod[0][0],
                "primary_rate_cost": IMPRO_RATES.get(prod[0][0], 2000),
                "cost_min": cmin,
                "cost_avg": cavg,
                "cost_max": cmax,
                "impro_price_min": rmin,
                "impro_price_avg": ravg,
                "impro_price_premium": rprem,
                "margin_risk": "YES" if pavg and cavg > pavg * 1.02 else "NO",
                "implied_margin_at_market_avg": round((pavg - cavg) / pavg * 100, 1)
                if pavg
                else None,
                "implied_margin_at_impro_avg": round((ravg - cavg) / ravg * 100, 1)
                if ravg
                else None,
            }
        )
    return out


def main():
    OUT.mkdir(exist_ok=True)
    svcs = []
    svcs += parse_file(BASE / "marketing-microservices.md", "M")
    svcs += parse_file(BASE / "it-microservices.md", "IT")
    svcs += parse_file(BASE / "consulting-microservices.md", "C")
    new_dir = BASE / "new-directions-microservices.md"
    if new_dir.exists():
        svcs += parse_file(new_dir, "N")
        for s in svcs:
            if s["id"].startswith("N-"):
                s["domain"] = "marketing"

    fields = list(svcs[0].keys())
    with open(OUT / "microservices-master.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(svcs)
    (OUT / "microservices-master.json").write_text(
        json.dumps(svcs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "impro-hourly-cost-rates.json").write_text(
        json.dumps(IMPRO_RATES, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    risk = sum(1 for s in svcs if s["margin_risk"] == "YES")
    print(f"Wrote {len(svcs)} services; margin_risk={risk}")
    for d in ("marketing", "it", "consulting"):
        sub = [s for s in svcs if s["domain"] == d]
        print(
            d,
            "n=",
            len(sub),
            "med_cost=",
            int(st.median([s["cost_avg"] for s in sub])),
            "med_impro=",
            int(st.median([s["impro_price_avg"] for s in sub])),
        )


if __name__ == "__main__":
    main()
