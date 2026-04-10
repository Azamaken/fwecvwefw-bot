import asyncio
import html
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import quote

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ButtonStyle, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8379607939:AAEXgHunA820-9b1cQxzUstc-e3FCVeKpZw")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@testbostsr")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "nolyktg")  # without @
DEFAULT_PRODUCT_PHOTO_URL = os.getenv(
    "DEFAULT_PRODUCT_PHOTO_URL",
    "https://i.postimg.cc/pVzRd8jv/image.png",
)
DEFAULT_DELIVERY_TEXT = os.getenv("DEFAULT_DELIVERY_TEXT", "3-12 дней")
DEFAULT_PAYMENT_TEXT = os.getenv(
    "DEFAULT_PAYMENT_TEXT",
    "Click | Payme | Перевод по карте",
)
ORDER_BUTTON_TEXT = os.getenv("ORDER_BUTTON_TEXT", "🛒 Заказать")
VIEW_BUTTON_TEXT = os.getenv("VIEW_BUTTON_TEXT", "📸 Смотреть фото")

# Укажи свои эмодзи тут напрямую.
# Премиум эмодзи нужно задавать именно HTML-тегом <tg-emoji ...>...</tg-emoji>
PRICE_EMOJI = '<tg-emoji emoji-id="5409048419211682843">💵</tg-emoji>'
DELIVERY_EMOJI = '<tg-emoji emoji-id="5253742260054409879">✉️</tg-emoji>'
SIZE_EMOJI = '📐'
PAYMENT_EMOJI = '<tg-emoji emoji-id="5472250091332993630">💳</tg-emoji>'

ADMIN_IDS = {
    int(x)
    for x in os.getenv("ADMIN_IDS", "7528568061").replace(" ", "").split(",")
    if x.strip().isdigit()
}

PRODUCT_GALLERIES: Dict[str, dict] = {}
BOT_USERNAME: str = ""

logging.basicConfig(level=logging.INFO)
router = Router()


@dataclass
class DraftPost:
    title: str
    price: str
    sizes: str
    photo_url: str
    extra_photos: List[str]
    delivery: str = DEFAULT_DELIVERY_TEXT
    payment: str = DEFAULT_PAYMENT_TEXT


class CreatePost(StatesGroup):
    waiting_for_extra_photos = State()
    waiting_for_title = State()
    waiting_for_price = State()
    waiting_for_sizes = State()
    waiting_for_photo_url = State()
    waiting_for_confirm = State()


class GalleryStates(StatesGroup):
    viewing = State()


# =========================
# HELPERS
# =========================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def safe_text(text: str) -> str:
    return html.escape(text.strip())


def build_post_text(data: DraftPost) -> str:
    return (
        f"<b><i>{safe_text(data.title)}</i></b>n\n"
        f"━━━━━━━━━━━━━━━━━━━━"
        f"{PRICE_EMOJI} <b>Цена:</b> {safe_text(data.price)}\n"
        f"{DELIVERY_EMOJI} <b>Доставка:</b> {safe_text(data.delivery)}\n"
        f"{SIZE_EMOJI} <b>Размеры:</b> {safe_text(data.sizes)}\n"
        f"{PAYMENT_EMOJI} <b>Оплата:</b> {safe_text(data.payment)}\n"
    )


def build_order_url(title: str) -> str:
    message = (
        f"Привет, хочу заказать {title}. "
        f"Хотел бы уточнить размеры, наличие и детали по доставке."
    )
    return f"https://t.me/{OWNER_USERNAME.lstrip('@')}?text={quote(message)}"


def generate_gallery_token() -> str:
    return secrets.token_urlsafe(18).replace("-", "").replace("_", "")


def build_main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🛍 Создать пост",
            callback_data="menu_newpost",
            style=ButtonStyle.PRIMARY,
        )
    )
    return builder.as_markup()


def build_extra_photos_kb(can_finish: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_finish:
        builder.row(
            InlineKeyboardButton(
                text="✅ Готово",
                callback_data="extra_done",
                style=ButtonStyle.SUCCESS,
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⏭ Без доп. фото",
            callback_data="extra_skip",
            style=ButtonStyle.PRIMARY,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="cancel_post",
            style=ButtonStyle.DANGER,
        )
    )
    return builder.as_markup()


def build_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🚀 Опубликовать",
            callback_data="publish_post",
            style=ButtonStyle.SUCCESS,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Начать заново",
            callback_data="restart_post",
            style=ButtonStyle.PRIMARY,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="cancel_post",
            style=ButtonStyle.DANGER,
        )
    )
    return builder.as_markup()


def build_post_kb(title: str, gallery_token: Optional[str]) -> InlineKeyboardMarkup:
    bot_target = BOT_USERNAME.lstrip("@")
    buttons: List[List[InlineKeyboardButton]] = []

    if gallery_token:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=VIEW_BUTTON_TEXT,
                    url=f"https://t.me/{bot_target}?start={gallery_token}",
                    style=ButtonStyle.PRIMARY,
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text=ORDER_BUTTON_TEXT,
                url=build_order_url(title),
                style=ButtonStyle.PRIMARY,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_gallery_kb(title: str, gallery_token: str, index: int, total: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []

    if total > 1:
        nav_row: List[InlineKeyboardButton] = []
        if index > 0:
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"gallery:{gallery_token}:{index - 1}",
                    style=ButtonStyle.PRIMARY,
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                text=f"{index + 1}/{total}",
                callback_data="gallery_info",
            )
        )
        if index < total - 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"gallery:{gallery_token}:{index + 1}",
                    style=ButtonStyle.PRIMARY,
                )
            )
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=ORDER_BUTTON_TEXT,
                url=build_order_url(title),
                style=ButtonStyle.PRIMARY,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_draft(state: FSMContext) -> DraftPost:
    data = await state.get_data()
    return DraftPost(
        title=data.get("title", ""),
        price=data.get("price", ""),
        sizes=data.get("sizes", ""),
        photo_url=data.get("photo_url", DEFAULT_PRODUCT_PHOTO_URL),
        extra_photos=data.get("extra_photos", []),
    )


async def open_gallery_message(message: Message, gallery_token: str, state: FSMContext):
    gallery = PRODUCT_GALLERIES.get(gallery_token)
    if not gallery:
        await message.answer("Галерея не найдена или устарела.")
        return

    await state.set_state(GalleryStates.viewing)
    await state.update_data(gallery_token=gallery_token, gallery_index=0)

    await message.answer_photo(
        photo=gallery["photos"][0],
        caption=f"<b>{safe_text(gallery['title'])}</b>",
        reply_markup=build_gallery_kb(gallery["title"], gallery_token, 0, len(gallery["photos"])),
    )


# =========================
# START / MENU
# =========================
@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    args = ""
    if message.text and len(message.text.split(maxsplit=1)) > 1:
        args = message.text.split(maxsplit=1)[1].strip()

    if args:
        await open_gallery_message(message, args, state)
        return

    if not is_admin(message.from_user.id):
        await message.answer(
            "Привет! 👋\n\n"
            "Бот     предназначен для создания и управления товарными постами в канале.\n"
            f"Подпишись на наш канал: {CHANNEL_ID}\n\n"
            "Если у вас есть вопросы, свяжитесь с администратором."
        )
        return

    await state.clear()
    await message.answer(
        "Привет. Это панель оформления товарных постов. Всё управление через inline-кнопки ниже.",
        reply_markup=build_main_menu_kb(),
    )


@router.callback_query(F.data == "menu_newpost")
async def newpost_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await state.update_data(extra_photos=[])
    await state.set_state(CreatePost.waiting_for_extra_photos)
    await callback.message.answer(
        "Скиньте до 4 дополнительных фото товара."
        "Главная картинка для поста будет отдельной прямой ссылкой."
        "Если доп. фото не нужны — нажмите «Без доп. фото»."
        "Когда закончите — нажмите «Готово».",
        reply_markup=build_extra_photos_kb(can_finish=False),
    )
    await callback.answer()


# =========================
# CREATE POST FLOW
# =========================
@router.message(CreatePost.waiting_for_extra_photos, F.photo)
async def collect_extra_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("extra_photos", [])

    if len(photos) >= 4:
        await message.answer("Максимум 4 дополнительных фото. Нажмите «Готово».")
        return

    photos.append(message.photo[-1].file_id)
    await state.update_data(extra_photos=photos)
    await message.answer(
        f"Доп. фото добавлено: {len(photos)}/4",
        reply_markup=build_extra_photos_kb(can_finish=True),
    )


@router.callback_query(F.data == "extra_skip")
async def skip_extra_photos(callback: CallbackQuery, state: FSMContext):
    await state.update_data(extra_photos=[])
    await state.set_state(CreatePost.waiting_for_title)
    await callback.message.answer("Теперь отправьте название товара.")
    await callback.answer()


@router.callback_query(F.data == "extra_done")
async def done_extra_photos(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get("extra_photos", [])
    if not photos:
        await callback.answer("Сначала добавьте хотя бы 1 фото или нажмите «Без доп. фото»", show_alert=True)
        return

    await state.set_state(CreatePost.waiting_for_title)
    await callback.message.answer("Теперь отправьте название товара.")
    await callback.answer()


@router.message(CreatePost.waiting_for_title, F.text)
async def get_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(CreatePost.waiting_for_price)
    await message.answer("Теперь отправьте цену. Пример: 777.000")


@router.message(CreatePost.waiting_for_price, F.text)
async def get_price(message: Message, state: FSMContext):
    await state.update_data(price=message.text.strip())
    await state.set_state(CreatePost.waiting_for_sizes)
    await message.answer("Теперь отправьте размеры. Пример: 44, 45")


@router.message(CreatePost.waiting_for_sizes, F.text)
async def get_sizes(message: Message, state: FSMContext):
    await state.update_data(sizes=message.text.strip())
    await state.set_state(CreatePost.waiting_for_photo_url)
    await message.answer(
        "Теперь отправьте прямую ссылку на главную фото для поста."
        "Если хотите оставить дефолтную, отправьте минус: -"
        f"Сейчас дефолт: <code>{html.escape(DEFAULT_PRODUCT_PHOTO_URL)}</code>"
    )


@router.message(CreatePost.waiting_for_photo_url, F.text)
async def get_photo_url(message: Message, state: FSMContext):
    text = message.text.strip()
    photo_url = DEFAULT_PRODUCT_PHOTO_URL if text == "-" else text
    await state.update_data(photo_url=photo_url)

    draft = await get_draft(state)
    gallery_photos = draft.extra_photos.copy()
    gallery_token: Optional[str] = None

    if gallery_photos:
        gallery_token = generate_gallery_token()
        PRODUCT_GALLERIES[gallery_token] = {
            "title": draft.title,
            "photos": gallery_photos,
        }
        await state.update_data(gallery_token=gallery_token)

    await state.set_state(CreatePost.waiting_for_confirm)
    await message.answer_photo(
        photo=draft.photo_url,
        caption=build_post_text(draft),
        reply_markup=build_post_kb(draft.title, gallery_token),
    )
    await message.answer("Проверьте предпросмотр.", reply_markup=build_confirm_kb())


# =========================
# PREVIEW / PUBLISH / CANCEL
# =========================
@router.callback_query(F.data == "publish_post")
async def publish_post(callback: CallbackQuery, bot: Bot, state: FSMContext):
    draft = await get_draft(state)
    data = await state.get_data()
    gallery_token = data.get("gallery_token")

    if not draft.title or not draft.photo_url:
        await callback.answer("Черновик повреждён", show_alert=True)
        return

    try:
        await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=draft.photo_url,
            caption=build_post_text(draft),
            reply_markup=build_post_kb(draft.title, gallery_token),
        )
        await state.clear()
        await callback.message.answer("✅ Пост опубликован в канал.", reply_markup=build_main_menu_kb())
        await callback.answer()
    except TelegramBadRequest as e:
        await callback.message.answer(f"❌ Ошибка Telegram: {e}")
        await callback.answer()
    except Exception as e:
        await callback.message.answer(f"❌ Не удалось опубликовать пост: {e}")
        await callback.answer()


@router.callback_query(F.data == "restart_post")
async def restart_post(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(extra_photos=[])
    await state.set_state(CreatePost.waiting_for_extra_photos)
    await callback.message.answer(
        "Начинаем заново. Скиньте до 4 дополнительных фото товара.",
        reply_markup=build_extra_photos_kb(can_finish=False),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_post")
async def cancel_post(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Создание поста отменено.", reply_markup=build_main_menu_kb())
    await callback.answer()


# =========================
# GALLERY
# =========================
@router.callback_query(F.data == "gallery_info")
async def gallery_info(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("gallery:"))
async def gallery_page(callback: CallbackQuery, state: FSMContext):
    try:
        _, gallery_token, raw_index = callback.data.split(":", maxsplit=2)
        new_index = int(raw_index)
    except Exception:
        await callback.answer("Ошибка галереи", show_alert=True)
        return

    gallery = PRODUCT_GALLERIES.get(gallery_token)
    if not gallery:
        await callback.answer("Галерея не найдена", show_alert=True)
        return

    photos = gallery["photos"]
    if new_index < 0 or new_index >= len(photos):
        await callback.answer()
        return

    await state.set_state(GalleryStates.viewing)
    await state.update_data(gallery_token=gallery_token, gallery_index=new_index)

    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=photos[new_index],
            caption=f"<b>{safe_text(gallery['title'])}</b>",
            parse_mode="HTML",
        ),
        reply_markup=build_gallery_kb(gallery["title"], gallery_token, new_index, len(photos)),
    )
    await callback.answer()


# =========================
# FALLBACKS
# =========================
@router.message(CreatePost.waiting_for_extra_photos)
async def wrong_extra_photos(message: Message):
    await message.answer("На этом шаге отправьте фото или нажмите кнопку ниже.")


@router.message()
async def fallback(message: Message):
    if is_admin(message.from_user.id):
        await message.answer("Используйте меню ниже.", reply_markup=build_main_menu_kb())
    else:
        await message.answer("Доступа к панели нет.")


# =========================
# MAIN
# =========================
async def main() -> None:
    if BOT_TOKEN == "PUT_BOT_TOKEN_HERE":
        raise RuntimeError("Укажите BOT_TOKEN в переменных окружения")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    global BOT_USERNAME
    BOT_USERNAME = (me.username or "").lstrip("@")

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
