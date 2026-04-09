"""
Web Dashboard — FastAPI
Çalıştır: uvicorn web_app:app --reload --port 8000
"""
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import json
from datetime import date, timedelta

import os
from database import get_db, init_db
from ai_parser import parse_receipt

# Railway Volume'da kalıcı photos klasörü: PHOTOS_DIR=/data/photos
PHOTOS_DIR = Path(os.getenv("PHOTOS_DIR", "photos"))
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Restoran Yönetim")
app.mount("/photos", StaticFiles(directory=str(PHOTOS_DIR)), name="photos")

templates = Jinja2Templates(directory="templates")
templates.env.filters["today_date"] = lambda _: date.today().isoformat()


# ─────────────────────────── Yardımcılar ──────────────────────────

def fetch_one(query, params=()):
    db = get_db(); row = db.execute(query, params).fetchone(); db.close()
    return dict(row) if row else None

def fetch_all(query, params=()):
    db = get_db(); rows = db.execute(query, params).fetchall(); db.close()
    return [dict(r) for r in rows]

def scalar(query, params=()):
    db = get_db()
    row = db.execute(query, params).fetchone()
    db.close()
    return (row[0] or 0) if row else 0


# ─────────────────────────── Ana sayfa ────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, period: str = "today"):
    if period == "today":
        date_filter_r = "date(r.created_at) = date('now','localtime')"
        date_filter_i = "date(i.income_date) = date('now','localtime')"
        label = "Bugün"
    elif period == "week":
        date_filter_r = "date(r.created_at) >= date('now','localtime','-7 days')"
        date_filter_i = "date(i.income_date) >= date('now','localtime','-7 days')"
        label = "Son 7 Gün"
    elif period == "month":
        date_filter_r = "strftime('%Y-%m',r.created_at) = strftime('%Y-%m','now','localtime')"
        date_filter_i = "strftime('%Y-%m',i.income_date) = strftime('%Y-%m','now','localtime')"
        label = "Bu Ay"
    else:
        date_filter_r = "1=1"
        date_filter_i = "1=1"
        label = "Tüm Zamanlar"

    total_gider = scalar(f"SELECT COALESCE(SUM(total_amount),0) FROM receipts r WHERE {date_filter_r} AND r.parse_status='success'")
    total_gelir = scalar(f"SELECT COALESCE(SUM(amount),0) FROM income i WHERE {date_filter_i}")
    net         = total_gelir - total_gider
    n_fis       = scalar(f"SELECT COUNT(*) FROM receipts r WHERE {date_filter_r} AND r.parse_status='success'")

    # Failed/pending receipts (always shown regardless of period)
    failed_receipts = fetch_all(
        "SELECT id, photo_path, parse_status, parse_error, created_at, telegram_username "
        "FROM receipts WHERE parse_status IN ('failed','pending') ORDER BY created_at DESC LIMIT 20"
    )

    son_fisler = fetch_all(
        f"SELECT r.id, r.store_name, r.receipt_date, r.total_amount, r.currency, r.created_at, r.parse_status "
        f"FROM receipts r WHERE {date_filter_r} AND r.parse_status='success' ORDER BY r.created_at DESC LIMIT 10"
    )

    # Fetch items for each recent receipt
    fis_ids = [f["id"] for f in son_fisler]
    fis_items = {}
    if fis_ids:
        placeholders = ",".join("?" * len(fis_ids))
        rows = fetch_all(
            f"SELECT receipt_id, item_name, quantity, unit, total_price FROM receipt_items "
            f"WHERE receipt_id IN ({placeholders}) ORDER BY receipt_id, id",
            tuple(fis_ids)
        )
        for row in rows:
            fis_items.setdefault(row["receipt_id"], []).append(row)

    kategori_data = fetch_all(
        "SELECT ri.category, ROUND(SUM(ri.total_price),2) as toplam "
        "FROM receipt_items ri JOIN receipts r ON ri.receipt_id=r.id "
        f"WHERE {date_filter_r} AND ri.category IS NOT NULL "
        "GROUP BY ri.category ORDER BY toplam DESC"
    )

    trend = []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        g   = scalar("SELECT COALESCE(SUM(total_amount),0) FROM receipts WHERE date(created_at)=?", (d,))
        inc = scalar("SELECT COALESCE(SUM(amount),0) FROM income WHERE date(income_date)=?", (d,))
        trend.append({"tarih": d[-5:], "gider": g, "gelir": inc})

    dusuk_stok = fetch_all(
        "SELECT item_name, current_quantity, unit, min_quantity FROM stock "
        "WHERE min_quantity > 0 AND current_quantity <= min_quantity"
    )

    stok = fetch_all("""
        SELECT s.*,
               (SELECT unit_price FROM receipt_items
                WHERE lower(item_name)=lower(s.item_name) AND unit_price > 0
                ORDER BY id DESC LIMIT 1) as last_price,
               CAST(julianday('now','localtime') - julianday(s.last_updated) AS INTEGER) as days_ago
        FROM stock s ORDER BY s.category, s.item_name
    """)

    # Estimated total stock value
    total_stok_degeri = sum(
        (s["current_quantity"] or 0) * (s["last_price"] or 0) for s in stok
    )

    son_gelirler = fetch_all(
        f"SELECT * FROM income i WHERE {date_filter_i} ORDER BY created_at DESC LIMIT 20"
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "period": period, "label": label,
        "total_gelir": total_gelir, "total_gider": total_gider,
        "net": net, "n_fis": n_fis,
        "son_fisler": son_fisler,
        "fis_items": fis_items,
        "son_gelirler": son_gelirler,
        "kategori_json": json.dumps(kategori_data),
        "trend_json": json.dumps(trend),
        "dusuk_stok": dusuk_stok,
        "stok": stok,
        "total_stok_degeri": total_stok_degeri,
        "failed_receipts": failed_receipts,
    })


# ─────────────────────────── Fişler ───────────────────────────────

@app.get("/fis/{receipt_id}", response_class=HTMLResponse)
async def fis_detay(request: Request, receipt_id: int):
    fis = fetch_one("SELECT * FROM receipts WHERE id=?", (receipt_id,))
    if not fis:
        raise HTTPException(status_code=404, detail="Fiş bulunamadı")
    items = fetch_all("SELECT * FROM receipt_items WHERE receipt_id=? ORDER BY id", (receipt_id,))
    # Web'den erişilebilir foto URL'i
    photo_url = None
    if fis.get("photo_path"):
        p = Path(fis["photo_path"])
        if p.exists():
            photo_url = f"/photos/{p.name}"
    return templates.TemplateResponse("fis_detay.html", {
        "request": request, "fis": fis, "items": items, "photo_url": photo_url
    })

@app.post("/fis/{receipt_id}/sil")
async def fis_sil(receipt_id: int):
    db = get_db()
    row = db.execute("SELECT photo_path, type FROM receipts WHERE id=?", (receipt_id,)).fetchone()
    if not row:
        db.close()
        return RedirectResponse("/", status_code=303)

    # Fotoğrafı sil
    if row["photo_path"]:
        Path(row["photo_path"]).unlink(missing_ok=True)

    # Stoğu geri al: alım fişiyse stoğu düş, tüketim fişiyse stoğu geri ekle
    items = db.execute(
        "SELECT item_name, quantity FROM receipt_items WHERE receipt_id=?", (receipt_id,)
    ).fetchall()

    receipt_type = row["type"] or "expense"
    for item in items:
        name = item["item_name"]
        qty  = item["quantity"] or 0
        if qty <= 0:
            continue
        if receipt_type == "consumption":
            # Tüketim fişi silindi → stoğu geri ekle
            db.execute("""
                UPDATE stock SET
                    current_quantity = current_quantity + ?,
                    last_updated = datetime('now','localtime')
                WHERE item_name = ?
            """, (qty, name))
        else:
            # Alım fişi silindi → stoğu düş (0'ın altına inme)
            db.execute("""
                UPDATE stock SET
                    current_quantity = MAX(0, current_quantity - ?),
                    last_updated = datetime('now','localtime')
                WHERE item_name = ?
            """, (qty, name))

    db.execute("DELETE FROM receipts WHERE id=?", (receipt_id,))
    db.commit()
    db.close()
    return RedirectResponse("/", status_code=303)


@app.post("/fis/{receipt_id}/retry")
async def fis_retry(receipt_id: int):
    """Re-run AI parsing on a failed/pending receipt."""
    db = get_db()
    row = db.execute("SELECT photo_path, type FROM receipts WHERE id=?", (receipt_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Receipt not found")

    photo_path = row["photo_path"]
    if not photo_path or not Path(photo_path).exists():
        db.execute("UPDATE receipts SET parse_status='failed', parse_error='Photo file not found' WHERE id=?", (receipt_id,))
        db.commit(); db.close()
        return JSONResponse({"ok": False, "error": "Photo file not found"})

    try:
        parsed, raw = parse_receipt(photo_path)
        tuketim = (row["type"] == "consumption")

        db.execute("""
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

        # Delete old items and re-insert
        db.execute("DELETE FROM receipt_items WHERE receipt_id=?", (receipt_id,))

        for item in (parsed.get("items") or []):
            name    = item.get("item_name") or "?"
            qty     = float(item.get("quantity") or 0)
            unit    = item.get("unit") or ""
            cat     = item.get("category") or "other"
            u_price = float(item.get("unit_price") or 0)
            t_price = float(item.get("total_price") or 0)

            db.execute("""
                INSERT INTO receipt_items
                    (receipt_id, item_name, category, quantity, unit, unit_price, total_price)
                VALUES (?,?,?,?,?,?,?)
            """, (receipt_id, name, cat, qty, unit, u_price, t_price))

            if name and qty > 0:
                if tuketim:
                    db.execute("""
                        UPDATE stock SET
                            current_quantity = MAX(0, current_quantity - ?),
                            last_updated     = datetime('now','localtime')
                        WHERE item_name = ?
                    """, (qty, name))
                else:
                    db.execute("""
                        INSERT INTO stock (item_name, category, current_quantity, unit, last_updated)
                        VALUES (?,?,?,?, datetime('now','localtime'))
                        ON CONFLICT(item_name) DO UPDATE SET
                            current_quantity = current_quantity + ?,
                            category         = COALESCE(excluded.category, category),
                            last_updated     = datetime('now','localtime')
                    """, (name, cat, qty, unit, qty))

        db.commit(); db.close()
        return RedirectResponse("/", status_code=303)

    except Exception as e:
        db.execute("UPDATE receipts SET parse_status='failed', parse_error=? WHERE id=?",
                   (str(e)[:500], receipt_id))
        db.commit(); db.close()
        return JSONResponse({"ok": False, "error": str(e)})


# ─────────────────────────── Gelir ────────────────────────────────

@app.post("/gelir/ekle")
async def gelir_ekle(amount: float = Form(...), description: str = Form(""), income_date: str = Form("")):
    db = get_db()
    db.execute("INSERT INTO income (amount, description, income_date) VALUES (?,?,?)",
               (amount, description, income_date or date.today().isoformat()))
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)

@app.post("/gelir/{income_id}/sil")
async def gelir_sil(income_id: int):
    db = get_db()
    db.execute("DELETE FROM income WHERE id=?", (income_id,))
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)

@app.post("/gelir/{income_id}/duzenle")
async def gelir_duzenle(income_id: int, amount: float = Form(...), description: str = Form("")):
    db = get_db()
    db.execute("UPDATE income SET amount=?, description=? WHERE id=?", (amount, description, income_id))
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)


# ─────────────────────────── Stok ─────────────────────────────────

@app.post("/stok/guncelle")
async def stok_guncelle(item_name: str = Form(...), quantity: float = Form(...),
                         unit: str = Form(""), min_quantity: float = Form(0)):
    db = get_db()
    db.execute("""
        INSERT INTO stock (item_name, current_quantity, unit, min_quantity, last_updated)
        VALUES (?,?,?,?, datetime('now','localtime'))
        ON CONFLICT(item_name) DO UPDATE SET
            current_quantity = ?,
            unit             = COALESCE(NULLIF(?,''), unit),
            min_quantity     = ?,
            last_updated     = datetime('now','localtime')
    """, (item_name, quantity, unit, min_quantity, quantity, unit, min_quantity))
    db.commit(); db.close()
    return RedirectResponse("/?tab=stok", status_code=303)

@app.post("/stok/kullan")
async def stok_kullan(item_name: str = Form(...), quantity: float = Form(...)):
    db = get_db()
    db.execute("""
        UPDATE stock SET
            current_quantity = MAX(0, current_quantity - ?),
            last_updated     = datetime('now','localtime')
        WHERE item_name = ?
    """, (quantity, item_name))
    db.commit(); db.close()
    return RedirectResponse("/?tab=stok", status_code=303)

@app.post("/stok/{item_name}/sil")
async def stok_sil(item_name: str):
    db = get_db()
    db.execute("DELETE FROM stock WHERE item_name=?", (item_name,))
    db.commit(); db.close()
    return RedirectResponse("/?tab=stok", status_code=303)


# ─────────────────────────── API ──────────────────────────────────

@app.get("/api/summary")
async def api_summary():
    return {
        "bugun_gelir": scalar("SELECT COALESCE(SUM(amount),0) FROM income WHERE date(income_date)=date('now','localtime')"),
        "bugun_gider": scalar("SELECT COALESCE(SUM(total_amount),0) FROM receipts WHERE date(created_at)=date('now','localtime')"),
        "toplam_stok": scalar("SELECT COUNT(*) FROM stock"),
        "dusuk_stok":  scalar("SELECT COUNT(*) FROM stock WHERE min_quantity>0 AND current_quantity<=min_quantity"),
    }


# ─────────────────────────── Başlangıç ────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    print("Web dashboard basladi -> http://localhost:8000")
