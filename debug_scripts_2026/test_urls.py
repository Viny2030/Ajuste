import asyncio, httpx, json

async def test():
    with open('data/processed/normas_infoleg_2024.json', encoding='utf-8') as f:
        normas = json.load(f)
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        for n in normas[:3]:
            url = n['url_infoleg']
            try:
                r = await client.get(url)
                print(f"{n['norma_id']}: {r.status_code} | {url[:70]}")
            except Exception as e:
                print(f"{n['norma_id']}: ERROR {e}")

asyncio.run(test())
