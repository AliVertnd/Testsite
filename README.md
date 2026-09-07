# Каталог микроуслуг Impro

Единый каталог **атомарных** услуг агентства (маркетинг + IT + консалтинг) для рынка РФ.

**308 микроуслуг** (273 базовых + 35 новых: AI SEO, GEO, PR, Telegram Ads, Авито, внедрение ИИ).

## Порядок работы (v2)

1. **Локомотив** — входное предложение с адаптацией по триггерам  
2. **Фиксированные услуги** — сборка из атомов, прайс-матрицы, калькулятор  
3. **Гибридные** — фикс-база + переменная часть  
4. **% от результата** — после гибрида  
5. **Комплексы и Bitrix** — после утверждения первых услуг  

| Сейчас | Файл |
|---|---|
| Локомотив «Диагностика ROMI» | [offers/locomotive-romi-diagnostic.md](offers/locomotive-romi-diagnostic.md) |
| Фикс-пакеты волны 1 | [catalog/06-fixed-services-wave1.md](catalog/06-fixed-services-wave1.md) |
| Калькулятор (HTML) | [calculator/index.html](calculator/index.html) |
| Карточка компании | [docs/company-profile.md](docs/company-profile.md) |
| План и роли | [docs/work-plan-v2.md](docs/work-plan-v2.md) |

## Быстрый старт

| Что нужно | Куда смотреть |
|---|---|
| Что реально продают на рынке + линейки | [catalog/01-services-by-direction.md](catalog/01-services-by-direction.md) |
| Каталог отдельных услуг по направлениям | [catalog/02-services-catalog.md](catalog/02-services-catalog.md) |
| Полноценные услуги с рынка под Impro | [catalog/03-full-services-market-fit.md](catalog/03-full-services-market-fit.md) |
| Полный индекс + цены | [catalog/INDEX.md](catalog/INDEX.md) |
| Карточки маркетинга (118) | [research/marketing-microservices.md](research/marketing-microservices.md) |
| Карточки IT (90) | [research/it-microservices.md](research/it-microservices.md) |
| Карточки консалтинга (65) | [research/consulting-microservices.md](research/consulting-microservices.md) |
| AI SEO / GEO / PR / ИИ (35) | [research/new-directions-microservices.md](research/new-directions-microservices.md) |
| Ставки часа специалистов | [research/hourly-rates-rf.md](research/hourly-rates-rf.md) |
| Себестоимость по услугам | [catalog/services-cost-table.md](catalog/services-cost-table.md) |
| Модель расчёта | [catalog/cost-model.md](catalog/cost-model.md) |
| Комплексы | [catalog/complexes.md](catalog/complexes.md) — **черновик, этап позже** |
| CSV для Excel / CRM | [catalog/microservices-master.csv](catalog/microservices-master.csv) |
| Витрина полных карточек (17 полей) | [catalog/sample-full-cards.md](catalog/sample-full-cards.md) |

## Принцип

Считаем **не пакеты**, а микроуслуги, которые можно оценить отдельно и собирать в любые комплексы.

Пример SEO (не «продвижение», а атомы):

- Сбор семантики
- Кластеризация
- Технический аудит
- ТЗ на статьи
- Линкбилдинг
- Настройка Метрики

Цена комплекса = **сумма** входящих микроуслуг. Отдельного прайса на комплекс нет.

## Методология (5 этапов)

1. **Полный список микроуслуг** — без цен и пакетов  
2. **Карточка каждой услуги** — результат, границы, драйверы объёма, параметры оценки, часы, специалисты, рынок, upsell  
3. **Рыночная статистика РФ** — min / avg / max именно атома (не ретейнера)  
4. **Внутренние нормативы** — трудоёмкость + себестоимость через ставки часа  
5. **Комплексы** — только сборка из описанных атомов  

Структура карточки и детали: [docs/methodology.md](docs/methodology.md).

## Ценообразование Impro (кратко)

```
Себестоимость = часы × ставка_микса × 0.85 × (1 + PM 10–15%)
Цена Impro    = себестоимость / (1 − маржа)  + коррекция к рынку
```

Маржа: min 30% · avg 38% · premium 45%.

Нишевые коэффициенты к часам (банкротство 1.4–1.8, недвижимость 1.3–1.6 и т.д.) — в [catalog/cost-model.md](catalog/cost-model.md).

## Примеры комплексов (Impro avg)

| Комплекс | Состав (атомы) | Impro avg |
|---|---|---:|
| SEO Базовый | M-001 + M-003 + M-007 | ~129 000 ₽ |
| Запуск Директ | M-022…M-035 (5 атомов) | см. complexes |
| Лендинг Slice | дизайн + вёрстка + форма + CRM + аналитика + QA | ~336 000 ₽ |
| Growth Engine | семантика + Директ + лендинг + CRM + аналитика | ~518 000 ₽ |

Точные суммы: [catalog/complexes.md](catalog/complexes.md).

## Источники рынка

Прайсы и обзоры агентств РФ 2024–2026 (SEO Jazz, Kokoc, SEOMGROUP, Team-B, REDBE, Lead.Media, Workspace.ru, RealHR/ppc.world, Habr Career, Dev-Ins, IQ Dev, РАМУ rate card и др.). Цифры — **синтез mid-market**, не оферта.

## Пересчёт себестоимости

```bash
python3 scripts/recalc_costs.py
```

Обновляет `catalog/microservices-master.csv` и производные таблицы.
