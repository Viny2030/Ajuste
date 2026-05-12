# app/core/engine.py
class AnalizadorPresupuestario:
    def __init__(self, db_session):
        self.db = db_session
        # En una versión real, esto vendría de una tabla de 'macro_indices'
        self.ipc_acumulado_2023_2026 = 8.5  # Ejemplo: 850% de inflación acumulada

    def calcular_variacion_real(self, base, modificaciones):
        # Sumar todas las modificaciones detectadas por el scraper
        total_modificaciones = sum(m.monto_neto for m in modificaciones)
        vigente_actual = base.monto_vigente + total_modificaciones

        # CALCULO CRÍTICO:
        # Traemos el valor actual a moneda de enero 2023
        valor_real_ajustado = vigente_actual / self.ipc_acumulado_2023_2026

        variacion_real = ((valor_real_ajustado / base.monto_original) - 1) * 100

        return {
            "monto_real_en_moneda_2023": round(valor_real_ajustado, 2),
            "variacion_real_porcentual": round(variacion_real, 2),
            "estado_ajuste": "REDUCCIÓN" if variacion_real < 0 else "INCREMENTO"
        }