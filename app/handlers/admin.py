import asyncio
import os
import csv
import io
import zipfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

from app.db.repo import Repo, UserRow
from app.db.sqlite import session as db_session
from app.keyboards.admin import (
    admin_menu,
    admin_users_menu,
    admin_orders_menu,
    admin_stats_menu,
    admin_mailing_menu,
    mailing_running_kb,
    mailing_cancel_kb,
    mailing_confirm_kb,
    mailing_tariff_select_kb,
    admin_settings_menu,
    admin_tariffs_menu,
    tariff_row_btn,
    tariff_actions_kb,
    confirm_tariff_create_active_kb,
    order_actions,
    user_profile_actions,
    confirm_mark_paid_kb,
    confirm_reissue_kb,
    confirm_rotate_kb,
)
from app.keyboards.common import back_to_menu, _abs_url
from marzban_api import MarzbanAPI

router = Router()


class AdminUserSearch(StatesGroup):
    waiting_query = State()


class AdminManualPaid(StatesGroup):
    waiting_order_id = State()


class AdminOrderSearch(StatesGroup):
    waiting_query = State()


class AdminTariffCreate(StatesGroup):
    waiting_title = State()
    waiting_days = State()
    waiting_price = State()
    waiting_data_gb = State()
    waiting_active = State()


class AdminTariffEdit(StatesGroup):
    waiting_value = State()


class AdminMailing(StatesGroup):
    waiting_text = State()
    waiting_confirm = State()


async def _replace(call: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer(
        text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _is_admin(call_or_msg, admin_tg_id: int) -> bool:
    uid = call_or_msg.from_user.id
    return int(uid) == int(admin_tg_id)


def _fmt_ts(ts: Optional[int]) -> str:
    if not isinstance(ts, int):
        return "-"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


def _parse_search_query(q: str) -> str:
    return (q or "").strip()


def _build_marzban() -> MarzbanAPI:
    return MarzbanAPI(
        base_url=os.getenv("MARZBAN_URL", "http://127.0.0.1:8000"),
        api_key=os.getenv("MARZBAN_API_KEY", ""),
        admin_user=os.getenv("MARZBAN_ADMIN_USER", ""),
        admin_pass=os.getenv("MARZBAN_ADMIN_PASS", ""),
    )


def _find_user(repo: Repo, q: str) -> Optional[UserRow]:
    """
    Принимает:
      - @username / username
      - tg_id (число)
      - tg_123...
    """
    s = (q or "").strip()
    if not s:
        return None

    # tg_id
    if s.isdigit():
        return repo.get_user(int(s))

    # @username
    if s.startswith("@"):
        s2 = s[1:].strip()
        if not s2:
            return None
        return repo.get_user_by_username(s2)

    # tg_123...
    if s.lower().startswith("tg_"):
        return repo.get_user_by_marzban_username(s)

    # plain username
    return repo.get_user_by_username(s)


async def _get_marzban_status(username: str) -> Tuple[str, str, Optional[str]]:
    """
    returns (status, expire_str, subscription_url_abs_or_none)
    """
    m = _build_marzban()
    u = await m.get_user(username)
    if not isinstance(u, dict):
        return "not_found", "-", None

    status = str(u.get("status") or "-")
    expire = u.get("expire")
    expire_str = _fmt_ts(int(expire)) if isinstance(expire, (int, float)) else "-"

    sub_url = None
    for key in ("subscription_url", "sub_url", "access_url", "url"):
        v = u.get(key)
        if isinstance(v, str) and v:
            sub_url = v
            break
    if not sub_url:
        for key in ("links", "urls"):
            v = u.get(key)
            if isinstance(v, list) and v and isinstance(v[0], str):
                sub_url = v[0]
                break

    return status, expire_str, _abs_url(sub_url) if sub_url else None


async def _render_user_profile(repo: Repo, tg_id: int) -> Tuple[str, Optional[int]]:
    u = repo.get_user(tg_id)
    if not u:
        return "❌ Пользователь не найден в базе.", None

    uname = f"@{u.tg_username}" if u.tg_username else "(без username)"
    marzban_username = u.marzban_username

    # Marzban details
    try:
        status, expire_str, sub_url = await _get_marzban_status(marzban_username)
    except Exception as e:
        status, expire_str, sub_url = "error", "-", None
        marzban_err = str(e)
    else:
        marzban_err = ""

    # orders
    orders = repo.list_orders_for_user(tg_id, limit=3)
    if orders:
        lines_orders = []
        for o in orders:
            lines_orders.append(f"#{o.id} | {o.amount}₽ | {o.status} | {o.created_at}")
        orders_block = "\n".join(lines_orders)
    else:
        orders_block = "(нет заказов)"

    # subscription link from DB last ISSUED (more reliable for UI)
    last_issued = repo.get_last_valid_issued_order_for_user(tg_id)
    issued_link = _abs_url(last_issued.access_url) if (last_issued and last_issued.access_url) else None

    link_line = issued_link or sub_url or "-"

    text = (
        "👤 <b>Профиль пользователя</b>\n\n"
        f"<b>tg_id:</b> <code>{u.tg_id}</code>\n"
        f"<b>username:</b> {uname}\n"
        f"<b>marzban_username:</b> <code>{marzban_username}</code>\n"
        f"<b>Marzban status:</b> <code>{status}</code>\n"
        f"<b>Marzban expire:</b> <code>{expire_str}</code>\n"
        f"<b>subscription:</b> {link_line}\n"
        f"<b>created_at:</b> <code>{u.created_at}</code>\n"
    )

    if marzban_err:
        text += f"\n⚠️ <b>Marzban error:</b> <code>{marzban_err[:300]}</code>\n"

    text += "\n<b>Последние заказы:</b>\n" + orders_block
    return text, u.tg_id


def _orders_list_kb(order_ids: List[int], back_cb: str = "admin:orders_menu") -> InlineKeyboardMarkup:
    rows = []
    buf = []
    for oid in order_ids:
        buf.append(InlineKeyboardButton(text=f"🧾 #{oid}", callback_data=f"admin:order:refresh:{oid}"))
        if len(buf) == 2:
            rows.append(buf)
            buf = []
    if buf:
        rows.append(buf)

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fetch_order_ids(where_sql: str, params: tuple, limit: int = 20) -> List[int]:
    with db_session() as db:
        rows = db.execute(
            f"""
            SELECT id
            FROM orders
            WHERE {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()
    return [int(r["id"]) for r in rows]


def _render_order_card(repo: Repo, order_id: int) -> str:
    o = repo.get_order(order_id)
    if not o:
        return "❌ Заказ не найден."

    tariff = repo.get_tariff(int(o.tariff_id))
    t_title = tariff.title if tariff else f"Тариф #{o.tariff_id}"

    text = (
        f"🧾 <b>Заказ #{o.id}</b>\n\n"
        f"<b>tg_id:</b> <code>{o.tg_id}</code>\n"
        f"<b>tariff:</b> {t_title}\n"
        f"<b>amount:</b> <b>{o.amount} ₽</b>\n"
        f"<b>status:</b> <code>{o.status}</code>\n"
        f"<b>created_at:</b> <code>{o.created_at}</code>\n"
        f"<b>decided_at:</b> <code>{o.decided_at or '-'}</code>\n"
        f"<b>issued_at:</b> <code>{o.issued_at or '-'}</code>\n"
        f"<b>payment_id:</b> <code>{o.payment_id or '-'}</code>\n"
        f"<b>payment_url:</b> {o.payment_url or '-'}\n"
        f"<b>access_url:</b> {o.access_url or '-'}\n"
        f"<b>admin_tg_id:</b> <code>{o.admin_tg_id or '-'}</code>\n"
        f"<b>error_text:</b> <code>{(o.error_text or '-')[:350]}</code>\n"
    )
    return text


def _iso_now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _iso_since(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).replace(microsecond=0).isoformat()


def _stats_period(repo: Repo, title: str, since_iso: str) -> str:
    """
    Статистика по БД за период created_at >= since_iso.
    created_at хранится как ISO UTC, строковое сравнение ок.
    """
    with db_session() as db:
        # оплаченные (в нашей логике: PAID/APPROVED/ISSUED)
        row_paid = db.execute(
            """
            SELECT
                COUNT(*) as cnt,
                COALESCE(SUM(amount), 0) as sum_amount,
                COALESCE(COUNT(DISTINCT tg_id), 0) as uniq_users
            FROM orders
            WHERE status IN ('PAID','APPROVED','ISSUED')
              AND created_at >= ?
            """,
            (since_iso,),
        ).fetchone()

        row_all = db.execute(
            """
            SELECT
                COUNT(*) as cnt
            FROM orders
            WHERE created_at >= ?
            """,
            (since_iso,),
        ).fetchone()

        row_wait_payment = db.execute(
            """
            SELECT COUNT(*) as cnt
            FROM orders
            WHERE status='WAIT_PAYMENT'
              AND created_at >= ?
            """,
            (since_iso,),
        ).fetchone()

        row_wait_confirm = db.execute(
            """
            SELECT COUNT(*) as cnt
            FROM orders
            WHERE status='WAIT_CONFIRM'
              AND created_at >= ?
            """,
            (since_iso,),
        ).fetchone()

        row_errors = db.execute(
            """
            SELECT COUNT(*) as cnt
            FROM orders
            WHERE status='ERROR'
              AND created_at >= ?
            """,
            (since_iso,),
        ).fetchone()

        row_new_users = db.execute(
            """
            SELECT COUNT(*) as cnt
            FROM users
            WHERE created_at >= ?
            """,
            (since_iso,),
        ).fetchone()

    paid_cnt = int(row_paid["cnt"] or 0)
    paid_sum = int(row_paid["sum_amount"] or 0)
    paid_users = int(row_paid["uniq_users"] or 0)

    all_cnt = int(row_all["cnt"] or 0)
    wp = int(row_wait_payment["cnt"] or 0)
    wc = int(row_wait_confirm["cnt"] or 0)
    er = int(row_errors["cnt"] or 0)
    nu = int(row_new_users["cnt"] or 0)

    text = (
        f"📊 <b>{title}</b>\n\n"
        f"<b>Период от:</b> <code>{since_iso}</code>\n"
        f"<b>Сейчас:</b> <code>{_iso_now_utc()}</code>\n\n"
        f"🧾 <b>Заказы всего:</b> <b>{all_cnt}</b>\n"
        f"✅ <b>Оплаченные (PAID/APPROVED/ISSUED):</b> <b>{paid_cnt}</b>\n"
        f"👥 <b>Плативших пользователей:</b> <b>{paid_users}</b>\n"
        f"💰 <b>Выручка:</b> <b>{paid_sum} ₽</b>\n\n"
        f"⏳ <b>WAIT_PAYMENT:</b> {wp}\n"
        f"🧑‍⚖️ <b>WAIT_CONFIRM:</b> {wc}\n"
        f"⚠️ <b>ERROR:</b> {er}\n\n"
        f"🆕 <b>Новых пользователей:</b> {nu}\n"
    )
    return text


async def _stats_active_expired(repo: Repo, limit_users: int = 50) -> str:
    """
    Берём последних N пользователей из БД и проверяем их в Marzban.
    """
    users = repo.list_recent_users(limit=int(limit_users))
    if not users:
        return "📈 <b>Активные / истёкшие</b>\n\nПользователей в базе нет."

    m = _build_marzban()
    now_ts = int(datetime.now(timezone.utc).timestamp())

    active = 0
    expired = 0
    disabled = 0
    not_found = 0
    errors = 0

    expiring_soon: list[tuple[int, str, int]] = []  # (tg_id, marzban_username, expire_ts)

    for u in users:
        try:
            mu = await m.get_user(u.marzban_username)
            if not isinstance(mu, dict):
                not_found += 1
                continue

            st = str(mu.get("status") or "-")
            exp = mu.get("expire")
            exp_i = int(exp) if isinstance(exp, (int, float)) else None

            if st == "disabled":
                disabled += 1
                continue

            # active/expired by expire timestamp
            if exp_i is None:
                # если без expire — считаем как active (условно), но это редкость
                active += 1
            else:
                if exp_i > now_ts:
                    active += 1
                    # в "скоро истечёт" кладём до 7 дней
                    if exp_i <= now_ts + 7 * 86400:
                        expiring_soon.append((int(u.tg_id), u.marzban_username, exp_i))
                else:
                    expired += 1

        except Exception:
            errors += 1

    expiring_soon.sort(key=lambda x: x[2])

    lines = [
        "📈 <b>Активные / истёкшие</b>",
        "",
        f"<b>Проверено пользователей:</b> {len(users)}",
        f"✅ <b>Active:</b> {active}",
        f"⌛️ <b>Expired:</b> {expired}",
        f"⛔️ <b>Disabled:</b> {disabled}",
        f"❓ <b>Not found:</b> {not_found}",
        f"⚠️ <b>Errors:</b> {errors}",
    ]

    if expiring_soon:
        lines.append("\n<b>Скоро истекают (≤ 7 дней):</b>")
        for tg_id, mz_user, exp_ts in expiring_soon[:15]:
            lines.append(f"• <code>{tg_id}</code> | <code>{mz_user}</code> | <code>{_fmt_ts(exp_ts)}</code>")

    lines.append("\n<i>Примечание: это выборка по последним пользователям в БД, не по всем.</i>")
    return "\n".join(lines)


# ========= HOME =========

@router.callback_query(F.data == "menu:admin")
async def admin_home(call: CallbackQuery, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _replace(call, "🛠 Админка", reply_markup=admin_menu())
    await call.answer()


# ========= MENUS =========

@router.callback_query(F.data == "admin:users_menu")
async def admin_users_menu_handler(call: CallbackQuery, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _replace(call, "👥 Пользователи", reply_markup=admin_users_menu())
    await call.answer()


@router.callback_query(F.data == "admin:orders_menu")
async def admin_orders_menu_handler(call: CallbackQuery, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _replace(call, "🧾 Заказы / оплаты", reply_markup=admin_orders_menu())
    await call.answer()


@router.callback_query(F.data == "admin:stats_menu")
async def admin_stats_menu_handler(call: CallbackQuery, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _replace(call, "📊 Статистика", reply_markup=admin_stats_menu())
    await call.answer()


# ========= MAILING =========

_mailing_lock = {"busy": False, "by": None, "stop": False}


async def _safe_answer(call: CallbackQuery, text: str = "", show_alert: bool = False) -> None:
    try:
        await call.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "admin:mailing_menu")
async def admin_mailing_menu_handler(call: CallbackQuery, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _replace(call, "📣 <b>Рассылка</b>\n\nВыбери сегмент 👇", reply_markup=admin_mailing_menu())
    await _safe_answer(call)


@router.callback_query(F.data == "admin:mailing:cancel")
async def admin_mailing_cancel(call: CallbackQuery, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    # если рассылка уже идёт — "Отмена" = "остановить"
    if _mailing_lock.get("busy"):
        if int(_mailing_lock.get("by") or 0) != int(admin_tg_id):
            await _safe_answer(call, "Рассылка уже выполняется другим админом", show_alert=True)
            return

        _mailing_lock["stop"] = True
        _mailing_lock["busy"] = False
        _mailing_lock["by"] = None
        await state.clear()
        await _safe_answer(call, "Останавливаю…")
        await _replace(
            call,
            "⛔️ Останавливаю рассылку…\n\nЭто может занять несколько секунд.",
            reply_markup=admin_mailing_menu(),
        )
        return

    # обычная отмена (когда рассылка ещё не запущена)
    await state.clear()
    _mailing_lock["busy"] = False
    _mailing_lock["by"] = None
    _mailing_lock["stop"] = False
    await _safe_answer(call, "Отменено")
    await _replace(call, "📣 <b>Рассылка</b>\n\nВыбери сегмент 👇", reply_markup=admin_mailing_menu())


@router.callback_query(F.data == "admin:mailing:stop")
async def admin_mailing_stop(call: CallbackQuery, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    if not _mailing_lock["busy"]:
        await _safe_answer(call, "Рассылка не запущена", show_alert=True)
        return

    # останавливать может только тот админ, кто запустил
    if int(_mailing_lock.get("by") or 0) != int(admin_tg_id):
        await _safe_answer(call, "Остановить может только тот, кто запустил рассылку", show_alert=True)
        return

    _mailing_lock["stop"] = True
    _mailing_lock["busy"] = False
    _mailing_lock["by"] = None
    await _safe_answer(call, "Останавливаю…")


@router.callback_query(F.data.in_({"admin:mailing:all", "admin:mailing:active", "admin:mailing:expired"}))
async def admin_mailing_segment(call: CallbackQuery, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    seg = call.data.split(":")[-1]
    await state.clear()
    await state.update_data(segment=seg, tariff_id=None)
    await state.set_state(AdminMailing.waiting_text)

    names = {"all": "Всем", "active": "Только активным", "expired": "Только истёкшим"}
    await _safe_answer(call)
    await _replace(
        call,
        f"📣 <b>Рассылка:</b> {names.get(seg, seg)}\n\n"
        "Отправь текст одним сообщением.\n"
        "HTML разрешён (как везде в боте).\n\n"
        "Чтобы отменить — нажми «Отмена».",
        reply_markup=mailing_cancel_kb(),
    )


@router.callback_query(F.data == "admin:mailing:tariff")
async def admin_mailing_tariff_start(call: CallbackQuery, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    tariffs = repo.list_tariffs_all()
    if not tariffs:
        await _safe_answer(call, "Тарифов нет", show_alert=True)
        return

    await _safe_answer(call)
    await _replace(call, "📣 <b>Рассылка по тарифу</b>\n\nВыбери тариф 👇", reply_markup=mailing_tariff_select_kb(tariffs))


@router.callback_query(F.data.startswith("admin:mailing:tariff_select:"))
async def admin_mailing_tariff_selected(call: CallbackQuery, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tariff_id = int(call.data.split(":")[-1])
    t = repo.get_tariff(tariff_id)
    if not t:
        await _safe_answer(call, "Тариф не найден", show_alert=True)
        return

    await state.clear()
    await state.update_data(segment="tariff", tariff_id=tariff_id)
    await state.set_state(AdminMailing.waiting_text)

    await _safe_answer(call)
    await _replace(
        call,
        f"📣 <b>Рассылка:</b> По тарифу «{t.title}»\n\n"
        "Отправь текст одним сообщением.\n"
        "HTML разрешён.\n\n"
        "Чтобы отменить — нажми «Отмена».",
        reply_markup=mailing_cancel_kb(),
    )


@router.message(AdminMailing.waiting_text)
async def admin_mailing_text(msg: Message, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(msg, admin_tg_id):
        return

    text = (msg.text or "").strip()
    if not text:
        await msg.answer("Текст пустой. Отправь текст одним сообщением или нажми Отмена.", reply_markup=mailing_cancel_kb())
        return

    data = await state.get_data()
    segment = data.get("segment")
    tariff_id = data.get("tariff_id")

    if segment == "all":
        tg_ids = repo.list_all_user_tg_ids()
        seg_name = "Всем"
    elif segment == "active":
        tg_ids = repo.list_active_subscriber_tg_ids()
        seg_name = "Только активным"
    elif segment == "expired":
        tg_ids = repo.list_expired_subscriber_tg_ids()
        seg_name = "Только истёкшим"
    elif segment == "tariff" and tariff_id:
        tg_ids = repo.list_last_issued_tg_ids_by_tariff(int(tariff_id))
        t = repo.get_tariff(int(tariff_id))
        seg_name = f"По тарифу «{t.title}»" if t else f"По тарифу #{tariff_id}"
    else:
        tg_ids = []
        seg_name = str(segment)

    await state.update_data(mailing_text=text, recipients=tg_ids, seg_name=seg_name)
    await state.set_state(AdminMailing.waiting_confirm)

    preview = text
    if len(preview) > 1200:
        preview = preview[:1200] + "\n…"

    await msg.answer(
        "🧾 <b>Предпросмотр рассылки</b>\n\n"
        f"<b>Сегмент:</b> {seg_name}\n"
        f"<b>Получателей:</b> <code>{len(tg_ids)}</code>\n\n"
        f"{preview}",
        reply_markup=mailing_confirm_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "admin:mailing:confirm")
async def admin_mailing_confirm(call: CallbackQuery, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    if _mailing_lock["busy"]:
        await _safe_answer(call, "Рассылка уже выполняется", show_alert=True)
        return

    data = await state.get_data()
    text = data.get("mailing_text") or ""
    tg_ids = data.get("recipients") or []
    seg_name = data.get("seg_name") or "-"

    if not text or not tg_ids:
        await _safe_answer(call, "Некому отправлять или пустой текст", show_alert=True)
        return

    # lock
    _mailing_lock["busy"] = True
    _mailing_lock["by"] = int(admin_tg_id)
    _mailing_lock["stop"] = False

    total = len(tg_ids)
    sent = 0
    failed = 0
    stopped = False
    progress_msg = None

    await _safe_answer(call, "Запускаю…")

    # покажем “живое” сообщение, которое будем обновлять прогрессом
    try:
        await call.message.delete()
    except Exception:
        pass

    try:
        progress_msg = await call.message.answer(
            f"🚀 <b>Рассылка запущена</b>\n\n"
            f"<b>Сегмент:</b> {seg_name}\n"
            f"<b>Получателей:</b> <code>{total}</code>\n\n"
            f"Отправлено: <code>0</code>\n"
            f"Ошибок: <code>0</code>\n"
            f"Статус: <b>в процессе</b>",
            parse_mode="HTML",
            reply_markup=mailing_running_kb(),
            disable_web_page_preview=True,
        )

        async def _update_progress(force: bool = False) -> None:
            # обновляем редко, чтобы не словить flood на edit_message_text
            if not progress_msg:
                return
            if (not force) and (sent + failed) % 25 != 0:
                return
            try:
                await progress_msg.edit_text(
                    f"🚀 <b>Рассылка запущена</b>\n\n"
                    f"<b>Сегмент:</b> {seg_name}\n"
                    f"<b>Получателей:</b> <code>{total}</code>\n\n"
                    f"Отправлено: <code>{sent}</code>\n"
                    f"Ошибок: <code>{failed}</code>\n"
                    f"Статус: <b>в процессе</b>",
                    parse_mode="HTML",
                    reply_markup=mailing_running_kb(),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

        # отправка
        for uid in tg_ids:
            if _mailing_lock["stop"]:
                stopped = True
                break

            try:
                await call.bot.send_message(
                    chat_id=int(uid),
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                sent += 1

            except TelegramRetryAfter as e:
                # Telegram просит подождать N секунд
                try:
                    wait_s = int(getattr(e, "retry_after", 3) or 3)
                except Exception:
                    wait_s = 3
                await asyncio.sleep(wait_s)
                # повторим один раз после ожидания
                try:
                    await call.bot.send_message(
                        chat_id=int(uid),
                        text=text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    sent += 1
                except Exception:
                    failed += 1

            except (TelegramForbiddenError, TelegramBadRequest):
                failed += 1
            except Exception:
                failed += 1

            await _update_progress(force=False)
            await asyncio.sleep(0.03)

        # финальный апдейт
        final_header = "⛔️ <b>Рассылка остановлена</b>\n\n" if stopped else "✅ <b>Рассылка завершена</b>\n\n"
        status_line = "⛔️ <b>остановлена</b>" if stopped else "✅ <b>завершена</b>"

        final_text = (
            final_header
            + f"<b>Сегмент:</b> {seg_name}\n"
            + f"<b>Получателей:</b> <code>{total}</code>\n"
            + f"<b>Отправлено:</b> <code>{sent}</code>\n"
            + f"<b>Ошибок:</b> <code>{failed}</code>\n\n"
            + f"Статус: {status_line}"
        )

        try:
            if progress_msg:
                await progress_msg.edit_text(
                    final_text,
                    parse_mode="HTML",
                    reply_markup=admin_mailing_menu(),
                    disable_web_page_preview=True,
                )
            else:
                raise RuntimeError("progress_msg is None")
        except Exception:
            await call.message.answer(
                final_text,
                parse_mode="HTML",
                reply_markup=admin_mailing_menu(),
                disable_web_page_preview=True,
            )

    finally:
        # unlock всегда, даже если упали на любом месте
        _mailing_lock["busy"] = False
        _mailing_lock["by"] = None
        _mailing_lock["stop"] = False
        try:
            await state.clear()
        except Exception:
            pass


    # лог финиша
    try:
        repo.log(
            action="admin_mailing_finished",
            actor_tg_id=int(admin_tg_id),
            payload={"segment": seg_name, "total": total, "sent": sent, "failed": failed, "stopped": stopped},
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin:settings_menu")
async def admin_settings_menu_handler(call: CallbackQuery, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await _replace(call, "⚙️ Настройки", reply_markup=admin_settings_menu())
    await call.answer()


# ========= SETTINGS ACTIONS =========

@router.callback_query(F.data == "admin:settings:cleanup_wait_payment")
async def admin_settings_cleanup_wait_payment(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    # По умолчанию 3600 сек (1 час) как в твоем cleanup_wait_payment.py
    cancelled = repo.expire_wait_payment(ttl_seconds=3600)

    await call.answer("Готово")
    await _replace(
        call,
        f"🧹 Очистка выполнена.\n\n"
        f"Отменено WAIT_PAYMENT (таймаут 1 час): <b>{cancelled}</b>",
        reply_markup=admin_settings_menu(),
    )


def _csv_bytes(headers: list[str], rows: list[list]) -> bytes:
    """
    Делает CSV в UTF-8 с BOM, чтобы Excel нормально открывал кириллицу.
    """
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    text = buf.getvalue()
    return ("\ufeff" + text).encode("utf-8")


@router.callback_query(F.data == "admin:settings:export")
async def admin_settings_export(call: CallbackQuery, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    # Собираем данные напрямую из sqlite (через db_session), чтобы не плодить методы в Repo
    try:
        with db_session() as db:
            users = db.execute(
                "SELECT tg_id, tg_username, marzban_username, created_at FROM users ORDER BY created_at DESC"
            ).fetchall()
            tariffs = db.execute(
                "SELECT id, title, days, price, data_gb, is_active FROM tariffs ORDER BY id ASC"
            ).fetchall()
            orders = db.execute(
                """
                SELECT id, tg_id, tariff_id, amount, status, created_at, decided_at, admin_tg_id,
                       error_text, payment_id, payment_url, issued_at, access_url
                FROM orders
                ORDER BY id DESC
                """
            ).fetchall()

        users_csv = _csv_bytes(
            ["tg_id", "tg_username", "marzban_username", "created_at"],
            [[r["tg_id"], r["tg_username"], r["marzban_username"], r["created_at"]] for r in users],
        )

        tariffs_csv = _csv_bytes(
            ["id", "title", "days", "price", "data_gb", "is_active"],
            [[r["id"], r["title"], r["days"], r["price"], r["data_gb"], r["is_active"]] for r in tariffs],
        )

        orders_csv = _csv_bytes(
            [
                "id",
                "tg_id",
                "tariff_id",
                "amount",
                "status",
                "created_at",
                "decided_at",
                "admin_tg_id",
                "error_text",
                "payment_id",
                "payment_url",
                "issued_at",
                "access_url",
            ],
            [
                [
                    r["id"],
                    r["tg_id"],
                    r["tariff_id"],
                    r["amount"],
                    r["status"],
                    r["created_at"],
                    r["decided_at"],
                    r["admin_tg_id"],
                    r["error_text"],
                    r["payment_id"],
                    r["payment_url"],
                    r["issued_at"],
                    r["access_url"],
                ]
                for r in orders
            ],
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = Path("/tmp")
        zip_path = out_dir / f"shifty_export_{ts}.zip"

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("users.csv", users_csv)
            z.writestr("tariffs.csv", tariffs_csv)
            z.writestr("orders.csv", orders_csv)

        doc = FSInputFile(str(zip_path))
        await call.bot.send_document(
            chat_id=call.from_user.id,
            document=doc,
            caption=f"📥 Экспорт CSV\n\nusers={len(users)}, tariffs={len(tariffs)}, orders={len(orders)}",
        )

        await call.answer("Отправил файлом ✅")
        await _replace(call, "📥 Экспорт готов. Файл отправил тебе в личку.", reply_markup=admin_settings_menu())

    except Exception as e:
        await call.answer("Ошибка", show_alert=True)
        await _replace(call, f"❌ Ошибка экспорта: <code>{str(e)[:900]}</code>", reply_markup=admin_settings_menu())


# ========= TARIFFS =========

def _fmt_data_gb(v: Optional[int]) -> str:
    return "∞" if v is None else str(int(v))


def _render_tariff_card(repo: Repo, tariff_id: int) -> str:
    t = repo.get_tariff(int(tariff_id))
    if not t:
        return "❌ Тариф не найден."

    st = "✅ active" if int(t.is_active) == 1 else "⛔️ inactive"
    text = (
        f"🏷 <b>Тариф #{t.id}</b>\n\n"
        f"<b>status:</b> <code>{st}</code>\n"
        f"<b>title:</b> <b>{t.title}</b>\n"
        f"<b>days:</b> <code>{t.days}</code>\n"
        f"<b>price:</b> <code>{t.price}</code> ₽\n"
        f"<b>data_gb:</b> <code>{_fmt_data_gb(t.data_gb)}</code>\n"
    )
    return text


@router.callback_query(F.data == "admin:settings:tariffs")
async def admin_tariffs_list(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tariffs = repo.list_tariffs_all()
    if not tariffs:
        await _replace(
            call,
            "🏷 <b>Тарифы</b>\n\nПока тарифов нет.\nНажми «➕ Создать тариф».",
            reply_markup=admin_tariffs_menu(),
        )
        await call.answer()
        return

    # текст
    lines = ["🏷 <b>Тарифы</b>\n", "<i>Нажми на тариф, чтобы открыть карточку.</i>\n"]
    for t in tariffs:
        st = "✅" if int(t.is_active) == 1 else "⛔️"
        lines.append(f"{st} <b>#{t.id}</b> — {t.title} | {t.days}д | {t.price}₽ | {_fmt_data_gb(t.data_gb)}GB")

    # клавиатура: список тарифов + меню
    kb_rows = []
    buf = []
    for t in tariffs:
        btn = tariff_row_btn(t.id, t.title, t.is_active)
        buf.append(btn)
        if len(buf) == 1:
            kb_rows.append(buf)
            buf = []
    if buf:
        kb_rows.append(buf)

    # добавим нижнее меню
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows + admin_tariffs_menu().inline_keyboard)

    await _replace(call, "\n".join(lines), reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("admin:tariff:view:"))
async def admin_tariff_view(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    tariff_id = int(call.data.split(":")[-1])
    t = repo.get_tariff(tariff_id)
    if not t:
        await call.answer("Тариф не найден", show_alert=True)
        return
    await _replace(call, _render_tariff_card(repo, tariff_id), reply_markup=tariff_actions_kb(tariff_id, t.is_active))
    await call.answer()


@router.callback_query(F.data.startswith("admin:tariff:toggle:"))
async def admin_tariff_toggle(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    tariff_id = int(call.data.split(":")[-1])
    t = repo.toggle_tariff(tariff_id)
    if not t:
        await call.answer("Тариф не найден", show_alert=True)
        return
    await call.answer("OK")
    await _replace(call, _render_tariff_card(repo, tariff_id), reply_markup=tariff_actions_kb(tariff_id, t.is_active))


@router.callback_query(F.data.startswith("admin:tariff:duplicate:"))
async def admin_tariff_duplicate(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    tariff_id = int(call.data.split(":")[-1])
    t2 = repo.duplicate_tariff(tariff_id)
    if not t2:
        await call.answer("Тариф не найден", show_alert=True)
        return
    await call.answer("Скопировал ✅")
    await _replace(
        call,
        f"🧬 Создал копию тарифа: <b>#{t2.id}</b>\n\n" + _render_tariff_card(repo, int(t2.id)),
        reply_markup=tariff_actions_kb(int(t2.id), int(t2.is_active)),
    )


@router.callback_query(F.data == "admin:tariff:create")
async def admin_tariff_create_start(call: CallbackQuery, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminTariffCreate.waiting_title)
    await _replace(
        call,
        "➕ <b>Создание тарифа</b>\n\n"
        "Шаг 1/5 — отправь <b>название</b> тарифа.\n"
        "Например: <code>30 дней / Безлимит</code>\n\n"
        "Отмена: <code>отмена</code>",
        reply_markup=admin_tariffs_menu(),
    )
    await call.answer()


@router.message(AdminTariffCreate.waiting_title)
async def admin_tariff_create_title(msg: Message, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(msg, admin_tg_id):
        return

    txt = (msg.text or "").strip()
    if txt.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await msg.answer("Ок, отменено.", reply_markup=admin_settings_menu())
        return

    if len(txt) < 2:
        await msg.answer("Название слишком короткое. Попробуй ещё раз.")
        return

    await state.update_data(title=txt)
    await state.set_state(AdminTariffCreate.waiting_days)
    await msg.answer(
        "Шаг 2/5 — отправь <b>days</b> (целое число).\nНапример: <code>30</code>\n\nОтмена: <code>отмена</code>",
        parse_mode="HTML",
        reply_markup=admin_tariffs_menu(),
    )


@router.message(AdminTariffCreate.waiting_days)
async def admin_tariff_create_days(msg: Message, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(msg, admin_tg_id):
        return

    txt = (msg.text or "").strip()
    if txt.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await msg.answer("Ок, отменено.", reply_markup=admin_settings_menu())
        return

    if not txt.isdigit():
        await msg.answer("Нужно число. Например: 30")
        return

    days = int(txt)
    if days <= 0 or days > 3650:
        await msg.answer("Слишком странное значение. Дай число от 1 до 3650.")
        return

    await state.update_data(days=days)
    await state.set_state(AdminTariffCreate.waiting_price)
    await msg.answer(
        "Шаг 3/5 — отправь <b>price</b> (в рублях, целое число).\nНапример: <code>199</code>\n\nОтмена: <code>отмена</code>",
        parse_mode="HTML",
        reply_markup=admin_tariffs_menu(),
    )


@router.message(AdminTariffCreate.waiting_price)
async def admin_tariff_create_price(msg: Message, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(msg, admin_tg_id):
        return

    txt = (msg.text or "").strip()
    if txt.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await msg.answer("Ок, отменено.", reply_markup=admin_settings_menu())
        return

    if not txt.isdigit():
        await msg.answer("Нужно число. Например: 199")
        return

    price = int(txt)
    if price < 0 or price > 2_000_000:
        await msg.answer("Слишком большое/странное число.")
        return

    await state.update_data(price=price)
    await state.set_state(AdminTariffCreate.waiting_data_gb)
    await msg.answer(
        "Шаг 4/5 — отправь <b>data_gb</b>.\n"
        "• число (например <code>100</code>)\n"
        "• или <code>∞</code> / <code>безлимит</code> / <code>-</code> чтобы сделать безлимит\n\n"
        "Отмена: <code>отмена</code>",
        parse_mode="HTML",
        reply_markup=admin_tariffs_menu(),
    )


@router.message(AdminTariffCreate.waiting_data_gb)
async def admin_tariff_create_data_gb(msg: Message, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(msg, admin_tg_id):
        return

    txt = (msg.text or "").strip().lower()
    if txt in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await msg.answer("Ок, отменено.", reply_markup=admin_settings_menu())
        return

    data_gb: Optional[int]
    if txt in ("∞", "безлимит", "unlimited", "-", "null", "none"):
        data_gb = None
    else:
        if not txt.isdigit():
            await msg.answer("Нужно число (например 100) или '∞/безлимит/-'.")
            return
        data_gb = int(txt)
        if data_gb <= 0 or data_gb > 100_000:
            await msg.answer("Слишком странное значение (1..100000).")
            return

    await state.update_data(data_gb=data_gb)
    await state.set_state(AdminTariffCreate.waiting_active)

    await msg.answer(
        "Шаг 5/5 — сделать тариф активным?\nВыбери кнопкой:",
        parse_mode="HTML",
        reply_markup=confirm_tariff_create_active_kb(),
    )


@router.callback_query(F.data.startswith("admin:tariff:create_active:"))
async def admin_tariff_create_finish(call: CallbackQuery, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    active = int(call.data.split(":")[-1])
    data = await state.get_data()

    title = str(data.get("title") or "").strip()
    days = int(data.get("days") or 0)
    price = int(data.get("price") or 0)
    data_gb = data.get("data_gb", None)

    if not title or days <= 0:
        await state.clear()
        await call.answer("Данные потерялись, начни заново", show_alert=True)
        await _replace(call, "❌ Ошибка анкеты. Нажми «➕ Создать тариф» заново.", reply_markup=admin_tariffs_menu())
        return

    t = repo.create_tariff(title=title, days=days, price=price, data_gb=data_gb, is_active=active)
    await state.clear()

    await call.answer("Создал ✅")
    await _replace(call, "✅ Тариф создан.\n\n" + _render_tariff_card(repo, int(t.id)), reply_markup=tariff_actions_kb(int(t.id), int(t.is_active)))


@router.callback_query(F.data.startswith("admin:tariff:edit:"))
async def admin_tariff_edit_start(call: CallbackQuery, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    # admin:tariff:edit:<field>:<id>
    parts = call.data.split(":")
    field = parts[-2]
    tariff_id = int(parts[-1])

    t = repo.get_tariff(tariff_id)
    if not t:
        await call.answer("Тариф не найден", show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminTariffEdit.waiting_value)
    await state.update_data(tariff_id=tariff_id, field=field)

    cur_val = getattr(t, field, None) if field in ("title", "days", "price", "data_gb") else None
    cur_val_str = _fmt_data_gb(cur_val) if field == "data_gb" else str(cur_val)

    hint = {
        "title": "Отправь новое <b>название</b> тарифа.",
        "days": "Отправь новое <b>число дней</b> (целое).",
        "price": "Отправь новую <b>цену</b> (целое число).",
        "data_gb": "Отправь новый <b>лимит GB</b>: число или ∞/безлимит/- для безлимита.",
    }.get(field, "Отправь новое значение.")

    await _replace(
        call,
        f"✏️ <b>Редактирование тарифа #{tariff_id}</b>\n\n"
        f"<b>Поле:</b> <code>{field}</code>\n"
        f"<b>Текущее:</b> <code>{cur_val_str}</code>\n\n"
        f"{hint}\n\n"
        "Отмена: <code>отмена</code>",
        reply_markup=tariff_actions_kb(tariff_id, int(t.is_active)),
    )
    await call.answer()


@router.message(AdminTariffEdit.waiting_value)
async def admin_tariff_edit_apply(msg: Message, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(msg, admin_tg_id):
        return

    txt_raw = (msg.text or "").strip()
    if txt_raw.lower() in ("отмена", "cancel", "/cancel"):
        data = await state.get_data()
        await state.clear()
        tariff_id = int(data.get("tariff_id") or 0)
        t = repo.get_tariff(tariff_id)
        if t:
            await msg.answer("Ок, отменено.", reply_markup=tariff_actions_kb(tariff_id, int(t.is_active)))
        else:
            await msg.answer("Ок, отменено.", reply_markup=admin_settings_menu())
        return

    data = await state.get_data()
    tariff_id = int(data.get("tariff_id") or 0)
    field = str(data.get("field") or "")

    t = repo.get_tariff(tariff_id)
    if not t:
        await state.clear()
        await msg.answer("Тариф не найден.", reply_markup=admin_settings_menu())
        return

    # normalize + validate
    if field == "title":
        val = txt_raw.strip()
        if len(val) < 2:
            await msg.answer("Слишком коротко. Дай нормальное название.")
            return
        repo.update_tariff(tariff_id, title=val)

    elif field == "days":
        if not txt_raw.isdigit():
            await msg.answer("Нужно число дней.")
            return
        days = int(txt_raw)
        if days <= 0 or days > 3650:
            await msg.answer("Дай число 1..3650.")
            return
        repo.update_tariff(tariff_id, days=days)

    elif field == "price":
        if not txt_raw.isdigit():
            await msg.answer("Нужна цена числом.")
            return
        price = int(txt_raw)
        if price < 0 or price > 2_000_000:
            await msg.answer("Слишком большое/странное число.")
            return
        repo.update_tariff(tariff_id, price=price)

    elif field == "data_gb":
        s = txt_raw.strip().lower()
        if s in ("∞", "безлимит", "unlimited", "-", "null", "none"):
            repo.update_tariff(tariff_id, data_gb=None)
        else:
            if not s.isdigit():
                await msg.answer("Нужно число (например 100) или '∞/безлимит/-'.")
                return
            gb = int(s)
            if gb <= 0 or gb > 100_000:
                await msg.answer("Дай число 1..100000.")
                return
            repo.update_tariff(tariff_id, data_gb=gb)

    else:
        await msg.answer("Неизвестное поле.")
        return

    await state.clear()
    t2 = repo.get_tariff(tariff_id)
    if not t2:
        await msg.answer("Готово.", reply_markup=admin_settings_menu())
        return

    await msg.answer("✅ Обновил.\n\n" + _render_tariff_card(repo, tariff_id), reply_markup=tariff_actions_kb(tariff_id, int(t2.is_active)))


# ========= STATS =========

@router.callback_query(F.data == "admin:stats:day")
async def admin_stats_day(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    text = _stats_period(repo, "Статистика за 24 часа", _iso_since(timedelta(days=1)))
    await _replace(call, text, reply_markup=admin_stats_menu())
    await call.answer()


@router.callback_query(F.data == "admin:stats:week")
async def admin_stats_week(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    text = _stats_period(repo, "Статистика за 7 дней", _iso_since(timedelta(days=7)))
    await _replace(call, text, reply_markup=admin_stats_menu())
    await call.answer()


@router.callback_query(F.data == "admin:stats:month")
async def admin_stats_month(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return
    text = _stats_period(repo, "Статистика за 30 дней", _iso_since(timedelta(days=30)))
    await _replace(call, text, reply_markup=admin_stats_menu())
    await call.answer()


@router.callback_query(F.data == "admin:stats:queue")
async def admin_stats_queue(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    q = repo.list_paid_unissued(limit=20)
    if not q:
        await _replace(call, "🧾 <b>Очередь выдачи</b>\n\nОчередь пустая ✅", reply_markup=admin_stats_menu())
        await call.answer()
        return

    lines = ["🧾 <b>Очередь выдачи</b> (20):", ""]
    ids: List[int] = []
    for o in q:
        ids.append(int(o.id))
        lines.append(f"#{o.id} | tg:{o.tg_id} | {o.amount}₽ | <code>{o.status}</code> | {o.created_at}")

    await _replace(call, "\n".join(lines), reply_markup=_orders_list_kb(ids, back_cb="admin:stats_menu"))
    await call.answer()


@router.callback_query(F.data == "admin:stats:active_expired")
async def admin_stats_active_expired(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await call.answer("Считаю…")
    text = await _stats_active_expired(repo, limit_users=50)
    await _replace(call, text, reply_markup=admin_stats_menu())


# ========= USERS: SEARCH =========

@router.callback_query(F.data == "admin:user:search")
async def admin_user_search_start(call: CallbackQuery, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(AdminUserSearch.waiting_query)
    await _replace(
        call,
        "🔎 <b>Найти пользователя</b>\n\n"
        "Отправь одним сообщением:\n"
        "• <code>@username</code>\n"
        "• <code>tg_id</code> (число)\n"
        "• <code>tg_123...</code>\n\n"
        "Чтобы отменить — отправь <code>отмена</code>.",
        reply_markup=admin_users_menu(),
    )
    await call.answer()


@router.message(AdminUserSearch.waiting_query)
async def admin_user_search_query(msg: Message, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(msg, admin_tg_id):
        return

    q = _parse_search_query(msg.text or "")
    if not q:
        await msg.answer("Напиши запрос: @username / tg_id / tg_123... или 'отмена'.")
        return

    if q.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await msg.answer("Ок, отменено.", reply_markup=admin_users_menu())
        return

    u = _find_user(repo, q)
    await state.clear()

    if not u:
        await msg.answer("❌ Не нашёл пользователя в базе.", reply_markup=admin_users_menu())
        return

    text, _ = await _render_user_profile(repo, u.tg_id)
    await msg.answer(text, reply_markup=user_profile_actions(u.tg_id), disable_web_page_preview=True)


@router.callback_query(F.data == "admin:users:last")
async def admin_users_last(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    users = repo.list_recent_users(limit=15)
    if not users:
        await _replace(call, "👥 Пользователи: пока пусто.", reply_markup=admin_users_menu())
        await call.answer()
        return

    lines = ["👥 <b>Последние пользователи</b> (15):", ""]
    for u in users:
        uname = f"@{u.tg_username}" if u.tg_username else "(без username)"
        lines.append(f"• <code>{u.tg_id}</code> | {uname} | <code>{u.marzban_username}</code>")
    lines.append("\nНажми «🔎 Найти пользователя», чтобы открыть профиль.")

    await _replace(call, "\n".join(lines), reply_markup=admin_users_menu())
    await call.answer()


# ========= USER PROFILE + ACTIONS =========

@router.callback_query(F.data.startswith("admin:user:profile:"))
async def admin_user_profile(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(call.data.split(":")[-1])
    text, _ = await _render_user_profile(repo, tg_id)
    await _replace(call, text, reply_markup=user_profile_actions(tg_id))
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:add_days:"))
async def admin_user_add_days(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    parts = call.data.split(":")
    tg_id = int(parts[-2])
    days = int(parts[-1])

    u = repo.get_user(tg_id)
    if not u:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    m = _build_marzban()
    try:
        _ = await m.ensure_user_and_get_access_url(u.marzban_username, days=days)
        try:
            repo.log("admin_add_days", admin_tg_id, {"tg_id": tg_id, "days": days})
        except Exception:
            pass
    except Exception as e:
        await call.answer("Ошибка продления", show_alert=True)
        await _replace(call, f"❌ Ошибка продления: <code>{str(e)[:700]}</code>", reply_markup=user_profile_actions(tg_id))
        return

    text, _ = await _render_user_profile(repo, tg_id)
    await _replace(call, f"✅ Продлил на {days} дней.\n\n{text}", reply_markup=user_profile_actions(tg_id))
    await call.answer()


async def _set_user_status(username: str, status: str) -> None:
    """
    status: "active" or "disabled"
    """
    m = _build_marzban()
    if status == "active":
        await m.ensure_user_and_get_access_url(username, days=0)
        return

    # disabled
    try:
        await m.update_user(username, {"status": "disabled"})
        return
    except Exception:
        # fallback
        cur = await m.get_user(username)
        if not isinstance(cur, dict):
            raise

        payload = {"status": "disabled"}
        if isinstance(cur.get("expire"), (int, float)):
            payload["expire"] = int(cur["expire"])

        inbounds = cur.get("inbounds")
        proxies = cur.get("proxies")

        if not isinstance(inbounds, dict) or not isinstance(proxies, dict):
            inbounds_map, proxies_template = await m._get_defaults()  # type: ignore[attr-defined]
            if not isinstance(inbounds, dict):
                inbounds = inbounds_map
            if not isinstance(proxies, dict):
                proxies = proxies_template

        payload["inbounds"] = inbounds
        payload["proxies"] = proxies

        await m.update_user(username, payload)


@router.callback_query(F.data.startswith("admin:user:block:"))
async def admin_user_block(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(call.data.split(":")[-1])
    u = repo.get_user(tg_id)
    if not u:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    try:
        await _set_user_status(u.marzban_username, "disabled")
        try:
            repo.log("admin_block_user", admin_tg_id, {"tg_id": tg_id})
        except Exception:
            pass
    except Exception as e:
        await call.answer("Ошибка блокировки", show_alert=True)
        await _replace(call, f"❌ Ошибка блокировки: <code>{str(e)[:700]}</code>", reply_markup=user_profile_actions(tg_id))
        return

    text, _ = await _render_user_profile(repo, tg_id)
    await _replace(call, f"⛔️ Заблокирован.\n\n{text}", reply_markup=user_profile_actions(tg_id))
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:unblock:"))
async def admin_user_unblock(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(call.data.split(":")[-1])
    u = repo.get_user(tg_id)
    if not u:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    try:
        await _set_user_status(u.marzban_username, "active")
        try:
            repo.log("admin_unblock_user", admin_tg_id, {"tg_id": tg_id})
        except Exception:
            pass
    except Exception as e:
        await call.answer("Ошибка разблокировки", show_alert=True)
        await _replace(call, f"❌ Ошибка разблокировки: <code>{str(e)[:700]}</code>", reply_markup=user_profile_actions(tg_id))
        return

    text, _ = await _render_user_profile(repo, tg_id)
    await _replace(call, f"✅ Разблокирован.\n\n{text}", reply_markup=user_profile_actions(tg_id))
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:link:"))
async def admin_user_link(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(call.data.split(":")[-1])
    u = repo.get_user(tg_id)
    if not u:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    last = repo.get_last_valid_issued_order_for_user(tg_id)
    link = _abs_url(last.access_url) if (last and last.access_url) else None

    if not link:
        try:
            _, _, sub_url = await _get_marzban_status(u.marzban_username)
            link = sub_url
        except Exception:
            link = None

    if not link:
        await call.answer("Ссылка не найдена", show_alert=True)
        return

    await call.answer("Готово")
    await _replace(call, f"🔗 Ссылка подписки:\n{link}", reply_markup=user_profile_actions(tg_id))


@router.callback_query(F.data.startswith("admin:user:orders:"))
async def admin_user_orders(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(call.data.split(":")[-1])
    u = repo.get_user(tg_id)
    if not u:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    orders = repo.list_orders_for_user(tg_id, limit=20)
    if not orders:
        await _replace(call, "🧾 Заказы: пусто.", reply_markup=user_profile_actions(tg_id))
        await call.answer()
        return

    lines = [f"🧾 <b>Заказы пользователя</b> <code>{tg_id}</code>:", ""]
    for o in orders:
        lines.append(f"#{o.id} | {o.amount}₽ | {o.status} | {o.created_at}")
    await _replace(call, "\n".join(lines), reply_markup=user_profile_actions(tg_id))
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:rotate:"))
async def admin_user_rotate(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(call.data.split(":")[-1])
    u = repo.get_user(tg_id)
    if not u:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    await call.answer("Подтверди")
    await _replace(
        call,
        "⚠️ <b>Перевыпуск ключа</b>\n\n"
        "Это действие пересоздаст пользователя в Marzban и обновит ссылку подписки.\n"
        "Срок действия сохранится.\n\n"
        f"<b>tg_id:</b> <code>{tg_id}</code>\n"
        f"<b>marzban_username:</b> <code>{u.marzban_username}</code>\n",
        reply_markup=confirm_rotate_kb(tg_id),
    )


@router.callback_query(F.data.startswith("admin:user:rotate_confirm:"))
async def admin_user_rotate_confirm(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    tg_id = int(call.data.split(":")[-1])
    u = repo.get_user(tg_id)
    if not u:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    await call.answer("Делаю…")
    m = _build_marzban()
    try:
        new_url_raw = await m.rotate_user(u.marzban_username)
        new_url = _abs_url(new_url_raw)

        try:
            repo.log("admin_rotate_user", admin_tg_id, {"tg_id": tg_id, "marzban_username": u.marzban_username})
        except Exception:
            pass

        text, _ = await _render_user_profile(repo, tg_id)
        await _replace(
            call,
            "✅ <b>Ключ перевыпущен</b>\n\n"
            f"🔗 <b>Новая ссылка:</b>\n{new_url}\n\n"
            + text,
            reply_markup=user_profile_actions(tg_id),
        )

        # уведомим пользователя (если не получится — не критично)
        try:
            await call.bot.send_message(
                chat_id=tg_id,
                text=f"♻️ Доступ обновлён.\n\n🔗 Новая ссылка: {new_url}",
                reply_markup=back_to_menu(),
                disable_web_page_preview=True,
            )
        except Exception:
            pass

    except Exception as e:
        await call.answer("Ошибка", show_alert=True)
        await _replace(
            call,
            f"❌ Ошибка перевыпуска: <code>{str(e)[:800]}</code>",
            reply_markup=user_profile_actions(tg_id),
        )

# ========= ORDERS: SEARCH =========

@router.callback_query(F.data == "admin:order:search")
async def admin_order_search_start(call: CallbackQuery, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(AdminOrderSearch.waiting_query)
    await _replace(
        call,
        "🔎 <b>Найти заказ</b>\n\n"
        "Отправь одним сообщением:\n"
        "• <code>order_id</code> (число)\n"
        "• или <code>payment_id</code> (строка)\n\n"
        "Чтобы отменить — отправь <code>отмена</code>.",
        reply_markup=admin_orders_menu(),
    )
    await call.answer()


@router.message(AdminOrderSearch.waiting_query)
async def admin_order_search_query(msg: Message, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(msg, admin_tg_id):
        return

    q = (msg.text or "").strip()
    if not q:
        await msg.answer("Напиши запрос: order_id (число) или payment_id. Или 'отмена'.")
        return

    if q.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await msg.answer("Ок, отменено.", reply_markup=admin_orders_menu())
        return

    # by order_id
    if q.isdigit():
        oid = int(q)
        await state.clear()
        card = _render_order_card(repo, oid)
        if "❌" in card:
            await msg.answer("❌ Заказ не найден.", reply_markup=admin_orders_menu())
            return
        await msg.answer(card, reply_markup=order_actions(oid), disable_web_page_preview=True)
        return

    # by payment_id
    payment_id = q
    with db_session() as db:
        row = db.execute("SELECT id FROM orders WHERE payment_id=? ORDER BY id DESC LIMIT 1", (payment_id,)).fetchone()
    await state.clear()
    if not row:
        await msg.answer("❌ Заказ по payment_id не найден.", reply_markup=admin_orders_menu())
        return

    oid = int(row["id"])
    await msg.answer(_render_order_card(repo, oid), reply_markup=order_actions(oid), disable_web_page_preview=True)


# ========= ORDERS MENU (NEW) =========

@router.callback_query(F.data == "admin:orders:last_paid")
async def admin_orders_last_paid(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    ids = _fetch_order_ids("status IN ('PAID','APPROVED','ISSUED')", (), limit=20)
    if not ids:
        await _replace(call, "📌 Последние оплаты: пока пусто.", reply_markup=admin_orders_menu())
        await call.answer()
        return

    lines = ["📌 <b>Последние оплаты</b> (20):", ""]
    for oid in ids:
        o = repo.get_order(oid)
        if not o:
            continue
        lines.append(f"#{o.id} | tg:{o.tg_id} | {o.amount}₽ | <code>{o.status}</code> | {o.created_at}")

    await _replace(call, "\n".join(lines), reply_markup=_orders_list_kb(ids, back_cb="admin:orders_menu"))
    await call.answer()


@router.callback_query(F.data == "admin:orders:wait_payment")
async def admin_orders_wait_payment(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    ids = _fetch_order_ids("status='WAIT_PAYMENT'", (), limit=20)
    if not ids:
        await _replace(call, "⏳ WAIT_PAYMENT: нет активных.", reply_markup=admin_orders_menu())
        await call.answer()
        return

    lines = ["⏳ <b>Ожидают оплату</b> (20):", ""]
    for oid in ids:
        o = repo.get_order(oid)
        if not o:
            continue
        lines.append(f"#{o.id} | tg:{o.tg_id} | {o.amount}₽ | {o.created_at}")

    await _replace(call, "\n".join(lines), reply_markup=_orders_list_kb(ids, back_cb="admin:orders_menu"))
    await call.answer()


@router.callback_query(F.data == "admin:orders:errors")
async def admin_orders_errors(call: CallbackQuery, admin_tg_id: int, repo: Repo) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    ids = _fetch_order_ids("status='ERROR'", (), limit=20)
    if not ids:
        await _replace(call, "⚠️ Ошибки выдачи: пусто.", reply_markup=admin_orders_menu())
        await call.answer()
        return

    lines = ["⚠️ <b>Ошибки выдачи</b> (20):", ""]
    for oid in ids:
        o = repo.get_order(oid)
        if not o:
            continue
        et = (o.error_text or "-")[:120]
        lines.append(f"#{o.id} | tg:{o.tg_id} | {o.amount}₽ | {o.created_at}\n  <code>{et}</code>")

    await _replace(call, "\n".join(lines), reply_markup=_orders_list_kb(ids, back_cb="admin:orders_menu"))
    await call.answer()


@router.callback_query(F.data == "admin:orders:manual_paid")
async def admin_orders_manual_paid_start(call: CallbackQuery, admin_tg_id: int, state: FSMContext) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(AdminManualPaid.waiting_order_id)
    await _replace(
        call,
        "🧰 <b>Ручная отметка оплаты</b>\n\n"
        "Отправь <b>order_id</b> числом.\n"
        "Например: <code>123</code>\n\n"
        "Чтобы отменить — отправь <code>отмена</code>.",
        reply_markup=admin_orders_menu(),
    )
    await call.answer()


@router.message(AdminManualPaid.waiting_order_id)
async def admin_orders_manual_paid_apply(msg: Message, admin_tg_id: int, repo: Repo, state: FSMContext) -> None:
    if not _is_admin(msg, admin_tg_id):
        return

    txt = (msg.text or "").strip()
    if txt.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await msg.answer("Ок, отменено.", reply_markup=admin_orders_menu())
        return

    if not txt.isdigit():
        await msg.answer("Нужно число (order_id). Или отправь 'отмена'.")
        return

    oid = int(txt)
    o = repo.get_order(oid)
    if not o:
        await msg.answer("❌ Заказ не найден.", reply_markup=admin_orders_menu())
        return

    repo.mark_paid(order_id=oid, payment_id=o.payment_id)
    try:
        repo.log("admin_manual_paid", admin_tg_id, {"order_id": oid})
    except Exception:
        pass

    await state.clear()
    card = _render_order_card(repo, oid)
    await msg.answer(
        "✅ Отметил как <code>PAID</code>.\n\n" + card,
        reply_markup=order_actions(oid),
        disable_web_page_preview=True,
    )


# ========= ORDER ACTIONS (NEW) =========

@router.callback_query(F.data.startswith("admin:order:mark_paid_confirm:"))
async def admin_order_mark_paid_confirm(call: CallbackQuery, repo: Repo, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    order_id = int(call.data.split(":")[-1])
    o = repo.get_order(order_id)
    if not o:
        await call.answer("Заказ не найден", show_alert=True)
        return

    repo.mark_paid(order_id=order_id, payment_id=o.payment_id)
    try:
        repo.log("admin_mark_paid", admin_tg_id, {"order_id": order_id})
    except Exception:
        pass

    await call.answer("OK")
    await _replace(
        call,
        "✅ Отмечено как <code>PAID</code>\n\n" + _render_order_card(repo, order_id),
        reply_markup=order_actions(order_id),
    )


@router.callback_query(F.data.startswith("admin:order:mark_paid:"))
async def admin_order_mark_paid(call: CallbackQuery, repo: Repo, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    order_id = int(call.data.split(":")[-1])
    o = repo.get_order(order_id)
    if not o:
        await call.answer("Заказ не найден", show_alert=True)
        return

    await call.answer("Подтверди")
    await _replace(
        call,
        "⚠️ Подтверди отметку оплаты (PAID):\n\n" + _render_order_card(repo, order_id),
        reply_markup=confirm_mark_paid_kb(order_id),
    )


@router.callback_query(F.data.startswith("admin:order:reissue_confirm:"))
async def admin_order_reissue_confirm(call: CallbackQuery, repo: Repo, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    order_id = int(call.data.split(":")[-1])
    o = repo.get_order(order_id)
    if not o:
        await call.answer("Заказ не найден", show_alert=True)
        return

    if o.status not in ("PAID", "APPROVED", "ISSUED"):
        await call.answer(f"Нельзя перевыдать при статусе {o.status}", show_alert=True)
        return

    tariff = repo.get_tariff(int(o.tariff_id))
    if not tariff:
        await call.answer("Тариф не найден", show_alert=True)
        return

    # ensure user
    user = repo.ensure_user(tg_id=int(o.tg_id), tg_username=None)
    username = user.marzban_username

    m = _build_marzban()
    try:
        access_url_raw = await m.ensure_user_and_get_access_url(username=username, days=int(tariff.days))
        access_url = _abs_url(access_url_raw)
        repo.issue_access(order_id=int(o.id), access_url=access_url)

        try:
            repo.log("admin_reissue", admin_tg_id, {"order_id": int(o.id), "tg_id": int(o.tg_id)})
        except Exception:
            pass

        # уведомим пользователя
        try:
            await call.bot.send_message(
                chat_id=int(o.tg_id),
                text=f"✅ Доступ обновлён.\n\n🔗 Ссылка: {access_url}",
                reply_markup=back_to_menu(),
                disable_web_page_preview=True,
            )
        except Exception:
            pass

    except Exception as e:
        await call.answer("Ошибка перевыдачи", show_alert=True)
        repo.set_order_status(
            order_id=int(o.id),
            status="ERROR",
            admin_tg_id=None,
            error_text=f"REISSUE_ERROR: {type(e).__name__}: {e}",
            set_decided_at=True,
        )
        await _replace(call, f"❌ Ошибка перевыдачи: <code>{str(e)[:700]}</code>", reply_markup=order_actions(order_id))
        return

    await call.answer("Готово")
    await _replace(call, "✅ Перевыдача выполнена.\n\n" + _render_order_card(repo, order_id), reply_markup=order_actions(order_id))


@router.callback_query(F.data.startswith("admin:order:reissue:"))
async def admin_order_reissue(call: CallbackQuery, repo: Repo, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    order_id = int(call.data.split(":")[-1])
    o = repo.get_order(order_id)
    if not o:
        await call.answer("Заказ не найден", show_alert=True)
        return

    if o.status not in ("PAID", "APPROVED", "ISSUED"):
        await call.answer(f"Нельзя перевыдать при статусе {o.status}", show_alert=True)
        return

    await call.answer("Подтверди")
    await _replace(call, "⚠️ Подтверди перевыдачу доступа:\n\n" + _render_order_card(repo, order_id), reply_markup=confirm_reissue_kb(order_id))


# ========= LEGACY (оставляем чтобы ничего не отвалилось) =========

@router.callback_query(F.data == "admin:orders")
async def admin_orders(call: CallbackQuery, repo: Repo, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    orders = repo.list_recent_orders(limit=10)
    if not orders:
        text = "📦 Заказы: пока пусто."
    else:
        lines = ["📦 Последние заказы (10):", ""]
        for o in orders:
            lines.append(f"#{o.id} | tg:{o.tg_id} | {o.amount}₽ | {o.status} | {o.created_at}")
        text = "\n".join(lines)

    await _replace(call, text, reply_markup=admin_orders_menu())
    await call.answer()


@router.callback_query(F.data == "admin:users")
async def admin_users(call: CallbackQuery, repo: Repo, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    users = repo.list_recent_users(limit=10)
    if not users:
        text = "👥 Пользователи: пока пусто."
    else:
        lines = ["👥 Последние пользователи (10):", ""]
        for u in users:
            uname = f"@{u.tg_username}" if u.tg_username else "(без username)"
            lines.append(f"tg:{u.tg_id} | {uname} | {u.marzban_username} | {u.created_at}")
        text = "\n".join(lines)

    await _replace(call, text, reply_markup=admin_users_menu())
    await call.answer()


# ========= ORDERS actions (старый блок, оставляем как есть) =========

@router.callback_query(F.data.startswith("admin:order:approve:"))
async def admin_order_approve(call: CallbackQuery, repo: Repo, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    order_id = int(call.data.split(":")[-1])
    order = repo.get_order(order_id)
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order.status != "WAIT_CONFIRM":
        await call.answer(f"Заказ уже обработан: {order.status}", show_alert=True)
        return

    repo.set_order_status(order_id, "APPROVED", admin_tg_id=admin_tg_id)
    try:
        repo.log("order_approved", admin_tg_id, {"order_id": order_id})
    except Exception:
        pass

    await _replace(call, f"✅ Заказ #{order_id} подтверждён.", reply_markup=admin_menu())
    await call.answer()

    await call.bot.send_message(
        chat_id=order.tg_id,
        text="✅ Заказ подтверждён! Скоро выдам доступ.",
        reply_markup=back_to_menu(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("admin:order:reject:"))
async def admin_order_reject(call: CallbackQuery, repo: Repo, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    order_id = int(call.data.split(":")[-1])
    order = repo.get_order(order_id)
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order.status != "WAIT_CONFIRM":
        await call.answer(f"Заказ уже обработан: {order.status}", show_alert=True)
        return

    repo.set_order_status(order_id, "REJECTED", admin_tg_id=admin_tg_id)
    try:
        repo.log("order_rejected", admin_tg_id, {"order_id": order_id})
    except Exception:
        pass

    await _replace(call, f"❌ Заказ #{order_id} отклонён.", reply_markup=admin_menu())
    await call.answer()

    await call.bot.send_message(
        chat_id=order.tg_id,
        text="❌ Заказ отклонён. Если это ошибка — напиши в поддержку.",
        reply_markup=back_to_menu(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("admin:order:refresh:"))
async def admin_order_refresh(call: CallbackQuery, repo: Repo, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        await call.answer("Нет доступа", show_alert=True)
        return

    order_id = int(call.data.split(":")[-1])
    await _replace(call, _render_order_card(repo, order_id), reply_markup=order_actions(order_id))
    await call.answer()


# ========= FALLBACK (чтобы не было "not handled" для админских коллбэков) =========

@router.callback_query(F.data.startswith("admin:"))
async def admin_unknown_callback(call: CallbackQuery, admin_tg_id: int) -> None:
    if not _is_admin(call, admin_tg_id):
        return
    await call.answer("Кнопка пока не подключена.", show_alert=True)
