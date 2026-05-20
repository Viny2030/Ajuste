import sqlite3
conn = sqlite3.connect("sql_app.db")
cur = conn.cursor()
cur.execute("""
    SELECT DISTINCT m.jurisdiccion_id, m.programa_id, pb.entidad_desc
    FROM modificaciones m
    LEFT JOIN presupuesto_base pb ON pb.id = m.partida_id
    WHERE pb.entidad_desc LIKE '%Inteligencia%'
       OR pb.entidad_desc LIKE '%SIDE%'
       OR pb.entidad_desc LIKE '%Secretaria de Inteligencia%'
""")
for r in cur.fetchall(): print(r)
conn.close()