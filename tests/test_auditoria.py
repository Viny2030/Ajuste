"""Tests de scripts/verificar_cifras.py y .github/scripts/acumular_das.py (sin red)."""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


def _cargar(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, RAIZ / ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


acu = _cargar("acumular_das", ".github/scripts/acumular_das.py")
ver = _cargar("verificar_cifras", "scripts/verificar_cifras.py")


# ── acumular_das ─────────────────────────────────────────────────────────────

def test_acumular_nunca_borra_y_conserva_primera_vez():
    previo = [{"norma_id": "DA-18-2026", "fecha_boletin": "2026-05-06", "primera_vez_visto": "2026-05-07"},
              {"norma_id": "DA-20-2026", "fecha_boletin": "2026-05-11", "primera_vez_visto": "2026-05-12"}]
    hoy = [{"norma_id": "DA-18-2026", "fecha_boletin": "2026-05-06", "tipo": "DA"},
           {"norma_id": "DA-30-2026", "fecha_boletin": "2026-10-01"}]
    res, agregadas, ausentes = acu.acumular(hoy, previo, "2026-10-20")
    ids = [r["norma_id"] for r in res]
    assert ids == ["DA-18-2026", "DA-20-2026", "DA-30-2026"]   # DA-20 no se pierde
    assert agregadas == 1 and ausentes == 1
    d18 = res[0]
    assert d18["primera_vez_visto"] == "2026-05-07" and d18["tipo"] == "DA"
    assert res[2]["primera_vez_visto"] == "2026-10-20"


def test_acumular_es_idempotente():
    hoy = [{"norma_id": "DA-1-2026", "fecha_boletin": "2026-01-02"}]
    uno, _, _ = acu.acumular(hoy, [], "2026-10-20")
    dos, agregadas, _ = acu.acumular(hoy, uno, "2026-10-21")
    assert uno == dos and agregadas == 0


# ── verificar_cifras ─────────────────────────────────────────────────────────

def _bcra(id_variable, n_pagina):
    """Simula la API BCRA paginada: IPC 1 %/mes 2023-01..2024-12, TC = año."""
    if id_variable == 27:
        detalle = [{"fecha": f"{a}-{m:02d}-28", "valor": 1.0} for a in (2023, 2024) for m in range(1, 13)]
    else:
        detalle = [{"fecha": f"{a}-{m:02d}-15", "valor": 100.0 if a == 2023 else 200.0}
                   for a in (2023, 2024) for m in range(1, 13)]
    detalle = detalle[::-1]  # la API devuelve lo más nuevo primero
    return detalle, n_pagina


def _fake_get(status, evol, normativa, n_pagina=5):
    def get(url, timeout=60):
        if "api.bcra.gob.ar" in url:
            from urllib.parse import parse_qs, urlparse
            u = urlparse(url)
            idv = int(u.path.rsplit("/", 1)[-1])
            off = int(parse_qs(u.query)["offset"][0])
            todo, n = _bcra(idv, n_pagina)
            return {"metadata": {"resultset": {"count": len(todo)}},
                    "results": [{"detalle": todo[off:off + n]}]}
        if url.endswith("/health"):
            return {"status": "ok"}
        if url.endswith("/api/v1/status"):
            return status
        if "evolucion-real" in url:
            return evol
        if "normativa" in url:
            return normativa
        raise AssertionError(url)
    return get


def _status_correcto():
    # factor esperado: promedio niveles 2024 / promedio 2023 con 1 %/mes = 1,01**12
    return {"version": "x", "deflactor": {
        "anio_base": 2023, "anio_comp": 2024, "factor": 1.01 ** 12,
        "tc_promedio_base": 100.0, "tc_promedio_comp": 200.0, "ultimo_mes_ipc": "2024-12",
        "fuente_ipc": "BCRA v4.0", "fuente_tc": "BCRA v4.0"}}


def _preparar(monkeypatch, tmp_path, status, total, previo=None, normativa=None):
    monkeypatch.setattr(ver, "SNAPSHOT", tmp_path / "snap.json")
    monkeypatch.setattr(ver, "ACUMULADO", tmp_path / "acu.json")
    if previo is not None:
        (tmp_path / "snap.json").write_text(json.dumps(previo))
    (tmp_path / "acu.json").write_text(json.dumps([{"norma_id": "DA-1-2024"}]))
    normativa = normativa or {"total": 1, "items": [{"norma_id": "DA-1-2024"}]}
    monkeypatch.setattr(ver, "_get_json", _fake_get(status, [{"ejercicio": 2024, "total_nominal": total}], normativa))


def test_todo_ok_y_pagina_la_api(monkeypatch, tmp_path):
    _preparar(monkeypatch, tmp_path, _status_correcto(), 100.0)
    res, snap = ver.verificar("https://x", hoy=date(2025, 1, 20))
    assert not res.fallas, res.filas
    assert abs(snap["deflactor"]["factor"] - 1.01 ** 12) < 1e-6   # 24 registros en páginas de 5


def test_detecta_factor_distinto(monkeypatch, tmp_path):
    st = _status_correcto(); st["deflactor"]["factor"] = 1.2
    _preparar(monkeypatch, tmp_path, st, 100.0)
    res, _ = ver.verificar("https://x", hoy=date(2025, 1, 20))
    assert any("factor" in c for _, c, _ in res.fallas)


def test_detecta_caida_del_total_y_ipc_viejo(monkeypatch, tmp_path):
    _preparar(monkeypatch, tmp_path, _status_correcto(), 80.0,
              previo={"total_nominal": {"ejercicio": 2024, "valor": 100.0}})
    res, _ = ver.verificar("https://x", hoy=date(2025, 6, 1))
    controles = {c for _, c, _ in res.fallas}
    assert "Presupuesto" in controles and "Frescura IPC" in controles


def test_avisa_norma_perdida(monkeypatch, tmp_path):
    _preparar(monkeypatch, tmp_path, _status_correcto(), 100.0, normativa={"total": 0, "items": []})
    res, _ = ver.verificar("https://x", hoy=date(2025, 1, 20))
    assert any(e == "ADVERTENCIA" and c == "Normativa" for e, c, _ in res.filas)


def test_snapshot_no_avanza_si_hay_fallas(monkeypatch, tmp_path):
    _preparar(monkeypatch, tmp_path, _status_correcto(), 50.0,
              previo={"total_nominal": {"ejercicio": 2024, "valor": 100.0}})
    monkeypatch.setattr(ver, "verificar", lambda base: _falla())
    assert ver.main(["--base-url", "https://x"]) == 1
    assert json.loads((tmp_path / "snap.json").read_text())["total_nominal"]["valor"] == 100.0


def _falla():
    r = ver.Resultado(); r.fail("Presupuesto", "x")
    return r, {"total_nominal": {"ejercicio": 2024, "valor": 50.0}}
