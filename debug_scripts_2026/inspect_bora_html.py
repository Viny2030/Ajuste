import httpx
import re

BASE = "https://www.boletinoficial.gob.ar"
r = httpx.get(BASE + "/seccion/primera/20240606", timeout=15, follow_redirects=True)

print(f"Status: {r.status_code}, chars: {len(r.text)}\n")

# Buscar bloques <script> inline (no src=)
scripts_inline = re.findall(r'<script(?![^>]*src)[^>]*>(.*?)</script>', r.text, re.DOTALL)
for i, s in enumerate(scripts_inline):
    s = s.strip()
    if s:
        print(f"\n--- inline script {i+1} ({len(s)} chars) ---")
        print(s[:2000])

# Buscar variables JS directamente
print("\n\n--- Variables var/let/const/window ---")
vars_js = re.findall(r'(?:var|let|const|window)\s+(\w+)\s*=\s*["\']?([^;"\'\n]{0,120})', r.text)
for name, val in vars_js:
    if any(k in name.lower() for k in ['url', 'path', 'seccion', 'aviso', 'norma', 'api', 'fetch']):
        print(f"  {name} = {val}")