"""
Web Dashboard — FastAPI
Çalıştır: uvicorn web_app:app --reload --port 8000
"""
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import csv
import io
import json
import secrets
from datetime import date, timedelta

import os
from database import get_db, init_db
from ai_parser import parse_receipt

# Railway Volume'da kalıcı photos klasörü: PHOTOS_DIR=/data/photos
PHOTOS_DIR = Path(os.getenv("PHOTOS_DIR", "photos"))
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

# HTTP Basic Auth — set DASHBOARD_USER and DASHBOARD_PASS in env vars.
# If not set, auth is disabled (useful for local dev).
DASH_USER = os.getenv("DASHBOARD_USER", "")
DASH_PASS = os.getenv("DASHBOARD_PASS", "")
_security = HTTPBasic(auto_error=False)

def require_auth(credentials: HTTPBasicCredentials = Depends(_security)):
    if not DASH_USER or not DASH_PASS:
        return  # auth disabled — no env vars set
    if credentials is None:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"}, detail="Login required")
    ok_user = secrets.compare_digest(credentials.username.encode(), DASH_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), DASH_PASS.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"}, detail="Invalid credentials")

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
async def dashboard(request: Request, period: str = "today", _auth: None = Depends(require_auth)):
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

    # Date filter for manual expenses (same logic as receipts)
    if period == "today":
        date_filter_m = "date(expense_date) = date('now','localtime')"
    elif period == "week":
        date_filter_m = "date(expense_date) >= date('now','localtime','-7 days')"
    elif period == "month":
        date_filter_m = "strftime('%Y-%m',expense_date) = strftime('%Y-%m','now','localtime')"
    else:
        date_filter_m = "1=1"

    receipt_gider  = scalar(f"SELECT COALESCE(SUM(total_amount),0) FROM receipts r WHERE {date_filter_r} AND r.parse_status='success'")
    manual_gider   = scalar(f"SELECT COALESCE(SUM(amount),0) FROM manual_expenses WHERE {date_filter_m}")
    total_gider    = receipt_gider + manual_gider
    total_gelir    = scalar(f"SELECT COALESCE(SUM(amount),0) FROM income i WHERE {date_filter_i}")
    net            = total_gelir - total_gider
    n_fis          = scalar(f"SELECT COUNT(*) FROM receipts r WHERE {date_filter_r} AND r.parse_status='success'")

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

    son_manual = fetch_all(
        f"SELECT * FROM manual_expenses WHERE {date_filter_m} ORDER BY expense_date DESC, created_at DESC LIMIT 30"
    )

    # Budget vs actual (always current month)
    budgets = fetch_all("SELECT * FROM budgets ORDER BY category")
    budget_status = []
    for b in budgets:
        if b["scope"] == "receipt":
            spent = scalar("""
                SELECT COALESCE(SUM(ri.total_price),0)
                FROM receipt_items ri JOIN receipts r ON ri.receipt_id=r.id
                WHERE ri.category=? AND strftime('%Y-%m',r.created_at)=strftime('%Y-%m','now','localtime')
                  AND r.parse_status='success'
            """, (b["category"],))
        else:
            spent = scalar("""
                SELECT COALESCE(SUM(amount),0) FROM manual_expenses
                WHERE category=? AND strftime('%Y-%m',expense_date)=strftime('%Y-%m','now','localtime')
            """, (b["category"],))
        pct = round(spent / b["monthly_limit"] * 100, 1) if b["monthly_limit"] else 0
        budget_status.append({**b, "spent": spent, "pct": min(pct, 100), "raw_pct": pct,
                               "over": spent > b["monthly_limit"]})

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "period": period, "label": label,
        "total_gelir": total_gelir, "total_gider": total_gider,
        "receipt_gider": receipt_gider, "manual_gider": manual_gider,
        "net": net, "n_fis": n_fis,
        "son_fisler": son_fisler,
        "fis_items": fis_items,
        "son_gelirler": son_gelirler,
        "son_manual": son_manual,
        "kategori_json": json.dumps(kategori_data),
        "trend_json": json.dumps(trend),
        "dusuk_stok": dusuk_stok,
        "stok": stok,
        "total_stok_degeri": total_stok_degeri,
        "failed_receipts": failed_receipts,
        "budget_status": budget_status,
    })


# ─────────────────────────── Fişler ───────────────────────────────

@app.get("/fis/{receipt_id}", response_class=HTMLResponse)
async def fis_detay(request: Request, receipt_id: int, _auth: None = Depends(require_auth)):
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
async def fis_sil(receipt_id: int, _auth: None = Depends(require_auth)):
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
async def fis_retry(receipt_id: int, _auth: None = Depends(require_auth)):
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
async def gelir_ekle(amount: float = Form(...), description: str = Form(""), income_date: str = Form(""), _auth: None = Depends(require_auth)):
    db = get_db()
    db.execute("INSERT INTO income (amount, description, income_date) VALUES (?,?,?)",
               (amount, description, income_date or date.today().isoformat()))
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)

@app.post("/gelir/{income_id}/sil")
async def gelir_sil(income_id: int, _auth: None = Depends(require_auth)):
    db = get_db()
    db.execute("DELETE FROM income WHERE id=?", (income_id,))
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)

@app.post("/gelir/{income_id}/duzenle")
async def gelir_duzenle(income_id: int, amount: float = Form(...), description: str = Form(""), _auth: None = Depends(require_auth)):
    db = get_db()
    db.execute("UPDATE income SET amount=?, description=? WHERE id=?", (amount, description, income_id))
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)


# ─────────────────────────── Stok ─────────────────────────────────

@app.post("/stok/guncelle")
async def stok_guncelle(item_name: str = Form(...), quantity: float = Form(...),
                         unit: str = Form(""), min_quantity: float = Form(0), _auth: None = Depends(require_auth)):
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
async def stok_kullan(item_name: str = Form(...), quantity: float = Form(...), _auth: None = Depends(require_auth)):
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
async def stok_sil(item_name: str, _auth: None = Depends(require_auth)):
    db = get_db()
    db.execute("DELETE FROM stock WHERE item_name=?", (item_name,))
    db.commit(); db.close()
    return RedirectResponse("/?tab=stok", status_code=303)


# ─────────────────────────── API ──────────────────────────────────

@app.get("/api/summary")
async def api_summary(_auth: None = Depends(require_auth)):
    return {
        "bugun_gelir": scalar("SELECT COALESCE(SUM(amount),0) FROM income WHERE date(income_date)=date('now','localtime')"),
        "bugun_gider": scalar("SELECT COALESCE(SUM(total_amount),0) FROM receipts WHERE date(created_at)=date('now','localtime')"),
        "toplam_stok": scalar("SELECT COUNT(*) FROM stock"),
        "dusuk_stok":  scalar("SELECT COUNT(*) FROM stock WHERE min_quantity>0 AND current_quantity<=min_quantity"),
    }


# ──────────────────────── Manual Expenses ─────────────────────────

EXPENSE_CATEGORIES = ["rent","utilities","salary","insurance","maintenance","marketing","supplies","other"]

@app.post("/expenses/ekle")
async def expenses_ekle(
    amount: float = Form(...),
    description: str = Form(""),
    category: str = Form("other"),
    expense_date: str = Form(""),
    tax_amount: float = Form(0),
    is_recurring: int = Form(0),
    recur_day: int = Form(0),
    _auth: None = Depends(require_auth),
):
    db = get_db()
    db.execute(
        "INSERT INTO manual_expenses (amount, description, category, expense_date, tax_amount, is_recurring, recur_day) VALUES (?,?,?,?,?,?,?)",
        (amount, description, category, expense_date or date.today().isoformat(),
         tax_amount, is_recurring, recur_day or None)
    )
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)

@app.post("/expenses/{exp_id}/sil")
async def expenses_sil(exp_id: int, _auth: None = Depends(require_auth)):
    db = get_db()
    db.execute("DELETE FROM manual_expenses WHERE id=?", (exp_id,))
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)

@app.post("/expenses/{exp_id}/duzenle")
async def expenses_duzenle(
    exp_id: int,
    amount: float = Form(...),
    description: str = Form(""),
    category: str = Form("other"),
    expense_date: str = Form(""),
    tax_amount: float = Form(0),
    _auth: None = Depends(require_auth),
):
    db = get_db()
    db.execute(
        "UPDATE manual_expenses SET amount=?,description=?,category=?,expense_date=?,tax_amount=? WHERE id=?",
        (amount, description, category, expense_date or date.today().isoformat(), tax_amount, exp_id)
    )
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)


# ───────────────────────────── Budgets ────────────────────────────

@app.post("/budgets/guncelle")
async def budgets_guncelle(
    category: str = Form(...),
    monthly_limit: float = Form(...),
    scope: str = Form("receipt"),
    _auth: None = Depends(require_auth),
):
    db = get_db()
    db.execute("""
        INSERT INTO budgets (category, monthly_limit, scope, updated_at)
        VALUES (?,?,?, datetime('now','localtime'))
        ON CONFLICT(category) DO UPDATE SET
            monthly_limit = ?, scope = ?, updated_at = datetime('now','localtime')
    """, (category, monthly_limit, scope, monthly_limit, scope))
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)

@app.post("/budgets/{budget_id}/sil")
async def budgets_sil(budget_id: int, _auth: None = Depends(require_auth)):
    db = get_db()
    db.execute("DELETE FROM budgets WHERE id=?", (budget_id,))
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)


# ───────────────────────────── Tax Summary ────────────────────────

@app.get("/tax-summary", response_class=HTMLResponse)
async def tax_summary(request: Request, year: int = None, _auth: None = Depends(require_auth)):
    if not year:
        year = date.today().year

    # Monthly tax from receipts
    receipt_tax = fetch_all("""
        SELECT strftime('%Y-%m', created_at) AS month,
               ROUND(SUM(tax_amount),2) AS tax,
               ROUND(SUM(total_amount),2) AS total,
               COUNT(*) AS n
        FROM receipts
        WHERE strftime('%Y', created_at) = ? AND parse_status='success' AND tax_amount > 0
        GROUP BY month ORDER BY month
    """, (str(year),))

    # Monthly tax from manual expenses
    manual_tax = fetch_all("""
        SELECT strftime('%Y-%m', expense_date) AS month,
               ROUND(SUM(tax_amount),2) AS tax,
               ROUND(SUM(amount),2) AS total
        FROM manual_expenses
        WHERE strftime('%Y', expense_date) = ? AND tax_amount > 0
        GROUP BY month ORDER BY month
    """, (str(year),))

    # Merge by month
    months_data = {}
    for r in receipt_tax:
        months_data.setdefault(r["month"], {"month": r["month"], "receipt_tax": 0, "manual_tax": 0, "total_tax": 0, "taxable_total": 0})
        months_data[r["month"]]["receipt_tax"] = r["tax"]
        months_data[r["month"]]["taxable_total"] += r["total"]
    for m in manual_tax:
        months_data.setdefault(m["month"], {"month": m["month"], "receipt_tax": 0, "manual_tax": 0, "total_tax": 0, "taxable_total": 0})
        months_data[m["month"]]["manual_tax"] = m["tax"]
        months_data[m["month"]]["taxable_total"] += m["total"]
    for k in months_data:
        months_data[k]["total_tax"] = round(months_data[k]["receipt_tax"] + months_data[k]["manual_tax"], 2)

    monthly = sorted(months_data.values(), key=lambda x: x["month"])
    yearly_tax = round(sum(m["total_tax"] for m in monthly), 2)
    yearly_taxable = round(sum(m["taxable_total"] for m in monthly), 2)

    # Available years
    years = fetch_all("""
        SELECT DISTINCT strftime('%Y', created_at) AS yr FROM receipts
        WHERE parse_status='success' AND tax_amount > 0
        UNION
        SELECT DISTINCT strftime('%Y', expense_date) FROM manual_expenses WHERE tax_amount > 0
        ORDER BY yr DESC
    """)

    return templates.TemplateResponse("tax_summary.html", {
        "request": request,
        "monthly": monthly,
        "yearly_tax": yearly_tax,
        "yearly_taxable": yearly_taxable,
        "year": year,
        "years": [r["yr"] for r in years],
    })


# ─────────────────────── Receipts Search/Pagination ───────────────

@app.get("/receipts", response_class=HTMLResponse)
async def receipts_page(
    request: Request,
    q: str = "",
    page: int = 1,
    per_page: int = 25,
    _auth: None = Depends(require_auth),
):
    offset = (page - 1) * per_page
    where_parts = ["r.parse_status = 'success'"]
    params = []

    if q:
        where_parts.append("""(
            r.store_name LIKE ? OR
            EXISTS (SELECT 1 FROM receipt_items ri WHERE ri.receipt_id=r.id AND ri.item_name LIKE ?)
        )""")
        params += [f"%{q}%", f"%{q}%"]

    where = "WHERE " + " AND ".join(where_parts)

    total_count = scalar(f"SELECT COUNT(*) FROM receipts r {where}", tuple(params))
    total_pages = max(1, (total_count + per_page - 1) // per_page)

    receipts = fetch_all(
        f"SELECT r.id, r.store_name, r.receipt_date, r.total_amount, r.tax_amount, r.currency, r.created_at "
        f"FROM receipts r {where} ORDER BY r.created_at DESC LIMIT ? OFFSET ?",
        tuple(params + [per_page, offset])
    )

    return templates.TemplateResponse("receipts_list.html", {
        "request": request,
        "receipts": receipts,
        "q": q,
        "page": page,
        "per_page": per_page,
        "total_count": total_count,
        "total_pages": total_pages,
    })


# ─────────────────────────── CSV Export ──────────────────────────

@app.get("/export/receipts.csv")
async def export_receipts(period: str = "all", _auth: None = Depends(require_auth)):
    """Download all receipts as CSV."""
    if period == "today":
        where = "WHERE date(r.created_at) = date('now','localtime')"
    elif period == "week":
        where = "WHERE date(r.created_at) >= date('now','localtime','-7 days')"
    elif period == "month":
        where = "WHERE strftime('%Y-%m',r.created_at) = strftime('%Y-%m','now','localtime')"
    else:
        where = ""

    rows = fetch_all(f"""
        SELECT r.id, r.created_at, r.store_name, r.receipt_date,
               r.total_amount, r.currency, r.type, r.parse_status,
               ri.item_name, ri.category, ri.quantity, ri.unit,
               ri.unit_price, ri.total_price
        FROM receipts r
        LEFT JOIN receipt_items ri ON ri.receipt_id = r.id
        {where}
        ORDER BY r.created_at DESC, ri.id
    """)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "receipt_id", "created_at", "store_name", "receipt_date",
        "total_amount", "currency", "type", "parse_status",
        "item_name", "category", "quantity", "unit", "unit_price", "item_total"
    ])
    for r in rows:
        writer.writerow([
            r["id"], r["created_at"], r["store_name"], r["receipt_date"],
            r["total_amount"], r["currency"], r["type"], r["parse_status"],
            r["item_name"], r["category"], r["quantity"], r["unit"],
            r["unit_price"], r["total_price"]
        ])

    output.seek(0)
    filename = f"receipts_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/export/income.csv")
async def export_income(period: str = "all", _auth: None = Depends(require_auth)):
    """Download all income records as CSV."""
    if period == "today":
        where = "WHERE date(income_date) = date('now','localtime')"
    elif period == "week":
        where = "WHERE date(income_date) >= date('now','localtime','-7 days')"
    elif period == "month":
        where = "WHERE strftime('%Y-%m',income_date) = strftime('%Y-%m','now','localtime')"
    else:
        where = ""

    rows = fetch_all(f"SELECT id, income_date, amount, currency, description FROM income {where} ORDER BY income_date DESC")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "date", "amount", "currency", "description"])
    for r in rows:
        writer.writerow([r["id"], r["income_date"], r["amount"], r["currency"], r["description"]])

    output.seek(0)
    filename = f"income_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/export/stock.csv")
async def export_stock(_auth: None = Depends(require_auth)):
    """Download current stock as CSV."""
    rows = fetch_all("SELECT item_name, category, current_quantity, unit, min_quantity, last_updated FROM stock ORDER BY category, item_name")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["item_name", "category", "current_quantity", "unit", "min_quantity", "last_updated"])
    for r in rows:
        writer.writerow([r["item_name"], r["category"], r["current_quantity"], r["unit"], r["min_quantity"], r["last_updated"]])

    output.seek(0)
    filename = f"stock_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ─────────────────────────── Suppliers ───────────────────────────

@app.get("/suppliers", response_class=HTMLResponse)
async def suppliers_page(request: Request, _auth: None = Depends(require_auth)):
    # Per-store summary
    stores = fetch_all("""
        SELECT
            store_name,
            COUNT(*)                          AS receipt_count,
            ROUND(SUM(total_amount), 2)       AS total_spent,
            ROUND(AVG(total_amount), 2)       AS avg_per_visit,
            MAX(receipt_date)                 AS last_visit,
            MIN(receipt_date)                 AS first_visit
        FROM receipts
        WHERE parse_status = 'success' AND store_name IS NOT NULL AND store_name != ''
        GROUP BY store_name
        ORDER BY total_spent DESC
    """)

    # Top items per store with latest unit price
    store_items = {}
    for s in stores:
        name = s["store_name"]
        items = fetch_all("""
            SELECT
                ri.item_name,
                ri.category,
                ri.unit,
                SUM(ri.quantity)                                          AS total_qty,
                ROUND(SUM(ri.total_price), 2)                            AS total_spent,
                (SELECT unit_price FROM receipt_items ri2
                 JOIN receipts r2 ON ri2.receipt_id = r2.id
                 WHERE r2.store_name = ? AND ri2.item_name = ri.item_name
                   AND ri2.unit_price > 0
                 ORDER BY r2.receipt_date DESC LIMIT 1)                   AS last_price,
                (SELECT unit_price FROM receipt_items ri3
                 JOIN receipts r3 ON ri3.receipt_id = r3.id
                 WHERE r3.store_name = ? AND ri3.item_name = ri.item_name
                   AND ri3.unit_price > 0
                 ORDER BY r3.receipt_date DESC LIMIT 1 OFFSET 1)          AS prev_price
            FROM receipt_items ri
            JOIN receipts r ON ri.receipt_id = r.id
            WHERE r.store_name = ? AND r.parse_status = 'success'
            GROUP BY ri.item_name
            ORDER BY total_spent DESC
            LIMIT 20
        """, (name, name, name))
        store_items[name] = items

    # Price change alerts: items whose last price > prev price by >5%
    price_alerts = fetch_all("""
        SELECT
            r.store_name,
            ri.item_name,
            ri.unit,
            ri.unit_price AS last_price,
            prev.unit_price AS prev_price,
            ROUND((ri.unit_price - prev.unit_price) * 100.0 / prev.unit_price, 1) AS change_pct
        FROM receipt_items ri
        JOIN receipts r ON ri.receipt_id = r.id
        JOIN (
            SELECT ri2.item_name, r2.store_name, ri2.unit_price,
                   ROW_NUMBER() OVER (PARTITION BY ri2.item_name, r2.store_name
                                      ORDER BY r2.receipt_date DESC) AS rn
            FROM receipt_items ri2
            JOIN receipts r2 ON ri2.receipt_id = r2.id
            WHERE ri2.unit_price > 0 AND r2.parse_status = 'success'
        ) prev ON prev.item_name = ri.item_name
              AND prev.store_name = r.store_name
              AND prev.rn = 2
        WHERE ri.unit_price > 0
          AND r.parse_status = 'success'
          AND ri.id = (
              SELECT MAX(ri3.id) FROM receipt_items ri3
              JOIN receipts r3 ON ri3.receipt_id = r3.id
              WHERE ri3.item_name = ri.item_name AND r3.store_name = r.store_name
                AND ri3.unit_price > 0
          )
          AND (ri.unit_price - prev.unit_price) * 100.0 / prev.unit_price > 5
        ORDER BY change_pct DESC
        LIMIT 20
    """)

    return templates.TemplateResponse("suppliers.html", {
        "request": request,
        "stores": stores,
        "store_items": store_items,
        "price_alerts": price_alerts,
    })


# ────────────────────────── Weekly Report ─────────────────────────

@app.get("/weekly-report", response_class=HTMLResponse)
async def weekly_report_page(request: Request, _auth: None = Depends(require_auth)):
    # This week vs last week by category
    this_week = fetch_all("""
        SELECT ri.category, ROUND(SUM(ri.total_price), 2) AS total
        FROM receipt_items ri
        JOIN receipts r ON ri.receipt_id = r.id
        WHERE date(r.created_at) >= date('now','localtime','-7 days')
          AND r.parse_status = 'success'
          AND ri.category IS NOT NULL
        GROUP BY ri.category ORDER BY total DESC
    """)
    last_week = fetch_all("""
        SELECT ri.category, ROUND(SUM(ri.total_price), 2) AS total
        FROM receipt_items ri
        JOIN receipts r ON ri.receipt_id = r.id
        WHERE date(r.created_at) >= date('now','localtime','-14 days')
          AND date(r.created_at) <  date('now','localtime','-7 days')
          AND r.parse_status = 'success'
          AND ri.category IS NOT NULL
        GROUP BY ri.category ORDER BY total DESC
    """)

    # Merge into comparison dict
    tw = {r["category"]: r["total"] for r in this_week}
    lw = {r["category"]: r["total"] for r in last_week}
    all_cats = sorted(set(tw) | set(lw))
    comparison = []
    for cat in all_cats:
        t = tw.get(cat, 0)
        l = lw.get(cat, 0)
        diff = t - l
        pct  = round((diff / l * 100), 1) if l else None
        comparison.append({"category": cat, "this_week": t, "last_week": l, "diff": diff, "pct": pct})
    comparison.sort(key=lambda x: x["this_week"], reverse=True)

    # Daily spend for last 14 days
    daily = []
    from datetime import timedelta as td
    for i in range(13, -1, -1):
        d = (date.today() - td(days=i)).isoformat()
        g = scalar("SELECT COALESCE(SUM(total_amount),0) FROM receipts WHERE date(created_at)=? AND parse_status='success'", (d,))
        daily.append({"date": d[-5:], "total": g, "week": "last" if i >= 7 else "this"})

    # Top items this week
    top_items = fetch_all("""
        SELECT ri.item_name, ri.category, ri.unit,
               ROUND(SUM(ri.quantity), 2) AS total_qty,
               ROUND(SUM(ri.total_price), 2) AS total_spent
        FROM receipt_items ri
        JOIN receipts r ON ri.receipt_id = r.id
        WHERE date(r.created_at) >= date('now','localtime','-7 days')
          AND r.parse_status = 'success'
        GROUP BY ri.item_name
        ORDER BY total_spent DESC LIMIT 15
    """)

    this_total = sum(r["total"] for r in this_week)
    last_total = sum(r["total"] for r in last_week)

    return templates.TemplateResponse("weekly_report.html", {
        "request": request,
        "comparison": comparison,
        "daily_json": json.dumps(daily),
        "top_items": top_items,
        "this_total": this_total,
        "last_total": last_total,
        "week_diff": this_total - last_total,
        "week_pct": round((this_total - last_total) / last_total * 100, 1) if last_total else None,
    })


# ─────────────────────────── Başlangıç ────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    print("Web dashboard basladi -> http://localhost:8000")
