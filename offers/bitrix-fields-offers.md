# Поля Bitrix под офферы (минимум)

**Статус:** спецификация для внедрения  
**Канон локомотива:** `offers/LOC-PILOT-canon.md`

---

## Сделка / лид — общие

| Поле | Тип | Значения / смысл |
|---|---|---|
| `track` | список | A / B |
| `entry_offer` | список | LOC-PILOT / OX-COM / OX-MKT / OX-IT / OX-AUTO / FIX / RET / OTHER |
| `trigger` | список | T1…T6 / OTHER |
| `niche` | список | developer / modular / renovation / other |
| `goal_one` | строка | одна бизнес-цель |
| `offer_tier` | список | Smoke / Standard / Serious / Start / Growth / Scale |
| `envelope_amount` | число | конверт ₽ (К1) |
| `impro_share_pct` | число | доля Impro (0.30–0.40) |
| `impro_amount` | число | работа Impro ₽ |
| `media_mode` | список | inside_envelope / separate |
| `modules` | множественный | MKT-PERF, MKT-SEO, MKT-CLASS, IT-LAND, IT-CRM, COM-ENABLE |
| `next_model` | список | OX / K1 / RET / B / EXIT |
| `credit_pct` | число | 1.0 / 0.5 / 0 |
| `decision_date` | дата | дата decision meeting |
| `decision_result` | список | OX / K1 / second_pilot / B / refuse |

---

## Стадии холодки (уже в логике M0–M3)

| Поле | Стадия |
|---|---|
| `m0_score` | M0 |
| `m1_hook` | M1 |
| `m2_kp_sent` | M2 (дата) |
| `m3_contract` | M3 |
| `pm_active` | PM |

---

## Правила заполнения для LOC-PILOT

1. `entry_offer = LOC-PILOT` при отправке КП пилота  
2. `envelope_amount` = утверждённый/предложенный конверт  
3. `impro_amount = envelope × impro_share_pct` (не ниже 250 000)  
4. `modules` ≥ каркас подразумевается; в поле — доп. модули  
5. После пилота обязательно `decision_result`  

Калькулятор: `calculator/index.html` · JSON: `offers/loc-pilot-calc.json`
