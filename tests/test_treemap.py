"""Árbol del presupuesto: armado, totales y endpoint."""
import os
import sys

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import treemap  # noqa: E402
from app.database import models  # noqa: E402
from app.main import app, get_db  # noqa: E402


def test_arma_tres_niveles_ordenados_y_cuadra_el_total():
    a = treemap.armar_arbol([
        ("1", "Chica", "10", "P", "1", "Personal", 10),
        ("2", "Grande", "20", "Q", "1", "Personal", 60),
        ("2", "Grande", "20", "Q", "5", "Transferencias", 30),
        ("2", "Grande", "21", "R", "1", "Personal", 100),
    ])
    assert a["total"] == 200
    assert [j["nombre"] for j in a["jurisdicciones"]] == ["Grande", "Chica"]
    grande = a["jurisdicciones"][0]
    assert grande["monto"] == 190
    assert [p["id"] for p in grande["programas"]] == ["21", "20"]
    assert [i["monto"] for i in grande["programas"][1]["incisos"]] == [60, 30]


def test_un_codigo_con_dos_nombres_usa_el_de_mayor_monto_y_no_duplica():
    a = treemap.armar_arbol([
        ("1", "J", "10", "Nombre viejo", "1", "Personal", 5),
        ("1", "J", "10", "Nombre vigente", "5", "Transferencias", 50),
    ])
    assert len(a["jurisdicciones"][0]["programas"]) == 1
    assert a["jurisdicciones"][0]["programas"][0]["nombre"] == "Nombre vigente"


def test_montos_sin_valor_positivo_se_informan_y_no_se_dibujan():
    a = treemap.armar_arbol([
        ("1", "J", "10", "P", "1", "Personal", 100),
        ("1", "J", "10", "P", "5", "Transferencias", -3),
        ("2", "Vacia", "20", "Q", "1", "Personal", 0),
        ("3", "Nula", "30", "R", "1", "Personal", None),
    ])
    assert a["omitidas"] == 3
    assert a["total"] == 100
    assert [j["nombre"] for j in a["jurisdicciones"]] == ["J"]


def test_nodo_sin_nombre_recibe_su_codigo():
    a = treemap.armar_arbol([("7", None, "9", " ", "1", "", 1)])
    j = a["jurisdicciones"][0]
    assert j["nombre"] == "Jurisdicción 7"
    assert j["programas"][0]["nombre"] == "Programa 9"
    assert j["programas"][0]["incisos"][0]["nombre"] == "Inciso 1"


def _cliente():
    motor = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    models.Base.metadata.create_all(bind=motor)
    Sesion = sessionmaker(bind=motor)
    s = Sesion()
    s.add(models.PresupuestoBase(
        ejercicio=2026, jurisdiccion_id="88", jurisdiccion_desc="Capital Humano",
        programa_id="16", programa_desc="Pensiones", inciso_id="5",
        inciso_desc="Transferencias", monto_vigente=900.0,
    ))
    s.add(models.PresupuestoBase(
        ejercicio=2026, jurisdiccion_id="88", jurisdiccion_desc="Capital Humano",
        programa_id="16", programa_desc="Pensiones", inciso_id="1",
        inciso_desc="Personal", monto_vigente=100.0,
    ))
    s.commit()

    def _db():
        yield s

    app.dependency_overrides[get_db] = _db
    treemap._cache.clear()
    return TestClient(app), s


def test_endpoint_devuelve_el_arbol_y_rechaza_un_anio_sin_datos():
    cliente, sesion = _cliente()
    try:
        r = cliente.get("/api/v1/analisis/treemap?anio=2026")
        assert r.status_code == 200
        d = r.json()
        assert d["anio"] == 2026 and d["disponibles"] == [2026] and d["total"] == 1000
        assert d["jurisdicciones"][0]["programas"][0]["incisos"][0]["nombre"] == "Transferencias"
        assert cliente.get("/api/v1/analisis/treemap").json()["anio"] == 2026
        assert cliente.get("/api/v1/analisis/treemap?anio=1999").status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
        treemap._cache.clear()
        sesion.close()


def test_la_pagina_se_sirve_con_los_textos_en_dos_idiomas():
    r = TestClient(app).get("/treemap")
    assert r.status_code == 200
    assert "Mapa del presupuesto" in r.text and "Budget map" in r.text
