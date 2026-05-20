from sqlalchemy import create_engine, text
engine = create_engine('sqlite:///sql_app.db')
with engine.connect() as c:
    # Total vigente 2023 en la DB — si está en pesos debería ser ~decenas de billones
    # si está en millones debería ser ~decenas de millones
    r = c.execute(text("""
        SELECT 
            ROUND(SUM(monto_vigente), 0) as sum_vigente,
            ROUND(SUM(monto_vigente) / 1e12, 2) as en_billones,
            ROUND(SUM(monto_vigente) / 1e6, 2) as en_millones_de_millones
        FROM presupuesto_base WHERE ejercicio=2023
    """)).fetchone()
    print('2023 monto_vigente total:')
    print(dict(r._mapping))
    print()
    # Mismo para 2024
    r = c.execute(text("""
        SELECT 
            ROUND(SUM(monto_vigente), 0) as sum_vigente,
            ROUND(SUM(monto_vigente) / 1e12, 2) as en_billones,
            ROUND(SUM(monto_vigente) * 1e6 / 1e12, 2) as si_fueran_millones_en_billones
        FROM presupuesto_base WHERE ejercicio=2024
    """)).fetchone()
    print('2024 monto_vigente total:')
    print(dict(r._mapping))