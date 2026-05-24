# scripts/social/router_social.py
"""
Router Social - KPIs gasto social 2023 vs 2026
Endpoints:
  GET /api/social/kpis          -> todos los KPIs (formato compatible con main.html)
  GET /api/social/kpis/{sector} -> KPI de un sector especifico
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime

IPC_FACTOR  = 10.53
TC_USD_2023 = 187.0
TC_USD_2026 = 1395.0

SECTORES_SOCIAL = {
    "jubilaciones": {
        "titulo":    "Jubilaciones y Pensiones",
        "subtitulo": "Prestaciones previsionales ANSES 2023 → 2026",
        "icon":      "👴",
        "color":     "#3d1e6e",
        "jur_2023":  ["75"],
        "prg_2023":  ["16","17","21","30","31"],
        "jur_2026":  ["88"],
        "prg_2026":  ["16","17","21","30","31"],
        "fuente":    "Presupuesto Abierto · Ministerio de Economía",
        "alertas":   ["Movilidad jubilatoria por decreto desde 2024", "Fusión en jur 88 Capital Humano 2026"],
    },
    "sueldos": {
        "titulo":    "Sueldos Estatales (Masa Salarial)",
        "subtitulo": "Inciso 1 Personal — todas las jurisdicciones 2023 → 2026",
        "icon":      "👷",
        "color":     "#1a5276",
        "jur_2023":  ["1","5","10","20","25","30","35","40","41","45","50","80","88","89","90","91"],
        "prg_2023":  None,
        "inciso":    "1",
        "jur_2026":  ["1","5","10","20","25","30","35","40","41","45","50","80","88","89","90","91"],
        "prg_2026":  None,
        "fuente":    "Presupuesto Abierto · Inciso 1 Personal",
        "alertas":   ["Incluye personal civil y fuerzas de seguridad", "No incluye empresas públicas"],
    },
    "obra_publica": {
        "titulo":    "Obra Pública / Inversión Real",
        "subtitulo": "Infraestructura y obras de capital 2023 → 2026",
        "icon":      "🏗️",
        "color":     "#1a3a6e",
        "jur_2023":  ["64","57","65"],
        "prg_2023":  None,
        "jur_2026":  ["50"],
        "prg_2026":  ["62","63","48","51","82","54","16","37","69","57","5","15","52"],
        "fuente":    "Presupuesto Abierto · Ministerio de Infraestructura",
        "alertas":   ["Reorganización ministerial 2024: jur 64/57/65 → jur 50", "Recorte histórico en inversión pública"],
    },
    "empleo_publico": {
        "titulo":    "Empleo Público (estimado por masa salarial)",
        "subtitulo": "Inciso 1 Personal como proxy de dotación 2023 → 2026",
        "icon":      "🏛️",
        "color":     "#7d6608",
        "jur_2023":  ["1","5","10","20","25","30","35","40","41","45","50","80","88","89","90","91"],
        "prg_2023":  None,
        "inciso":    "1",
        "jur_2026":  ["1","5","10","20","25","30","35","40","41","45","50","80","88","89","90","91"],
        "prg_2026":  None,
        "fuente":    "Presupuesto Abierto · Inciso 1 Personal (proxy dotación)",
        "alertas":   ["Dato estimado: masa salarial no refleja cantidad exacta de agentes", "Reducción real implica ajuste salarial y/o bajas de personal"],
    },
    "salud": {
        "titulo":    "Ministerio de Salud",
        "subtitulo": "Presupuesto total Salud 2023 → 2026",
        "icon":      "🏥",
        "color":     "#145a2a",
        "jur_2023":  ["80"],
        "prg_2023":  None,
        "excluir_2026": ["23","36","69","70"],
        "jur_2026":  ["80"],
        "prg_2026":  None,
        "fuente":    "Presupuesto Abierto · Ministerio de Salud jur 80",
        "alertas":   ["Se excluyen transferencias especiales prg 23/36/69/70"],
    },
}


def _sumar(db: Session, jurs: list, ejercicio: int,
           programas: list = None, excluir_prg: list = None,
           inciso: str = None) -> float:
    campo = "monto_original" if ejercicio == 2023 else "monto_vigente"
    jur_in = ", ".join(f"'{j}'" for j in jurs)
    clauses = [f"ejercicio = {ejercicio}", f"jurisdiccion_id IN ({jur_in})"]
    if programas:
        prg_in = ", ".join(f"'{p}'" for p in programas)
        clauses.append(f"programa_id IN ({prg_in})")
    if excluir_prg:
        exc_in = ", ".join(f"'{p}'" for p in excluir_prg)
        clauses.append(f"programa_id NOT IN ({exc_in})")
    if inciso:
        clauses.append(f"inciso_id = '{inciso}'")
    sql = text(f"SELECT COALESCE(SUM({campo}), 0) FROM presupuesto_base WHERE {' AND '.join(clauses)}")
    return float(db.execute(sql).scalar() or 0)


def _build_kpi(db: Session, clave: str, cfg: dict) -> dict:
    m2023 = _sumar(db, cfg["jur_2023"], 2023,
                   programas=cfg.get("prg_2023"),
                   inciso=cfg.get("inciso"))
    m2026 = _sumar(db, cfg["jur_2026"], 2026,
                   programas=cfg.get("prg_2026"),
                   excluir_prg=cfg.get("excluir_2026"),
                   inciso=cfg.get("inciso"))

    var_nominal  = (m2026 / m2023 - 1) * 100 if m2023 > 0 and m2026 > 0 else None
    var_real_ipc = (m2026 / IPC_FACTOR / m2023 - 1) * 100 if m2023 > 0 and m2026 > 0 else None
    usd_2023 = m2023 / TC_USD_2023 if m2023 > 0 else None
    usd_2026 = m2026 / TC_USD_2026 if m2026 > 0 else None
    var_usd  = (usd_2026 / usd_2023 - 1) * 100 if usd_2023 and usd_2026 else None

    # Formato compatible con main.html loadSocial()
    valor_base   = round(m2023 / 1e9, 1)   # en miles de millones
    valor_actual = round(m2026 / 1e9, 1) if m2026 else 0
    var_abs      = round(valor_actual - valor_base, 1)
    var_pct      = round(var_nominal, 1) if var_nominal is not None else 0

    return {
        # Campos para main.html
        "titulo":        cfg["titulo"],
        "subtitulo":     cfg["subtitulo"],
        "icon":          cfg["icon"],
        "valor_base":    valor_base,
        "valor_actual":  valor_actual,
        "tasa_actual":   round(var_real_ipc, 1) if var_real_ipc is not None else None,
        "var_absoluta":  var_abs,
        "var_pct":       var_pct,
        "alertas":       cfg.get("alertas", []),
        "fuente":        cfg.get("fuente", ""),
        # Campos extendidos
        "sector":            clave,
        "color":             cfg["color"],
        "monto_2023_mm":     round(m2023 / 1e6, 1),
        "monto_2026_mm":     round(m2026 / 1e6, 1) if m2026 else None,
        "var_nominal_pct":   round(var_nominal,  1) if var_nominal  is not None else None,
        "var_real_ipc_pct":  round(var_real_ipc, 1) if var_real_ipc is not None else None,
        "monto_2023_usd_mm": round(usd_2023 / 1e6, 1) if usd_2023 else None,
        "monto_2026_usd_mm": round(usd_2026 / 1e6, 1) if usd_2026 else None,
        "var_usd_pct":       round(var_usd, 1) if var_usd is not None else None,
        "ipc_factor":        IPC_FACTOR,
        "tc_usd_2023":       TC_USD_2023,
        "tc_usd_2026":       TC_USD_2026,
    }


router = APIRouter(prefix="/api/social", tags=["Social"])


def get_db_dependency():
    from app.database.session import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/kpis")
async def social_kpis(db: Session = Depends(get_db_dependency)):
    kpis = [_build_kpi(db, k, v) for k, v in SECTORES_SOCIAL.items()]
    return {
        "generado_en":        datetime.utcnow().isoformat(),
        "ultima_actualizacion": datetime.utcnow().isoformat(),
        "comparativa":        "2023 vs 2026",
        "ipc_acumulado":      IPC_FACTOR,
        "tc_usd_2023":        TC_USD_2023,
        "tc_usd_2026":        TC_USD_2026,
        "kpis":               kpis,
    }


@router.get("/kpis/{sector}")
async def social_kpi_sector(sector: str, db: Session = Depends(get_db_dependency)):
    if sector not in SECTORES_SOCIAL:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"Sector '{sector}' no reconocido. Opciones: {list(SECTORES_SOCIAL.keys())}"
        )
    kpi = _build_kpi(db, sector, SECTORES_SOCIAL[sector])
    return {"generado_en": datetime.utcnow().isoformat(), **kpi}