"""Tests del cierre de la auditoría 2026-10: validación de PDFs, reintento de
normas, marca de reclasificación y nota de año en curso. Sin red."""
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


# ── PDFs: un HTML guardado como .pdf no cuenta ──────────────────────────────

def test_es_pdf_y_pdf_valido(tmp_path):
    from app.scrapers.pdf_check import es_pdf, pdf_valido
    assert es_pdf(b"%PDF-1.7\n...")
    assert es_pdf(b"\n\n%PDF-1.4")                      # cabecera dentro del primer KB
    assert not es_pdf(b"     <html><head><!-- Global site tag -->")
    pdf, html, chico = tmp_path / "a.pdf", tmp_path / "b.pdf", tmp_path / "c.pdf"
    pdf.write_bytes(b"%PDF-1.7" + b"x" * 600)
    html.write_bytes(b"<html>" + b"x" * 2600)            # como DA-24-2025 / DA-39-2025
    chico.write_bytes(b"%PDF")
    assert pdf_valido(pdf) and not pdf_valido(html) and not pdf_valido(chico)
    assert not pdf_valido(tmp_path / "no-existe.pdf")


def test_descargas_fallidas_trata_html_como_fallida(tmp_path, monkeypatch):
    from scripts import reintentar_normativas as r
    monkeypatch.setattr(r.daily_sync, "RAW_PDF_DIR", tmp_path)
    (tmp_path / "DA-1-2025.pdf").write_bytes(b"%PDF-1.7" + b"x" * 600)   # legítima
    (tmp_path / "DA-2-2025.pdf").write_bytes(b"<html>" + b"x" * 2600)    # HTML
    normas = [SimpleNamespace(norma_id=f"DA-{i}-2025") for i in (1, 2, 3)]
    consulta = SimpleNamespace(filter=lambda *a: SimpleNamespace(all=lambda: normas))
    db = SimpleNamespace(query=lambda *a: consulta)
    assert [n.norma_id for n in r.descargas_fallidas(db)] == ["DA-2-2025", "DA-3-2025"]


def test_norma_data_usa_link_valido_del_bora():
    from scripts.reintentar_normativas import _como_norma_data
    n = SimpleNamespace(norma_id="DA-24-2025", tipo_norma="DA", numero="24", anio=2025,
                        fecha_publicacion=datetime(2025, 9, 22), titulo="", pdf_url="http://x/norma.htm",
                        tipo_accion=None, pdf_hash=None)
    d = _como_norma_data(n)
    assert d["fecha_boletin"] == "2025-09-22"
    assert d["url_bora"] == "https://www.boletinoficial.gob.ar/seccion/primera/20250922"
    assert d["url_infoleg"] == "http://x/norma.htm"


# ── Ranking: marca de posible reclasificación ───────────────────────────────

class _DB:
    def __init__(self, filas):
        self.filas = filas

    def execute(self, sql, params=None):
        return SimpleNamespace(fetchall=lambda: self.filas)


def _fila(base, comp, prog="1"):
    return SimpleNamespace(jurisdiccion_id="40", jurisdiccion_desc="J", programa_id=prog,
                           programa_desc="P", inciso_id="2", inciso_desc="I",
                           base_vigente=base, base_original=base, comp_vigente=comp)


def test_ranking_marca_caidas_nominales_mayores_al_90(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "_get_ipc_factor", lambda *a: 5.0)
    monkeypatch.setattr(m, "_tc_promedio", lambda a: 100.0)
    monkeypatch.setattr(m.deflactor, "metadata", lambda *a: {})
    r = m.ranking_ajuste(top_n=20, top=20, anio_base=2023, anio_comp=2026,
                         db=_DB([_fila(1000, 3, "a"), _fila(1000, 200, "b"), _fila(1000, 2000, "c")]))
    marcas = {x["programa_id"]: x["revisar_reclasificacion"] for x in r["ranking"]}
    assert marcas == {"a": True, "b": False, "c": False}        # -99,7 % / -80 % / +100 %
    assert "revisar_reclasificacion" in r["advertencia"]


# ── Nota de año en curso ────────────────────────────────────────────────────

def test_nota_anio_en_curso(monkeypatch):
    from app.core import deflactor as d
    monkeypatch.setattr(d, "_cargar", lambda: {
        "niveles": {"2023-06": 1.0, f"{date.today().year}-03": 5.0, "2024-06": 3.0},
        "tc_por_anio": {}, "tc_actual": None, "ultimo_mes_ipc": None,
        "fuente_ipc": "x", "fuente_tc": "x"})
    actual = d.metadata(2023, date.today().year)
    assert actual["anio_comp_en_curso"] is True
    assert "a la fecha" in actual["nota_anio_en_curso"] and "1 meses" in actual["nota_anio_en_curso"]
    cerrado = d.metadata(2023, 2024)
    assert cerrado["anio_comp_en_curso"] is False and cerrado["nota_anio_en_curso"] is None
