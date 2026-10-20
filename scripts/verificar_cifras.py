"""
scripts/verificar_cifras.py
===========================
Auditoría diaria de las cifras publicadas por el MAP, independiente del
código de la app: recalcula por su cuenta lo que se puede recalcular y
compara contra la API en producción.

Controles:
  1. Salud        — /health y /api/v1/status responden 200.
  2. Deflactor    — factor 2023→año actual y TC promedio de cada año,
                    recalculados desde la API BCRA v4.0 (paginada), contra
                    /api/v1/status. Tolerancia 0,5 %.
  3. Frescura IPC — el último mes de IPC usado no tiene más de 75 días.
  4. Presupuesto  — el total nominal del año actual (evolucion-real) no
                    cae más de 10 % contra la última corrida (una caída así
                    indica un ZIP de MECON incompleto, no un recorte).
  5. Normativa    — ninguna norma de data/das_acumulado.json desaparece de
                    lo que sirve /api/v1/normativa/.

Sólo usa la biblioteca estándar (no necesita pip install).
Sale con código 1 si algún control FALLA; las ADVERTENCIAS no cortan.

Uso:
  python scripts/verificar_cifras.py
  python scripts/verificar_cifras.py --base-url https://ajuste.ecosistematransparencia.com.ar --reporte reporte.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

BASE_URL = os.environ.get("AJUSTE_API_URL", "https://ajuste.ecosistematransparencia.com.ar")
BCRA = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
RAIZ = Path(__file__).resolve().parents[1]
SNAPSHOT = RAIZ / "data" / "verificacion" / "ultima_verificacion.json"
ACUMULADO = RAIZ / "data" / "das_acumulado.json"

TOL_REL = 0.005          # 0,5 %
MAX_CAIDA_TOTAL = 0.10   # 10 %
MAX_DIAS_IPC = 75


def _get_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "MAP-verificador/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def serie_bcra(id_variable: int, desde: str = "2023-01-01") -> list[dict]:
    """Serie completa, paginada (la API corta en 3000 empezando por lo más nuevo)."""
    hasta = date.today().isoformat()
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"desde": desde, "hasta": hasta, "limit": 3000, "offset": offset})
        data = _get_json(f"{BCRA}/{id_variable}?{q}")
        pagina = data["results"][0]["detalle"] if data.get("results") else []
        out.extend(pagina)
        offset += len(pagina)
        if not pagina or offset >= data["metadata"]["resultset"]["count"]:
            break
    return sorted(out, key=lambda r: r["fecha"])


def recalcular(anio_base: int, anio_comp: int) -> dict:
    """Mismo criterio que app/core/deflactor.py, implementado aparte."""
    nivel, niveles = 1.0, {}
    for r in serie_bcra(27):
        nivel *= 1 + float(r["valor"]) / 100
        niveles[r["fecha"][:7]] = nivel

    def prom(anio):
        v = [x for m, x in niveles.items() if m.startswith(str(anio))]
        return sum(v) / len(v) if v else None

    tc_anio: dict[int, list[float]] = {}
    for r in serie_bcra(5):
        tc_anio.setdefault(int(r["fecha"][:4]), []).append(float(r["valor"]))

    return {
        "factor": prom(anio_comp) / prom(anio_base),
        "tc_promedio_base": sum(tc_anio[anio_base]) / len(tc_anio[anio_base]),
        "tc_promedio_comp": sum(tc_anio[anio_comp]) / len(tc_anio[anio_comp]),
        "ultimo_mes_ipc": max(niveles),
    }


class Resultado:
    def __init__(self):
        self.filas: list[tuple[str, str, str]] = []

    def ok(self, control, detalle):   self.filas.append(("OK", control, detalle))
    def warn(self, control, detalle): self.filas.append(("ADVERTENCIA", control, detalle))
    def fail(self, control, detalle): self.filas.append(("FALLA", control, detalle))

    @property
    def fallas(self):
        return [f for f in self.filas if f[0] == "FALLA"]

    def markdown(self, base_url: str) -> str:
        icono = {"OK": "✅", "ADVERTENCIA": "⚠️", "FALLA": "❌"}
        lineas = [f"## Verificación de cifras MAP — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
                  f"Producción: {base_url}", "",
                  "| | Control | Detalle |", "|---|---|---|"]
        lineas += [f"| {icono[e]} | {c} | {d} |" for e, c, d in self.filas]
        return "\n".join(lineas) + "\n"


def _cerca(a: float, b: float) -> bool:
    return abs(a - b) <= TOL_REL * max(abs(a), abs(b), 1e-9)


def verificar(base_url: str, hoy: date | None = None) -> tuple[Resultado, dict]:
    hoy = hoy or date.today()
    res, snapshot = Resultado(), {}

    # 1. Salud ------------------------------------------------------------
    try:
        _get_json(f"{base_url}/health")
        status = _get_json(f"{base_url}/api/v1/status")
        res.ok("Salud", f"/health y /api/v1/status OK (v{status.get('version')})")
    except Exception as e:
        res.fail("Salud", f"la API no responde: {e}")
        return res, snapshot

    d = status.get("deflactor", {})
    anio_base, anio_comp = d.get("anio_base", 2023), d.get("anio_comp", hoy.year)

    # 2. Deflactor y TC ---------------------------------------------------
    try:
        ref = recalcular(anio_base, anio_comp)
        for clave in ("factor", "tc_promedio_base", "tc_promedio_comp"):
            pub = float(d.get(clave) or 0)
            if _cerca(pub, ref[clave]):
                res.ok(f"Deflactor · {clave}", f"publicado {pub:,.4f} = recalculado {ref[clave]:,.4f}")
            else:
                res.fail(f"Deflactor · {clave}",
                         f"publicado {pub:,.4f} ≠ recalculado {ref[clave]:,.4f} (BCRA v4.0)")
        for clave in ("fuente_ipc", "fuente_tc"):
            if d.get(clave) != "BCRA v4.0":
                res.warn(f"Fuente · {clave}", f"producción usa '{d.get(clave)}' (respaldo), no BCRA v4.0")
        snapshot["deflactor"] = {k: round(v, 6) if isinstance(v, float) else v for k, v in ref.items()}
    except Exception as e:
        res.warn("Deflactor", f"no se pudo recalcular (BCRA no disponible): {e}")

    # 3. Frescura IPC -----------------------------------------------------
    ultimo = d.get("ultimo_mes_ipc")
    if ultimo:
        y, m = map(int, ultimo.split("-"))
        fin_mes = date(y + (m == 12), m % 12 + 1, 1)
        dias = (hoy - fin_mes).days
        (res.ok if dias <= MAX_DIAS_IPC else res.fail)(
            "Frescura IPC", f"último mes {ultimo} ({dias} días desde su cierre; máx. {MAX_DIAS_IPC})")
    else:
        res.fail("Frescura IPC", "status no informa ultimo_mes_ipc")

    # 4. Total presupuestario del año en curso ----------------------------
    previo = {}
    if SNAPSHOT.exists():
        try:
            previo = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        except Exception:
            previo = {}
    try:
        evol = _get_json(f"{base_url}/api/v1/analisis/evolucion-real")
        actual = next((r for r in evol if r["ejercicio"] == anio_comp), None)
        if not actual:
            res.fail("Presupuesto", f"evolucion-real no trae {anio_comp}")
        else:
            total = float(actual["total_nominal"])
            snapshot["total_nominal"] = {"ejercicio": anio_comp, "valor": total}
            ant = (previo.get("total_nominal") or {})
            if ant.get("ejercicio") == anio_comp and ant.get("valor"):
                caida = 1 - total / float(ant["valor"])
                txt = (f"{anio_comp}: ${total / 1e12:,.2f} billones "
                       f"(anterior ${float(ant['valor']) / 1e12:,.2f}; {-caida * 100:+.1f} %)")
                (res.fail if caida > MAX_CAIDA_TOTAL else res.ok)("Presupuesto", txt)
            else:
                res.ok("Presupuesto", f"{anio_comp}: ${total / 1e12:,.2f} billones (primera corrida)")
    except Exception as e:
        res.fail("Presupuesto", f"evolucion-real falló: {e}")

    # 5. Normativa: nada de lo acumulado desaparece -----------------------
    try:
        norm = _get_json(f"{base_url}/api/v1/normativa/?limit=500")
        publicadas = {i.get("norma_id") for i in norm.get("items", [])}
        snapshot["normativa_total"] = norm.get("total")
        acumuladas = json.loads(ACUMULADO.read_text(encoding="utf-8")) if ACUMULADO.exists() else []
        faltan = sorted(a["norma_id"] for a in acumuladas if a["norma_id"] not in publicadas)
        if faltan:
            res.warn("Normativa", f"{len(faltan)} norma(s) ya descubiertas no figuran en la API: "
                                  f"{', '.join(faltan[:10])}")
        else:
            res.ok("Normativa", f"{norm.get('total')} normas publicadas; "
                                f"{len(acumuladas)} acumuladas, ninguna perdida")
    except Exception as e:
        res.warn("Normativa", f"no se pudo verificar: {e}")

    snapshot["fecha"] = hoy.isoformat()
    return res, snapshot


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--reporte", default=None, help="Guardar el reporte Markdown en este archivo")
    ap.add_argument("--no-snapshot", action="store_true", help="No actualizar la última verificación")
    args = ap.parse_args(argv)

    base = args.base_url.rstrip("/")
    res, snapshot = verificar(base)
    md = res.markdown(base)
    sys.stdout.buffer.write(md.encode("utf-8"))
    if args.reporte:
        Path(args.reporte).write_text(md, encoding="utf-8")

    # El snapshot sólo avanza si no hubo fallas: si el total cayó por un ZIP
    # roto, la próxima corrida sigue comparando contra el último valor sano.
    if not args.no_snapshot and not res.fallas and snapshot:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if res.fallas else 0


if __name__ == "__main__":
    sys.exit(main())
