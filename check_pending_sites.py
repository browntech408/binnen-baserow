import requests
from bs4 import BeautifulSoup

def check_site(name, urls):
    print(f"=== {name} ===")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for u in urls:
        try:
            r = requests.get(u, headers=headers, timeout=5, allow_redirects=True)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else "(no title)"
            print(f"  URL: {u}")
            print(f"    Status: {r.status_code}")
            print(f"    Final: {r.url}")
            print(f"    Title: {title}")
            print(f"    Snippet: {r.text[:200].strip()}")
        except Exception as e:
            print(f"  URL: {u} -> Error: {type(e).__name__}: {e}")

if __name__ == "__main__":
    check_site("Artimeta", ["https://www.artimeta.nl", "http://artimeta.nl"])
    check_site("Evidence", ["https://www.evidence-living.com", "http://evidence-living.com"])
    check_site("Odesi", ["https://www.odesi.nl", "http://odesi.nl"])
