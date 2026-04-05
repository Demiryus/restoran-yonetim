import anthropic
import base64
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

# Kısa prompt = daha az token = daha ucuz
PARSE_PROMPT = (
    "Fişi JSON'a çevir. SADECE JSON yaz, başka hiçbir şey ekleme.\n"
    '{"store_name":"...","receipt_date":"YYYY-MM-DD or null","total_amount":0.00,"currency":"CAD",'
    '"items":[{"item_name":"original name","category":"meat|bread|vegetable|fruit|dairy|beverage|cleaning|packaging|other",'
    '"quantity":0.0,"unit":"kg|adet|litre|paket","unit_price":0.00,"total_price":0.00}]}\n'
    "Use null for unknowns. Keep item names in their original language."
)

MEDIA_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",  ".webp": "image/webp",
    ".gif": "image/gif",
}


def parse_receipt(image_path: str) -> tuple[dict, str]:
    suffix = Path(image_path).suffix.lower()
    media_type = MEDIA_MAP.get(suffix, "image/jpeg")

    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode()

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # En ucuz Claude vision modeli
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {"type": "text", "text": PARSE_PROMPT},
            ],
        }],
    )

    raw_text = response.content[0].text
    clean = raw_text.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(clean)
    return parsed, raw_text
