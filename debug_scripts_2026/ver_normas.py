import csv
ids = {'43', '284', '398', '470'}
with open('data/processed/infoleg_normativa.csv', encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        if (row.get('tipo_norma') == 'Decisión Administrativa'
            and row.get('numero_norma','').strip() in ids
            and '2024' in row.get('fecha_boletin','')):
            print(f"--- DA {row['numero_norma']} ---")
            print(f"  sumario:  {row['titulo_sumario']}")
            print(f"  resumido: {row['titulo_resumido']}")
            print(f"  texto:    {row['texto_resumido'][:120]}")
