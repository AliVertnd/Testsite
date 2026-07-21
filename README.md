# Каталог микроуслуг Impro

Единый каталог **атомарных** услуг агентства (маркетинг + IT + консалтинг) для рынка РФ.

**273 микроуслуги** с карточками, рыночными ценами, трудоёмкостью, себестоимостью и рекомендуемой ценой Impro.

## Быстрый старт

| Что нужно | Куда смотреть |
|---|---|
| Полный индекс + цены | [catalog/INDEX.md](catalog/INDEX.md) |
| Карточки маркетинга (118) | [research/marketing-microservices.md](research/marketing-microservices.md) |
| Карточки IT (90) | [research/it-microservices.md](research/it-microservices.md) |
| Карточки консалтинга (65) | [research/consulting-microservices.md](research/consulting-microservices.md) |
| Ставки часа специалистов | [research/hourly-rates-rf.md](research/hourly-rates-rf.md) |
| Себестоимость по услугам | [catalog/services-cost-table.md](catalog/services-cost-table.md) |
| Модель расчёта | [catalog/cost-model.md](catalog/cost-model.md) |
| Комплексы (сумма атомов) | [catalog/complexes.md](catalog/complexes.md) |
| CSV для Excel / CRM | [catalog/microservices-master.csv](catalog/microservices-master.csv) |

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
