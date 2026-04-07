"""
Restoran Yönetim Telegram Botu
Komutlar:
  /start              - Karşılama
  /gelir 500 Öğle     - Gelir ekle
  /ozet               - Bugünkü özet
  /stok               - Stok durumu
  /stokduzenle        - Stok miktarını SET et  (örn: /stokduzenle tavuk 10 kg)
  /stokkullan         - Stoktan düş           (örn: /stokkullan tavuk 2)
  /stoksil            - Stok kalemi sil        (örn: /stoksil tavuk)
  Fotoğraf göndermek  - Fiş işleme
"""
import os
from datetime import datetime
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


# ──────────────────────────── /start ──────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


# ──────────────────────────── Fotoğraf ────────────────────────────

TUKETIM_KELIMELERI = {"kullan", "use", "consume", "deduct", "çıkar", "cikar", "tüket", "tuket", "sarf"}

def _tuketim_modu(caption: str | None) -> bool:
    """Caption 'kullan', 'çıkar' vb. içeriyorsa tüketim modu (stoktan düş)."""
    if not caption:
        return False
    low = caption.lower()
    return any(k in low for k in TUKETIM_KELIMELERI)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption  = update.message.caption or ""
    tuketim  = _tuketim_modu(caption)
    mod_text = "deducting from stock" if tuketim else "adding to stock"

    msg = await update.message.reply_text(f"Analyzing receipt... ({mod_text})")

    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    photo_path = f"{PHOTOS_DIR}/{ts}_{photo.file_id}.jpg"
    await tg_file.download_to_drive(photo_path)

    try:
        parsed, raw = parse_receipt(photo_path)

        db = get_db()
        cur = db.cursor()

        cur.execute("""
            INSERT INTO receipts
                (telegram_user_id, telegram_username, photo_path,
                 store_name, receipt_date, total_amount, currency, type, raw_ai_response)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            str(update.effective_user.id),
            update.effective_user.username or "unknown",
            photo_path,
            parsed.get("store_name") or "Bilinmiyor",
            parsed.get("receipt_date"),
            parsed.get("total_amount") or 0,
            parsed.get("currency") or "CAD",
            "consumption" if tuketim else "expense",
            raw,
        ))
        receipt_id = cur.lastrowid

        items = parsed.get("items") or []
        stok_satirlari = []

        for item in items:
            name    = item.get("item_name") or "?"
            qty     = float(item.get("quantity") or 0)
            unit    = item.get("unit") or ""
            cat     = item.get("category") or "diğer"
            u_price = float(item.get("unit_price") or 0)
            t_price = float(item.get("total_price") or 0)

            cur.execute("""
                INSERT INTO receipt_items
                    (receipt_id, item_name, category, quantity, unit, unit_price, total_price)
                VALUES (?,?,?,?,?,?,?)
            """, (receipt_id, name, cat, qty, unit, u_price, t_price))

            if name and qty > 0:
                if tuketim:
                    # Stoktan düş — sıfırın altına inme
                    cur.execute("""
                        UPDATE stock SET
                            current_quantity = MAX(0, current_quantity - ?),
                            last_updated     = datetime('now','localtime')
                        WHERE item_name = ?
                    """, (qty, name))
                    stok_satirlari.append(f"  ➖ {name}: -{qty:.1f} {unit}")
                else:
                    # Stoğa ekle — yoksa yeni kayıt oluştur
                    cur.execute("""
                        INSERT INTO stock (item_name, category, current_quantity, unit, last_updated)
                        VALUES (?,?,?,?, datetime('now','localtime'))
                        ON CONFLICT(item_name) DO UPDATE SET
                            current_quantity = current_quantity + ?,
                            category         = COALESCE(excluded.category, category),
                            last_updated     = datetime('now','localtime')
                    """, (name, cat, qty, unit, qty))
                    stok_satirlari.append(f"  ➕ {name}: +{qty:.1f} {unit}")

        db.commit()
        db.close()

        cur_sym = parsed.get("currency") or "CAD"
        total   = parsed.get("total_amount") or 0
        item_lines = "\n".join(
            f"  • {i.get('item_name','?')}  "
            f"{i.get('quantity','?')} {i.get('unit','')}  "
            f"→ {float(i.get('total_price') or 0):.2f} {cur_sym}"
            for i in items[:12]
        ) or "  (ürün okunamadı)"

        stok_blok = "\n".join(stok_satirlari[:12]) if stok_satirlari else "  (stok değişikliği yok)"
        mod_emoji = "📤" if tuketim else "📥"

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
        await msg.edit_text(
            f"Failed to read receipt.\n`{e}`\n\n"
            "Try taking a clearer, well-lit photo.",
            parse_mode="Markdown",
        )


# ──────────────────────────── /gelir ──────────────────────────────
async def cmd_gelir(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


# ──────────────────────────── /ozet ───────────────────────────────
async def cmd_ozet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db    = get_db()
    gider = db.execute("SELECT COALESCE(SUM(total_amount),0) FROM receipts WHERE date(created_at)=date('now','localtime')").fetchone()[0]
    gelir = db.execute("SELECT COALESCE(SUM(amount),0)       FROM income   WHERE date(income_date)=date('now','localtime')").fetchone()[0]
    n_fis = db.execute("SELECT COUNT(*)                      FROM receipts WHERE date(created_at)=date('now','localtime')").fetchone()[0]
    db.close()

    net   = gelir - gider
    emoji = "Profitable" if net >= 0 else "In loss"

    await update.message.reply_text(
        f"*Today's Summary*\n\n"
        f"Income  : {gelir:>10.2f} CAD\n"
        f"Expense : {gider:>10.2f} CAD  ({n_fis} receipt(s))\n"
        f"{'─'*30}\n"
        f"Net     : {net:>10.2f} CAD  ({emoji})",
        parse_mode="Markdown",
    )


# ──────────────────────────── /stok ───────────────────────────────
async def cmd_stok(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        warn  = " [LOW]" if r["min_quantity"] and r["current_quantity"] <= r["min_quantity"] else ""
        text += f"  - {r['item_name']}: {r['current_quantity']:.1f} {r['unit'] or ''}{warn}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────── /stokduzenle ─────────────────────────────
async def cmd_stok_duzenle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /stockset chicken 10 kg"""
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


# ─────────────────────── /stokkullan ──────────────────────────────
async def cmd_stok_kullan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /stockuse chicken 2"""
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


# ─────────────────────── /stoksil ─────────────────────────────────
async def cmd_stok_sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /stockdel chicken"""
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

    print(f"Bot basladi | Dashboard: {WEB_URL}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
