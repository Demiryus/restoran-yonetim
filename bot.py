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

from database import get_db, init_db
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
        "*Restaurant Management System*\n\n"
        "Send a receipt photo to add items to stock automatically.\n"
        "Add caption *use* / *consume* to deduct from stock instead.\n\n"
        "Commands:\n"
        "`/income 500 Lunch service` — Add income\n"
        "`/summary` — Today's income/expense summary\n"
        "`/stock` — View stock levels\n"
        "`/stockset chicken 5 kg` — Set stock quantity\n"
        "`/stockuse chicken 2` — Deduct from stock\n"
        "`/stockdel chicken` — Delete stock item\n"
        f"\nDashboard: {WEB_URL}",
        parse_mode="Markdown",
    )


# ──────────────────────────── Photo handler ────────────────────────

TUKETIM_KELIMELERI = {"kullan", "use", "consume", "deduct", "çıkar", "cikar", "tüket", "tuket", "sarf"}

def _tuketim_modu(caption: str | None) -> bool:
    if not caption:
        return False
    low = caption.lower()
    return any(k in low for k in TUKETIM_KELIMELERI)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await _deny(update); return

    caption  = update.message.caption or ""
    tuketim  = _tuketim_modu(caption)
    mod_text = "deducting from stock" if tuketim else "adding to stock"

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
        "consumption" if tuketim else "expense",
        "pending",
        None,
    ))
    receipt_id = cur.lastrowid
    db.commit()

    # ── Step 3: AI parsing ──────────────────────────────────────────
    await msg.edit_text(f"Analyzing receipt with AI... ({mod_text})")

    try:
        parsed, raw = parse_receipt(photo_path)

        cur.execute("""
            UPDATE receipts SET
                store_name      = ?,
                receipt_date    = ?,
                total_amount    = ?,
                currency        = ?,
                parse_status    = 'success',
                parse_error     = NULL,
                raw_ai_response = ?
            WHERE id = ?
        """, (
            parsed.get("store_name") or "Unknown",
            parsed.get("receipt_date"),
            parsed.get("total_amount") or 0,
            parsed.get("currency") or "CAD",
            raw,
            receipt_id,
        ))

        items = parsed.get("items") or []
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
                if tuketim:
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
        mod_emoji = "\U0001F4E4" if tuketim else "\U0001F4E5"

        await msg.edit_text(
            f"*Receipt saved!*\n\n"
            f"Store: {parsed.get('store_name','?')}\n"
            f"Date: {parsed.get('receipt_date') or 'Unknown'}\n"
            f"Total: *{total:.2f} {cur_sym}*\n\n"
            f"Items ({len(items)}):\n{item_lines}\n\n"
            f"{mod_emoji} *Stock {'deducted' if tuketim else 'updated'}:*\n{stok_blok}\n\n"
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
    gider = db.execute("SELECT COALESCE(SUM(total_amount),0) FROM receipts WHERE date(created_at)=date('now','localtime') AND parse_status='success'").fetchone()[0]
    gelir = db.execute("SELECT COALESCE(SUM(amount),0)       FROM income   WHERE date(income_date)=date('now','localtime')").fetchone()[0]
    n_fis = db.execute("SELECT COUNT(*)                      FROM receipts WHERE date(created_at)=date('now','localtime') AND parse_status='success'").fetchone()[0]
    n_fail= db.execute("SELECT COUNT(*)                      FROM receipts WHERE date(created_at)=date('now','localtime') AND parse_status='failed'").fetchone()[0]
    low_stock = db.execute("SELECT COUNT(*) FROM stock WHERE min_quantity>0 AND current_quantity<=min_quantity").fetchone()[0]
    db.close()

    net   = gelir - gider
    emoji = "\U0001F4C8" if net >= 0 else "\U0001F4C9"
    fail_note  = f"\n\u26A0\uFE0F {n_fail} receipt(s) failed — retry at dashboard" if n_fail else ""
    stock_note = f"\n\U0001F534 {low_stock} item(s) LOW in stock" if low_stock else ""

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{emoji} *Daily Summary*\n\n"
            f"Income  : {gelir:>10.2f} CAD\n"
            f"Expense : {gider:>10.2f} CAD  ({n_fis} receipt(s))\n"
            f"{'─'*30}\n"
            f"Net     : {net:>10.2f} CAD  ({'Profitable' if net >= 0 else 'In loss'})"
            f"{fail_note}{stock_note}\n\n"
            f"[Open Dashboard]({WEB_URL})"
        ),
        parse_mode="Markdown",
    )


# ──────────────────────── Scheduled daily summary ──────────────────
async def job_daily_summary(context):
    """Runs daily at SUMMARY_TIME_UTC — sends summary to all NOTIFY_USER_IDS."""
    for uid in NOTIFY_USER_IDS:
        try:
            await _send_summary(uid, context)
        except Exception as e:
            print(f"[SUMMARY] Failed to send to {uid}: {e}")


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
    app.add_handler(CommandHandler("stockdel", cmd_stok_sil))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Daily summary job
    if NOTIFY_USER_IDS:
        app.job_queue.run_daily(job_daily_summary, time=SUMMARY_TIME)
        print(f"Daily summary scheduled at {SUMMARY_TIME} UTC → {NOTIFY_USER_IDS}")
    else:
        print("No NOTIFY_USER_IDS set — daily summary disabled")

    if ALLOWED_USER_IDS:
        print(f"Auth enabled — allowed users: {ALLOWED_USER_IDS}")
    else:
        print("WARNING: ALLOWED_USER_IDS not set — bot is open to everyone")

    print(f"Bot basladi | Dashboard: {WEB_URL}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
