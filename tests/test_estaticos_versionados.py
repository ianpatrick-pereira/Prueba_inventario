"""Estaticos propios con version e iconos a prueba de CSS que no carga (T-F1-18)."""
import hashlib
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.template import engines

from cuentas.templatetags.estaticos import url_versionada, version_de

pytestmark = pytest.mark.django_db
RAIZ = Path(__file__).resolve().parent.parent
CSS = RAIZ / "cuentas" / "static" / "css" / "sistema.css"
JS = RAIZ / "cuentas" / "static" / "js" / "sistema.js"


@pytest.fixture
def adm(client):
    u = get_user_model().objects.create_user("adm", password="Zq7-mPx2vL-prueba")
    u.groups.add(Group.objects.get(name="Administrador"))
    client.force_login(u)
    return client


def corto(ruta):
    return hashlib.sha256(ruta.read_bytes()).hexdigest()[:10]


def test_la_url_del_css_y_del_js_lleva_la_version_del_contenido(adm):
    t = adm.get("/").content.decode()
    assert f'href="/static/css/sistema.css?v={corto(CSS)}"' in t
    assert f'src="/static/js/sistema.js?v={corto(JS)}"' in t


def test_la_version_cambia_si_cambia_el_contenido_y_es_estable_si_no(tmp_path):
    f = tmp_path / "a.css"
    f.write_text("body { color: red }", encoding="utf-8")
    v1 = version_de(str(f))
    assert re.fullmatch(r"[0-9a-f]{10}", v1) and version_de(str(f)) == v1          # estable
    f.write_text("body { color: blue }", encoding="utf-8")
    v2 = version_de(str(f))
    assert v2 != v1 and v2 == hashlib.sha256(b"body { color: blue }").hexdigest()[:10]
    f.write_text("body { color: bluee }", encoding="utf-8")                        # distinto tamano
    assert version_de(str(f)) not in (v1, v2)
    assert version_de(str(tmp_path / "no-existe.css")) == ""


def test_misma_longitud_y_contenido_distinto_tambien_cambia_la_version(tmp_path):
    import os

    f = tmp_path / "b.js"
    f.write_text("var a = 1;", encoding="utf-8")
    v1 = version_de(str(f))
    f.write_text("var a = 2;", encoding="utf-8")
    os.utime(f, ns=(1, 2))                  # fecha distinta: se vuelve a calcular el hash
    assert version_de(str(f)) != v1


def test_url_versionada_y_archivo_inexistente_no_falla():
    assert url_versionada("css/sistema.css") == f"/static/css/sistema.css?v={corto(CSS)}"
    assert url_versionada("css/no-existe.css") == "/static/css/no-existe.css"


def test_la_etiqueta_funciona_en_una_plantilla_sin_contexto():
    t = engines["django"].from_string("{% load estaticos %}{% static_v 'js/sistema.js' %}").render({})
    assert t == f"/static/js/sistema.js?v={corto(JS)}"


def test_la_pagina_500_tambien_usa_el_css_versionado():
    from django.template.loader import get_template

    assert f"/static/css/sistema.css?v={corto(CSS)}" in get_template("500.html").render({})


def test_el_servidor_sigue_sirviendo_el_css_con_el_parametro_de_version(adm):
    from django.contrib.staticfiles.views import serve
    from django.test import RequestFactory

    r = serve(RequestFactory().get(f"/static/css/sistema.css?v={corto(CSS)}"), "css/sistema.css", insecure=True)
    assert r.status_code == 200


# ------------------------------------------------------------------ iconos a prueba de fallos
def plantillas():
    return list(RAIZ.glob("cuentas/templates/**/*.html")) + list(RAIZ.glob("inventario/templates/**/*.html"))


def test_todos_los_svg_de_las_plantillas_tienen_tamano_y_color_propios():
    svgs = [(p, m.group(0)) for p in plantillas() for m in re.finditer(r"<svg\b[^>]*>", p.read_text(encoding="utf-8"))]
    assert len(svgs) >= 4
    for p, tag in svgs:
        assert re.search(r'\bwidth="\d+"', tag) and re.search(r'\bheight="\d+"', tag), (p.name, tag)
        assert 'fill="none"' in tag and 'stroke="currentColor"' in tag, (p.name, tag)   # color del texto sin CSS
        assert 'aria-hidden="true"' in tag and "viewBox=" in tag, (p.name, tag)


def test_el_menu_renderizado_tiene_los_cuatro_iconos_de_20_px(adm):
    t = adm.get("/").content.decode()
    assert len(re.findall(r'<svg class="icono" width="20" height="20"', t)) == 4


def test_no_hay_imagenes_sin_tamano_en_las_plantillas():
    for p in plantillas():
        for tag in re.findall(r"<img\b[^>]*>", p.read_text(encoding="utf-8")):
            assert re.search(r'\bwidth="\d+"', tag) and re.search(r'\bheight="\d+"', tag), (p.name, tag)


def test_el_css_conserva_su_regla_de_icono_como_refuerzo():
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"\.lateral \.icono\s*\{[^}]*width:\s*20px[^}]*height:\s*20px", css)
