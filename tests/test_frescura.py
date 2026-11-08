"""GET /api/frescura con los datos del repo."""
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402


def test_frescura():
    with TestClient(app) as c:
        d = c.get("/api/frescura").json()
        assert d["formato"] == "frescura/1" and d["sitio"] == "ajuste"
        assert {x["id"] for x in d["datos"]} == {"presupuesto", "ipc", "macro", "das", "social"}
        assert all(x["dato"] and x["fuente"] for x in d["datos"])
        assert c.get("/static/frescura.js").status_code == 200
        assert "frescura-ficha" in c.get("/").text
