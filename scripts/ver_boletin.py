import json
with open('data/processed/normas_infoleg_2024.json', encoding='utf-8') as f:
    normas = json.load(f)
for n in normas:
    print(f"{n['norma_id']:20} | boletin: {n['numero_boletin']} | url_bora: {n['url_bora']}")
