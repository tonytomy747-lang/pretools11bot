"""
Browser admin dashboard — runs in the same process as the bot, on the same
PORT Render already health-checks. No separate hosting needed.

Auth: single shared password (DASHBOARD_PASSWORD env var) -> session cookie.
Set DASHBOARD_PASSWORD in your host's dashboard/env, same place as BOT_TOKEN.
"""

import os
import time

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

import db

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip() or os.getenv(
    "BOT_TOKEN", "dev-secret-change-me"
)
SHOP_NAME = os.getenv("SHOP_NAME", "Digital Shop")
PAGE_SIZE = 20

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")


# ------------------------------------------------------------------ layout
def esc(text) -> str:
    import html as _html
    return _html.escape(str(text if text is not None else ""))


def page(title: str, body: str, active: str = "") -> HTMLResponse:
    nav_items = [
        ("", "Overview"),
        ("products", "Products"),
        ("orders", "Orders"),
        ("topups", "Top-ups"),
        ("coupons", "Coupons"),
        ("tickets", "Tickets"),
        ("users", "Users"),
        ("log", "Admin log"),
    ]
    nav = "".join(
        f'<a href="/{href}" class="{"active" if href == active else ""}">{label}</a>'
        for href, label in nav_items
    )
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · {esc(SHOP_NAME)} admin</title>
<style>
:root {{ color-scheme: light dark; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font-family:-apple-system,Segoe UI,Roboto,sans-serif; background:#0f1115; color:#e6e8eb; }}
header {{ display:flex; align-items:center; gap:1.5rem; padding:0.9rem 1.4rem; background:#171a21; border-bottom:1px solid #262a33; flex-wrap:wrap; }}
header h1 {{ font-size:1rem; margin:0; font-weight:600; color:#f5f6f7; }}
nav a {{ color:#9aa2ad; text-decoration:none; margin-right:1.1rem; font-size:0.88rem; padding:0.3rem 0; border-bottom:2px solid transparent; }}
nav a.active, nav a:hover {{ color:#fff; border-bottom-color:#5b8cff; }}
main {{ padding:1.4rem; max-width:1200px; margin:0 auto; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:0.9rem; margin-bottom:1.4rem; }}
.card {{ background:#171a21; border:1px solid #262a33; border-radius:10px; padding:0.9rem 1.1rem; }}
.card .num {{ font-size:1.6rem; font-weight:700; color:#fff; }}
.card .label {{ font-size:0.78rem; color:#9aa2ad; margin-top:0.15rem; }}
table {{ width:100%; border-collapse:collapse; background:#171a21; border:1px solid #262a33; border-radius:10px; overflow:hidden; font-size:0.86rem; }}
th, td {{ text-align:left; padding:0.55rem 0.8rem; border-bottom:1px solid #21252e; }}
th {{ color:#9aa2ad; font-weight:600; font-size:0.76rem; text-transform:uppercase; letter-spacing:.03em; }}
tr:last-child td {{ border-bottom:none; }}
.badge {{ display:inline-block; padding:0.15rem 0.5rem; border-radius:99px; font-size:0.74rem; font-weight:600; }}
.b-paid, .b-approved {{ background:#123d24; color:#5fd88a; }}
.b-pending {{ background:#3d3312; color:#e8c25a; }}
.b-awaiting_payment {{ background:#2a2d36; color:#9aa2ad; }}
.b-rejected, .b-cancelled {{ background:#3d1717; color:#ef7b7b; }}
.b-open {{ background:#123d24; color:#5fd88a; }}
.b-closed {{ background:#2a2d36; color:#9aa2ad; }}
.toolbar {{ display:flex; gap:0.6rem; margin-bottom:0.9rem; flex-wrap:wrap; align-items:center; }}
.toolbar a.btn, button, input[type=submit] {{ background:#5b8cff; color:#fff; border:none; padding:0.45rem 0.9rem; border-radius:7px; font-size:0.84rem; text-decoration:none; cursor:pointer; }}
.toolbar a.btn.secondary, button.secondary {{ background:#262a33; }}
.toolbar a.btn.danger, button.danger {{ background:#7a2323; }}
input[type=text], input[type=number], input[type=password], select, textarea {{
  background:#0f1115; border:1px solid #33384433; border:1px solid #333844; color:#e6e8eb;
  padding:0.4rem 0.6rem; border-radius:6px; font-size:0.85rem;
}}
textarea {{ width:100%; min-height:70px; font-family:inherit; }}
.pager {{ display:flex; gap:0.5rem; margin-top:0.9rem; }}
.pager a {{ color:#9aa2ad; text-decoration:none; padding:0.3rem 0.7rem; background:#171a21; border:1px solid #262a33; border-radius:6px; font-size:0.82rem; }}
.pager a.active {{ color:#fff; border-color:#5b8cff; }}
.mono {{ font-family:ui-monospace,Consolas,monospace; font-size:0.82rem; }}
form.inline {{ display:inline; }}
.muted {{ color:#9aa2ad; }}
.login-wrap {{ display:flex; align-items:center; justify-content:center; min-height:100vh; }}
.login-box {{ background:#171a21; border:1px solid #262a33; border-radius:12px; padding:2rem 2.2rem; width:100%; max-width:320px; }}
.login-box h1 {{ font-size:1.1rem; margin:0 0 1rem; }}
.login-box input {{ width:100%; margin-bottom:0.8rem; }}
.login-box button {{ width:100%; }}
.err {{ color:#ef7b7b; font-size:0.84rem; margin-bottom:0.8rem; }}
.section {{ margin-bottom:1.6rem; }}
.section h2 {{ font-size:0.95rem; color:#c7ccd3; margin:0 0 0.7rem; }}
</style></head>
<body>
<header>
  <h1>{esc(SHOP_NAME)} · admin</h1>
  <nav>{nav}</nav>
  <a href="/logout" style="margin-left:auto;color:#9aa2ad;font-size:0.82rem;text-decoration:none;">Log out</a>
</header>
<main>{body}</main>
</body></html>""")


def status_badge(status: str, labels: dict) -> str:
    label = labels.get(status, status)
    return f'<span class="badge b-{esc(status)}">{esc(label)}</span>'


def pager(base: str, page_no: int, total: int, size: int) -> str:
    pages = max(1, (total + size - 1) // size)
    if pages <= 1:
        return ""
    out = ['<div class="pager">']
    for i in range(pages):
        cls = "active" if i == page_no else ""
        sep = "&" if "?" in base else "?"
        out.append(f'<a class="{cls}" href="{base}{sep}page={i}">{i + 1}</a>')
    out.append("</div>")
    return "".join(out)


STATUS_LABEL = {
    "awaiting_payment": "Awaiting payment",
    "pending": "Under review",
    "paid": "Paid",
    "rejected": "Rejected",
    "cancelled": "Cancelled",
}
TOPUP_STATUS_LABEL = {
    "awaiting_payment": "Awaiting payment",
    "pending": "Under review",
    "approved": "Credited",
    "rejected": "Rejected",
    "cancelled": "Cancelled",
}


# ------------------------------------------------------------------- auth
def require_login(request: Request):
    return bool(request.session.get("ok"))


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, err: str = ""):
    if require_login(request):
        return RedirectResponse("/")
    err_html = f'<div class="err">{esc(err)}</div>' if err else ""
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Log in · {esc(SHOP_NAME)} admin</title>
<style>
:root {{ color-scheme: light dark; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:-apple-system,Segoe UI,Roboto,sans-serif; background:#0f1115; color:#e6e8eb; }}
.login-wrap {{ display:flex; align-items:center; justify-content:center; min-height:100vh; }}
.login-box {{ background:#171a21; border:1px solid #262a33; border-radius:12px; padding:2rem 2.2rem; width:100%; max-width:320px; }}
.login-box h1 {{ font-size:1.1rem; margin:0 0 1rem; color:#f5f6f7; }}
.login-box input {{ width:100%; margin-bottom:0.8rem; background:#0f1115; border:1px solid #333844; color:#e6e8eb; padding:0.5rem 0.6rem; border-radius:6px; font-size:0.9rem; }}
.login-box button {{ width:100%; background:#5b8cff; color:#fff; border:none; padding:0.55rem; border-radius:7px; font-size:0.9rem; cursor:pointer; }}
.err {{ color:#ef7b7b; font-size:0.84rem; margin-bottom:0.8rem; }}
</style></head>
<body><div class="login-wrap"><div class="login-box">
<h1>{esc(SHOP_NAME)} admin</h1>
{err_html}
<form method="post" action="/login">
  <input type="password" name="password" placeholder="Admin password" autofocus required>
  <button type="submit">Log in</button>
</form>
</div></div></body></html>""")


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    if not DASHBOARD_PASSWORD:
        return RedirectResponse(
            "/login?err=" + "DASHBOARD_PASSWORD is not set on the server.", status_code=303
        )
    if password == DASHBOARD_PASSWORD:
        request.session["ok"] = True
        request.session["t"] = time.time()
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?err=" + "Wrong password.", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


def guard(request: Request):
    """Returns a redirect response if not authed, else None."""
    if not require_login(request):
        return RedirectResponse("/login")
    return None


# --------------------------------------------------------------- overview
@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    if (r := guard(request)) is not None:
        return r
    total_orders = db.count_orders()
    pending_orders = db.count_orders(status="pending")
    total_topups = db.count_topups()
    pending_topups = db.count_topups(status="pending")
    open_tickets = db.count_tickets(status="open")
    users = db.count_users()
    revenue = db.revenue()
    products = db.count_products(only_active=False)

    cards = [
        (f"{revenue:,.2f}", "Revenue (paid orders)"),
        (str(users), "Users"),
        (str(products), "Products"),
        (str(total_orders), "Total orders"),
        (str(pending_orders), "Orders pending review"),
        (str(total_topups), "Total top-ups"),
        (str(pending_topups), "Top-ups pending review"),
        (str(open_tickets), "Open tickets"),
    ]
    grid = "".join(
        f'<div class="card"><div class="num">{esc(n)}</div><div class="label">{esc(l)}</div></div>'
        for n, l in cards
    )
    recent_orders = db.list_orders(limit=8, offset=0)
    rows = "".join(
        f"<tr><td class='mono'>#{o['id']:05d}</td><td>{esc(o['product_title'])}</td>"
        f"<td>{money(o['price'])}</td><td>{status_badge(o['status'], STATUS_LABEL)}</td>"
        f"<td class='muted'>{esc(o['created_at'])}</td></tr>"
        for o in recent_orders
    )
    body = f"""
    <div class="grid">{grid}</div>
    <div class="section">
      <h2>Recent orders</h2>
      <table><thead><tr><th>ID</th><th>Product</th><th>Price</th><th>Status</th><th>Created</th></tr></thead>
      <tbody>{rows or '<tr><td colspan=5 class="muted">No orders yet.</td></tr>'}</tbody></table>
    </div>
    """
    return page("Overview", body, active="")


def money(v):
    return f"{float(v or 0):,.2f}"


# --------------------------------------------------------------- products
@app.get("/products", response_class=HTMLResponse)
def products_list(request: Request, page_no: int = 0):
    if (r := guard(request)) is not None:
        return r
    total = db.count_products(only_active=False)
    items = db.list_products(only_active=False, limit=PAGE_SIZE, offset=page_no * PAGE_SIZE)
    cats = {c["id"]: c["name"] for c in db.list_categories()}
    rows = "".join(
        f"<tr><td class='mono'>{p['id']}</td><td>{esc(p['title'])}</td>"
        f"<td>{esc(cats.get(p['category_id'], '—'))}</td>"
        f"<td>{money(p['price'])}</td>"
        f"<td>{'∞' if p['stock'] < 0 else p['stock']}</td>"
        f"<td>{'✅' if p['is_active'] else '⛔'}</td>"
        f"<td>"
        f"<form class='inline' method='post' action='/products/{p['id']}/toggle'><button class='secondary'>{'Deactivate' if p['is_active'] else 'Activate'}</button></form> "
        f"<form class='inline' method='post' action='/products/{p['id']}/delete' onsubmit=\"return confirm('Delete this product?')\"><button class='danger'>Delete</button></form>"
        f"</td></tr>"
        for p in items
    )
    body = f"""
    <div class="toolbar"><span class="muted">{total} products</span></div>
    <table><thead><tr><th>ID</th><th>Title</th><th>Category</th><th>Price</th><th>Stock</th><th>Active</th><th></th></tr></thead>
    <tbody>{rows or '<tr><td colspan=7 class="muted">No products.</td></tr>'}</tbody></table>
    {pager('/products', page_no, total, PAGE_SIZE)}
    <p class="muted">Add / edit products from the bot's Telegram admin panel (needs photo upload) — this page manages visibility and stock.</p>
    """
    return page("Products", body, active="products")


@app.post("/products/{pid}/toggle")
def product_toggle(request: Request, pid: int):
    if (r := guard(request)) is not None:
        return r
    p = db.get_product(pid)
    if p:
        db.update_product(pid, is_active=0 if p["is_active"] else 1)
    return RedirectResponse("/products", status_code=303)


@app.post("/products/{pid}/delete")
def product_delete(request: Request, pid: int):
    if (r := guard(request)) is not None:
        return r
    db.delete_product(pid)
    return RedirectResponse("/products", status_code=303)


# ----------------------------------------------------------------- orders
@app.get("/orders", response_class=HTMLResponse)
def orders_list(request: Request, page_no: int = 0, status: str = ""):
    if (r := guard(request)) is not None:
        return r
    status = status or None
    total = db.count_orders(status=status)
    items = db.list_orders(status=status, limit=PAGE_SIZE, offset=page_no * PAGE_SIZE)
    filters_html = "".join(
        f'<a class="btn {"secondary" if status != s else ""}" href="/orders?status={s}">{l}</a>'
        for s, l in [("", "All")] + list(STATUS_LABEL.items())
    )
    rows = "".join(
        f"<tr><td class='mono'>#{o['id']:05d}</td><td>{esc(o['username'] or o['user_id'])}</td>"
        f"<td>{esc(o['product_title'])}</td><td>{money(o['price'])}</td>"
        f"<td>{esc(o['network'] or '—')}</td>"
        f"<td class='mono'>{esc((o['txid'] or '—')[:16])}</td>"
        f"<td>{status_badge(o['status'], STATUS_LABEL)}</td>"
        f"<td>"
        + (f"""<form class='inline' method='post' action='/orders/{o['id']}/status'><input type='hidden' name='status' value='paid'><button>Approve</button></form>
             <form class='inline' method='post' action='/orders/{o['id']}/status'><input type='hidden' name='status' value='rejected'><button class='danger'>Reject</button></form>"""
           if o['status'] == 'pending' else '<span class="muted">—</span>')
        + f"</td></tr>"
        for o in items
    )
    body = f"""
    <div class="toolbar">{filters_html}</div>
    <table><thead><tr><th>ID</th><th>Buyer</th><th>Product</th><th>Price</th><th>Network</th><th>TXID</th><th>Status</th><th></th></tr></thead>
    <tbody>{rows or '<tr><td colspan=8 class="muted">No orders.</td></tr>'}</tbody></table>
    {pager('/orders?status=' + (status or ''), page_no, total, PAGE_SIZE)}
    """
    return page("Orders", body, active="orders")


@app.post("/orders/{oid}/status")
def order_set_status(request: Request, oid: int, status: str = Form(...)):
    if (r := guard(request)) is not None:
        return r
    ok = db.set_order_status_from(oid, ["pending"], status, note="via dashboard")
    if ok and status == "rejected":
        o = db.get_order(oid)
        if o:
            db.restock_one(o["product_id"])
    return RedirectResponse("/orders", status_code=303)


# ----------------------------------------------------------------- topups
@app.get("/topups", response_class=HTMLResponse)
def topups_list(request: Request, page_no: int = 0, status: str = ""):
    if (r := guard(request)) is not None:
        return r
    status = status or None
    total = db.count_topups(status=status)
    items = db.list_topups(status=status, limit=PAGE_SIZE, offset=page_no * PAGE_SIZE)
    filters_html = "".join(
        f'<a class="btn {"secondary" if status != s else ""}" href="/topups?status={s}">{l}</a>'
        for s, l in [("", "All")] + list(TOPUP_STATUS_LABEL.items())
    )
    rows = "".join(
        f"<tr><td class='mono'>#{t['id']:05d}</td><td>{esc(t['username'] or t['user_id'])}</td>"
        f"<td>{money(t['amount'])}</td><td>{esc(t['network'] or '—')}</td>"
        f"<td class='mono'>{esc((t['txid'] or '—')[:16])}</td>"
        f"<td>{status_badge(t['status'], TOPUP_STATUS_LABEL)}</td>"
        f"<td>"
        + (f"""<form class='inline' method='post' action='/topups/{t['id']}/status'><input type='hidden' name='status' value='approved'><button>Approve</button></form>
             <form class='inline' method='post' action='/topups/{t['id']}/status'><input type='hidden' name='status' value='rejected'><button class='danger'>Reject</button></form>"""
           if t['status'] == 'pending' else '<span class="muted">—</span>')
        + f"</td></tr>"
        for t in items
    )
    body = f"""
    <div class="toolbar">{filters_html}</div>
    <table><thead><tr><th>ID</th><th>User</th><th>Amount</th><th>Network</th><th>TXID</th><th>Status</th><th></th></tr></thead>
    <tbody>{rows or '<tr><td colspan=7 class="muted">No top-ups.</td></tr>'}</tbody></table>
    {pager('/topups?status=' + (status or ''), page_no, total, PAGE_SIZE)}
    """
    return page("Top-ups", body, active="topups")


@app.post("/topups/{tid}/status")
def topup_set_status(request: Request, tid: int, status: str = Form(...)):
    if (r := guard(request)) is not None:
        return r
    t = db.get_topup(tid)
    ok = db.set_topup_status_from(tid, ["pending"], status, note="via dashboard")
    if ok and status == "approved" and t:
        db.adjust_balance(t["user_id"], float(t["amount"] or 0))
    return RedirectResponse("/topups", status_code=303)


# ---------------------------------------------------------------- coupons
@app.get("/coupons", response_class=HTMLResponse)
def coupons_list(request: Request, page_no: int = 0):
    if (r := guard(request)) is not None:
        return r
    total = db.count_coupons()
    items = db.list_coupons(limit=PAGE_SIZE, offset=page_no * PAGE_SIZE)
    rows = "".join(
        f"<tr><td class='mono'>{esc(c['code'])}</td><td>{esc(c['kind'])}</td>"
        f"<td>{money(c['amount'])}</td>"
        f"<td>{c['used_count']}{'' if c['max_uses'] < 0 else '/' + str(c['max_uses'])}</td>"
        f"<td>{'✅' if c['is_active'] else '⛔'}</td>"
        f"<td class='muted'>{esc(c['expires_at'] or 'never')}</td>"
        f"<td>"
        f"<form class='inline' method='post' action='/coupons/{c['id']}/toggle'><button class='secondary'>{'Deactivate' if c['is_active'] else 'Activate'}</button></form> "
        f"<form class='inline' method='post' action='/coupons/{c['id']}/delete' onsubmit=\"return confirm('Delete this coupon?')\"><button class='danger'>Delete</button></form>"
        f"</td></tr>"
        for c in items
    )
    body = f"""
    <div class="section">
      <h2>New coupon</h2>
      <form method="post" action="/coupons/new" style="display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center;">
        <input type="text" name="code" placeholder="CODE" required>
        <select name="kind"><option value="percent">% off</option><option value="fixed">Fixed amount</option></select>
        <input type="number" step="0.01" name="amount" placeholder="Amount" required style="width:110px">
        <input type="number" name="max_uses" placeholder="Max uses (-1=∞)" value="-1" style="width:150px">
        <input type="number" name="max_uses_per_user" placeholder="Per user" value="1" style="width:100px">
        <input type="number" step="0.01" name="min_order" placeholder="Min order" value="0" style="width:110px">
        <button type="submit">Create</button>
      </form>
    </div>
    <table><thead><tr><th>Code</th><th>Kind</th><th>Amount</th><th>Used</th><th>Active</th><th>Expires</th><th></th></tr></thead>
    <tbody>{rows or '<tr><td colspan=7 class="muted">No coupons.</td></tr>'}</tbody></table>
    {pager('/coupons', page_no, total, PAGE_SIZE)}
    """
    return page("Coupons", body, active="coupons")


@app.post("/coupons/new")
def coupon_create(
    request: Request,
    code: str = Form(...),
    kind: str = Form(...),
    amount: float = Form(...),
    max_uses: int = Form(-1),
    max_uses_per_user: int = Form(1),
    min_order: float = Form(0),
):
    if (r := guard(request)) is not None:
        return r
    try:
        db.add_coupon(code, kind, amount, max_uses, max_uses_per_user, min_order)
    except Exception:
        pass
    return RedirectResponse("/coupons", status_code=303)


@app.post("/coupons/{cid}/toggle")
def coupon_toggle(request: Request, cid: int):
    if (r := guard(request)) is not None:
        return r
    c = db.get_coupon_by_id(cid)
    if c:
        db.set_coupon_active(cid, not c["is_active"])
    return RedirectResponse("/coupons", status_code=303)


@app.post("/coupons/{cid}/delete")
def coupon_delete(request: Request, cid: int):
    if (r := guard(request)) is not None:
        return r
    db.delete_coupon(cid)
    return RedirectResponse("/coupons", status_code=303)


# ---------------------------------------------------------------- tickets
@app.get("/tickets", response_class=HTMLResponse)
def tickets_list(request: Request, page_no: int = 0, status: str = "open"):
    if (r := guard(request)) is not None:
        return r
    status = status or None
    total = db.count_tickets(status=status)
    items = db.list_tickets(status=status, limit=PAGE_SIZE, offset=page_no * PAGE_SIZE)
    filters_html = "".join(
        f'<a class="btn {"secondary" if status != s else ""}" href="/tickets?status={s}">{l}</a>'
        for s, l in [("", "All"), ("open", "Open"), ("closed", "Closed")]
    )
    rows = "".join(
        f"<tr><td class='mono'>#{t['id']}</td><td>{esc(t['username'] or t['user_id'])}</td>"
        f"<td>{status_badge(t['status'], {'open': 'Open', 'closed': 'Closed'})}</td>"
        f"<td class='muted'>{esc((t['last_msg_preview'] or '')[:60])}</td>"
        f"<td class='muted'>{esc(t['updated_at'])}</td>"
        f"<td><a class='btn secondary' href='/tickets/{t['id']}'>Open</a></td></tr>"
        for t in items
    )
    body = f"""
    <div class="toolbar">{filters_html}</div>
    <table><thead><tr><th>ID</th><th>User</th><th>Status</th><th>Last message</th><th>Updated</th><th></th></tr></thead>
    <tbody>{rows or '<tr><td colspan=6 class="muted">No tickets.</td></tr>'}</tbody></table>
    {pager('/tickets?status=' + (status or ''), page_no, total, PAGE_SIZE)}
    """
    return page("Tickets", body, active="tickets")


@app.get("/tickets/{tid}", response_class=HTMLResponse)
def ticket_detail(request: Request, tid: int):
    if (r := guard(request)) is not None:
        return r
    t = db.get_ticket(tid)
    if not t:
        return RedirectResponse("/tickets")
    msgs = db.list_ticket_messages(tid, limit=100)
    thread = "".join(
        f"<div style='margin-bottom:0.7rem'><b>{esc(m['sender'])}</b> "
        f"<span class='muted' style='font-size:0.78rem'>{esc(m['created_at'])}</span>"
        f"<div>{esc(m['body'])}</div></div>"
        for m in msgs
    )
    close_btn = (
        f"<form method='post' action='/tickets/{tid}/close'><button class='danger'>Close ticket</button></form>"
        if t["status"] == "open" else ""
    )
    body = f"""
    <div class="section">
      <h2>Ticket #{tid} — {esc(t['username'] or t['user_id'])} {status_badge(t['status'], {'open': 'Open', 'closed': 'Closed'})}</h2>
      <div class="card">{thread or '<span class="muted">No messages.</span>'}</div>
      <p class="muted">Reply from the bot's Telegram admin panel — this view is read-only.</p>
      {close_btn}
    </div>
    """
    return page(f"Ticket #{tid}", body, active="tickets")


@app.post("/tickets/{tid}/close")
def ticket_close(request: Request, tid: int):
    if (r := guard(request)) is not None:
        return r
    db.close_ticket(tid)
    return RedirectResponse(f"/tickets/{tid}", status_code=303)


# ------------------------------------------------------------------ users
@app.get("/users", response_class=HTMLResponse)
def users_list(request: Request, page_no: int = 0):
    if (r := guard(request)) is not None:
        return r
    rows_data = db.execute(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?",
        (PAGE_SIZE, page_no * PAGE_SIZE), "all",
    )
    total = db.count_users()
    rows = "".join(
        f"<tr><td class='mono'>{u['user_id']}</td><td>{esc(u['username'] or '—')}</td>"
        f"<td>{esc(u['first_name'] or '—')}</td><td>{money(u['balance'])}</td>"
        f"<td>{'🚫' if u['is_blocked'] else '✅'}</td>"
        f"<td class='muted'>{esc(u['joined_at'])}</td>"
        f"<td><form class='inline' method='post' action='/users/{u['user_id']}/block'><button class='{'secondary' if u['is_blocked'] else 'danger'}'>{'Unblock' if u['is_blocked'] else 'Block'}</button></form></td></tr>"
        for u in rows_data
    )
    body = f"""
    <div class="toolbar"><span class="muted">{total} users</span></div>
    <table><thead><tr><th>ID</th><th>Username</th><th>Name</th><th>Balance</th><th>Active</th><th>Joined</th><th></th></tr></thead>
    <tbody>{rows or '<tr><td colspan=7 class="muted">No users.</td></tr>'}</tbody></table>
    {pager('/users', page_no, total, PAGE_SIZE)}
    """
    return page("Users", body, active="users")


@app.post("/users/{uid}/block")
def user_toggle_block(request: Request, uid: int):
    if (r := guard(request)) is not None:
        return r
    db.set_blocked(uid, not db.is_blocked(uid))
    return RedirectResponse("/users", status_code=303)


# -------------------------------------------------------------- admin log
@app.get("/log", response_class=HTMLResponse)
def admin_log(request: Request, page_no: int = 0):
    if (r := guard(request)) is not None:
        return r
    total = db.count_admin_log()
    items = db.list_admin_log(limit=PAGE_SIZE, offset=page_no * PAGE_SIZE)
    rows = "".join(
        f"<tr><td class='mono'>{a['admin_id']}</td><td>{esc(a['action'])}</td>"
        f"<td class='muted'>{esc(a['detail'])}</td><td class='muted'>{esc(a['created_at'])}</td></tr>"
        for a in items
    )
    body = f"""
    <table><thead><tr><th>Admin</th><th>Action</th><th>Detail</th><th>When</th></tr></thead>
    <tbody>{rows or '<tr><td colspan=4 class="muted">No log entries.</td></tr>'}</tbody></table>
    {pager('/log', page_no, total, PAGE_SIZE)}
    """
    return page("Admin log", body, active="log")


# ---------------------------------------------------------------- health
@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"
