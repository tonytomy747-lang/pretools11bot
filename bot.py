"""
Telegram Digital Shop Bot — USDT (manual verification).

Free to run: Telegram hosts your product images (we only store file_id),
Postgres/SQLite holds the data, and payment verification is done by you.
"""

import asyncio
import html
import io
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import qrcode

# Load a local .env when present (handy for running on your own machine).
# Must happen before `import db`, which reads DATABASE_URL at import time.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("shop")

# ----------------------------------------------------------------- config
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
}
SHOP_NAME = os.getenv("SHOP_NAME", "Digital Shop")
CURRENCY = os.getenv("CURRENCY", "USDT")
PORT = int(os.getenv("PORT", "8080"))
SEED_ON_BOOT = os.getenv("SEED_ON_BOOT", "true").strip().lower() not in (
    "0", "false", "no", "off",
)

PAGE_SIZE = 6
ADMIN_PAGE_SIZE = 8
PAYMENT_EXPIRY_MINUTES = int(os.getenv("PAYMENT_EXPIRY_MINUTES", "30"))

NETWORKS = [
    ("TRC20", "TRC20 · Tron"),
    ("BEP20", "BEP20 · BNB Chain"),
    ("ERC20", "ERC20 · Ethereum"),
    ("POLYGON", "Polygon"),
    ("SOL", "Solana"),
    ("TON", "TON"),
]
NET_LABEL = dict(NETWORKS)

STATUS_LABEL = {
    "awaiting_payment": "🕗 Awaiting payment",
    "pending": "🔎 Under review",
    "paid": "✅ Paid / confirmed",
    "rejected": "❌ Rejected",
    "cancelled": "🚫 Cancelled",
}

TOPUP_STATUS_LABEL = {
    "awaiting_payment": "🕗 Awaiting payment",
    "pending": "🔎 Under review",
    "approved": "✅ Credited",
    "rejected": "❌ Rejected",
    "cancelled": "🚫 Cancelled",
}

DEFAULT_WELCOME = (
    "Welcome to <b>{shop}</b>.\n\n"
    "Browse the catalogue, pick a product and pay with USDT on the network "
    "you prefer, or top up your wallet balance and check out instantly. "
    "After you send a payment, submit your transaction hash and an admin "
    "will confirm it."
)


# ----------------------------------------------------------------- helpers
def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def money(value) -> str:
    return f"{float(value or 0):,.2f}"


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def order_code(oid: int) -> str:
    return f"#{int(oid):05d}"


def topup_code(tid: int) -> str:
    return f"TU-{int(tid):05d}"


def kb(rows):
    return InlineKeyboardMarkup(rows)


def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


# persistent bottom "tab" menu — plain emoji text, no /commands
TAB_SHOP = "🛍 Shop"
TAB_PROFILE = "👤 Profile"
TAB_WALLET = "💰 Wallet"
TAB_ORDERS = "🧾 My orders"
TAB_SUPPORT = "💬 Support"
TAB_HOME = "🏠 Home"

TAB_ROUTES = {
    TAB_SHOP: "shop",
    TAB_PROFILE: "profile",
    TAB_WALLET: "wallet",
    TAB_ORDERS: "myorders:0",
    TAB_SUPPORT: "support",
    TAB_HOME: "home",
}


def tabs_markup():
    rows = [
        [TAB_SHOP, TAB_ORDERS],
        [TAB_PROFILE, TAB_WALLET],
        [TAB_SUPPORT, TAB_HOME],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def pager(prefix: str, page: int, total: int, page_size: int):
    """prefix must already contain everything except the page number."""
    pages = max(1, -(-total // page_size))
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(btn("◀️", f"{prefix}{page - 1}"))
    row.append(btn(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        row.append(btn("▶️", f"{prefix}{page + 1}"))
    return [row]


def make_qr(data: str):
    """Generate a QR code PNG for a wallet address. Returns bytes."""
    img = qrcode.make(data, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# simple per-user cooldown for spam-prone actions (txid submit, ticket msg)
_last_action = {}
RATE_LIMIT_SECONDS = 8


def rate_limited(user_id: int, bucket: str) -> bool:
    """True if the user must wait before repeating this action."""
    key = (user_id, bucket)
    last = _last_action.get(key, 0)
    t = time.monotonic()
    if t - last < RATE_LIMIT_SECONDS:
        return True
    _last_action[key] = t
    return False


def configured_wallets():
    out = []
    for code, label in NETWORKS:
        addr = db.get_setting(f"wallet_{code}")
        if addr:
            out.append((code, label, addr))
    return out


async def render(update: Update, text: str, markup=None, photo=None):
    """Show a screen, editing in place when possible."""
    query = update.callback_query
    if query is None:
        if photo:
            await update.effective_chat.send_photo(
                photo, caption=text, reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.effective_chat.send_message(
                text, reply_markup=markup, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        return

    msg = query.message
    has_photo = bool(msg and msg.photo)
    try:
        if photo and has_photo:
            await query.edit_message_media(
                InputMediaPhoto(photo, caption=text,
                                parse_mode=ParseMode.HTML),
                reply_markup=markup,
            )
            return
        if not photo and not has_photo:
            await query.edit_message_text(
                text, reply_markup=markup, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        log.debug("edit failed, falling back: %s", e)
    except Exception as e:  # noqa: BLE001
        log.debug("edit failed, falling back: %s", e)

    # message type changes (text <-> photo) need a fresh message
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass
    if photo:
        await update.effective_chat.send_photo(
            photo, caption=text, reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.effective_chat.send_message(
            text, reply_markup=markup, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def notify_admins(context, text, markup=None):
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id, text, reply_markup=markup,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("could not notify admin %s: %s", admin_id, e)


# ----------------------------------------------------------------- screens
def main_menu_markup(user_id):
    rows = [
        [btn("🛍  Browse shop", "shop")],
        [btn("👤  Profile", "profile"), btn("💰  Wallet", "wallet")],
        [btn("🧾  My orders", "myorders:0"), btn("💬  Support", "support")],
    ]
    if is_admin(user_id):
        rows.append([btn("⚙️  Admin panel", "am")])
    return kb(rows)


async def deliver_order(context, o, p):
    """Send the buyer their confirmation + auto-delivery content, if any.

    Shared by admin-approve (crypto orders) and instant balance checkout.
    """
    msg = (
        f"✅ <b>Payment confirmed</b> — order {order_code(o['id'])}\n\n"
        f"Product: <b>{esc(o['product_title'])}</b>\n"
        f"Amount: {money(o['price'])} {CURRENCY} ({esc(o['network'])})\n\n"
    )
    if p and p["delivery_content"]:
        msg += (
            "📩 Please contact support with your order ID "
            f"<b>{order_code(o['id'])}</b> to receive your account logins.\n"
            "⏱ Support usually replies within a few hours."
        )
    else:
        msg += "An admin will deliver your product shortly."
    try:
        await context.bot.send_message(o["user_id"], msg,
                                       parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001
        log.warning("could not notify buyer %s: %s", o["user_id"], e)


def welcome_text():
    tpl = db.get_setting("welcome", DEFAULT_WELCOME)
    try:
        return tpl.format(shop=esc(SHOP_NAME))
    except Exception:  # noqa: BLE001
        return tpl


async def show_home(update, context):
    await render(update, welcome_text(),
                 main_menu_markup(update.effective_user.id))


async def show_shop(update, context):
    cats = db.categories_with_counts()
    total = db.count_products(only_active=True)

    if not cats:
        await show_category(update, context, 0, 0)
        return

    rows = []
    for c in cats:
        rows.append([btn(f"📂 {c['name']}  ({c['n']})", f"cat:{c['id']}:0")])
    if total:
        rows.append([btn(f"🗂  All products  ({total})", "cat:0:0")])
    rows.append([btn("⬅️  Back", "home")])

    txt = f"🛍 <b>{esc(SHOP_NAME)}</b>\n\nPick a category:"
    if not total:
        txt = f"🛍 <b>{esc(SHOP_NAME)}</b>\n\nThe shop is empty right now."
    await render(update, txt, kb(rows))


async def show_category(update, context, cid: int, page: int):
    cat = db.get_category(cid) if cid else None
    cat_filter = cid if cid else None  # cid == 0 means "everything"
    back = "shop" if db.list_categories() else "home"
    sort = (context.user_data or {}).get("shop_sort", "default")
    search = (context.user_data or {}).get("shop_search")
    total = db.count_products(category_id=cat_filter, only_active=True, search=search)
    items = db.list_products(
        category_id=cat_filter, only_active=True,
        limit=PAGE_SIZE, offset=page * PAGE_SIZE, search=search, sort=sort,
    )

    title = esc(cat["name"]) if cat else "All products"
    if search:
        title += f' · "{esc(search)}"'

    SORT_LABELS = {"default": "Default", "price_asc": "Price ↑",
                   "price_desc": "Price ↓", "newest": "Newest"}
    sort_row = [btn(("✅ " if sort == k else "") + label, f"csort:{cid}:{k}")
                for k, label in SORT_LABELS.items()]

    if not total:
        rows = [sort_row[:2], sort_row[2:],
                [btn("🔎  Search", "csearch"), btn("🚫  Clear search", "csclr")]
                if search else [btn("🔎  Search", "csearch")],
                [btn("⬅️  Back", back)]]
        await render(update, f"📂 <b>{title}</b>\n\nNothing here yet.", kb(rows))
        return

    lines = [f"📂 <b>{title}</b>  ·  {total} item(s)", ""]
    rows = []
    for p in items:
        stock = "" if p["stock"] < 0 else (
            "  · SOLD OUT" if p["stock"] == 0 else f"  · {p['stock']} left"
        )
        lines.append(
            f"• <b>{esc(p['title'])}</b> — {money(p['price'])} {CURRENCY}{stock}"
        )
        rows.append([btn(
            f"{p['title'][:28]}  ·  {money(p['price'])} {CURRENCY}",
            f"prod:{p['id']}:{cid}:{page}",
        )])

    rows += pager(f"cat:{cid}:", page, total, PAGE_SIZE)
    rows.append(sort_row[:2])
    rows.append(sort_row[2:])
    search_row = [btn("🔎  Search", "csearch")]
    if search:
        search_row.append(btn("🚫  Clear search", "csclr"))
    rows.append(search_row)
    rows.append([btn("⬅️  Back", back), btn("🏠  Home", "home")])
    await render(update, "\n".join(lines), kb(rows))


async def show_product(update, context, pid: int, cid: int, page: int):
    p = db.get_product(pid)
    if not p or not p["is_active"]:
        await render(update, "This product is no longer available.",
                     kb([[btn("⬅️  Back to shop", "shop")]]))
        return

    cat = db.get_category(p["category_id"]) if p["category_id"] else None
    desc = (p["description"] or "").strip()
    limit = 650 if p["photo_file_id"] else 3000
    if len(desc) > limit:
        desc = desc[:limit] + "…"

    parts = [f"<b>{esc(p['title'])}</b>"]
    if cat:
        parts.append(f"<i>{esc(cat['name'])}</i>")
    parts.append("")
    if desc:
        parts.append(esc(desc))
        parts.append("")
    parts.append(f"💵 Price: <b>{money(p['price'])} {CURRENCY}</b>")
    if p["stock"] == 0:
        parts.append("📦 <b>Sold out</b>")
    elif p["stock"] > 0:
        parts.append(f"📦 In stock: {p['stock']}")

    uid = update.effective_user.id
    is_fav = db.is_favorite(uid, p["id"])

    rows = []
    if p["stock"] != 0:
        rows.append([btn(f"💳  Buy for {money(p['price'])} {CURRENCY}",
                         f"buy:{p['id']}")])
    rows.append([btn("💔  Unfavorite" if is_fav else "❤️  Favorite",
                     f"favt:{p['id']}:{cid}:{page}")])
    rows.append([btn("⬅️  Back", f"cat:{cid}:{page}"), btn("🏠  Home", "home")])

    await render(update, "\n".join(parts), kb(rows),
                 photo=p["photo_file_id"] or None)


def _checkout_price(context, pid, base_price):
    """Apply a coupon staged in user_data for this product, if any."""
    coupon = (context.user_data or {}).get("checkout_coupon")
    if not coupon or coupon.get("pid") != pid:
        return base_price, 0, None
    discount = min(coupon["discount"], base_price)
    return round(base_price - discount, 2), discount, coupon["code"]


async def show_networks(update, context, pid: int):
    p = db.get_product(pid)
    if not p or not p["is_active"] or p["stock"] == 0:
        await render(update, "This product is no longer available.",
                     kb([[btn("⬅️  Back to shop", "shop")]]))
        return

    wallets = configured_wallets()
    uid = update.effective_user.id
    bal = db.get_balance(uid)
    price, discount, coupon_code = _checkout_price(context, pid, p["price"])
    if not wallets and bal < price:
        await render(
            update,
            "⚠️ No payment wallets are configured yet.\n"
            "Please contact support.",
            kb([[btn("⬅️  Back", f"prod:{pid}:0:0")]]),
        )
        return

    rows = []
    if bal >= price:
        rows.append([btn(f"💰 Pay from balance ({money(bal)} {CURRENCY})",
                         f"balpay:{pid}")])
    rows += [[btn(f"💠 {label}", f"net:{pid}:{code}")]
             for code, label, _ in wallets]
    if coupon_code:
        rows.append([btn("🚫  Remove coupon", f"cpnrm:{pid}")])
    else:
        rows.append([btn("🎟  Have a coupon?", f"cpna:{pid}")])
    rows.append([btn("⬅️  Back", f"prod:{pid}:0:0")])

    txt = f"<b>{esc(p['title'])}</b>\n"
    if discount:
        txt += (
            f"Price: <s>{money(p['price'])}</s> "
            f"<b>{money(price)} {CURRENCY}</b> "
            f"(coupon {esc(coupon_code)}, -{money(discount)})\n"
        )
    else:
        txt += f"Amount: <b>{money(price)} {CURRENCY}</b>\n"
    txt += (
        f"Your balance: {money(bal)} {CURRENCY}\n\n"
        "Choose how you want to pay:"
    )
    await render(update, txt, kb(rows))


async def show_payment(update, context, pid: int, net: str):
    user = update.effective_user
    p = db.get_product(pid)
    address = db.get_setting(f"wallet_{net}")
    if not p or not address:
        await render(update, "Something went wrong. Please start again.",
                     kb([[btn("🏠  Home", "home")]]))
        return

    price, discount, coupon_code = _checkout_price(context, pid, p["price"])
    oid = db.create_order(user.id, user.username, p, net, address,
                          price=price, discount=discount, coupon_code=coupon_code)
    if coupon_code:
        coupon = db.get_coupon(coupon_code)
        if coupon:
            db.redeem_coupon(coupon["id"], user.id, oid)
    context.user_data.pop("checkout_coupon", None)

    discount_line = (
        f"Discount: -{money(discount)} {CURRENCY} (coupon {esc(coupon_code)})\n"
        if discount else ""
    )
    txt = (
        f"🧾 <b>Order {order_code(oid)}</b>\n\n"
        f"Product: <b>{esc(p['title'])}</b>\n"
        f"{discount_line}"
        f"Amount: <b>{money(price)} {CURRENCY}</b>\n"
        f"Network: <b>{esc(NET_LABEL.get(net, net))}</b>\n\n"
        f"Send exactly <b>{money(price)} {CURRENCY}</b> to this address:\n"
        f"<code>{esc(address)}</code>\n"
        "<i>(tap the address to copy, or scan the QR code above)</i>\n\n"
        "⚠️ Send only USDT on the "
        f"<b>{esc(net)}</b> network. Funds sent on another network are lost.\n\n"
        f"⏱ This order expires in <b>{PAYMENT_EXPIRY_MINUTES} minutes</b> if no "
        "TXID is submitted.\n\n"
        "When the transfer is done, tap <b>I have paid</b> and send your "
        "transaction hash (TXID)."
    )
    rows = [
        [btn("✅  I have paid", f"paid:{oid}")],
        [btn("🚫  Cancel order", f"cxl:{oid}")],
    ]
    try:
        qr = make_qr(address)
        await update.effective_chat.send_photo(qr, caption=txt,
                                               reply_markup=kb(rows),
                                               parse_mode=ParseMode.HTML)
    except Exception as e:  # noqa: BLE001
        log.debug("qr send failed: %s", e)
        await render(update, txt, kb(rows))


async def show_my_orders(update, context, page: int):
    uid = update.effective_user.id
    total = db.count_orders(user_id=uid)
    items = db.list_orders(user_id=uid, limit=5, offset=page * 5)

    if not total:
        await render(update, "You have no orders yet.",
                     kb([[btn("🛍  Browse shop", "shop")],
                         [btn("🏠  Home", "home")]]))
        return

    lines = ["🧾 <b>Your orders</b>", ""]
    for o in items:
        lines.append(
            f"{order_code(o['id'])} · <b>{esc(o['product_title'])}</b>\n"
            f"     {money(o['price'])} {CURRENCY} · {esc(o['network'])} · "
            f"{STATUS_LABEL.get(o['status'], o['status'])}"
        )
    rows = pager("myorders:", page, total, 5)
    rows.append([btn("🛍  Shop", "shop"), btn("🏠  Home", "home")])
    await render(update, "\n".join(lines), kb(rows))


# ----------------------------------------------------------------- profile
async def show_profile(update, context):
    u = update.effective_user
    urow = db.execute("SELECT * FROM users WHERE user_id = ?", (u.id,), "one")
    bal = db.get_balance(u.id)
    orders_n = db.count_orders(user_id=u.id)
    spent = db.user_spent(u.id)
    ticket = db.get_open_ticket(u.id)
    refs = db.count_referrals(u.id)
    favs = db.count_favorites(u.id)

    txt = (
        f"👤 <b>Your profile</b>\n\n"
        f"Name: {esc(u.first_name or '—')}\n"
        f"Username: {('@' + esc(u.username)) if u.username else '—'}\n"
        f"Telegram ID: <code>{u.id}</code>\n"
        f"Member since: {esc(urow['joined_at']) if urow else '—'}\n\n"
        f"💰 Wallet balance: <b>{money(bal)} {CURRENCY}</b>\n"
        f"🧾 Orders placed: <b>{orders_n}</b>\n"
        f"💵 Total spent: <b>{money(spent)} {CURRENCY}</b>\n"
        f"❤️ Favorites: <b>{favs}</b>\n"
        f"🤝 Referrals: <b>{refs}</b>\n"
    )
    if ticket:
        txt += "\n🎫 You have an open support ticket."

    rows = [
        [btn("💰  Wallet", "wallet"), btn("🧾  My orders", "myorders:0")],
        [btn("❤️  Favorites", "favs:0"), btn("🤝  Referrals", "refinfo")],
        [btn("💬  Support", "support")],
        [btn("🏠  Home", "home")],
    ]
    await render(update, txt, kb(rows))


async def show_referral_info(update, context):
    u = update.effective_user
    refs = db.count_referrals(u.id)
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{u.id}"
    txt = (
        "🤝 <b>Referrals</b>\n\n"
        f"Your referral link:\n<code>{esc(link)}</code>\n\n"
        f"People invited: <b>{refs}</b>\n"
    )
    if REFERRAL_BONUS > 0:
        txt += (
            f"\nYou earn <b>{money(REFERRAL_BONUS)} {CURRENCY}</b> for every "
            "new user who joins with your link."
        )
    await render(update, txt, kb([[btn("👤  Profile", "profile")],
                                   [btn("🏠  Home", "home")]]))


async def show_favorites(update, context, page: int):
    uid = update.effective_user.id
    total = db.count_favorites(uid)
    items = db.list_favorites(uid, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    if not total:
        await render(update, "❤️ You have no favorites yet.",
                     kb([[btn("🛍  Browse shop", "shop")],
                         [btn("🏠  Home", "home")]]))
        return
    lines = [f"❤️ <b>Your favorites</b>  ·  {total} item(s)", ""]
    rows = []
    for p in items:
        avail = p["is_active"] and p["stock"] != 0
        mark = "" if avail else "  · unavailable"
        lines.append(f"• <b>{esc(p['title'])}</b> — {money(p['price'])} {CURRENCY}{mark}")
        rows.append([btn(f"{p['title'][:28]}", f"prod:{p['id']}:0:0")])
    rows += pager("favs:", page, total, PAGE_SIZE)
    rows.append([btn("🏠  Home", "home")])
    await render(update, "\n".join(lines), kb(rows))


# ----------------------------------------------------------------- wallet
async def show_wallet(update, context):
    uid = update.effective_user.id
    bal = db.get_balance(uid)
    pending = db.count_topups(status="pending", user_id=uid)
    txt = (
        f"💰 <b>Your wallet</b>\n\n"
        f"Balance: <b>{money(bal)} {CURRENCY}</b>\n"
    )
    if pending:
        txt += f"🔎 {pending} top-up(s) under review.\n"
    txt += "\nTop up your balance to check out instantly, no waiting."
    rows = [
        [btn("➕  Top up", "topup")],
        [btn("🧾  Top-up history", "tuh:0")],
        [btn("🛍  Shop", "shop"), btn("🏠  Home", "home")],
    ]
    await render(update, txt, kb(rows))


async def start_topup(update, context):
    context.user_data["fsm"] = {"step": "topup_amount"}
    await render(
        update,
        f"➕ <b>Top up wallet</b>\n\nSend the amount in {CURRENCY} you want "
        "to add (e.g. 20 or 50.5).",
        kb([[btn("🚫 Cancel", "wallet")]]),
    )


async def show_topup_networks(update, context):
    fsm = context.user_data.get("fsm") or {}
    amount = fsm.get("amount")
    if not amount:
        return await show_wallet(update, context)
    wallets = configured_wallets()
    if not wallets:
        await render(
            update,
            "⚠️ No payment wallets are configured yet.\nPlease contact support.",
            kb([[btn("⬅️  Back", "wallet")]]),
        )
        return
    rows = [[btn(f"💠 {label}", f"topnet:{code}")] for code, label, _ in wallets]
    rows.append([btn("⬅️  Back", "wallet")])
    txt = (
        f"➕ <b>Top up</b>\nAmount: <b>{money(amount)} {CURRENCY}</b>\n\n"
        "Choose the network you want to pay on:"
    )
    await render(update, txt, kb(rows))


async def show_topup_payment(update, context, net: str):
    fsm = context.user_data.get("fsm") or {}
    amount = fsm.get("amount")
    user = update.effective_user
    address = db.get_setting(f"wallet_{net}")
    if not amount or not address:
        await render(update, "Something went wrong. Please start again.",
                     kb([[btn("💰  Wallet", "wallet")]]))
        return

    tid = db.create_topup(user.id, user.username, amount, net, address)
    context.user_data["fsm"] = {"step": "topup_txid", "tid": tid}

    txt = (
        f"🧾 <b>Top-up {topup_code(tid)}</b>\n\n"
        f"Amount: <b>{money(amount)} {CURRENCY}</b>\n"
        f"Network: <b>{esc(NET_LABEL.get(net, net))}</b>\n\n"
        f"Send exactly <b>{money(amount)} {CURRENCY}</b> to this address:\n"
        f"<code>{esc(address)}</code>\n"
        "<i>(tap the address to copy, or scan the QR code above)</i>\n\n"
        "⚠️ Send only USDT on the "
        f"<b>{esc(net)}</b> network. Funds sent on another network are lost.\n\n"
        f"⏱ This top-up expires in <b>{PAYMENT_EXPIRY_MINUTES} minutes</b> if no "
        "TXID is submitted.\n\n"
        "When the transfer is done, tap <b>I have paid</b> and send your "
        "transaction hash (TXID)."
    )
    rows = [
        [btn("✅  I have paid", f"tpaid:{tid}")],
        [btn("🚫  Cancel", f"tcxl:{tid}")],
    ]
    try:
        qr = make_qr(address)
        await update.effective_chat.send_photo(qr, caption=txt,
                                               reply_markup=kb(rows),
                                               parse_mode=ParseMode.HTML)
    except Exception as e:  # noqa: BLE001
        log.debug("qr send failed: %s", e)
        await render(update, txt, kb(rows))


async def show_topup_history(update, context, page: int):
    uid = update.effective_user.id
    total = db.count_topups(user_id=uid)
    items = db.list_topups(user_id=uid, limit=5, offset=page * 5)
    if not total:
        await render(update, "You have no top-ups yet.",
                     kb([[btn("➕  Top up", "topup")],
                         [btn("💰  Wallet", "wallet")]]))
        return
    lines = ["🧾 <b>Your top-ups</b>", ""]
    for t in items:
        lines.append(
            f"{topup_code(t['id'])} · <b>{money(t['amount'])} {CURRENCY}</b>\n"
            f"     {esc(t['network'])} · "
            f"{TOPUP_STATUS_LABEL.get(t['status'], t['status'])}"
        )
    rows = pager("tuh:", page, total, 5)
    rows.append([btn("💰  Wallet", "wallet"), btn("🏠  Home", "home")])
    await render(update, "\n".join(lines), kb(rows))


# ----------------------------------------------------------------- support (tickets)
def ticket_thread_text(ticket, messages):
    lines = [f"🎫 <b>Support ticket #{ticket['id']:05d}</b>", ""]
    if not messages:
        lines.append("<i>No messages yet.</i>")
    for m in messages:
        who = "You" if m["sender"] == "user" else "Support"
        lines.append(f"<b>{who}:</b> {esc(m['body'])}")
    lines.append("")
    if ticket["status"] == "open":
        lines.append("Send a message below to continue the conversation.")
    else:
        lines.append("<i>This ticket is closed.</i>")
    return "\n".join(lines)


async def show_support(update, context):
    uid = update.effective_user.id
    ticket = db.get_open_ticket(uid)
    if not ticket:
        handle = db.get_setting("support", "")
        txt = "💬 <b>Support</b>\n\n"
        if handle:
            txt += f"Contact: {esc(handle)}\n\n"
        txt += (
            "Open a ticket and an admin will reply to you right here in the "
            "chat — no need to leave Telegram."
        )
        await render(update, txt, kb([[btn("🎫  Open a ticket", "tkn")],
                                       [btn("🏠  Home", "home")]]))
        return

    messages = db.list_ticket_messages(ticket["id"])
    context.user_data["fsm"] = {"step": "ticket_msg", "tid": ticket["id"]}
    rows = [
        [btn("🔒  Close ticket", f"tkc:{ticket['id']}")],
        [btn("🏠  Home", "home")],
    ]
    await render(update, ticket_thread_text(ticket, messages), kb(rows))


# ----------------------------------------------------------------- admin UI
async def show_admin(update, context):
    pend = db.count_orders(status="pending")
    tpend = db.count_topups(status="pending")
    topen = db.count_tickets(status="open")
    txt = (
        "⚙️ <b>Admin panel</b>\n\n"
        f"Pending payments to review: <b>{pend}</b>\n"
        f"Pending top-ups: <b>{tpend}</b>\n"
        f"Open tickets: <b>{topen}</b>"
    )
    rows = [
        [btn(f"🔎  Review payments ({pend})", "ao:pending:0")],
        [btn(f"💰  Topups ({tpend})", "at:pending:0"),
         btn(f"🎫  Tickets ({topen})", "tk:open:0")],
        [btn("📦  Products", "ap:0"), btn("📂  Categories", "ac")],
        [btn("💠  Wallets", "aw"), btn("📊  Stats", "astat")],
        [btn("🎟  Coupons", "acp:0"), btn("💵  Adjust balance", "aadj")],
        [btn("🔍  Search order/txid", "asrch"), btn("📜  Admin log", "alog:0")],
        [btn("📣  Broadcast", "abc"), btn("📝  Texts", "ast")],
        [btn("🏠  Home", "home")],
    ]
    await render(update, txt, kb(rows))


async def show_admin_products(update, context, page: int):
    total = db.count_products(only_active=False)
    items = db.list_products(only_active=False, limit=ADMIN_PAGE_SIZE,
                             offset=page * ADMIN_PAGE_SIZE)
    rows = [[btn("➕  Add product", "apn")]]
    for p in items:
        mark = "🟢" if p["is_active"] else "🔴"
        rows.append([btn(
            f"{mark} {p['title'][:26]} · {money(p['price'])}",
            f"apv:{p['id']}",
        )])
    rows += pager("ap:", page, total, ADMIN_PAGE_SIZE)
    rows.append([btn("⬅️  Back", "am")])
    await render(update, f"📦 <b>Products</b> — {total} total", kb(rows))


async def show_admin_product(update, context, pid: int):
    p = db.get_product(pid)
    if not p:
        await show_admin_products(update, context, 0)
        return
    cat = db.get_category(p["category_id"]) if p["category_id"] else None
    stock = "unlimited" if p["stock"] < 0 else str(p["stock"])
    txt = (
        f"<b>{esc(p['title'])}</b>  (id {p['id']})\n\n"
        f"{esc((p['description'] or '')[:500])}\n\n"
        f"💵 {money(p['price'])} {CURRENCY}\n"
        f"📂 {esc(cat['name']) if cat else '—'}\n"
        f"📦 Stock: {stock}\n"
        f"🖼 Image: {'yes' if p['photo_file_id'] else 'no'}\n"
        f"🚚 Auto-delivery: {'yes' if p['delivery_content'] else 'no'}\n"
        f"Status: {'🟢 visible' if p['is_active'] else '🔴 hidden'}"
    )
    rows = [
        [btn("✏️ Title", f"ape:{pid}:title"),
         btn("✏️ Description", f"ape:{pid}:description")],
        [btn("💵 Price", f"ape:{pid}:price"),
         btn("📦 Stock", f"ape:{pid}:stock")],
        [btn("🖼 Image", f"ape:{pid}:photo"),
         btn("🚚 Delivery text", f"ape:{pid}:delivery")],
        [btn("📂 Category", f"apsc:{pid}")],
        [btn("🔴 Hide" if p["is_active"] else "🟢 Show", f"apt:{pid}")],
        [btn("🗑  Delete", f"apd:{pid}")],
        [btn("⬅️  Back", "ap:0")],
    ]
    await render(update, txt, kb(rows), photo=p["photo_file_id"] or None)


async def show_admin_categories(update, context):
    cats = db.categories_with_counts()
    rows = [[btn("➕  Add category", "acn")]]
    for c in cats:
        rows.append([
            btn(f"✏️ {c['name']} ({c['n']})", f"acr:{c['id']}"),
            btn("🗑", f"acd:{c['id']}"),
        ])
    rows.append([btn("⬅️  Back", "am")])
    await render(update, "📂 <b>Categories</b>\n\nTap a name to rename it.",
                 kb(rows))


async def show_wallets(update, context):
    lines = ["💠 <b>Payment wallets</b>", ""]
    rows = []
    for code, label in NETWORKS:
        addr = db.get_setting(f"wallet_{code}")
        lines.append(
            f"<b>{esc(label)}</b>: "
            + (f"<code>{esc(addr)}</code>" if addr else "<i>not set</i>")
        )
        row = [btn(f"✏️ {code}", f"awe:{code}")]
        if addr:
            row.append(btn("🗑", f"awd:{code}"))
        rows.append(row)
    rows.append([btn("⬅️  Back", "am")])
    lines.append("")
    lines.append("Only networks with an address show up at checkout.")
    await render(update, "\n".join(lines), kb(rows))


# ----------------------------------------------------------------- admin coupons
def coupon_line(c):
    amt = f"{c['amount']:.0f}%" if c["kind"] == "percent" else f"{money(c['amount'])} {CURRENCY}"
    uses = f"{c['used_count']}/{c['max_uses']}" if c["max_uses"] >= 0 else f"{c['used_count']}/∞"
    state = "🟢" if c["is_active"] else "🔴"
    return f"{state} <b>{esc(c['code'])}</b> — {amt} off · used {uses}"


async def show_admin_coupons(update, context, page: int):
    total = db.count_coupons()
    items = db.list_coupons(limit=ADMIN_PAGE_SIZE, offset=page * ADMIN_PAGE_SIZE)
    rows = [[btn("➕  New coupon", "acpn")]]
    for c in items:
        rows.append([btn(f"{'🟢' if c['is_active'] else '🔴'} {c['code']}", f"acpv:{c['id']}")])
    rows += pager("acp:", page, total, ADMIN_PAGE_SIZE)
    rows.append([btn("⬅️  Back", "am")])
    body = f"🎟 <b>Coupons</b>\n\n{total} coupon(s)."
    if not total:
        body += "\n\nNo coupons yet."
    await render(update, body, kb(rows))


async def show_admin_coupon(update, context, cid: int):
    c = db.get_coupon_by_id(cid)
    if not c:
        return await show_admin_coupons(update, context, 0)
    amt = f"{c['amount']:.0f}%" if c["kind"] == "percent" else f"{money(c['amount'])} {CURRENCY}"
    txt = (
        f"🎟 <b>{esc(c['code'])}</b>\n\n"
        f"Discount: <b>{amt}</b> ({c['kind']})\n"
        f"Uses: {c['used_count']} / {'∞' if c['max_uses'] < 0 else c['max_uses']}\n"
        f"Max per user: {c['max_uses_per_user']}\n"
        f"Min order: {money(c['min_order'])} {CURRENCY}\n"
        f"Expires: {esc(c['expires_at']) if c['expires_at'] else 'never'}\n"
        f"Status: {'🟢 active' if c['is_active'] else '🔴 disabled'}"
    )
    rows = [
        [btn("🔴 Disable" if c["is_active"] else "🟢 Enable", f"acpt:{cid}")],
        [btn("🗑  Delete", f"acpd:{cid}")],
        [btn("⬅️  Back", "acp:0")],
    ]
    await render(update, txt, kb(rows))


async def show_admin_orders(update, context, status: str, page: int):
    st = None if status == "all" else status
    total = db.count_orders(status=st)
    items = db.list_orders(status=st, limit=ADMIN_PAGE_SIZE,
                           offset=page * ADMIN_PAGE_SIZE)
    rows = []
    for o in items:
        rows.append([btn(
            f"{order_code(o['id'])} · {o['product_title'][:18]} · "
            f"{money(o['price'])}",
            f"aov:{o['id']}",
        )])
    rows += pager(f"ao:{status}:", page, total, ADMIN_PAGE_SIZE)
    rows.append([
        btn("🔎 Pending", "ao:pending:0"), btn("✅ Paid", "ao:paid:0"),
    ])
    rows.append([btn("🗂 All", "ao:all:0"), btn("⬅️  Back", "am")])
    label = {"pending": "under review", "paid": "confirmed",
             "rejected": "rejected", "all": "all"}.get(status, status)
    body = f"🧾 <b>Orders — {label}</b>\n\n{total} order(s)."
    if not total:
        body += "\n\nNothing here."
    await render(update, body, kb(rows))


def order_detail_text(o):
    uname = f"@{o['username']}" if o["username"] else "—"
    return (
        f"🧾 <b>Order {order_code(o['id'])}</b>\n\n"
        f"Product: <b>{esc(o['product_title'])}</b>\n"
        f"Amount: <b>{money(o['price'])} {CURRENCY}</b>\n"
        f"Network: <b>{esc(o['network'])}</b>\n"
        f"Status: {STATUS_LABEL.get(o['status'], o['status'])}\n\n"
        f"Buyer: {esc(uname)}\n"
        f"Buyer ID: <code>{o['user_id']}</code>\n"
        f"TXID: <code>{esc(o['txid'] or '—')}</code>\n"
        f"Created: {esc(o['created_at'])}"
        + (f"\nNote: {esc(o['note'])}" if o["note"] else "")
    )


async def show_admin_order(update, context, oid: int):
    o = db.get_order(oid)
    if not o:
        await show_admin_orders(update, context, "pending", 0)
        return
    rows = []
    if o["status"] in ("pending", "awaiting_payment"):
        rows.append([btn("✅ Approve", f"aok:{oid}"),
                     btn("❌ Reject", f"ano:{oid}")])
    blocked = db.is_blocked(o["user_id"])
    rows.append([btn("✅ Unblock buyer" if blocked else "🚫 Block buyer",
                     f"aub:{o['user_id']}:{oid}")])
    rows.append([btn("⬅️  Back", "ao:pending:0"), btn("🏠  Admin", "am")])
    await render(update, order_detail_text(o), kb(rows))


# ------------------------------------------------------------ admin topups
async def show_admin_topups(update, context, status: str, page: int):
    st = None if status == "all" else status
    total = db.count_topups(status=st)
    items = db.list_topups(status=st, limit=ADMIN_PAGE_SIZE,
                           offset=page * ADMIN_PAGE_SIZE)
    rows = []
    for t in items:
        rows.append([btn(
            f"{topup_code(t['id'])} · {money(t['amount'])} {CURRENCY} · "
            f"{esc(t['network'])}",
            f"atv:{t['id']}",
        )])
    rows += pager(f"at:{status}:", page, total, ADMIN_PAGE_SIZE)
    rows.append([
        btn("🔎 Pending", "at:pending:0"), btn("✅ Approved", "at:approved:0"),
    ])
    rows.append([btn("🗂 All", "at:all:0"), btn("⬅️  Back", "am")])
    label = {"pending": "under review", "approved": "credited",
             "rejected": "rejected", "all": "all"}.get(status, status)
    body = f"💰 <b>Top-ups — {label}</b>\n\n{total} top-up(s)."
    if not total:
        body += "\n\nNothing here."
    await render(update, body, kb(rows))


def topup_detail_text(t):
    uname = f"@{t['username']}" if t["username"] else "—"
    return (
        f"💰 <b>Top-up {topup_code(t['id'])}</b>\n\n"
        f"Amount: <b>{money(t['amount'])} {CURRENCY}</b>\n"
        f"Network: <b>{esc(t['network'])}</b>\n"
        f"Status: {TOPUP_STATUS_LABEL.get(t['status'], t['status'])}\n\n"
        f"User: {esc(uname)}\n"
        f"User ID: <code>{t['user_id']}</code>\n"
        f"TXID: <code>{esc(t['txid'] or '—')}</code>\n"
        f"Created: {esc(t['created_at'])}"
        + (f"\nNote: {esc(t['note'])}" if t["note"] else "")
    )


async def show_admin_topup(update, context, tid: int):
    t = db.get_topup(tid)
    if not t:
        await show_admin_topups(update, context, "pending", 0)
        return
    rows = []
    if t["status"] in ("pending", "awaiting_payment"):
        rows.append([btn("✅ Approve", f"atk:{tid}"),
                     btn("❌ Reject", f"atn:{tid}")])
    rows.append([btn("⬅️  Back", "at:pending:0"), btn("🏠  Admin", "am")])
    await render(update, topup_detail_text(t), kb(rows))


# ----------------------------------------------------------- admin tickets
def admin_ticket_text(ticket, messages):
    uname = f"@{ticket['username']}" if ticket["username"] else "—"
    lines = [
        f"🎫 <b>Ticket #{ticket['id']:05d}</b>  "
        f"({'open' if ticket['status'] == 'open' else 'closed'})",
        f"User: {esc(uname)}  ·  ID: <code>{ticket['user_id']}</code>",
        "",
    ]
    if not messages:
        lines.append("<i>No messages yet.</i>")
    for m in messages:
        who = "User" if m["sender"] == "user" else "You"
        lines.append(f"<b>{who}:</b> {esc(m['body'])}")
    return "\n".join(lines)


async def show_admin_tickets(update, context, status: str, page: int):
    st = None if status == "all" else status
    total = db.count_tickets(status=st)
    items = db.list_tickets(status=st, limit=ADMIN_PAGE_SIZE,
                            offset=page * ADMIN_PAGE_SIZE)
    rows = []
    for t in items:
        preview = (t["last_msg_preview"] or "")[:24]
        rows.append([btn(f"#{t['id']:05d} · {preview}", f"tkv:{t['id']}")])
    rows += pager(f"tk:{status}:", page, total, ADMIN_PAGE_SIZE)
    rows.append([btn("🟢 Open", "tk:open:0"), btn("🔒 Closed", "tk:closed:0")])
    rows.append([btn("🗂 All", "tk:all:0"), btn("⬅️  Back", "am")])
    body = f"🎫 <b>Tickets — {status}</b>\n\n{total} ticket(s)."
    if not total:
        body += "\n\nNothing here."
    await render(update, body, kb(rows))


async def show_admin_ticket(update, context, tid: int):
    t = db.get_ticket(tid)
    if not t:
        await show_admin_tickets(update, context, "open", 0)
        return
    messages = db.list_ticket_messages(t["id"])
    rows = []
    if t["status"] == "open":
        rows.append([btn("💬 Reply", f"treply:{tid}"),
                     btn("🔒 Close", f"tkclose:{tid}")])
    rows.append([btn("⬅️  Back", "tk:open:0"), btn("🏠  Admin", "am")])
    await render(update, admin_ticket_text(t, messages), kb(rows))


# ----------------------------------------------------------------- admin log
ADMIN_ACTION_LABEL = {
    "adjust_balance": "💵 Balance adjust",
    "block_user": "🚫 Block/unblock",
    "coupon_create": "🎟 Coupon created",
    "coupon_toggle": "🎟 Coupon toggled",
    "coupon_delete": "🎟 Coupon deleted",
}


async def show_admin_log(update, context, page: int):
    total = db.count_admin_log()
    items = db.list_admin_log(limit=ADMIN_PAGE_SIZE, offset=page * ADMIN_PAGE_SIZE)
    lines = ["📜 <b>Admin action log</b>", ""]
    if not items:
        lines.append("<i>No actions logged yet.</i>")
    for a in items:
        label = ADMIN_ACTION_LABEL.get(a["action"], esc(a["action"]))
        lines.append(
            f"{esc(a['created_at'])} · admin <code>{a['admin_id']}</code>\n"
            f"     {label} — {esc(a['detail'])}"
        )
    rows = pager("alog:", page, total, ADMIN_PAGE_SIZE)
    rows.append([btn("⬅️  Back", "am")])
    await render(update, "\n".join(lines), kb(rows))


# ----------------------------------------------------------------- admin search
async def show_admin_search_result(update, context, term: str, page: int):
    o_total = db.count_search_orders(term)
    t_total = db.count_search_topups(term)
    orders = db.search_orders(term, limit=5, offset=page * 5)
    topups = db.search_topups(term, limit=5, offset=page * 5)
    lines = [f"🔍 <b>Search results for</b> <code>{esc(term)}</code>", ""]
    rows = []
    if orders:
        lines.append(f"🧾 Orders ({o_total}):")
        for o in orders:
            lines.append(f"  {order_code(o['id'])} · {esc(o['product_title'])[:24]}")
            rows.append([btn(f"{order_code(o['id'])}", f"aov:{o['id']}")])
    if topups:
        lines.append(f"\n💰 Top-ups ({t_total}):")
        for t in topups:
            lines.append(f"  {topup_code(t['id'])} · {money(t['amount'])} {CURRENCY}")
            rows.append([btn(f"{topup_code(t['id'])}", f"atv:{t['id']}")])
    if not orders and not topups:
        lines.append("Nothing found.")
    rows.append([btn("🔍  New search", "asrch"), btn("⬅️  Back", "am")])
    await render(update, "\n".join(lines), kb(rows))


# ----------------------------------------------------------------- commands
REFERRAL_BONUS = float(os.getenv("REFERRAL_BONUS", "0") or 0)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    is_new = db.execute("SELECT 1 FROM users WHERE user_id = ?", (u.id,), "one") is None
    db.upsert_user(u.id, u.username, u.first_name)
    if db.is_blocked(u.id):
        await update.message.reply_text("🚫 You are blocked from using this bot.")
        return

    if is_new and context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                ref_id = int(arg[4:])
            except ValueError:
                ref_id = None
            if ref_id and db.set_referrer_if_unset(u.id, ref_id):
                if REFERRAL_BONUS > 0 and db.mark_ref_bonus_paid(u.id):
                    db.adjust_balance(ref_id, REFERRAL_BONUS)
                    try:
                        await context.bot.send_message(
                            ref_id,
                            f"🎉 Someone joined using your referral link! "
                            f"{money(REFERRAL_BONUS)} {CURRENCY} has been added "
                            "to your balance.",
                        )
                    except Exception:  # noqa: BLE001
                        pass

    context.user_data.pop("fsm", None)
    await update.message.reply_text(
        "Menu opened 👇", reply_markup=tabs_markup(),
    )
    await update.message.reply_text(
        welcome_text(), reply_markup=main_menu_markup(u.id),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Your Telegram ID: <code>{update.effective_user.id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("You are not an admin.")
        return
    context.user_data.pop("fsm", None)
    await show_admin(update, context)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("fsm", None)
    await update.message.reply_text(
        "Cancelled.", reply_markup=main_menu_markup(update.effective_user.id)
    )


# ----------------------------------------------------------------- buttons
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = update.effective_user.id
    if db.is_blocked(uid) and not is_admin(uid):
        await q.answer("🚫 You are blocked.", show_alert=True)
        return
    await q.answer()

    parts = data.split(":")
    head = parts[0]

    # navigating away from a prompt cancels that prompt
    if head in ("shop", "cat", "prod", "buy", "net", "myorders", "support",
                "profile", "wallet", "topup"):
        context.user_data.pop("fsm", None)

    # ---- public
    if head == "noop":
        return
    if head == "home":
        context.user_data.pop("fsm", None)
        return await show_home(update, context)
    if head == "shop":
        return await show_shop(update, context)
    if head == "cat":
        return await show_category(update, context, int(parts[1]),
                                   int(parts[2]))
    if head == "csort":
        context.user_data["shop_sort"] = parts[2]
        return await show_category(update, context, int(parts[1]), 0)
    if head == "csearch":
        context.user_data["fsm"] = {"step": "shop_search"}
        return await render(
            update, "🔎 Send the product name (or part of it) to search for.",
            kb([[btn("🚫 Cancel", "shop")]]))
    if head == "csclr":
        context.user_data.pop("shop_search", None)
        return await show_category(update, context, 0, 0)
    if head == "prod":
        return await show_product(update, context, int(parts[1]),
                                  int(parts[2]), int(parts[3]))
    if head == "favt":
        pid, cid, page = int(parts[1]), int(parts[2]), int(parts[3])
        if db.is_favorite(uid, pid):
            db.remove_favorite(uid, pid)
        else:
            db.add_favorite(uid, pid)
        return await show_product(update, context, pid, cid, page)
    if head == "favs":
        return await show_favorites(update, context, int(parts[1]))
    if head == "refinfo":
        return await show_referral_info(update, context)
    if head == "buy":
        context.user_data.pop("checkout_coupon", None)
        return await show_networks(update, context, int(parts[1]))
    if head == "cpna":
        pid = int(parts[1])
        context.user_data["fsm"] = {"step": "coupon_code", "pid": pid}
        return await render(
            update, "🎟 Send your coupon code.",
            kb([[btn("🚫 Cancel", f"buy:{pid}")]]))
    if head == "cpnrm":
        pid = int(parts[1])
        context.user_data.pop("checkout_coupon", None)
        return await show_networks(update, context, pid)
    if head == "net":
        return await show_payment(update, context, int(parts[1]), parts[2])
    if head == "myorders":
        return await show_my_orders(update, context, int(parts[1]))
    if head == "support":
        return await show_support(update, context)
    if head == "profile":
        return await show_profile(update, context)

    if head == "balpay":
        pid = int(parts[1])
        p = db.get_product(pid)
        if not p or not p["is_active"] or p["stock"] == 0:
            return await render(update, "This product is no longer available.",
                                kb([[btn("⬅️  Back to shop", "shop")]]))
        if not db.decrement_stock_if_available(pid):
            return await render(update, "Sold out just now — sorry!",
                                kb([[btn("⬅️  Back to shop", "shop")]]))
        if not db.deduct_balance_if_enough(uid, p["price"]):
            db.restock_one(pid)  # release the reservation, payment failed
            return await render(update, "Insufficient balance.",
                                kb([[btn("💰 Wallet", "wallet")]]))
        user = update.effective_user
        oid = db.create_order(user.id, user.username, p, "BALANCE", None)
        db.set_order_status(oid, "paid")
        o = db.get_order(oid)
        await deliver_order(context, o, p)
        return await render(
            update, f"✅ Paid from balance — order {order_code(oid)}.",
            kb([[btn("🧾 My orders", "myorders:0")], [btn("🏠 Home", "home")]]))

    if head == "wallet":
        return await show_wallet(update, context)
    if head == "topup":
        return await start_topup(update, context)
    if head == "topnet":
        fsm = context.user_data.get("fsm") or {}
        if fsm.get("step") != "topup_amount_set":
            return await show_wallet(update, context)
        return await show_topup_payment(update, context, parts[1])
    if head == "tuh":
        return await show_topup_history(update, context, int(parts[1]))
    if head == "tpaid":
        tid = int(parts[1])
        t = db.get_topup(tid)
        if not t or t["user_id"] != uid:
            return
        if t["status"] not in ("awaiting_payment",):
            return await render(
                update, f"Top-up {topup_code(tid)} is already submitted.",
                kb([[btn("💰 Wallet", "wallet")]]))
        context.user_data["fsm"] = {"step": "topup_txid", "tid": tid}
        return await render(
            update,
            f"Top-up {topup_code(tid)} — please send your <b>transaction "
            "hash (TXID)</b> as a message.\n\nSend /cancel to abort.",
            kb([[btn("🚫  Cancel", f"tcxl:{tid}")]]),
        )
    if head == "tcxl":
        tid = int(parts[1])
        t = db.get_topup(tid)
        if t and t["user_id"] == uid and t["status"] == "awaiting_payment":
            db.set_topup_status(tid, "cancelled")
        context.user_data.pop("fsm", None)
        return await render(update, f"Top-up {topup_code(tid)} cancelled.",
                            main_menu_markup(uid))

    if head == "tkn":
        tid = db.create_ticket(uid, update.effective_user.username)
        context.user_data["fsm"] = {"step": "ticket_msg", "tid": tid}
        return await render(
            update,
            f"🎫 <b>Ticket #{tid:05d} opened.</b>\n\n"
            "Send your message and an admin will reply here.",
            kb([[btn("🔒  Close ticket", f"tkc:{tid}")],
                [btn("🏠  Home", "home")]]),
        )
    if head == "tkc":
        tid = int(parts[1])
        t = db.get_ticket(tid)
        if t and t["user_id"] == uid:
            db.close_ticket(tid)
        context.user_data.pop("fsm", None)
        return await render(update, f"Ticket #{tid:05d} closed.",
                            main_menu_markup(uid))

    if head == "paid":
        oid = int(parts[1])
        o = db.get_order(oid)
        if not o or o["user_id"] != uid:
            return
        if o["status"] not in ("awaiting_payment",):
            return await render(
                update, f"Order {order_code(oid)} is already submitted.",
                kb([[btn("🧾 My orders", "myorders:0")]]))
        context.user_data["fsm"] = {"step": "txid", "oid": oid}
        return await render(
            update,
            f"Order {order_code(oid)} — please send your <b>transaction hash "
            "(TXID)</b> as a message.\n\n"
            "You can find it in your wallet's transaction history. "
            "Send /cancel to abort.",
            kb([[btn("🚫  Cancel order", f"cxl:{oid}")]]),
        )

    if head == "cxl":
        oid = int(parts[1])
        o = db.get_order(oid)
        if o and o["user_id"] == uid and o["status"] == "awaiting_payment":
            db.set_order_status(oid, "cancelled")
        context.user_data.pop("fsm", None)
        return await render(update, f"Order {order_code(oid)} cancelled.",
                            main_menu_markup(uid))

    # ---- admin only
    if not is_admin(uid):
        return

    if head == "am":
        context.user_data.pop("fsm", None)
        return await show_admin(update, context)
    if head == "ap":
        return await show_admin_products(update, context, int(parts[1]))
    if head == "apv":
        return await show_admin_product(update, context, int(parts[1]))
    if head == "apt":
        p = db.get_product(int(parts[1]))
        db.update_product(p["id"], is_active=0 if p["is_active"] else 1)
        return await show_admin_product(update, context, p["id"])
    if head == "apd":
        pid = int(parts[1])
        return await render(
            update, "Delete this product permanently?",
            kb([[btn("🗑 Yes, delete", f"apdy:{pid}"),
                 btn("⬅️ No", f"apv:{pid}")]]),
        )
    if head == "apdy":
        db.delete_product(int(parts[1]))
        return await show_admin_products(update, context, 0)

    if head == "apn":
        context.user_data["fsm"] = {"step": "new_title", "draft": {}}
        return await render(
            update,
            "➕ <b>New product</b>\n\nStep 1/6 — send the <b>title</b>.\n"
            "Send /cancel at any time to stop.",
            kb([[btn("🚫 Cancel", "am")]]),
        )

    if head == "apc":  # category for a new product draft
        fsm = context.user_data.get("fsm") or {}
        draft = fsm.get("draft")
        if not draft:
            return await show_admin(update, context)
        cid = int(parts[1]) or None
        pid = db.add_product(
            draft["title"], draft.get("description", ""),
            draft.get("price", 0), draft.get("photo"),
            draft.get("delivery"), cid, draft.get("stock", -1),
        )
        context.user_data.pop("fsm", None)
        await render(update, f"✅ Product created (id {pid}).",
                     kb([[btn("📦 Open product", f"apv:{pid}")],
                         [btn("➕ Add another", "apn")],
                         [btn("⚙️ Admin", "am")]]))
        return

    if head == "apsc":  # change category of an existing product
        pid = int(parts[1])
        if len(parts) == 2:
            rows = [[btn("— none —", f"apsc:{pid}:0")]]
            for c in db.list_categories():
                rows.append([btn(c["name"], f"apsc:{pid}:{c['id']}")])
            rows.append([btn("⬅️ Back", f"apv:{pid}")])
            return await render(update, "Pick a category:", kb(rows))
        cid = int(parts[2]) or None
        db.update_product(pid, category_id=cid)
        return await show_admin_product(update, context, pid)

    if head == "ape":
        pid, field = int(parts[1]), parts[2]
        context.user_data["fsm"] = {"step": "edit", "pid": pid, "field": field}
        prompts = {
            "title": "Send the new <b>title</b>.",
            "description": "Send the new <b>description</b>.",
            "price": f"Send the new <b>price</b> in {CURRENCY} (e.g. 12.50).",
            "stock": "Send the new <b>stock</b> count "
                     "(a number, or -1 for unlimited).",
            "photo": "Send the new <b>image</b> as a photo. "
                     "Send /skip to remove the current image.",
            "delivery": "Send the <b>auto-delivery text</b> (sent to the buyer "
                        "the moment you approve their payment). "
                        "Send /skip to disable auto-delivery.",
        }
        return await render(update, prompts.get(field, "Send the new value."),
                            kb([[btn("🚫 Cancel", f"apv:{pid}")]]))

    if head == "ac":
        return await show_admin_categories(update, context)
    if head == "acn":
        context.user_data["fsm"] = {"step": "cat_new"}
        return await render(update, "Send the <b>category name</b>.",
                            kb([[btn("🚫 Cancel", "ac")]]))
    if head == "acr":
        context.user_data["fsm"] = {"step": "cat_rename", "cid": int(parts[1])}
        return await render(update, "Send the <b>new name</b>.",
                            kb([[btn("🚫 Cancel", "ac")]]))
    if head == "acd":
        cid = int(parts[1])
        return await render(
            update, "Delete this category? Its products stay but become "
                    "uncategorised.",
            kb([[btn("🗑 Yes", f"acdy:{cid}"), btn("⬅️ No", "ac")]]))
    if head == "acdy":
        db.delete_category(int(parts[1]))
        return await show_admin_categories(update, context)

    if head == "aw":
        return await show_wallets(update, context)
    if head == "awe":
        net = parts[1]
        context.user_data["fsm"] = {"step": "wallet", "net": net}
        return await render(
            update,
            f"Send the USDT <b>{esc(net)}</b> receiving address.\n\n"
            "Double-check it — this is what buyers will send funds to.",
            kb([[btn("🚫 Cancel", "aw")]]))
    if head == "awd":
        db.del_setting(f"wallet_{parts[1]}")
        return await show_wallets(update, context)

    if head == "ao":
        return await show_admin_orders(update, context, parts[1],
                                       int(parts[2]))
    if head == "aov":
        return await show_admin_order(update, context, int(parts[1]))
    if head == "aub":
        target_uid, oid = int(parts[1]), int(parts[2])
        now_blocked = not db.is_blocked(target_uid)
        db.set_blocked(target_uid, now_blocked)
        db.log_admin_action(uid, "block_user",
                            f"user {target_uid} -> {'blocked' if now_blocked else 'unblocked'}")
        return await show_admin_order(update, context, oid)

    if head == "aok":
        oid = int(parts[1])
        o = db.get_order(oid)
        if not o:
            return
        if not db.set_order_status_from(oid, ("pending", "awaiting_payment"), "paid"):
            return await show_admin_order(update, context, oid)  # already processed
        p = db.get_product(o["product_id"])
        if p and o["network"] != "BALANCE" and p["stock"] > 0:
            db.update_product(p["id"], stock=p["stock"] - 1)
        o = db.get_order(oid)
        await deliver_order(context, o, p)
        return await show_admin_order(update, context, oid)

    if head == "ano":
        oid = int(parts[1])
        context.user_data["fsm"] = {"step": "reject", "oid": oid}
        return await render(
            update,
            f"Send a short reason for rejecting order {order_code(oid)}.\n"
            "It will be forwarded to the buyer. Send /skip for no reason.",
            kb([[btn("🚫 Cancel", f"aov:{oid}")]]))

    if head == "astat":
        txt = (
            "📊 <b>Stats</b>\n\n"
            f"Users: <b>{db.count_users()}</b>\n"
            f"Products: <b>{db.count_products(only_active=False)}</b>\n"
            f"Orders under review: <b>{db.count_orders(status='pending')}</b>\n"
            f"Confirmed orders: <b>{db.count_orders(status='paid')}</b>\n"
            f"Confirmed revenue: <b>{money(db.revenue())} {CURRENCY}</b>"
        )
        return await render(update, txt, kb([[btn("⬅️  Back", "am")]]))

    # ---- admin: coupons
    if head == "acp":
        return await show_admin_coupons(update, context, int(parts[1]))
    if head == "acpn":
        context.user_data["fsm"] = {"step": "coupon_new_code"}
        return await render(
            update, "🎟 Send the new coupon <b>code</b> (letters/numbers).",
            kb([[btn("🚫 Cancel", "acp:0")]]))
    if head == "acpv":
        return await show_admin_coupon(update, context, int(parts[1]))
    if head == "acpt":
        cid = int(parts[1])
        c = db.get_coupon_by_id(cid)
        if c:
            db.set_coupon_active(cid, not c["is_active"])
            db.log_admin_action(uid, "coupon_toggle", c["code"])
        return await show_admin_coupon(update, context, cid)
    if head == "acpd":
        cid = int(parts[1])
        return await render(
            update, "Delete this coupon permanently?",
            kb([[btn("🗑 Yes, delete", f"acpdy:{cid}"),
                 btn("⬅️ No", f"acpv:{cid}")]]))
    if head == "acpdy":
        cid = int(parts[1])
        c = db.get_coupon_by_id(cid)
        db.delete_coupon(cid)
        if c:
            db.log_admin_action(uid, "coupon_delete", c["code"])
        return await show_admin_coupons(update, context, 0)

    # ---- admin: manual balance adjust
    if head == "aadj":
        context.user_data["fsm"] = {"step": "adj_uid"}
        return await render(
            update, "💵 Send the <b>Telegram user ID</b> to adjust balance for.",
            kb([[btn("🚫 Cancel", "am")]]))

    # ---- admin: search
    if head == "asrch":
        context.user_data["fsm"] = {"step": "admin_search"}
        return await render(
            update, "🔍 Send a TXID (partial ok) or an exact buyer/user ID.",
            kb([[btn("🚫 Cancel", "am")]]))

    # ---- admin: log
    if head == "alog":
        return await show_admin_log(update, context, int(parts[1]))

    if head == "abc":
        context.user_data["fsm"] = {"step": "broadcast"}
        return await render(
            update,
            "📣 Send the message you want to broadcast to every user.\n"
            "HTML formatting is supported. Send /cancel to abort.",
            kb([[btn("🚫 Cancel", "am")]]))

    if head == "ast":
        w = db.get_setting("welcome", DEFAULT_WELCOME)
        s = db.get_setting("support", "—")
        return await render(
            update,
            "📝 <b>Texts</b>\n\n"
            f"<b>Welcome message</b>\n{esc(w[:400])}\n\n"
            f"<b>Support contact</b>\n{esc(s)}",
            kb([[btn("✏️ Welcome", "asw")], [btn("✏️ Support", "ass")],
                [btn("⬅️  Back", "am")]]))
    if head == "asw":
        context.user_data["fsm"] = {"step": "welcome"}
        return await render(
            update,
            "Send the new welcome message. HTML allowed. "
            "Use <code>{shop}</code> for the shop name.",
            kb([[btn("🚫 Cancel", "ast")]]))
    if head == "ass":
        context.user_data["fsm"] = {"step": "support"}
        return await render(update, "Send the support contact (e.g. @yourname).",
                            kb([[btn("🚫 Cancel", "ast")]]))

    # ---- admin: topups
    if head == "at":
        return await show_admin_topups(update, context, parts[1], int(parts[2]))
    if head == "atv":
        return await show_admin_topup(update, context, int(parts[1]))
    if head == "atk":
        tid = int(parts[1])
        t = db.get_topup(tid)
        if not t:
            return
        if not db.set_topup_status_from(
            tid, ("pending", "awaiting_payment"), "approved"
        ):
            return await show_admin_topup(update, context, tid)  # already processed
        db.adjust_balance(t["user_id"], t["amount"])
        try:
            await context.bot.send_message(
                t["user_id"],
                f"✅ <b>Top-up confirmed</b> — {topup_code(tid)}\n\n"
                f"{money(t['amount'])} {CURRENCY} has been credited to your "
                f"wallet balance. New balance: "
                f"{money(db.get_balance(t['user_id']))} {CURRENCY}.",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("could not notify buyer %s: %s", t["user_id"], e)
        return await show_admin_topup(update, context, tid)
    if head == "atn":
        tid = int(parts[1])
        context.user_data["fsm"] = {"step": "topup_reject", "tid": tid}
        return await render(
            update,
            f"Send a short reason for rejecting top-up {topup_code(tid)}.\n"
            "It will be forwarded to the user. Send /skip for no reason.",
            kb([[btn("🚫 Cancel", f"atv:{tid}")]]))

    # ---- admin: tickets
    if head == "tk":
        return await show_admin_tickets(update, context, parts[1], int(parts[2]))
    if head == "tkv":
        return await show_admin_ticket(update, context, int(parts[1]))
    if head == "treply":
        tid = int(parts[1])
        context.user_data["fsm"] = {"step": "ticket_reply", "tid": tid}
        return await render(
            update, f"Send your reply to ticket #{tid:05d}.",
            kb([[btn("🚫 Cancel", f"tkv:{tid}")]]))
    if head == "tkclose":
        tid = int(parts[1])
        db.close_ticket(tid)
        try:
            await context.bot.send_message(
                db.get_ticket(tid)["user_id"],
                f"🔒 Support ticket #{tid:05d} has been closed by an admin.",
            )
        except Exception:  # noqa: BLE001
            pass
        return await show_admin_ticket(update, context, tid)


# ----------------------------------------------------------------- messages
async def category_picker(update, context):
    rows = [[btn("— none —", "apc:0")]]
    for c in db.list_categories():
        rows.append([btn(c["name"], f"apc:{c['id']}")])
    rows.append([btn("🚫 Cancel", "am")])
    await update.message.reply_text(
        "Step 6/6 — pick a category:", reply_markup=kb(rows),
        parse_mode=ParseMode.HTML,
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.upsert_user(u.id, u.username, u.first_name)
    if db.is_blocked(u.id) and not is_admin(u.id):
        return
    msg = update.message
    text = (msg.text or msg.caption or "").strip()
    photo_id = msg.photo[-1].file_id if msg.photo else None
    fsm = context.user_data.get("fsm")

    if text in TAB_ROUTES:
        context.user_data.pop("fsm", None)
        dest = TAB_ROUTES[text]
        if dest == "shop":
            return await show_shop(update, context)
        if dest == "profile":
            return await show_profile(update, context)
        if dest == "wallet":
            return await show_wallet(update, context)
        if dest == "myorders:0":
            return await show_my_orders(update, context, 0)
        if dest == "support":
            return await show_support(update, context)
        if dest == "home":
            return await show_home(update, context)

    if not fsm:
        await msg.reply_text(
            "Use the menu below 👇", reply_markup=main_menu_markup(u.id)
        )
        return

    step = fsm.get("step")

    # ---------- buyer: product search
    if step == "shop_search":
        context.user_data.pop("fsm", None)
        context.user_data["shop_search"] = text[:100]
        return await show_category(update, context, 0, 0)

    # ---------- buyer: coupon code entry
    if step == "coupon_code":
        pid = fsm["pid"]
        p = db.get_product(pid)
        context.user_data.pop("fsm", None)
        if not p:
            return await show_shop(update, context)
        coupon, err = db.validate_coupon(text, u.id, p["price"])
        if err:
            await msg.reply_text(f"❌ {err}",
                                 reply_markup=kb([[btn("⬅️ Back", f"buy:{pid}")]]))
            return
        discount = db.coupon_discount(coupon, p["price"])
        context.user_data["checkout_coupon"] = {
            "pid": pid, "code": coupon["code"], "discount": discount,
        }
        await msg.reply_text(
            f"✅ Coupon <b>{esc(coupon['code'])}</b> applied: "
            f"-{money(discount)} {CURRENCY}",
            parse_mode=ParseMode.HTML,
        )
        return await show_networks(update, context, pid)

    # ---------- buyer: submitting a TXID
    if step == "txid":
        oid = fsm["oid"]
        o = db.get_order(oid)
        if not o or o["user_id"] != u.id:
            context.user_data.pop("fsm", None)
            return
        if rate_limited(u.id, "txid"):
            await msg.reply_text("⏳ Please wait a few seconds and try again.")
            return
        txid = text.split()[0] if text else ""
        if len(txid) < 10:
            await msg.reply_text(
                "That does not look like a transaction hash. "
                "Please paste the full TXID, or send /cancel."
            )
            return
        if db.txid_exists(txid):
            await msg.reply_text(
                "This transaction hash has already been submitted. "
                "If you think this is a mistake, contact support."
            )
            return
        db.set_order_txid(oid, txid)
        context.user_data.pop("fsm", None)
        await msg.reply_text(
            f"✅ Thanks! Order {order_code(oid)} is now under review.\n"
            "You'll get a message here as soon as the payment is confirmed.",
            reply_markup=main_menu_markup(u.id), parse_mode=ParseMode.HTML,
        )
        o = db.get_order(oid)
        await notify_admins(
            context,
            "🔔 <b>New payment to review</b>\n\n" + order_detail_text(o),
            kb([[btn("✅ Approve", f"aok:{oid}"),
                 btn("❌ Reject", f"ano:{oid}")]]),
        )
        return

    # ---------- buyer: wallet top-up amount
    if step == "topup_amount":
        try:
            amount = float(text.replace(",", "."))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("Send a positive number, e.g. 20 or 50.5")
            return
        fsm["amount"] = amount
        fsm["step"] = "topup_amount_set"
        wallets = configured_wallets()
        if not wallets:
            context.user_data.pop("fsm", None)
            await msg.reply_text(
                "⚠️ No payment wallets are configured yet. Please contact "
                "support.", reply_markup=main_menu_markup(u.id))
            return
        rows = [[btn(f"💠 {label}", f"topnet:{code}")]
                for code, label, _ in wallets]
        rows.append([btn("⬅️  Back", "wallet")])
        await msg.reply_text(
            f"➕ <b>Top up</b>\nAmount: <b>{money(amount)} {CURRENCY}</b>\n\n"
            "Choose the network you want to pay on:",
            reply_markup=kb(rows), parse_mode=ParseMode.HTML,
        )
        return

    # ---------- buyer: submitting a top-up TXID
    if step == "topup_txid":
        tid = fsm["tid"]
        t = db.get_topup(tid)
        if not t or t["user_id"] != u.id:
            context.user_data.pop("fsm", None)
            return
        if rate_limited(u.id, "txid"):
            await msg.reply_text("⏳ Please wait a few seconds and try again.")
            return
        txid = text.split()[0] if text else ""
        if len(txid) < 10:
            await msg.reply_text(
                "That does not look like a transaction hash. "
                "Please paste the full TXID, or send /cancel."
            )
            return
        if db.topup_txid_exists(txid):
            await msg.reply_text(
                "This transaction hash has already been submitted. "
                "If you think this is a mistake, contact support."
            )
            return
        db.set_topup_txid(tid, txid)
        context.user_data.pop("fsm", None)
        await msg.reply_text(
            f"✅ Thanks! Top-up {topup_code(tid)} is now under review.\n"
            "Your balance will update as soon as it's confirmed.",
            reply_markup=main_menu_markup(u.id), parse_mode=ParseMode.HTML,
        )
        t = db.get_topup(tid)
        await notify_admins(
            context,
            "🔔 <b>New top-up to review</b>\n\n" + topup_detail_text(t),
            kb([[btn("✅ Approve", f"atk:{tid}"),
                 btn("❌ Reject", f"atn:{tid}")]]),
        )
        return

    # ---------- buyer: ticket message
    if step == "ticket_msg":
        tid = fsm["tid"]
        t = db.get_ticket(tid)
        if not t or t["user_id"] != u.id or t["status"] != "open":
            context.user_data.pop("fsm", None)
            return
        if rate_limited(u.id, "ticket_msg"):
            await msg.reply_text("⏳ Please wait a few seconds before sending another message.")
            return
        db.add_ticket_message(tid, "user", text or "(no text)")
        await msg.reply_text(
            "✅ Sent to support. You'll be notified here when they reply.",
            reply_markup=kb([[btn("🔒 Close ticket", f"tkc:{tid}")],
                             [btn("🏠 Home", "home")]]),
        )
        uname = f"@{u.username}" if u.username else u.first_name or "—"
        await notify_admins(
            context,
            f"🔔 <b>New support message</b> — ticket #{tid:05d}\n"
            f"From: {esc(uname)} (<code>{u.id}</code>)\n\n{esc(text)}",
            kb([[btn("💬 Reply", f"treply:{tid}"),
                 btn("🔒 Close", f"tkclose:{tid}")]]),
        )
        return

    if not is_admin(u.id):
        context.user_data.pop("fsm", None)
        return

    # ---------- admin flows
    if step == "topup_reject":
        tid = fsm["tid"]
        reason = "" if text == "/skip" else text
        context.user_data.pop("fsm", None)
        if not db.set_topup_status_from(
            tid, ("pending", "awaiting_payment"), "rejected", reason or None
        ):
            await msg.reply_text(f"Top-up {topup_code(tid)} already processed.")
            return
        t = db.get_topup(tid)
        try:
            await context.bot.send_message(
                t["user_id"],
                f"❌ Top-up {topup_code(tid)} was rejected."
                + (f"\n\nReason: {esc(reason)}" if reason else "")
                + "\n\nIf you believe this is an error, contact support.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:  # noqa: BLE001
            pass
        await msg.reply_text(f"Top-up {topup_code(tid)} rejected.")
        return

    if step == "ticket_reply":
        tid = fsm["tid"]
        t = db.get_ticket(tid)
        if not t:
            context.user_data.pop("fsm", None)
            return
        db.add_ticket_message(tid, "admin", text or "(no text)")
        context.user_data.pop("fsm", None)
        try:
            await context.bot.send_message(
                t["user_id"],
                f"💬 <b>Support reply</b> — ticket #{tid:05d}\n\n{esc(text)}",
                parse_mode=ParseMode.HTML,
            )
            await msg.reply_text("✅ Reply sent.")
        except Exception as e:  # noqa: BLE001
            await msg.reply_text(f"⚠️ Could not deliver reply: {e}")
        return

    if step == "reject":
        oid = fsm["oid"]
        reason = "" if text == "/skip" else text
        context.user_data.pop("fsm", None)
        if not db.set_order_status_from(
            oid, ("pending", "awaiting_payment"), "rejected", reason or None
        ):
            await msg.reply_text(f"Order {order_code(oid)} already processed.")
            return
        o = db.get_order(oid)
        try:
            await context.bot.send_message(
                o["user_id"],
                f"❌ Order {order_code(oid)} was rejected."
                + (f"\n\nReason: {esc(reason)}" if reason else "")
                + "\n\nIf you believe this is an error, contact support.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:  # noqa: BLE001
            pass
        await msg.reply_text(f"Order {order_code(oid)} rejected.")
        return

    if step == "wallet":
        db.set_setting(f"wallet_{fsm['net']}", text)
        context.user_data.pop("fsm", None)
        await msg.reply_text(
            f"✅ {fsm['net']} address saved:\n<code>{esc(text)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[btn("💠 Wallets", "aw")],
                             [btn("⚙️ Admin", "am")]]),
        )
        return

    # ---------- admin: coupon creation wizard
    if step == "coupon_new_code":
        code = text.strip().upper()
        if not code or db.get_coupon(code):
            await msg.reply_text("That code is empty or already taken. Try another.")
            return
        fsm.update(step="coupon_new_kind", code=code)
        await msg.reply_text(
            "Discount type — send <b>percent</b> or <b>fixed</b>.",
            parse_mode=ParseMode.HTML)
        return

    if step == "coupon_new_kind":
        kind = text.strip().lower()
        if kind not in ("percent", "fixed"):
            await msg.reply_text("Send exactly 'percent' or 'fixed'.")
            return
        fsm.update(step="coupon_new_amount", kind=kind)
        unit = "%" if kind == "percent" else CURRENCY
        await msg.reply_text(f"Send the discount amount (number, {unit}).")
        return

    if step == "coupon_new_amount":
        try:
            amount = float(text.replace(",", "."))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("Send a positive number.")
            return
        fsm.update(step="coupon_new_maxuses", amount=amount)
        await msg.reply_text(
            "Max total uses — send a number, or -1 for unlimited.")
        return

    if step == "coupon_new_maxuses":
        try:
            max_uses = int(text)
        except ValueError:
            await msg.reply_text("Send a whole number, or -1 for unlimited.")
            return
        cid = db.add_coupon(
            fsm["code"], fsm["kind"], fsm["amount"], max_uses=max_uses,
        )
        db.log_admin_action(u.id, "coupon_create", fsm["code"])
        context.user_data.pop("fsm", None)
        await msg.reply_text(
            f"✅ Coupon <b>{esc(fsm['code'])}</b> created.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([[btn("🎟 Open coupon", f"acpv:{cid}")],
                             [btn("⚙️ Admin", "am")]]))
        return

    # ---------- admin: manual balance adjust wizard
    if step == "adj_uid":
        try:
            target_uid = int(text.strip())
        except ValueError:
            await msg.reply_text("Send a numeric Telegram user ID.")
            return
        fsm.update(step="adj_amount", target_uid=target_uid)
        bal = db.get_balance(target_uid)
        await msg.reply_text(
            f"Current balance for <code>{target_uid}</code>: "
            f"{money(bal)} {CURRENCY}\n\n"
            "Send the amount to add (or a negative number to deduct).",
            parse_mode=ParseMode.HTML)
        return

    if step == "adj_amount":
        try:
            delta = float(text.replace(",", "."))
            if delta == 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("Send a non-zero number, e.g. 10 or -5.")
            return
        target_uid = fsm["target_uid"]
        new_bal = db.adjust_balance(target_uid, delta)
        db.log_admin_action(
            u.id, "adjust_balance",
            f"user {target_uid} {'+' if delta >= 0 else ''}{delta} -> {new_bal}",
        )
        context.user_data.pop("fsm", None)
        await msg.reply_text(
            f"✅ Balance updated. New balance: {money(new_bal)} {CURRENCY}",
            reply_markup=kb([[btn("⚙️ Admin", "am")]]))
        try:
            await context.bot.send_message(
                target_uid,
                f"💵 Your balance was adjusted by an admin: "
                f"{'+' if delta >= 0 else ''}{money(delta)} {CURRENCY}.\n"
                f"New balance: {money(new_bal)} {CURRENCY}.",
            )
        except Exception:  # noqa: BLE001
            pass
        return

    # ---------- admin: search
    if step == "admin_search":
        term = text.strip()
        context.user_data.pop("fsm", None)
        await show_admin_search_result(update, context, term, 0)
        return

    if step == "cat_new":
        db.add_category(text)
        context.user_data.pop("fsm", None)
        await msg.reply_text("✅ Category added.",
                             reply_markup=kb([[btn("📂 Categories", "ac")]]))
        return

    if step == "cat_rename":
        db.rename_category(fsm["cid"], text)
        context.user_data.pop("fsm", None)
        await msg.reply_text("✅ Renamed.",
                             reply_markup=kb([[btn("📂 Categories", "ac")]]))
        return

    if step == "welcome":
        db.set_setting("welcome", text)
        context.user_data.pop("fsm", None)
        await msg.reply_text("✅ Welcome message updated.",
                             reply_markup=kb([[btn("⚙️ Admin", "am")]]))
        return

    if step == "support":
        db.set_setting("support", text)
        context.user_data.pop("fsm", None)
        await msg.reply_text("✅ Support contact updated.",
                             reply_markup=kb([[btn("⚙️ Admin", "am")]]))
        return

    if step == "broadcast":
        context.user_data.pop("fsm", None)
        ids = db.all_user_ids()
        sent = failed = 0
        status = await msg.reply_text(f"Sending to {len(ids)} users…")
        for i, uid in enumerate(ids, 1):
            try:
                await context.bot.send_message(
                    uid, text, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True)
                sent += 1
            except (Forbidden, BadRequest):
                failed += 1
            except Exception:  # noqa: BLE001
                failed += 1
            await asyncio.sleep(0.05)
            if i % 25 == 0:
                try:
                    await status.edit_text(f"Sending… {i}/{len(ids)}")
                except Exception:  # noqa: BLE001
                    pass
        await status.edit_text(f"📣 Done. Delivered {sent}, failed {failed}.")
        return

    if step == "edit":
        pid, field = fsm["pid"], fsm["field"]
        if field == "photo":
            if photo_id:
                db.update_product(pid, photo_file_id=photo_id)
            elif text == "/skip":
                db.update_product(pid, photo_file_id=None)
            else:
                await msg.reply_text("Please send a photo, or /skip.")
                return
        elif field == "price":
            try:
                db.update_product(pid, price=float(text.replace(",", ".")))
            except ValueError:
                await msg.reply_text("Send a number, e.g. 19.99")
                return
        elif field == "stock":
            try:
                db.update_product(pid, stock=int(text))
            except ValueError:
                await msg.reply_text("Send a whole number, or -1 for unlimited.")
                return
        elif field == "delivery":
            db.update_product(
                pid, delivery_content=None if text == "/skip" else text)
        else:
            db.update_product(pid, **{field: text})
        context.user_data.pop("fsm", None)
        await msg.reply_text("✅ Updated.",
                             reply_markup=kb([[btn("📦 Open product",
                                                   f"apv:{pid}")],
                                              [btn("⚙️ Admin", "am")]]))
        return

    # ---------- admin: new-product wizard
    draft = fsm.get("draft", {})

    if step == "new_title":
        draft["title"] = text[:120]
        fsm.update(step="new_desc", draft=draft)
        await msg.reply_text(
            "Step 2/6 — send the <b>description</b> (or /skip).",
            parse_mode=ParseMode.HTML)
        return

    if step == "new_desc":
        draft["description"] = "" if text == "/skip" else text
        fsm.update(step="new_price", draft=draft)
        await msg.reply_text(
            f"Step 3/6 — send the <b>price</b> in {CURRENCY} (e.g. 25 or 9.99).",
            parse_mode=ParseMode.HTML)
        return

    if step == "new_price":
        try:
            draft["price"] = float(text.replace(",", "."))
        except ValueError:
            await msg.reply_text("Send a number, e.g. 19.99")
            return
        fsm.update(step="new_photo", draft=draft)
        await msg.reply_text(
            "Step 4/6 — send the product <b>image</b> as a photo (or /skip).",
            parse_mode=ParseMode.HTML)
        return

    if step == "new_photo":
        if photo_id:
            draft["photo"] = photo_id
        elif text != "/skip":
            await msg.reply_text("Send a photo, or /skip.")
            return
        fsm.update(step="new_delivery", draft=draft)
        await msg.reply_text(
            "Step 5/6 — send the <b>auto-delivery text</b> that the buyer gets "
            "the instant you approve their payment (a key, link, code…).\n\n"
            "Send /skip if you deliver manually.",
            parse_mode=ParseMode.HTML)
        return

    if step == "new_delivery":
        draft["delivery"] = None if text == "/skip" else text
        fsm.update(step="new_cat", draft=draft)
        await category_picker(update, context)
        return


async def on_error(update, context):
    log.exception("handler error: %s", context.error)


# ----------------------------------------------------------------- runtime
class Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def log_message(self, *args):
        pass


def start_health_server():
    try:
        HTTPServer(("0.0.0.0", PORT), Health).serve_forever()
    except Exception as e:  # noqa: BLE001
        log.warning("health server stopped: %s", e)


async def keepalive():
    """Free Postgres tiers pause when idle; a periodic ping avoids that."""
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            db.execute("SELECT 1", (), "one")
        except Exception as e:  # noqa: BLE001
            log.warning("keepalive failed: %s", e)


async def post_init(app: Application):
    asyncio.create_task(keepalive())
    me = await app.bot.get_me()
    log.info("running as @%s | admins: %s", me.username, ADMIN_IDS or "NONE")
    for admin_id in ADMIN_IDS:
        try:
            await app.bot.send_message(admin_id, "🤖 Shop bot is online.")
        except Exception:  # noqa: BLE001
            pass


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS is empty — nobody can access the admin panel!")

    db.init()
    if SEED_ON_BOOT:
        try:
            import seed
            seed.run()
        except Exception as e:  # noqa: BLE001
            log.warning("catalogue seed skipped: %s", e)
    threading.Thread(target=start_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("shop", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO) & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(
        filters.COMMAND & filters.Regex(r"^/skip"), on_message))
    app.add_error_handler(on_error)

    log.info("starting polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
