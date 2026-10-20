"""Tests de los arreglos de los motores de actualización (auditoría 2026-10, puntos 2 a 9). Sin red."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


# ── Punto 3: unidad de los CSV de crédito ───────────────────────────────────

def test_escala_lee_la_columna_unidad():
    from scripts.sync_modificaciones import _escala
    assert _escala(pd.DataFrame({"unidad": ["millones_ars"] * 3})) == 1_000_000
    assert _escala(pd.DataFrame({"unidad": ["miles_ars"]})) == 1_000
    assert _escala(pd.DataFrame({"x": [1]})) == 1_000_000          # sin columna: millones
    with pytest.raises(ValueError):
        _escala(pd.DataFrame({"unidad": ["millones_ars", "miles_ars"]}))
    with pytest.raises(ValueError):
        _escala(pd.DataFrame({"unidad": ["dolares"]}))


def test_leer_csv_rechaza_unidad_inesperada(tmp_path):
    import csv, io, zipfile
    from scripts.actualizar_presupuesto import leer_csv
    def zip_con(unidad):
        b = io.StringIO()
        w = csv.DictWriter(b, fieldnames=["ejercicio_presupuestario", "credito_vigente", "unidad"])
        w.writeheader(); w.writerow({"ejercicio_presupuestario": "2026", "credito_vigente": "1,5", "unidad": unidad})
        p = tmp_path / f"{unidad}.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("c.csv", b.getvalue())
        return str(p)
    filas, _ = leer_csv(2026, zip_con("millones_ars"))
    assert filas[0]["monto_vigente"] == 1_500_000
    with pytest.raises(ValueError):
        leer_csv(2026, zip_con("miles_ars"))


# ── Punto 6: control de cordura antes del DELETE ────────────────────────────

def test_control_cordura():
    from scripts.actualizar_presupuesto import control_cordura
    assert control_cordura(0, 0, 10, 1e12) is None                        # base vacía: carga
    assert control_cordura(1000, 150e12, 1000, 152e12) is None            # sube: normal
    assert control_cordura(1000, 150e12, 1000, 140e12) is None            # -6,7 %: tolerado
    assert "vigente cae" in control_cordura(1000, 150e12, 1000, 100e12)   # -33 %
    assert "filas caen" in control_cordura(1000, 150e12, 500, 150e12)     # mitad de filas


# ── Punto 5/9: el respaldo CSV del deflactor prefiere el mayorista BCRA ─────

def _csv(tmp_path, filas):
    p = tmp_path / "macro.csv"
    p.write_text("fecha,indicador,valor,fuente\n" + "\n".join(filas) + "\n", encoding="utf-8")
    return str(p)


def test_desde_csv_prefiere_mayorista_bcra(tmp_path, monkeypatch):
    from app.core import deflactor
    monkeypatch.setattr(deflactor, "_CSV", _csv(tmp_path, [
        "2023-01-31,IPC_variacion_mensual,6.0,x",
        "2023-01-02,TC_oficial_venta,185.0,argentinadatos.com",
        "2023-01-02,TC_mayorista_bcra,178.14,BCRA v4.0",
    ]))
    ipc, tc = deflactor._desde_csv()
    assert ipc == {"2023-01": 6.0} and tc == [("2023-01-02", 178.14)]


def test_desde_csv_cae_al_oficial_si_no_hay_mayorista(tmp_path, monkeypatch):
    from app.core import deflactor
    monkeypatch.setattr(deflactor, "_CSV", _csv(tmp_path, ["2023-01-02,TC_oficial_venta,185.0,argentinadatos.com"]))
    _, tc = deflactor._desde_csv()
    assert tc == [("2023-01-02", 185.0)]


# ── Punto 2: /normativa/ sirve el acumulado y no duplica ────────────────────

def test_normativa_une_acumulado_sin_duplicar(tmp_path, monkeypatch):
    import app.main as m
    (tmp_path / "processed").mkdir()
    (tmp_path / "processed" / "das_presupuesto_2023_2026.json").write_text(json.dumps(
        [{"norma_id": "DA-4-2023", "fecha_boletin": "2023-01-10"}]))
    (tmp_path / "das_acumulado.json").write_text(json.dumps(
        [{"norma_id": "DA-26-2026", "fecha_boletin": "2026-09-04", "primera_vez_visto": "2026-10-20"},
         {"norma_id": "DA-18-2026", "fecha_boletin": "2026-05-06"}]))
    (tmp_path / "nuevas_das.json").write_text(json.dumps(
        [{"norma_id": "DA-26-2026", "fecha_boletin": "2026-09-04", "titulo": "nuevo"}]))
    monkeypatch.setattr(m, "_DATA", tmp_path)
    r = m.listar_normativa(skip=0, limit=50)
    ids = [i["norma_id"] for i in r["items"]]
    assert ids == ["DA-26-2026", "DA-18-2026", "DA-4-2023"]
    assert r["total"] == 3 and r["recientes_desde_bora"] == 2
    assert r["items"][0]["titulo"] == "nuevo" and r["items"][0]["primera_vez_visto"] == "2026-10-20"


# ── Punto 2: el acumulador no avisa por normas fuera de la ventana ──────────

def test_acumular_ignora_ausentes_fuera_de_ventana():
    import importlib.util
    spec = importlib.util.spec_from_file_location("acu", RAIZ / ".github/scripts/acumular_das.py")
    acu = importlib.util.module_from_spec(spec); spec.loader.exec_module(acu)
    previo = [{"norma_id": "DA-1-2026", "fecha_boletin": "2026-01-05"}]          # vieja: fuera de ventana
    hoy = [{"norma_id": "DA-30-2026", "fecha_boletin": "2026-10-01"}]
    res, agregadas, ausentes = acu.acumular(hoy, previo, "2026-10-20")
    assert len(res) == 2 and agregadas == 1 and ausentes == 0


def test_control_magnitud_detecta_cambio_de_unidad():
    # Los ZIP de MECON no traen columna "unidad": el control real es el orden
    # de magnitud del total (2023 = $40 billones, 2026 = $152 billones).
    from scripts.actualizar_presupuesto import control_magnitud
    assert control_magnitud(40.41e12) is None and control_magnitud(152.47e12) is None
    assert "unidad" in control_magnitud(152.47e9)     # publicado en miles → 1000 veces abajo
    assert "unidad" in control_magnitud(152.47e15)    # 1000 veces arriba
