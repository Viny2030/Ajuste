import httpx
urls = [
    "https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=400246",
    "https://www.infoleg.gob.ar/infolegInternet/verNorma.do?id=400246",
    "https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=404636",
]
for url in urls:
    try:
        r = httpx.get(url, follow_redirects=True, timeout=10)
        print(f"{r.status_code} | {r.url}")
        if r.status_code == 200:
            import re
            pdfs = re.findall(r'href=[\"\'"]([^"\']+\.pdf[^"\']*)["\']', r.text, re.I)
            print(f"  PDFs: {pdfs[:5]}")
    except Exception as e:
        print(f"ERROR: {e}")
