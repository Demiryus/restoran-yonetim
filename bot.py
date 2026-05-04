"""
Restoran Yönetim Telegram Botu
Commands:
  /start              - Welcome
  /income 500 Lunch   - Add income
  /summary            - Today's summary
  /stock              - Stock status
  /stockset chicken 10 kg  - Set stock quantity
  /stockuse chicken 2      - Deduct from stock
  /stockdel chicken        - Delete stock item
  Photo                    - Process receipt
"""
import os
from datetime import datetime, time
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes,
)

from database import get_db, init_db, apply_aliases, get_categories
from ai_parser import parse_receipt

load_dotenv()

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEB_URL    = os.getenv("WEB_URL", "http://localhost:8000")
PHOTOS_DIR = os.getenv("PHOTOS_DIR", "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)

# Comma-separated Telegram user IDs allowed to use the bot.
# Example: ALLOWED_USER_IDS=123456789,987654321
# Leave empty to allow everyone (not recommended for production).
_raw_ids = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS: set[int] = {
    int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()
}

# Daily summary time (UTC). Override with SUMMARY_TIME_UTC=HH:MM
_summary_time = os.getenv("SUMMARY_TIME_UTC", "20:00").split(":")
SUMMARY_TIME = time(int(_summary_time[0]), int(_summary_time[1]))

# Daily backup time (UTC). Override with BACKUP_TIME_UTC=HH:MM
_backup_time = os.getenv("BACKUP_TIME_UTC", "03:00").split(":")
BACKUP_TIME = time(int(_backup_time[0]), int(_backup_time[1]))

# Database path used by backup job (matches database.py default)
DB_PATH = os.getenv("DB_PATH", "restoran.db")

# Comma-separated user IDs that receive the daily summary.
# Defaults to ALLOWED_USER_IDS if not set separately.
_raw_notify = os.getenv("NOTIFY_USER_IDS", _raw_ids)
NOTIFY_USER_IDS: list[int] = [
    int(x.strip()) for x in _raw_notify.split(",") if x.strip().isdigit()
]


# ──────────────────────────── Auth guard ──────────────────────────
def _is_allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True  # open mode — no restrictions
    return update.effective_user.id in ALLOWED_USER_IDS

async def _deny(update: Update):
    uid = update.effective_user.id
    name = update.effective_user.username or update.effective_user.first_name
    await update.message.reply_text(
        f"Access denied. Your user ID is `{uid}`.\n"
        "Ask the administrator to add you to the allowed list.",
        parse_mode="Markdown",
    )
    print(f"[AUTH] Blocked user: {name} (id={uid})")


# ──────────────────────────── /start ──────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await _deny(update); return
    await update.message.reply_text(
        "*Bodega updated 5.5.26*\n\n"
        "Send a receipt photo to add items to stock automatically.\n"
        "Add caption *use* / *consume* to deduct from stock instead.\n"
        "Add caption *refund* / *return* / *iade* for vendor refunds.\n\n"
        "Commands:\n"
        "`/income 500 Lunch service` — Add income\n"
        "`/expense 3500 Monthly rent rent` — Add manual expense\n"
        "`/summary` — Today's income/expense summary\n"
        "`/weeklyreport` — 7-day comparison report\n"
        "`/stock` — View stock levels\n"
        "`/stockset chicken 5 kg` — Set stock quantity\n"
        "`/stockuse chicken 2` — Deduct from stock\n"
        "`/stockdel chicken` — Delete stock item\n"
        "`/backup` — Get a backup of the database now\n"
        f"\nDashboard: {WEB_URL}",
        parse_mode="Markdown",
    )


# ──────────────────────────── Photo handler ────────────────────────

TUKETIM_KELIMELERI = {"kullan", "use", "consume", "deduct", "çıkar", "cikar", "tüket", "tuket", "sarf"}
IADE_KELIMELERI    = {"iade", "refund", "return", "geri iade"}

def _receipt_mode(caption: str | None) -> str:
    """Returns 'refund', 'consumption', or 'expense'."""
    if not caption:
        return "expense"
    low = caption.lower()
    if any(k in low for k in IADE_KELIMELERI):
        return "refund"
    if any(k in low for k in TUKETIM_KELIMELERI):
        return "consumption"
    return "expense"

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await _deny(update); return

    caption  = update.message.caption or ""
    mode     = _receipt_mode(caption)
    tuketim  = (mode == "consumption")
    iade     = (mode == "refund")
    stock_subtracts = tuketim or iade
    if iade:
        mod_text = "refund — deducting from stock & expenses"
    elif tuketim:
        mod_text = "deducting from stock"
    else:
        mod_text = "adding to stock"

    msg = await update.message.reply_text(f"Downloading receipt... ({mod_text})")

    # ── Step 1: Download photo ──────────────────────────────────────
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    photo_path = f"{PHOTOS_DIR}/{ts}_{photo.file_id}.jpg"
    await tg_file.download_to_drive(photo_path)

    # ── Step 2: Save receipt record immediately (parse_status=pending) ──
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO receipts
            (telegram_user_id, telegram_username, photo_path,
             store_name, receipt_date, total_amount, currency, type,
             parse_status, raw_ai_response)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        str(update.effective_user.id),
        update.effective_user.username or "unknown",
        photo_path,
        None, None, 0,
        "CAD",
        mode,
        "pending",
        None,
    ))
    receipt_id = cur.lastrowid
    db.commit()

    # ── Step 3: AI parsing ──────────────────────────────────────────
    await msg.edit_text(f"Analyzing receipt with AI... ({mod_text})")

    try:
        parsed, raw = parse_receipt(photo_path, categories=get_categories())

        cur.execute("""
            UPDATE receipts SET
                store_name      = ?,
                receipt_date    = ?,
                total_amount    = ?,
                tax_amount      = ?,
                currency        = ?,
                parse_status    = 'success',
                parse_error     = NULL,
                raw_ai_response = ?
            WHERE id = ?
        """, (
            parsed.get("store_name") or "Unknown",
            parsed.get("receipt_date"),
            parsed.get("total_amount") or 0,
            parsed.get("tax_amount") or 0,
            parsed.get("currency") or "CAD",
            raw,
            receipt_id,
        ))

        items = apply_aliases(parsed.get("items") or [])
        stok_satirlari = []

        for item in items:
            name    = item.get("item_name") or "?"
            qty     = float(item.get("quantity") or 0)
            unit    = item.get("unit") or ""
            cat     = item.get("category") or "other"
            u_price = float(item.get("unit_price") or 0)
            t_price = float(item.get("total_price") or 0)

            cur.execute("""
                INSERT INTO receipt_items
                    (receipt_id, item_name, category, quantity, unit, unit_price, total_price)
                VALUES (?,?,?,?,?,?,?)
            """, (receipt_id, name, cat, qty, unit, u_price, t_price))

            if name and qty > 0:
                if stock_subtracts:
                    cur.execute("""
                        UPDATE stock SET
                            current_quantity = MAX(0, current_quantity - ?),
                            last_updated     = datetime('now','localtime')
                        WHERE item_name = ?
                    """, (qty, name))
                    stok_satirlari.append(f"  \u2796 {name}: -{qty:.1f} {unit}")
                else:
                    cur.execute("""
                        INSERT INTO stock (item_name, category, current_quantity, unit, last_updated)
                        VALUES (?,?,?,?, datetime('now','localtime'))
                        ON CONFLICT(item_name) DO UPDATE SET
                            current_quantity = current_quantity + ?,
                            category         = COALESCE(excluded.category, category),
                            last_updated     = datetime('now','localtime')
                    """, (name, cat, qty, unit, qty))
                    stok_satirlari.append(f"  \u2795 {name}: +{qty:.1f} {unit}")

        db.commit()
        db.close()

        cur_sym = parsed.get("currency") or "CAD"
        total   = parsed.get("total_amount") or 0
        item_lines = "\n".join(
            f"  \u2022 {i.get('item_name','?')}  "
            f"{i.get('quantity','?')} {i.get('unit','')}  "
            f"\u2192 {float(i.get('total_price') or 0):.2f} {cur_sym}"
            for i in items[:12]
        ) or "  (could not read items)"

        stok_blok = "\n".join(stok_satirlari[:12]) if stok_satirlari else "  (no stock changes)"
        if iade:
            mod_emoji = "\U0001F501"  # 🔁
            stock_label = "Stock returned (refund)"
            header = "*Refund saved!*"
        elif tuketim:
            mod_emoji = "\U0001F4E4"  # 📤
            stock_label = "Stock deducted"
            header = "*Receipt saved!*"
        else:
            mod_emoji = "\U0001F4E5"  # 📥
            stock_label = "Stock updated"
            header = "*Receipt saved!*"

        tax_amt = parsed.get("tax_amount") or 0
        tax_line = f"Tax: *{tax_amt:.2f} {cur_sym}*\n" if tax_amt else ""

        await msg.edit_text(
            f"{header}\n\n"
            f"Store: {parsed.get('store_name','?')}\n"
            f"Date: {parsed.get('receipt_date') or 'Unknown'}\n"
            f"Total: *{total:.2f} {cur_sym}*\n"
            f"{tax_line}\n"
            f"Items ({len(items)}):\n{item_lines}\n\n"
            f"{mod_emoji} *{stock_label}:*\n{stok_blok}\n\n"
            f"[Open Dashboard]({WEB_URL})",
            parse_mode="Markdown",
        )

    except Exception as e:
        db.execute("""
            UPDATE receipts SET
                parse_status = 'failed',
                parse_error  = ?
            WHERE id = ?
        """, (str(e)[:500], receipt_id))
        db.commit()
        db.close()

        await msg.edit_text(
            f"*Photo saved* (receipt #{receipt_id}), but AI parsing failed.\n"
            f"`{str(e)[:200]}`\n\n"
            f"You can retry from the dashboard: {WEB_URL}\n"
            "Or try taking a clearer, well-lit photo.",
            parse_mode="Markdown",
        )


# ──────────────────────────── /expense ────────────────────────────
async def cmd_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /expense 3500 rent  or  /expense 250 electricity utilities"""
    if not _is_allowed(update): await _deny(update); return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: `/expense <amount> <description> [category]`\n\n"
            "Categories: rent, utilities, salary, insurance, maintenance, marketing, supplies, other\n\n"
            "Examples:\n"
            "`/expense 3500 Monthly rent rent`\n"
            "`/expense 420 Hydro bill utilities`\n"
            "`/expense 2800 Staff wages salary`",
            parse_mode="Markdown"
        )
        return
    try:
        amount = float(args[0])
        known_cats = {"rent","utilities","salary","insurance","maintenance","marketing","supplies","other"}
        # Last arg may be a category
        if len(args) > 2 and args[-1].lower() in known_cats:
            category = args[-1].lower()
            desc = " ".join(args[1:-1])
        else:
            category = "other"
            desc = " ".join(args[1:]) if len(args) > 1 else "Manual expense"
        db = get_db()
        db.execute(
            "INSERT INTO manual_expenses (amount, description, category) VALUES (?,?,?)",
            (amount, desc, category)
        )
        db.commit(); db.close()
        await update.message.reply_text(
            f"Expense recorded: *{amount:.2f} CAD*\n"
            f"Description: {desc}\n"
            f"Category: {category}",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("Invalid amount. Example: `/expense 3500 rent rent`", parse_mode="Markdown")


# ──────────────────────────── /income ──────────────────────────────
async def cmd_gelir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): await _deny(update); return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/income 1500 Lunch service`", parse_mode="Markdown")
        return
    try:
        amount = float(args[0])
        desc   = " ".join(args[1:]) if len(args) > 1 else "End of day"
        db = get_db()
        db.execute("INSERT INTO income (amount, description) VALUES (?,?)", (amount, desc))
        db.commit(); db.close()
        await update.message.reply_text(f"Income added: *{amount:.2f} CAD* — {desc}", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("Invalid amount. Example: `/income 1500`", parse_mode="Markdown")


# ──────────────────────────── /summary ─────────────────────────────
async def cmd_ozet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): await _deny(update); return
    await _send_summary(update.effective_chat.id, context)


async def _send_summary(chat_id: int, context):
    """Build and send today's summary to a chat. Reused by scheduled job."""
    db    = get_db()
    gider = db.execute("""
        SELECT COALESCE(SUM(
            CASE WHEN type='refund' THEN -total_amount
                 WHEN type='consumption' THEN 0
                 ELSE total_amount END
        ), 0)
        FROM receipts WHERE date(created_at)=date('now','localtime') AND parse_status='success'
    """).fetchone()[0]
    tax   = db.execute("""
        SELECT COALESCE(SUM(
            CASE WHEN type='refund' THEN -tax_amount
                 WHEN type='consumption' THEN 0
                 ELSE tax_amount END
        ), 0)
        FROM receipts WHERE date(created_at)=date('now','localtime') AND parse_status='success'
    """).fetchone()[0]
    gelir = db.execute("SELECT COALESCE(SUM(amount),0)       FROM income   WHERE date(income_date)=date('now','localtime')").fetchone()[0]
    n_fis = db.execute("SELECT COUNT(*)                      FROM receipts WHERE date(created_at)=date('now','localtime') AND parse_status='success' AND type<>'refund'").fetchone()[0]
    n_iade= db.execute("SELECT COUNT(*)                      FROM receipts WHERE date(created_at)=date('now','localtime') AND parse_status='success' AND type='refund'").fetchone()[0]
    n_fail= db.execute("SELECT COUNT(*)                      FROM receipts WHERE date(created_at)=date('now','localtime') AND parse_status='failed'").fetchone()[0]
    low_stock = db.execute("SELECT COUNT(*) FROM stock WHERE min_quantity>0 AND current_quantity<=min_quantity").fetchone()[0]

    # Budget alerts — categories at or over 80%
    budget_alerts = db.execute("""
        SELECT b.category, b.monthly_limit, b.scope,
               CASE b.scope
                 WHEN 'receipt' THEN (
                   SELECT COALESCE(SUM(
                     CASE WHEN r.type='refund' THEN -ri.total_price
                          WHEN r.type='consumption' THEN 0
                          ELSE ri.total_price END
                   ),0)
                   FROM receipt_items ri JOIN receipts r ON ri.receipt_id=r.id
                   WHERE ri.category=b.category
                     AND strftime('%Y-%m',r.created_at)=strftime('%Y-%m','now','localtime')
                     AND r.parse_status='success'
                 )
                 ELSE (
                   SELECT COALESCE(SUM(amount),0) FROM manual_expenses
                   WHERE category=b.category
                     AND strftime('%Y-%m',expense_date)=strftime('%Y-%m','now','localtime')
                 )
               END AS spent
        FROM budgets b
    """).fetchall()
    db.close()

    over_budget  = [r for r in budget_alerts if r["spent"] >= r["monthly_limit"]]
    near_budget  = [r for r in budget_alerts if 0.8 * r["monthly_limit"] <= r["spent"] < r["monthly_limit"]]

    net   = gelir - gider
    emoji = "\U0001F4C8" if net >= 0 else "\U0001F4C9"
    fail_note   = f"\n\u26A0\uFE0F {n_fail} receipt(s) failed — retry at dashboard" if n_fail else ""
    stock_note  = f"\n\U0001F534 {low_stock} item(s) LOW in stock" if low_stock else ""
    over_note   = "".join(f"\n\U0001F6A8 Budget OVER: *{r['category']}* ${r['spent']:.0f}/${r['monthly_limit']:.0f}" for r in over_budget)
    near_note   = "".join(f"\n\U0001F7E1 Budget 80%+: *{r['category']}* ${r['spent']:.0f}/${r['monthly_limit']:.0f}" for r in near_budget)

    iade_note = f"  (incl. {n_iade} refund(s))" if n_iade else ""
    tax_note  = f"\nTax     : {tax:>10.2f} CAD" if tax else ""

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{emoji} *Daily Summary*\n\n"
            f"Income  : {gelir:>10.2f} CAD\n"
            f"Expense : {gider:>10.2f} CAD  ({n_fis} receipt(s)){iade_note}"
            f"{tax_note}\n"
            f"{'─'*30}\n"
            f"Net     : {net:>10.2f} CAD  ({'Profitable' if net >= 0 else 'In loss'})"
            f"{fail_note}{stock_note}{over_note}{near_note}\n\n"
            f"[Open Dashboard]({WEB_URL})"
        ),
        parse_mode="Markdown",
    )


# ──────────────────────── Backup ───────────────────────────────────
async def _send_backup(chat_id: int, context, label: str = "scheduled"):
    """Send a hot copy of the SQLite database as a Telegram document.
    Uses sqlite3 backup API so the copy is consistent even with WAL mode.
    """
    import sqlite3, os as _os
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = f"backup_{ts}.db"
    try:
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(tmp_path)
        with dst:
            src.backup(dst)
        dst.close(); src.close()

        size_kb = _os.path.getsize(tmp_path) / 1024

        # Counts for the caption
        c = sqlite3.connect(tmp_path)
        n_receipts = c.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
        n_items    = c.execute("SELECT COUNT(*) FROM receipt_items").fetchone()[0]
        n_stock    = c.execute("SELECT COUNT(*) FROM stock").fetchone()[0]
        c.close()

        with open(tmp_path, "rb") as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=f"bodega_{ts}.db",
                caption=(
                    f"\U0001F4E6 Bodega backup ({label})\n"
                    f"Time: {ts} UTC\n"
                    f"Size: {size_kb:.1f} KB\n"
                    f"Receipts: {n_receipts} · Items: {n_items} · Stock: {n_stock}"
                ),
            )
    finally:
        try: _os.remove(tmp_path)
        except Exception: pass


async def cmd_backup(update: Update, context):
    """On-demand backup: /backup"""
    if not _is_allowed(update): await _deny(update); return
    msg = await update.message.reply_text("\U0001F4E6 Creating backup...")
    try:
        await _send_backup(update.effective_chat.id, context, label="manual")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Backup failed: {e}")


async def job_daily_backup(context):
    """Runs daily at BACKUP_TIME_UTC — DMs the .db to all NOTIFY_USER_IDS."""
    for uid in NOTIFY_USER_IDS:
        try:
            await _send_backup(uid, context, label="auto-daily")
        except Exception as e:
            print(f"[BACKUP] Failed to send to {uid}: {e}")


# ──────────────────────── Scheduled daily summary ──────────────────
async def job_daily_summary(context):
    """Runs daily at SUMMARY_TIME_UTC — sends summary to all NOTIFY_USER_IDS."""
    for uid in NOTIFY_USER_IDS:
        try:
            await _send_summary(uid, context)
        except Exception as e:
            print(f"[SUMMARY] Failed to send to {uid}: {e}")


# ──────────────────────────── /weeklyreport ───────────────────────
async def cmd_weekly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): await _deny(update); return

    db = get_db()

    # This week vs last week totals (refund counts as negative, consumption excluded)
    signed_total = """
        CASE WHEN type='refund' THEN -total_amount
             WHEN type='consumption' THEN 0
             ELSE total_amount END
    """
    this_w = db.execute(f"""
        SELECT COALESCE(SUM({signed_total}),0) FROM receipts
        WHERE date(created_at) >= date('now','localtime','-7 days')
          AND parse_status='success'
    """).fetchone()[0]
    last_w = db.execute(f"""
        SELECT COALESCE(SUM({signed_total}),0) FROM receipts
        WHERE date(created_at) >= date('now','localtime','-14 days')
          AND date(created_at) <  date('now','localtime','-7 days')
          AND parse_status='success'
    """).fetchone()[0]

    # Category breakdown this week (signed by receipt type)
    cats = db.execute("""
        SELECT ri.category, ROUND(SUM(
            CASE WHEN r.type='refund' THEN -ri.total_price
                 WHEN r.type='consumption' THEN 0
                 ELSE ri.total_price END
        ),2) AS total
        FROM receipt_items ri
        JOIN receipts r ON ri.receipt_id=r.id
        WHERE date(r.created_at) >= date('now','localtime','-7 days')
          AND r.parse_status='success' AND ri.category IS NOT NULL
        GROUP BY ri.category ORDER BY total DESC LIMIT 8
    """).fetchall()

    # Top supplier this week (net = expense - refund)
    top_store = db.execute(f"""
        SELECT store_name, ROUND(SUM({signed_total}),2) AS total
        FROM receipts
        WHERE date(created_at) >= date('now','localtime','-7 days')
          AND parse_status='success' AND store_name IS NOT NULL
        GROUP BY store_name ORDER BY total DESC LIMIT 1
    """).fetchone()
    db.close()

    diff = this_w - last_w
    trend = f"▲ +${diff:.2f} more" if diff > 0 else (f"▼ ${abs(diff):.2f} less" if diff < 0 else "same as last week")

    cat_lines = "\n".join(
        f"  {'🥩' if c['category']=='meat' else '🍞' if c['category']=='bread' else '🥦' if c['category']=='vegetable' else '🧀' if c['category']=='dairy' else '📦'} {c['category']}: ${c['total']:.2f}"
        for c in cats
    ) or "  (no data)"

    store_line = f"\n🏪 Top supplier: *{top_store['store_name']}* (${top_store['total']:.2f})" if top_store else ""

    await update.message.reply_text(
        f"📊 *Weekly Cost Report*\n\n"
        f"This week : *${this_w:.2f} CAD*\n"
        f"Last week : ${last_w:.2f} CAD\n"
        f"Trend     : {trend}\n\n"
        f"*By Category (this week):*\n{cat_lines}"
        f"{store_line}\n\n"
        f"[Full report]({WEB_URL}/weekly-report)",
        parse_mode="Markdown",
    )


# ──────────────────────────── /stock ───────────────────────────────
async def cmd_stok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): await _deny(update); return
    db   = get_db()
    rows = db.execute(
        "SELECT item_name, category, current_quantity, unit, min_quantity "
        "FROM stock ORDER BY category, item_name"
    ).fetchall()
    db.close()

    if not rows:
        await update.message.reply_text("No stock data yet. Send a receipt photo to start.")
        return

    text    = "*Stock Status*\n"
    cur_cat = None
    for r in rows:
        if r["category"] != cur_cat:
            cur_cat = r["category"]
            text += f"\n*{cur_cat or 'Other'}*\n"
        warn  = " \U0001F534" if r["min_quantity"] and r["current_quantity"] <= r["min_quantity"] else ""
        text += f"  - {r['item_name']}: {r['current_quantity']:.1f} {r['unit'] or ''}{warn}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────── /stockset ─────────────────────────────────
async def cmd_stok_duzenle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): await _deny(update); return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/stockset chicken 10 kg`", parse_mode="Markdown")
        return
    try:
        name = args[0]
        qty  = float(args[1])
        unit = args[2] if len(args) > 2 else None
        db   = get_db()
        db.execute("""
            INSERT INTO stock (item_name, current_quantity, unit, last_updated)
            VALUES (?,?,?, datetime('now','localtime'))
            ON CONFLICT(item_name) DO UPDATE SET
                current_quantity = ?,
                unit             = COALESCE(?, unit),
                last_updated     = datetime('now','localtime')
        """, (name, qty, unit, qty, unit))
        db.commit(); db.close()
        await update.message.reply_text(f"Stock updated: *{name}* -> {qty} {unit or ''}", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("Invalid quantity.")


# ─────────────────────── /stockuse ─────────────────────────────────
async def cmd_stok_kullan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): await _deny(update); return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/stockuse chicken 2`", parse_mode="Markdown")
        return
    try:
        name = args[0]
        qty  = float(args[1])
        if qty <= 0:
            await update.message.reply_text("Invalid quantity. Use a positive number.")
            return
        db   = get_db()
        row  = db.execute("SELECT current_quantity, unit FROM stock WHERE item_name=?", (name,)).fetchone()
        if not row:
            await update.message.reply_text(f"`{name}` not found in stock.", parse_mode="Markdown")
            db.close()
            return
        new_qty = max(0.0, row["current_quantity"] - qty)
        db.execute("""
            UPDATE stock SET current_quantity=?, last_updated=datetime('now','localtime')
            WHERE item_name=?
        """, (new_qty, name))
        db.commit(); db.close()
        await update.message.reply_text(
            f"*{name}* deducted from stock\n"
            f"Before : {row['current_quantity']:.1f} {row['unit'] or ''}\n"
            f"Used   : -{qty:.1f}\n"
            f"Remaining: {new_qty:.1f} {row['unit'] or ''}",
            parse_mode="Markdown",
        )
    except ValueError:
        await update.message.reply_text("Invalid quantity.")


# ─────────────────────── /stockdel ─────────────────────────────────
async def cmd_stok_sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): await _deny(update); return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/stockdel chicken`", parse_mode="Markdown")
        return
    name = " ".join(args)
    db   = get_db()
    n    = db.execute("DELETE FROM stock WHERE item_name=?", (name,)).rowcount
    db.commit(); db.close()
    if n:
        await update.message.reply_text(f"`{name}` deleted from stock.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"`{name}` not found.", parse_mode="Markdown")


# ──────────────────────────── main ────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("income",   cmd_gelir))
    app.add_handler(CommandHandler("summary",  cmd_ozet))
    app.add_handler(CommandHandler("stock",    cmd_stok))
    app.add_handler(CommandHandler("stockset", cmd_stok_duzenle))
    app.add_handler(CommandHandler("stockuse", cmd_stok_kullan))
    app.add_handler(CommandHandler("expense",       cmd_expense))
    app.add_handler(CommandHandler("stockdel",     cmd_stok_sil))
    app.add_handler(CommandHandler("weeklyreport", cmd_weekly_report))
    app.add_handler(CommandHandler("backup",       cmd_backup))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Daily summary job
    if NOTIFY_USER_IDS:
        app.job_queue.run_daily(job_daily_summary, time=SUMMARY_TIME)
        app.job_queue.run_daily(job_daily_backup,  time=BACKUP_TIME)
        print(f"Daily summary scheduled at {SUMMARY_TIME} UTC → {NOTIFY_USER_IDS}")
        print(f"Daily backup  scheduled at {BACKUP_TIME} UTC → {NOTIFY_USER_IDS}")
    else:
        print("No NOTIFY_USER_IDS set — daily summary & backup disabled")

    if ALLOWED_USER_IDS:
        print(f"Auth enabled — allowed users: {ALLOWED_USER_IDS}")
    else:
        print("WARNING: ALLOWED_USER_IDS not set — bot is open to everyone")

    print(f"Bot basladi | Dashboard: {WEB_URL}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
