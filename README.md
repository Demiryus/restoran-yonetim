# 🍽️ Restoran Yönetim Sistemi

Telegram üzerinden fiş fotoğrafı gönder → AI okur → Veritabanına kaydeder → Web dashboard'da görüntüle.

---

## 📁 Dosya Yapısı
```
restoran-yonetim/
├── bot.py           ← Telegram botu
├── web_app.py       ← Web dashboard (FastAPI)
├── ai_parser.py     ← Claude Vision fiş okuyucu
├── database.py      ← SQLite veritabanı
├── requirements.txt
├── templates/
│   ├── dashboard.html
│   └── fis_detay.html
└── photos/          ← İndirilen fiş fotoğrafları
```

---

## ⚙️ Kurulum

### 1. Python ortamı
```bash
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

### 2. Telegram Bot Token al
- Telegram'da @BotFather'a yaz
- `/newbot` komutu → isim ver → TOKEN'ı kopyala

### 3. Environment variables
```bash
# Linux/Mac
export TELEGRAM_BOT_TOKEN="1234567890:ABCdef..."
export ANTHROPIC_API_KEY="sk-ant-..."
export WEB_URL="http://localhost:8000"   # veya sunucu IP'si

# Windows (PowerShell)
$env:TELEGRAM_BOT_TOKEN="1234567890:ABCdef..."
$env:ANTHROPIC_API_KEY="sk-ant-..."
```

### 4. Bot Token'ı bot.py içine gir (alternatif)
`bot.py` → 32. satır:
```python
BOT_TOKEN = "BURAYA_TOKEN_YAZI"
```

---

## 🚀 Çalıştırma

İki terminal aç:

**Terminal 1 — Web Dashboard:**
```bash
uvicorn web_app:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 — Telegram Bot:**
```bash
python bot.py
```

Web dashboard: http://localhost:8000

---

## 📲 Telegram Bot Komutları

| Komut | Açıklama |
|-------|----------|
| 📸 Fotoğraf gönder | Fişi AI ile okur, veritabanına kaydeder |
| `/gelir 1500 Öğle` | Gelir ekler |
| `/ozet` | Bugünkü gelir/gider özeti |
| `/stok` | Stok durumunu listeler |
| `/stokduzenle tavuk 10 kg` | Stok miktarını günceller |

---

## 🌐 Web Dashboard Özellikleri

- 📊 KPI kartları: Gelir, Gider, Net Kar, Stok sayısı
- 📈 7 günlük trend grafiği
- 🥧 Kategori dağılımı (et, ekmek, sebze vs.)
- 🧾 Son fişler listesi (tıkla → detay)
- 📦 Stok durumu + düşük stok uyarısı
- 💰 Web'den manuel gelir ekleme
- 🔍 Dönem filtresi: Bugün / 7 Gün / Bu Ay / Tümü

---

## ☁️ Sunucu'ya Yükleme (isteğe bağlı)

**DigitalOcean / VPS için:**
```bash
# systemd service oluştur
sudo nano /etc/systemd/system/restoran-bot.service

[Unit]
Description=Restoran Telegram Bot
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/restoran-yonetim
Environment=TELEGRAM_BOT_TOKEN=...
Environment=ANTHROPIC_API_KEY=...
ExecStart=/home/ubuntu/restoran-yonetim/venv/bin/python bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable restoran-bot
sudo systemctl start restoran-bot
```

---

## 🗄️ Veritabanı Tabloları

| Tablo | İçerik |
|-------|--------|
| `receipts` | Fişler (mağaza, tarih, toplam) |
| `receipt_items` | Fiş kalemleri (ürün, miktar, fiyat) |
| `stock` | Stok miktarları |
| `income` | Manuel gelir kayıtları |
