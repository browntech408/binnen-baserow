"""Test if we can update a formula button field via row update."""
import os
import requests
from dotenv import load_dotenv
load_dotenv()

url = os.getenv("BASEROW_URL", "").strip().rstrip("/")
token = os.getenv("BASEROW_TOKEN", "").strip()
headers = {"Authorization": f"Token {token}"}

# Try to update field_7404 on row 2
payload = {
    "field_7404": {
        "url": "http://localhost:5678/webhook-test/8b4c42ef-1e58-4a03-b2c6-807b98a883fd?record_id=2",
        "label": "SendToShopify"
    }
}
r = requests.patch(
    f"{url}/api/database/rows/table/742/2/?user_field_names=false",
    headers=headers,
    json=payload,
    timeout=60
)
print("Status Code:", r.status_code)
print("Response:", r.text[:500])
