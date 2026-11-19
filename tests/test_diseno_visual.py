"""Diseno visual (T-F1-13): hoja de estilos propia, sin recursos externos, paginas de error en
espanol, precio en la lista y filtro por categoria."""
import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.views import serve
from django.db import connection
from django.template.loader import get_template
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from inventario import servicios
from inventario.models import Categoria, Producto

pytestmark = pytest.mark.django_db
D = Decimal
RAIZ = Path(__file__).resolve().parent.parent
CSS = RAIZ / "cuentas" / "static" / "css" / "sistema.css"
CLAVE = "Zq7-mPx2vL-prueba"


def usuario(nombre, grupo=None):
    u = get_user_model().objects.create_user(nombre, password=CLAVE)
    if grupo:
        u.groups.add(Group.objects.get(name=grupo))
    return u


@pytest.fixture
def adm(client):
    client.force_login(usuario("adm", "Administrador"))
    return client


def html(r):
    return r.content.decode()


# ------------------------------------------------------------------ hoja de estilos
def test_base_enlaza_sistema_css_con_static_y_sin_estilo_en_linea():
    fuente = (RAIZ / "cuentas" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "{% static 'css/sistema.css' %}" in fuente
    assert "<style" not in fuente


def test_ninguna_plantilla_tiene_estilo_en_linea_ni_urls_externas():
    plantillas = list(RAIZ.glob("cuentas/templates/**/*.html")) + list(RAIZ.glob("inventario/templates/**/*.html"))
    assert len(plantillas) >= 15
    for p in plantillas:
        t = p.read_text(encoding="utf-8")
        assert "<style" not in t, p
        assert not re.search(r"https?://", t), p
        assert "<script" not in t and "onchange=" not in t and "onclick=" not in t, p


def test_la_pagina_renderizada_enlaza_el_css_local(adm):
    t = html(adm.get("/"))
    assert 'href="/static/css/sistema.css"' in t and "<style" not in t and "http://" not in t and "https://" not in t


def test_el_css_existe_se_encuentra_y_se_sirve_con_200():
    assert finders.find("css/sistema.css")
    r = serve(RequestFactory().get("/static/css/sistema.css"), "css/sistema.css", insecure=True)
    assert r.status_code == 200 and "text/css" in r["Content-Type"]
    assert b"--accent" in b"".join(r.streaming_content)


def test_el_css_no_usa_recursos_de_internet():
    css = CSS.read_text(encoding="utf-8")
    assert not re.search(r"https?:|@import|//fonts|url\(", css)


def _variables(bloque):
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", bloque))


def _luminancia(h):
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contraste(a, b):
    la, lb = sorted([_luminancia(a), _luminancia(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


PARES = [("fg", "bg"), ("fg", "panel"), ("muted", "bg"), ("muted", "panel"), ("accent", "panel"),
         ("accent-fg", "accent"), ("ok", "ok-soft"), ("warn", "warn-soft"), ("bad", "bad-soft"),
         ("accent", "accent-soft"), ("bad", "panel"), ("accent", "bg")]


@pytest.mark.parametrize("tema", ["claro", "oscuro"])
def test_contraste_de_la_paleta_cumple_wcag_aa(tema):
    css = CSS.read_text(encoding="utf-8")
    claro = _variables(css.split(":root {", 1)[1].split("}", 1)[0])
    if tema == "claro":
        v = claro
    else:
        oscuro = _variables(css.split("prefers-color-scheme: dark", 1)[1].split("}", 1)[0])
        v = {**claro, **oscuro}
    for a, b in PARES:
        assert _contraste(v[a], v[b]) >= 4.5, (tema, a, b, _contraste(v[a], v[b]))


def test_el_css_tiene_foco_visible_responsive_y_tablas_con_scroll_propio():
    css = CSS.read_text(encoding="utf-8")
    assert ":focus-visible" in css and "@media (max-width: 640px)" in css
    assert re.search(r"\.tabla-scroll\s*\{[^}]*overflow-x:\s*auto", css)
    assert "position: sticky" in css and "nth-child(even)" in css
    for comp in (".insignia-bajo", ".insignia-error", ".insignia-aviso", ".insignia-nueva", ".insignia-existente",
                 ".insignia-reversa", ".mensajes .success", ".mensajes .error", ".mensajes .warning", ".boton.peligro",
                 ".boton.secundario", ".paginas", ".buscador", ".acceso", ".tarjeta", ".errorlist"):
        assert comp in css, comp


# ------------------------------------------------------------------ paginas de error en espanol
def test_pagina_403_propia_en_espanol(client):
    client.force_login(usuario("sin"))
    r = client.get(reverse("producto_lista"))
    t = html(r)
    assert r.status_code == 403 and "No tienes permiso para ver esto" in t and "sistema.css" in t
    assert 'lang="es"' in t


def test_pagina_404_propia_en_espanol(adm):
    r = adm.get("/esto-no-existe/")
    t = html(r)
    assert r.status_code == 404 and "Esta página no existe" in t and "sistema.css" in t
    r = adm.get(reverse("producto_detalle", args=[99999]))
    assert r.status_code == 404 and "Esta página no existe" in html(r)


def test_pagina_500_existe_en_espanol_y_no_depende_del_resto():
    t = get_template("500.html").render({})
    assert "Algo salió mal" in t and "sistema.css" in t and "{%" not in t


# ------------------------------------------------------------------ inicio y menu
def test_inicio_tiene_accesos_grandes_segun_permisos(adm, client):
    t = html(adm.get("/"))
    assert t.count('class="acceso"') == 4 and "Importar desde Eleventa" in t
    c = Client()
    c.force_login(usuario("op", "Operador"))
    t = html(c.get("/"))
    assert t.count('class="acceso"') == 3 and "Importar desde Eleventa" not in t
    c2 = Client()
    c2.force_login(usuario("sin"))
    t = html(c2.get("/"))
    assert 'class="acceso"' not in t and "aún no tiene un perfil" in t


def test_el_menu_marca_la_pagina_actual(adm):
    t = html(adm.get(reverse("producto_lista")))
    assert re.search(r'href="/productos/" aria-current="page">Productos', t)
    assert 'aria-current="page">Proveedores' not in t
    assert 'aria-current="page">Categorías' in html(adm.get(reverse("categoria_lista")))


# ------------------------------------------------------------------ precio en la lista
def test_lista_muestra_precio_venta_en_formato_es_cl_despues_del_stock(adm):
    adm_u = get_user_model().objects.get(username="adm")
    p = Producto.objects.create(sku="P1", nombre="Con precio", precio_venta=D("5990"), precio_mayoreo=D("5000"),
                                costo=D("3500"))
    servicios.registrar_entrada(p, 3, adm_u)
    t = html(adm.get(reverse("producto_lista")))
    assert "Precio venta" in t and "5.990,00" in t
    assert t.index(">Stock<") < t.index(">Precio venta<") < t.index(">Stock mínimo<")
    ficha = html(adm.get(reverse("producto_detalle", args=[p.pk])))
    assert "3.500,00" in ficha and "5.990,00" in ficha and "5.000,00" in ficha and "Precios" in ficha


def test_el_precio_no_agrega_consultas(adm):
    def consultas():
        with CaptureQueriesContext(connection) as q:
            assert adm.get(reverse("producto_lista")).status_code == 200
        return len(q)

    Producto.objects.create(sku="A0", nombre="uno", precio_venta=D("100"))
    base = consultas()
    for i in range(25):
        Producto.objects.create(sku=f"A{i + 1}", nombre=f"n{i}", precio_venta=D(1000 + i))
    assert consultas() == base


# ------------------------------------------------------------------ filtro por categoria (ampliacion)
@pytest.fixture
def surtido():
    a, b = Categoria.objects.create(nombre="Aceites"), Categoria.objects.create(nombre="Filtros")
    Producto.objects.create(sku="AC1", nombre="Aceite uno", categoria=a)
    Producto.objects.create(sku="AC2", nombre="Aceite dos", categoria=a)
    Producto.objects.create(sku="FI1", nombre="Filtro de aire", categoria=b)
    Producto.objects.create(sku="SC1", nombre="Aceite sin categoria")
    return a, b


def skus(r):
    return sorted(p.sku for p in r.context["productos"])


def test_filtro_por_categoria_solo(adm, surtido):
    a, b = surtido
    url = reverse("producto_lista")
    assert skus(adm.get(url, {"categoria": a.pk})) == ["AC1", "AC2"]
    assert skus(adm.get(url, {"categoria": b.pk})) == ["FI1"]
    assert len(adm.get(url).context["productos"]) == 4


def test_filtro_por_categoria_combinado_con_busqueda(adm, surtido):
    a, _ = surtido
    r = adm.get(reverse("producto_lista"), {"categoria": a.pk, "q": "uno"})
    assert skus(r) == ["AC1"]
    r = adm.get(reverse("producto_lista"), {"categoria": a.pk, "q": "aceite"})
    assert skus(r) == ["AC1", "AC2"]                    # «Aceite sin categoria» queda fuera
    assert skus(adm.get(reverse("producto_lista"), {"q": "aceite"})) == ["AC1", "AC2", "SC1"]


def test_filtro_sin_categoria(adm, surtido):
    r = adm.get(reverse("producto_lista"), {"categoria": "ninguna"})
    assert skus(r) == ["SC1"]
    assert 'value="ninguna" selected' in html(r)


def test_paginacion_conserva_busqueda_y_categoria(adm):
    c = Categoria.objects.create(nombre="Grande")
    Producto.objects.bulk_create([Producto(sku=f"G{i:03d}", nombre=f"filtro {i:03d}", categoria=c) for i in range(70)])
    Producto.objects.bulk_create([Producto(sku=f"H{i:03d}", nombre=f"filtro otro {i}") for i in range(5)])
    r = adm.get(reverse("producto_lista"), {"categoria": c.pk, "q": "filtro"})
    assert len(r.context["productos"]) == 50 and r.context["page_obj"].paginator.count == 70
    t = html(r)
    assert f"?q=filtro&amp;categoria={c.pk}&amp;page=2" in t
    r2 = adm.get(reverse("producto_lista"), {"categoria": c.pk, "q": "filtro", "page": 2})
    assert len(r2.context["productos"]) == 20 and f"categoria={c.pk}&amp;page=1" in html(r2)
    assert all(p.categoria_id == c.pk for p in r2.context["productos"])


@pytest.mark.parametrize("valor", ["abc", "9999", "-1", "0", "1.5", "ninguna ", "x" * 300, "%00"])
def test_categoria_invalida_o_inexistente_avisa_y_no_filtra_sin_500(adm, surtido, valor):
    r = adm.get(reverse("producto_lista"), {"categoria": valor})
    if valor.strip() == "ninguna":
        assert r.status_code == 200 and skus(r) == ["SC1"]
        return
    assert r.status_code == 200 and len(r.context["productos"]) == 4
    assert "Categoría no válida" in html(r)


def test_selector_de_categoria_en_la_lista(adm, surtido):
    a, b = surtido
    t = html(adm.get(reverse("producto_lista"), {"categoria": b.pk}))
    assert 'name="categoria"' in t and "Todas" in t and "Sin categoría" in t
    assert f'<option value="{b.pk}" selected>Filtros</option>' in t and f'<option value="{a.pk}">Aceites</option>' in t


def test_lista_de_categorias_con_conteo_y_enlaces(adm, surtido):
    a, b = surtido
    Categoria.objects.create(nombre="Vacia")
    r = adm.get(reverse("categoria_lista"))
    conteo = {c.nombre: c.n_productos for c in r.context["categorias"]}
    assert conteo == {"Aceites": 2, "Filtros": 1, "Vacia": 0}
    t = html(r)
    assert f'<a href="/productos/?categoria={a.pk}">Aceites</a>' in t and "Editar" in t
    assert "Productos</th>" in t


def test_consultas_acotadas_en_lista_de_categorias_y_de_productos(adm):
    def consultas(nombre, datos=None):
        with CaptureQueriesContext(connection) as q:
            assert adm.get(reverse(nombre), datos or {}).status_code == 200
        return len(q)

    c0 = Categoria.objects.create(nombre="C0")
    Producto.objects.create(sku="X0", nombre="x", categoria=c0)
    cat1, prod1 = consultas("categoria_lista"), consultas("producto_lista", {"categoria": c0.pk})
    for i in range(1, 15):
        c = Categoria.objects.create(nombre=f"C{i}")
        Producto.objects.bulk_create([Producto(sku=f"Y{i}-{j}", nombre="y", categoria=c) for j in range(3)])
    assert consultas("categoria_lista") == cat1
    assert consultas("producto_lista", {"categoria": c0.pk}) == prod1


def test_enlace_de_categoria_en_la_ficha(adm, surtido):
    a, _ = surtido
    p = Producto.objects.get(sku="AC1")
    assert f'href="/productos/?categoria={a.pk}">Aceites</a>' in html(adm.get(reverse("producto_detalle", args=[p.pk])))


def test_permisos_del_filtro_no_cambian(client, surtido):
    a, _ = surtido
    client.force_login(usuario("sin"))
    assert client.get(reverse("producto_lista"), {"categoria": a.pk}).status_code == 403
    assert client.get(reverse("categoria_lista")).status_code == 403
    c = Client()
    c.force_login(usuario("op", "Operador"))
    assert c.get(reverse("producto_lista"), {"categoria": a.pk}).status_code == 200
    t = html(c.get(reverse("categoria_lista")))
    assert "Editar" in t and f"/productos/?categoria={a.pk}" in t      # el operador puede ver y editar
