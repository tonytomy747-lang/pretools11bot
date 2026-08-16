"""
Telegram Digital Shop Bot — USDT (manual verification).

Free to run: Telegram hosts your product images (we only store file_id),
Postgres/SQLite holds the data, and payment verification is done by you.
"""

import asyncio
import html
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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

PAGE_SIZE = 6
ADMIN_PAGE_SIZE = 8

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

DEFAULT_WELCOME = (
    "Welcome to <b>{shop}</b>.\n\n"
    "Browse the catalogue, pick a product and pay with USDT on the network "
    "you prefer. After you send the payment, submit your transaction hash "
    "and an admin will confirm your order."
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


def kb(rows):
    return InlineKeyboardMarkup(rows)


def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


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
        [btn("🧾  My orders", "myorders:0"), btn("💬  Support", "support")],
    ]
    if is_admin(user_id):
        rows.append([btn("⚙️  Admin panel", "am")])
    return kb(rows)


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
    total = db.count_products(category_id=cat_filter, only_active=True)
    items = db.list_products(
        category_id=cat_filter, only_active=True,
        limit=PAGE_SIZE, offset=page * PAGE_SIZE,
    )

    title = esc(cat["name"]) if cat else "All products"
    if not total:
        rows = [[btn("⬅️  Back", back)]]
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

    rows = []
    if p["stock"] != 0:
        rows.append([btn(f"💳  Buy for {money(p['price'])} {CURRENCY}",
                         f"buy:{p['id']}")])
    rows.append([btn("⬅️  Back", f"cat:{cid}:{page}"), btn("🏠  Home", "home")])

    await render(update, "\n".join(parts), kb(rows),
                 photo=p["photo_file_id"] or None)


async def show_networks(update, context, pid: int):
    p = db.get_product(pid)
    if not p or not p["is_active"] or p["stock"] == 0:
        await render(update, "This product is no longer available.",
                     kb([[btn("⬅️  Back to shop", "shop")]]))
        return

    wallets = configured_wallets()
    if not wallets:
        await render(
            update,
            "⚠️ No payment wallets are configured yet.\n"
            "Please contact support.",
            kb([[btn("⬅️  Back", f"prod:{pid}:0:0")]]),
        )
        return

    rows = [[btn(f"💠 {label}", f"net:{pid}:{code}")]
            for code, label, _ in wallets]
    rows.append([btn("⬅️  Back", f"prod:{pid}:0:0")])

    txt = (
        f"<b>{esc(p['title'])}</b>\n"
        f"Amount: <b>{money(p['price'])} {CURRENCY}</b>\n\n"
        "Choose the network you want to pay on:"
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

    oid = db.create_order(user.id, user.username, p, net, address)

    txt = (
        f"🧾 <b>Order {order_code(oid)}</b>\n\n"
        f"Product: <b>{esc(p['title'])}</b>\n"
        f"Amount: <b>{money(p['price'])} {CURRENCY}</b>\n"
        f"Network: <b>{esc(NET_LABEL.get(net, net))}</b>\n\n"
        f"Send exactly <b>{money(p['price'])} {CURRENCY}</b> to this address:\n"
        f"<code>{esc(address)}</code>\n"
        "<i>(tap the address to copy)</i>\n\n"
        "⚠️ Send only USDT on the "
        f"<b>{esc(net)}</b> network. Funds sent on another network are lost.\n\n"
        "When the transfer is done, tap <b>I have paid</b> and send your "
        "transaction hash (TXID)."
    )
    rows = [
        [btn("✅  I have paid", f"paid:{oid}")],
        [btn("🚫  Cancel order", f"cxl:{oid}")],
    ]
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


async def show_support(update, context):
    handle = db.get_setting("support", "")
    txt = "💬 <b>Support</b>\n\n"
    if handle:
        txt += f"Contact: {esc(handle)}\n\n"
    txt += (
        "Payments are checked manually. After you submit your TXID an admin "
        "reviews it, usually within a few hours. Your Telegram ID is "
        f"<code>{update.effective_user.id}</code> — include it when you "
        "message support."
    )
    await render(update, txt, kb([[btn("🏠  Home", "home")]]))


# ----------------------------------------------------------------- admin UI
async def show_admin(update, context):
    pend = db.count_orders(status="pending")
    txt = (
        "⚙️ <b>Admin panel</b>\n\n"
        f"Pending payments to review: <b>{pend}</b>"
    )
    rows = [
        [btn(f"🔎  Review payments ({pend})", "ao:pending:0")],
        [btn("📦  Products", "ap:0"), btn("📂  Categories", "ac")],
        [btn("💠  Wallets", "aw"), btn("📊  Stats", "astat")],
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
    rows.append([btn("⬅️  Back", "ao:pending:0"), btn("🏠  Admin", "am")])
    await render(update, order_detail_text(o), kb(rows))


# ----------------------------------------------------------------- commands
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.upsert_user(u.id, u.username, u.first_name)
    context.user_data.pop("fsm", None)
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
    await q.answer()

    parts = data.split(":")
    head = parts[0]

    # navigating away from a prompt cancels that prompt
    if head in ("shop", "cat", "prod", "buy", "net", "myorders", "support"):
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
    if head == "prod":
        return await show_product(update, context, int(parts[1]),
                                  int(parts[2]), int(parts[3]))
    if head == "buy":
        return await show_networks(update, context, int(parts[1]))
    if head == "net":
        return await show_payment(update, context, int(parts[1]), parts[2])
    if head == "myorders":
        return await show_my_orders(update, context, int(parts[1]))
    if head == "support":
        return await show_support(update, context)

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

    if head == "aok":
        oid = int(parts[1])
        o = db.get_order(oid)
        if not o:
            return
        db.set_order_status(oid, "paid")
        p = db.get_product(o["product_id"])
        if p and p["stock"] > 0:
            db.update_product(p["id"], stock=p["stock"] - 1)
        msg = (
            f"✅ <b>Payment confirmed</b> — order {order_code(oid)}\n\n"
            f"Product: <b>{esc(o['product_title'])}</b>\n"
            f"Amount: {money(o['price'])} {CURRENCY} ({esc(o['network'])})\n\n"
        )
        if p and p["delivery_content"]:
            msg += "Here is your product:\n\n" + esc(p["delivery_content"])
        else:
            msg += "An admin will deliver your product shortly."
        try:
            await context.bot.send_message(o["user_id"], msg,
                                           parse_mode=ParseMode.HTML,
                                           disable_web_page_preview=True)
        except Exception as e:  # noqa: BLE001
            log.warning("could not notify buyer %s: %s", o["user_id"], e)
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
    msg = update.message
    text = (msg.text or msg.caption or "").strip()
    photo_id = msg.photo[-1].file_id if msg.photo else None
    fsm = context.user_data.get("fsm")

    if not fsm:
        await msg.reply_text(
            "Use the menu below 👇", reply_markup=main_menu_markup(u.id)
        )
        return

    step = fsm.get("step")

    # ---------- buyer: submitting a TXID
    if step == "txid":
        oid = fsm["oid"]
        o = db.get_order(oid)
        if not o or o["user_id"] != u.id:
            context.user_data.pop("fsm", None)
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

    if not is_admin(u.id):
        context.user_data.pop("fsm", None)
        return

    # ---------- admin flows
    if step == "reject":
        oid = fsm["oid"]
        reason = "" if text == "/skip" else text
        db.set_order_status(oid, "rejected", reason or None)
        context.user_data.pop("fsm", None)
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
