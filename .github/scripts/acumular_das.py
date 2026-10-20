# .github/scripts/acumular_das.py
"""
Acumula en data/das_acumulado.json todas las normas que el workflow
"Daily BORA Discovery" va encontrando en data/nuevas_das.json.

Por qué: discover_bora.py reescribe nuevas_das.json completo en cada corrida.
Si un día el BORA responde a medias, las normas que ya se habían encontrado
desaparecen del archivo (y del commit). Este paso nunca borra: sólo agrega
normas nuevas y actualiza los datos de las existentes, guardando cuándo se
vio cada una por primera vez (sin fecha de "última vez": cambiaría el
archivo todos los días y generaría un commit diario sin datos nuevos).

No modifica nuevas_das.json ni la lógica del descubrimiento.
Sólo usa la biblioteca estándar.

Uso:
  python .github/scripts/acumular_das.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
NUEVAS = RAIZ / "data" / "nuevas_das.json"
ACUMULADO = RAIZ / "data" / "das_acumulado.json"


def _leer(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, list):
        raise ValueError(f"{ruta} no contiene una lista")
    return datos


def acumular(nuevas: list[dict], acumulado: list[dict], hoy: str) -> tuple[list[dict], int, int]:
    """Devuelve (acumulado actualizado, agregadas, ausentes hoy)."""
    por_id = {n["norma_id"]: dict(n) for n in acumulado}
    vistas_hoy = set()
    agregadas = 0
    for n in nuevas:
        nid = n.get("norma_id")
        if not nid:
            continue
        vistas_hoy.add(nid)
        if nid in por_id:
            primera = por_id[nid].get("primera_vez_visto", hoy)
            por_id[nid].update(n)
            por_id[nid]["primera_vez_visto"] = primera
        else:
            por_id[nid] = {**n, "primera_vez_visto": hoy}
            agregadas += 1

    # Sólo cuenta como "ausente" lo que cae dentro de la ventana revisada hoy:
    # lo anterior a la ventana móvil de 30 días es esperable que no aparezca.
    desde = min((str(n.get("fecha_boletin", "")) for n in nuevas if n.get("fecha_boletin")), default=None)
    ausentes = sum(1 for nid, n in por_id.items()
                   if nid not in vistas_hoy and desde and str(n.get("fecha_boletin", "")) >= desde)
    ordenado = sorted(por_id.values(), key=lambda x: (str(x.get("fecha_boletin", "")), x["norma_id"]))
    return ordenado, agregadas, ausentes


def main() -> int:
    nuevas = _leer(NUEVAS)
    acumulado = _leer(ACUMULADO)
    resultado, agregadas, ausentes = acumular(nuevas, acumulado, date.today().isoformat())

    ACUMULADO.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"nuevas_das.json: {len(nuevas)} - acumulado: {len(acumulado)} -> {len(resultado)} "
          f"(+{agregadas} nuevas)")
    if ausentes:
        # Una norma que ya no aparece no se borra: puede ser un fallo parcial del BORA.
        print(f"::warning::{ausentes} norma(s) del acumulado no aparecieron en la corrida de hoy "
              f"(se conservan).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
