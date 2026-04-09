# Restaurant Management System — User Guide

**Version:** 1.0  
**Last updated:** April 2026

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Getting Started](#2-getting-started)
3. [Telegram Bot](#3-telegram-bot)
   - 3.1 [Sending a Receipt Photo](#31-sending-a-receipt-photo)
   - 3.2 [Consumption Mode](#32-consumption-mode)
   - 3.3 [Commands Reference](#33-commands-reference)
4. [Web Dashboard](#4-web-dashboard)
   - 4.1 [Summary Cards](#41-summary-cards)
   - 4.2 [Charts](#42-charts)
   - 4.3 [Recent Receipts](#43-recent-receipts)
   - 4.4 [Stock Inventory](#44-stock-inventory)
   - 4.5 [Income Entries](#45-income-entries)
   - 4.6 [Failed Receipts](#46-failed-receipts)
5. [Stock Management](#5-stock-management)
6. [Income Tracking](#6-income-tracking)
7. [Receipt Detail Page](#7-receipt-detail-page)
8. [Time Period Filters](#8-time-period-filters)
9. [Tips & Best Practices](#9-tips--best-practices)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. System Overview

The Restaurant Management System helps you track daily expenses, income, and stock levels with minimal manual entry. The core workflow is:

1. **Receive a supplier invoice / receipt** → photograph it
2. **Send the photo to the Telegram bot** → AI reads it automatically
3. **Items are added to stock** and the expense is recorded
4. **Check the web dashboard** at any time for a live summary

The system has two components that work together:

| Component | What it does |
|-----------|-------------|
| **Telegram Bot** | Receives receipt photos, runs AI parsing, updates the database |
| **Web Dashboard** | Displays all data — receipts, stock, income, charts |

---

## 2. Getting Started

### Access the dashboard
Open your browser and go to:
```
https://web-production-77cb0.up.railway.app
```

### Access the Telegram bot
Search for your bot on Telegram and send `/start` to confirm it is running.

### First-time setup checklist
- [ ] Bot responds to `/start` on Telegram
- [ ] Dashboard loads and shows today's date
- [ ] Send a test receipt photo to verify AI parsing works

---

## 3. Telegram Bot

### 3.1 Sending a Receipt Photo

This is the primary way to log expenses and update stock.

**Steps:**
1. Take a clear photo of the receipt or invoice
2. Open the Telegram chat with the bot
3. Send the photo (no caption needed for stock addition)

**What happens automatically:**
- The photo is saved immediately — it will never be lost even if the AI fails
- The AI reads: store name, date, total amount, and every line item
- Each item is added to stock with its quantity and unit
- The expense is recorded in the database
- The bot replies with a summary of what was parsed

**Example bot reply:**
```
Receipt saved!

Store: Metro Wholesale
Date: 2026-04-09
Total: 847.50 CAD

Items (6):
  • Chicken Breast  10.0 kg  → 120.00 CAD
  • Beef Tenderloin  5.0 kg  → 210.00 CAD
  • Olive Oil  6.0 litre  → 72.00 CAD
  • Tomato  20.0 kg  → 45.00 CAD
  • Onion  15.0 kg  → 22.50 CAD
  • Flour  25.0 kg  → 37.50 CAD

📥 Stock updated:
  ➕ Chicken Breast: +10.0 kg
  ➕ Beef Tenderloin: +5.0 kg
  ...

Open Dashboard
```

**Photo tips for best AI accuracy:**
- Hold the camera steady — no blur
- Use good lighting, avoid shadows over the text
- Make sure the entire receipt fits in the frame
- If the receipt is long, photograph it flat on a table

---

### 3.2 Consumption Mode

When you use ingredients from stock (kitchen prep, waste, etc.), you can record it by sending a receipt photo **with a caption**.

**How to use:**
1. Take a photo of the usage record / kitchen sheet
2. Before sending, add a caption containing one of these words:

| Caption word | Language |
|---|---|
| `use` | English |
| `consume` | English |
| `deduct` | English |
| `kullan` | Turkish |
| `çıkar` | Turkish |
| `tüket` | Turkish |

**Example:** Send a photo with caption `use` → quantities will be **subtracted** from stock instead of added.

The bot reply will show `📤 Stock deducted` to confirm consumption mode was applied.

---

### 3.3 Commands Reference

| Command | Description | Example |
|---------|-------------|---------|
| `/start` | Show welcome message and command list | `/start` |
| `/income` | Record income (sales, service charges, etc.) | `/income 2500 Dinner service` |
| `/summary` | Today's income vs expense summary | `/summary` |
| `/stock` | View all current stock levels | `/stock` |
| `/stockset` | Set a stock item to a specific quantity | `/stockset chicken 10 kg` |
| `/stockuse` | Manually deduct from a stock item | `/stockuse chicken 2` |
| `/stockdel` | Delete a stock item entirely | `/stockdel chicken` |

#### `/income` — Recording Sales

Use this at the end of each shift or day to log revenue.

```
/income 3500 Lunch + Dinner
/income 1200 Catering event
/income 500
```

- First argument: amount in CAD
- Remaining text: description (optional, defaults to "End of day")

#### `/summary` — Daily Overview

Shows a quick profit/loss snapshot for today:

```
Today's Summary

Income  :    3500.00 CAD
Expense :     847.50 CAD  (2 receipts)
──────────────────────────────
Net     :    2652.50 CAD  (Profitable)
```

If any receipts failed to parse, a warning is shown with a link to retry from the dashboard.

#### `/stockset` — Set Stock Quantity

Use this to correct stock levels after a physical count:

```
/stockset "chicken breast" 8 kg
/stockset flour 15 kg
/stockset olive oil 4 litre
```

- If the item doesn't exist yet, it will be created
- The quantity is **set** (not added) to the value you provide

#### `/stockuse` — Manual Deduction

Deduct a quantity from stock without a photo:

```
/stockuse flour 2
/stockuse "olive oil" 0.5
```

Shows before/after quantities in the reply.

---

## 4. Web Dashboard

Open `https://web-production-77cb0.up.railway.app` in any browser.

### 4.1 Summary Cards

Four cards at the top show the key numbers for the selected time period:

| Card | Description |
|------|-------------|
| **Income** | Total sales recorded via `/income` or the web form |
| **Expense** | Total from successfully parsed receipts |
| **Net** | Income minus Expense (green = profit, red = loss) |
| **Receipts** | Number of receipts processed in the period |

### 4.2 Charts

**Category Breakdown (donut chart)**
Shows which categories account for the most spending. Categories are assigned by the AI when reading receipts: meat, bread, vegetable, fruit, dairy, beverage, cleaning, packaging, other.

**7-Day Trend (bar chart)**
Side-by-side bars for income (green) and expense (red) for each of the last 7 days. Useful for spotting unusually high cost days.

### 4.3 Recent Receipts

Lists the 10 most recent successfully parsed receipts for the selected period.

**Each row shows:**
- Receipt ID, store name, date, total amount
- Delete button (✕) — also reverses the stock change
- Expand arrow (▼) — click anywhere on the row to see line items

**Expanding a receipt:**
Click the row to reveal all items with quantities and prices. A "View full receipt →" link opens the detail page with the original photo.

**Deleting a receipt:**
Clicking ✕ and confirming will:
- Remove the receipt and all its items
- **Reverse the stock change** (purchased items are subtracted back from stock)
- This action cannot be undone

### 4.4 Stock Inventory

The right column shows the complete stock list, always visible.

**Features:**
- **Category filter buttons** — click a category name to show only those items
- **LOW warning** — shown in red when quantity is at or below the minimum threshold
- **Estimated value** — quantity × last known unit price (shown as a tag)
- **Progress bar** — visual fill level based on quantity vs. minimum threshold
- **Days ago** — how many days since the stock was last updated

**Updating stock from the dashboard:**
Each stock item has an edit form. You can set:
- New quantity
- Unit (kg, litre, adet, etc.)
- Minimum quantity threshold (triggers LOW warning)

**Using stock (deducting):**
The "Use" button on each stock row lets you deduct a quantity directly from the dashboard without using Telegram.

**Deleting a stock item:**
The ✕ button removes the item from stock entirely. The historical receipt data is not affected.

**Total stock value** is shown at the top of the stock panel — the sum of (current quantity × last unit price) for all items.

### 4.5 Income Entries

Below the main grid, all income records for the selected period are listed.

**Each entry shows:**
- Amount, description, date
- **Edit button** — change amount or description
- **Delete button** — remove the entry

**Adding income from the web:**
A form at the top of the income section lets you enter:
- Amount
- Description
- Date (defaults to today)

### 4.6 Failed Receipts

If the AI fails to parse a receipt (network error, unclear photo, etc.), a **red alert panel** appears at the top of the dashboard.

Each failed receipt shows:
- Receipt ID, date, Telegram username
- The error message
- **↺ Retry button** — re-runs AI parsing on the saved photo
- **✕ Delete button** — removes the record if the photo is unusable

> The photo is always saved to disk even when parsing fails, so retrying is always possible as long as the photo file exists.

---

## 5. Stock Management

### How stock is updated automatically

| Action | Effect on stock |
|--------|----------------|
| Send receipt photo (no caption) | Items **added** to existing quantities |
| Send receipt photo with `use` caption | Items **subtracted** from quantities (min 0) |
| Delete a receipt from dashboard | Stock change is **reversed** |
| `/stockset item qty unit` | Quantity **set** to exact value |
| `/stockuse item qty` | Quantity **subtracted** |
| Dashboard "Use" button | Quantity **subtracted** |

### Setting minimum thresholds

To get LOW warnings when stock runs low:
1. Find the item in the Stock Inventory panel
2. Click the edit icon
3. Set the **Min Qty** field (e.g., `5` for chicken breast)
4. Save

When `current_quantity ≤ min_quantity`, the item shows a red **[LOW]** badge in both the dashboard and the `/stock` bot command.

### Stock categories

The AI assigns one of these categories automatically:

| Category | Examples |
|----------|---------|
| meat | chicken, beef, lamb, fish |
| bread | bread, pita, bun, flour |
| vegetable | tomato, onion, pepper, lettuce |
| fruit | lemon, apple, orange |
| dairy | cheese, butter, cream, milk |
| beverage | water, juice, soda, coffee |
| cleaning | detergent, bleach, gloves |
| packaging | bags, containers, foil |
| other | anything unrecognized |

---

## 6. Income Tracking

Record income every day to maintain accurate profit/loss figures.

### Via Telegram bot
```
/income 4200 Saturday dinner service
/income 800 Private catering
/income 150 Bar sales
```

### Via Web Dashboard
Use the income form at the bottom of the dashboard. You can also set a specific past date if you forgot to log it.

### Editing income
Click the **Edit** button next to any income entry. Change the amount or description and save. The net figure updates immediately.

---

## 7. Receipt Detail Page

Click **"View full receipt →"** inside any expanded receipt row to open the full detail page.

**Shows:**
- All receipt metadata (store, date, total, currency, type)
- Complete item list with quantities, units, unit prices, and totals
- The original receipt photo (if available)
- A back link to the dashboard

This page is useful for verifying what the AI read vs. what the actual receipt says.

---

## 8. Time Period Filters

The dashboard supports four time periods, selected via buttons at the top:

| Button | Shows data for |
|--------|---------------|
| **Today** | Current calendar day |
| **Last 7 Days** | Rolling 7-day window |
| **This Month** | Current calendar month |
| **All Time** | Everything in the database |

The summary cards, charts, receipt list, and income list all update based on the selected period. **Stock inventory is always shown in full** regardless of the period filter.

---

## 9. Tips & Best Practices

**Daily routine:**
1. Morning: check `/stock` or the dashboard for low stock items before ordering
2. When supplies arrive: photograph each invoice and send to the bot
3. End of day: send `/income [total] [note]` for the day's sales
4. Weekly: use the 7-day trend chart to compare cost vs. revenue days

**Receipt photos:**
- Photograph immediately when goods arrive — don't let receipts pile up
- If the AI misreads an item name, you can manually correct it via `/stockset`
- Long receipts (>20 items): the bot shows the first 12 in the reply, but all items are saved

**Stock accuracy:**
- Do a physical stock count once a week and use `/stockset` to correct any drift
- Use consumption mode (caption `use`) when recording kitchen prep waste
- Set minimum quantities for your top ingredients to get LOW alerts

**Multiple receipts from same supplier:**
Each photo creates a separate receipt. Items are cumulative — sending two receipts for 5 kg chicken each will result in +10 kg total in stock.

---

## 10. Troubleshooting

### Bot is not responding
- Check that no other instance of the bot is running locally
- Open the Railway dashboard → Deploy Logs to see if there are errors
- If you see `Conflict: terminated by other getUpdates request` — a local bot process is still running; kill it

### Receipt parsed incorrectly
- The AI may misread blurry or poorly lit photos — retake with better lighting
- For critical items, verify the stock update via `/stock` after sending
- Use `/stockset` to manually correct any wrong quantities

### Dashboard shows no data
- Ensure the Railway environment variables are set (TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, DB_PATH, PHOTOS_DIR, WEB_URL)
- Ensure the Volume is mounted at `/data`
- Check that DB_PATH is set to `/data/restoran.db`

### Failed receipt in dashboard
- Click **↺ Retry** — this re-runs the AI on the saved photo
- If retry keeps failing, the photo may be too blurry; delete the record and send a new photo
- If the error mentions `credit` or `quota`, the Anthropic API key may be out of credits

### Stock levels seem wrong
- Remember that deleting a receipt **reverses** the stock change
- Use `/stock` on Telegram for a quick current count
- Do a manual physical count and correct with `/stockset`

### "Photo file not found" on retry
- The photo was saved locally but the server was redeployed without a Volume
- Ensure the Railway Volume is mounted at `/data` and PHOTOS_DIR is set to `/data/photos`
- Photos sent before the Volume was added cannot be recovered

---

*For technical issues or feature requests, contact your system administrator.*
