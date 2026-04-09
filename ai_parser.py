import anthropic
import base64
import json
import os
import re
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

PARSE_PROMPT = (
    "Parse this receipt into JSON. Output ONLY valid JSON, nothing else.\n"
    '{"store_name":"...","receipt_date":"YYYY-MM-DD or null","subtotal":0.00,"tax_amount":0.00,'
    '"total_amount":0.00,"currency":"CAD","tax_label":"GST|HST|PST or null",'
    '"items":[{"item_name":"original name","category":"meat|bread|vegetable|fruit|dairy|beverage|cleaning|packaging|other",'
    '"quantity":0.0,"unit":"kg|unit|litre|pack","unit_price":0.00,"total_price":0.00}]}\n'
    "Extract tax (GST/HST/PST) into tax_amount. total_amount = subtotal + tax_amount. "
    "Use null for unknowns. Keep item names in their original language."
)

MEDIA_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",  ".webp": "image/webp",
    ".gif": "image/gif",
}

MAX_RETRIES = 2


def _extract_json(text: str) -> dict:
    """Try multiple strategies to extract valid JSON from AI response."""
    # 1. Strip markdown code fences
    clean = text.replace("```json", "").replace("```", "").strip()

    # 2. Try direct parse
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # 3. Extract first {...} block with regex
    match = re.search(r'\{[\s\S]*\}', clean)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # 4. Try to repair truncated JSON: find last complete item
    # Remove trailing incomplete fragments after the last complete '}' in items array
    try:
        # Find the items array start and try to close it gracefully
        items_match = re.search(r'("items"\s*:\s*\[)([\s\S]*)', clean)
        if items_match:
            prefix = clean[:items_match.start(1)]
            items_raw = items_match.group(2)
            # Keep only complete item objects
            complete_items = re.findall(r'\{[^{}]*\}', items_raw)
            repaired = prefix + '"items": [' + ','.join(complete_items) + ']}'
            return json.loads(repaired)
    except (json.JSONDecodeError, AttributeError):
        pass

    raise ValueError(f"Could not extract valid JSON from AI response: {clean[:200]}")


def _call_ai(image_data: str, media_type: str) -> str:
    """Single AI call, returns raw text."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
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
    return response.content[0].text


def parse_receipt(image_path: str) -> tuple[dict, str]:
    suffix = Path(image_path).suffix.lower()
    media_type = MEDIA_MAP.get(suffix, "image/jpeg")

    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode()

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw_text = _call_ai(image_data, media_type)
            parsed = _extract_json(raw_text)
            return parsed, raw_text
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(1)  # brief pause before retry

    raise RuntimeError(f"AI parsing failed after {MAX_RETRIES} attempts: {last_error}")
