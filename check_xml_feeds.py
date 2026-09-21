import requests

feeds = {
    'Pronto Wonen': 'https://www.prontowonen.nl/media/feeds_nl/winkelfeed_pw_woonbloq.xml',
    'Profijt Meubel': 'https://www.profijtmeubel.nl/media/feeds_nl/winkelfeed_pm_woonbloq.xml',
    'Inhouse': 'https://www.in-house.nl/feeds/Heeswijk_IH.xml'
}

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

for name, url in feeds.items():
    try:
        r = requests.get(url, headers=headers, timeout=10, stream=True)
        length = r.headers.get("Content-Length", "unknown")
        print(f"{name}: Status {r.status_code}, Length: {length}")
        chunk = next(r.iter_content(400)).decode('utf-8', errors='ignore')
        print(f"  Snippet: {chunk[:200]}")
    except Exception as e:
        print(f"{name}: Error: {e}")
