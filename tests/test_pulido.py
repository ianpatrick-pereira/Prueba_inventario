"""Pulido profesional de la interfaz (T-F1-17): panel del dia, barra de stock, margen, chips,
filtro bajo minimo, formularios por secciones, estados vacios y sistema.js (D-58)."""
import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.staticfiles.views import serve
from django.db import connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from inventario import servicios
from inventario.calculos import calcular_barra_stock, calcular_margen
from inventario.models import Categoria, Movimiento, Producto

pytestmark = pytest.mark.django_db
D = Decimal
CLAVE = "Zq7-mPx2vL-prueba"
RAIZ = Path(__file__).resolve().parent.parent
CSS = (RAIZ / "cuentas" / "static" / "css" / "sistema.css").read_text(encoding="utf-8")


def usuario(nombre, grupo=None, permisos=()):
    u = get_user_model().objects.create_user(nombre, password=CLAVE)
    if grupo:
        u.groups.add(Group.objects.get(name=grupo))
    for c in permisos:
        u.user_permissions.add(Permission.objects.get(codename=c))
    return u


@pytest.fixture
def admin_user():
    return usuario("adm", "Administrador")


@pytest.fixture
def adm(client, admin_user):
    client.force_login(admin_user)
    return client


def html(r):
    return r.content.decode()


def cliente(u):
    c = Client()
    c.force_login(u)
    return c


def prod(sku="P1", **kw):
    datos = dict(nombre=f"Producto {sku}", costo=D("100"), precio_venta=D("200"), stock_min=D("0"), stock_max=D("0"))
    datos.update(kw)
    if "stock_max" not in kw:
        datos["stock_max"] = datos["stock_min"]          # el maximo no puede quedar bajo el minimo
    return Producto.objects.create(sku=sku, **datos)


def mov(p, u, cantidad, tipo, fecha=None):
    return Movimiento.objects.create(producto=p, usuario=u, tipo=tipo, cantidad=D(cantidad),
                                     fecha=fecha or timezone.now(), motivo="m" if tipo == "ajuste" else "")


# ------------------------------------------------------------------ panel del dia
def test_panel_con_conteos_correctos(adm, admin_user):
    a = prod("A", stock_min=D("5"), costo=D("100"))
    b = prod("B", stock_min=D("5"), costo=D("200"))
    c = prod("C", stock_min=D("0"), costo=D("300"))
    servicios.registrar_entrada(a, 2, admin_user)       # bajo minimo (2 <= 5)
    servicios.registrar_entrada(b, 10, admin_user)      # sobre el minimo
    servicios.registrar_entrada(c, 4, admin_user)       # sin minimo definido: no cuenta
    servicios.registrar_salida(b, 3, admin_user)
    r = adm.get("/")
    assert r.status_code == 200
    ctx = r.context
    assert ctx["bajo_minimo"] == 1
    assert (ctx["entradas_hoy"], ctx["unidades_entradas_hoy"]) == (3, D("16"))
    assert (ctx["salidas_hoy"], ctx["unidades_salidas_hoy"]) == (1, D("3"))
    assert ctx["valor_inventario"] == D("2") * 100 + D("7") * 200 + D("4") * 300       # 2.800
    t = html(r)
    assert "$2.800" in t and "Productos bajo mínimo" in t and "?bajo_minimo=1" in t
    assert "Entradas de hoy" in t and "Salidas de hoy" in t and "Últimos movimientos" in t


def test_hoy_se_cuenta_en_hora_de_chile(adm, admin_user):
    p = prod("H")
    ayer_tarde = timezone.make_aware(dt.datetime.combine(timezone.localdate() - dt.timedelta(days=1), dt.time(23, 59, 59)))
    antes_de_ayer = ayer_tarde - dt.timedelta(days=1)
    mov(p, admin_user, "7", "entrada", ayer_tarde)           # ayer 23:59:59 hora de Chile: NO es de hoy
    mov(p, admin_user, "5", "entrada", antes_de_ayer)
    mov(p, admin_user, "1", "entrada")                       # ahora: SI
    inicio_hoy = timezone.make_aware(dt.datetime.combine(timezone.localdate(), dt.time(0, 0, 1)))
    if inicio_hoy < timezone.now():
        mov(p, admin_user, "2", "entrada", inicio_hoy)       # hoy 00:00:01 hora de Chile: SI
        esperado = (2, D("3"))
    else:
        esperado = (1, D("1"))
    r = adm.get("/")
    assert (r.context["entradas_hoy"], r.context["unidades_entradas_hoy"]) == esperado
    from django.conf import settings

    assert settings.TIME_ZONE == "America/Santiago"


def test_valor_del_inventario_sin_costo_muestra_raya_y_nota(adm, admin_user):
    servicios.registrar_entrada(prod("A", costo=D("100")), 10, admin_user)
    servicios.registrar_entrada(prod("B", costo=D("0")), 3, admin_user)
    servicios.registrar_entrada(prod("C", costo=D("0")), 2, admin_user)
    prod("D", costo=D("0"))                                    # sin stock: no cuenta como «sin costo»
    r = adm.get("/")
    assert r.context["valor_inventario"] is None and r.context["sin_costo"] == 2
    t = html(r)
    assert "—" in t and "2 productos sin costo" in t and "$1.000" not in t


def test_nota_singular_y_sin_aviso_cuando_todo_tiene_costo(adm, admin_user):
    servicios.registrar_entrada(prod("B", costo=D("0")), 3, admin_user)
    assert "1 producto sin costo" in html(adm.get("/"))
    q = prod("Z", costo=D("50"))
    servicios.registrar_entrada(q, 2, admin_user)
    Producto.objects.filter(sku="B").update(costo=D("10"))
    t = html(adm.get("/"))
    assert "sin costo" not in t and "$130" in t


def test_ultimos_diez_movimientos_con_enlace_al_kardex(adm, admin_user):
    p = prod("U")
    for i in range(12):
        mov(p, admin_user, str(i + 1), "entrada", timezone.now() - dt.timedelta(minutes=12 - i))
    r = adm.get("/")
    ultimos = r.context["ultimos"]
    assert len(ultimos) == 10 and ultimos[0].cantidad == D("12.00") and ultimos[-1].cantidad == D("3.00")
    assert f'href="/productos/{p.pk}/kardex/"' in html(r)


def test_consultas_del_panel_acotadas(adm, admin_user):
    def consultas():
        with CaptureQueriesContext(connection) as q:
            assert adm.get("/").status_code == 200
        return len(q)

    a = prod("A", stock_min=D("5"))
    servicios.registrar_entrada(a, 1, admin_user)
    base = consultas()
    for i in range(30):
        p = prod(f"X{i}", stock_min=D("2"))
        servicios.registrar_entrada(p, 1 + i % 3, admin_user)
        servicios.registrar_salida(p, 1, admin_user)
    assert consultas() == base


def test_panel_segun_permisos(admin_user):
    p = prod("A")
    servicios.registrar_entrada(p, 4, admin_user)
    t = html(cliente(usuario("op", "Operador")).get("/"))                    # el Operador ve todo el panel
    assert "Valor del inventario" in t and "Entradas de hoy" in t and "Productos bajo mínimo" in t
    solo_prod = html(cliente(usuario("sp", permisos=["view_producto"])).get("/"))
    assert "Valor del inventario" in solo_prod and "Productos bajo mínimo" in solo_prod
    assert "Entradas de hoy" not in solo_prod and "Últimos movimientos" not in solo_prod
    solo_mov = html(cliente(usuario("sm", permisos=["view_movimiento"])).get("/"))
    assert "Entradas de hoy" in solo_mov and "Últimos movimientos" in solo_mov
    assert "Valor del inventario" not in solo_mov and "Productos bajo mínimo" not in solo_mov
    sin = cliente(usuario("sin"))
    r = sin.get("/")
    assert r.status_code == 200 and "aún no tiene un perfil" in html(r) and "Valor del inventario" not in html(r)
    assert r.context["hay_panel"] is False


def test_el_panel_no_escribe_nada(adm, admin_user):
    servicios.registrar_entrada(prod("A"), 3, admin_user)
    n = Movimiento.objects.count()
    adm.get("/")
    assert Movimiento.objects.count() == n


# ------------------------------------------------------------------ barra de stock
@pytest.mark.parametrize("stock,minimo,maximo,color", [
    (3, 6, 15, "rojo"), (6, 6, 15, "rojo"), (0, 0, 10, "rojo"),
    (7, 6, 15, "ambar"), (7.5, 6, 15, "ambar"), (8, 6, 15, "verde"), (15, 6, 15, "verde"), (20, 6, 15, "verde"),
    (4, 1, 10, "ambar"), (6, 1, 10, "verde"),
])
def test_color_de_la_barra_de_stock(stock, minimo, maximo, color):
    assert calcular_barra_stock(stock, minimo, maximo)["color"] == color


def test_barra_posiciones_y_limites():
    b = calcular_barra_stock(3, 6, 15)
    assert b["pct"] == 20.0 and b["marca"] == 40.0
    assert calcular_barra_stock(30, 6, 15)["pct"] == 100.0 and calcular_barra_stock(-5, 6, 15)["pct"] == 0.0
    assert calcular_barra_stock(5, 20, 15)["marca"] == 100.0


@pytest.mark.parametrize("maximo", [0, -1, None])
def test_sin_maximo_util_no_se_dibuja_la_barra(maximo):
    assert calcular_barra_stock(3, 1, maximo) is None


def test_barra_en_la_lista_con_texto_accesible(adm, admin_user):
    a = prod("RJ", stock_min=D("6"), stock_max=D("15"))
    b = prod("AM", stock_min=D("6"), stock_max=D("15"))
    c = prod("VD", stock_min=D("6"), stock_max=D("15"))
    sm = prod("SM", stock_min=D("0"), stock_max=D("0"))
    for p, n in ((a, 3), (b, 7), (c, 12), (sm, 1)):
        servicios.registrar_entrada(p, n, admin_user)
    t = html(adm.get(reverse("producto_lista")))
    assert t.count('class="barra-stock rojo"') == 1 and t.count('class="barra-stock ambar"') == 1
    assert t.count('class="barra-stock verde"') == 1
    assert 'aria-label="3 de 15, mínimo 6"' in t and 'title="3 de 15, mínimo 6"' in t and 'role="img"' in t
    fila_sm = next(f for f in t.split("<tr") if ">SM<" in f)
    assert "barra-stock" not in fila_sm                       # maximo 0: sin barra


def test_barra_en_la_ficha(adm, admin_user):
    p = prod("F", stock_min=D("6"), stock_max=D("15"))
    servicios.registrar_entrada(p, 9, admin_user)
    t = html(adm.get(reverse("producto_detalle", args=[p.pk])))
    assert 'class="barra-stock verde"' in t and 'aria-label="9 de 15, mínimo 6"' in t
    q = prod("G")
    assert "barra-stock" not in html(adm.get(reverse("producto_detalle", args=[q.pk])))


# ------------------------------------------------------------------ margen
@pytest.mark.parametrize("precio,costo,estado,pct", [
    (7990, 3200, "normal", 60), (200, 100, "normal", 50), (1000, 1000, "normal", 0), (300, 100, "normal", 67),
    (1000, 2000, "bajo_costo", -100), (0, 500, "bajo_costo", None), (500, 0, "sin_costo", None), (0, 0, "sin_costo", None),
])
def test_calculo_del_margen(precio, costo, estado, pct):
    assert calcular_margen(D(precio), D(costo)) == {"estado": estado, "pct": pct}


def test_margen_en_la_ficha(adm):
    normal = prod("N", precio_venta=D("7990"), costo=D("3200"))
    bajo = prod("B", precio_venta=D("1000"), costo=D("2000"))
    sin = prod("S", precio_venta=D("1000"), costo=D("0"))
    t = html(adm.get(reverse("producto_detalle", args=[normal.pk])))
    assert "Margen" in t and "60 %" in t and "bajo costo" not in t and "sin costo" not in t
    t = html(adm.get(reverse("producto_detalle", args=[bajo.pk])))
    assert '<span class="insignia insignia-error">bajo costo</span>' in t and "-100 %" in t
    t = html(adm.get(reverse("producto_detalle", args=[sin.pk])))
    assert '<span class="insignia insignia-aviso">sin costo</span>' in t and " %" not in t.split("Margen")[1].split("</div>")[0]


# ------------------------------------------------------------------ filtro bajo minimo + impresion
@pytest.fixture
def surtido(admin_user):
    c1, c2 = Categoria.objects.create(nombre="Aceites"), Categoria.objects.create(nombre="Filtros")
    datos = [("A1", c1, 2, 5, 15), ("A2", c1, 9, 5, 15), ("F1", c2, 1, 4, 10), ("F2", c2, 4, 4, 10), ("S1", None, 3, 0, 0)]
    for sku, c, st, mn, mx in datos:
        p = prod(sku, categoria=c, stock_min=D(mn), stock_max=D(mx))
        if st:
            servicios.registrar_entrada(p, st, admin_user)
    return c1, c2


def skus(r):
    return sorted(p.sku for p in r.context["productos"])


def test_filtro_bajo_minimo_solo_y_con_categoria(adm, surtido):
    c1, c2 = surtido
    url = reverse("producto_lista")
    assert skus(adm.get(url, {"bajo_minimo": "1"})) == ["A1", "F1", "F2"]          # 4 <= 4 cuenta; sin minimo (S1) no
    assert skus(adm.get(url, {"bajo_minimo": "1", "categoria": c1.pk})) == ["A1"]
    assert skus(adm.get(url, {"bajo_minimo": "1", "categoria": c2.pk})) == ["F1", "F2"]
    assert skus(adm.get(url, {"bajo_minimo": "1", "categoria": "ninguna"})) == []
    assert skus(adm.get(url, {"bajo_minimo": "1", "q": "F1"})) == ["F1"]
    assert len(adm.get(url).context["productos"]) == 5                            # sin el filtro, todos
    assert len(adm.get(url, {"bajo_minimo": "0"}).context["productos"]) == 5


def test_lista_bajo_minimo_titulo_aviso_y_columnas_de_impresion(adm, surtido):
    t = html(adm.get(reverse("producto_lista"), {"bajo_minimo": "1"}))
    assert "<h1>Productos bajo mínimo</h1>" in t and "Cantidad sugerida = máximo − stock" in t
    assert "data-imprimir" in t and 'class="solo-js"' in t
    fila = next(f for f in t.split("<tr") if ">A1<" in f)
    assert 'class="num solo-impresion">15</td>' in fila and 'class="num solo-impresion">13</td>' in fila   # max 15, sugerido 15-2
    fila_f2 = next(f for f in t.split("<tr") if ">F2<" in f)
    assert 'class="num solo-impresion">6</td>' in fila_f2                                                  # 10-4
    assert "Impreso el" in t


def test_sugerida_solo_si_es_mayor_que_cero(adm, admin_user):
    p = prod("Z", stock_min=D("1"), stock_max=D("5"))
    servicios.registrar_entrada(p, 8, admin_user)                     # sobre el maximo: sin sugerencia
    prod("Y", stock_min=D("0"), stock_max=D("0"))
    t = html(adm.get(reverse("producto_lista")))
    for sku in ("Z", "Y"):
        fila = next(f for f in t.split("<tr") if f">{sku}<" in f)
        assert re.search(r'class="num solo-impresion">\s*</td>', fila), sku


def test_boton_imprimir_bajo_minimo_en_la_lista_normal(adm, surtido):
    c1, _ = surtido
    t = html(adm.get(reverse("producto_lista")))
    assert 'href="?bajo_minimo=1"' in t and "Imprimir bajo mínimo" in t
    t = html(adm.get(reverse("producto_lista"), {"categoria": c1.pk}))
    assert f'href="?bajo_minimo=1&amp;categoria={c1.pk}"' in t


def test_la_lista_bajo_minimo_va_en_una_sola_pagina(adm, admin_user):
    for i in range(60):
        p = prod(f"B{i:02d}", stock_min=D("5"), stock_max=D("20"))
        servicios.registrar_entrada(p, 1, admin_user)
    r = adm.get(reverse("producto_lista"), {"bajo_minimo": "1"})
    assert len(r.context["productos"]) == 60 and not r.context["is_paginated"]
    assert len(adm.get(reverse("producto_lista")).context["productos"]) == 50


def test_css_de_impresion_oculta_barra_botones_y_muestra_columnas_extra():
    imp = CSS.split("@media print", 1)[1]
    assert ".lateral" in imp and "button" in imp and ".solo-impresion { display: table-cell" in imp
    assert ".no-imprimir" in imp and "@page" in imp
    assert re.search(r"\.solo-impresion\s*\{\s*display:\s*none", CSS)         # en pantalla no se ven


# ------------------------------------------------------------------ chips de categoria
def test_chips_de_categoria_en_lugar_del_selector(adm, surtido):
    c1, c2 = surtido
    t = html(adm.get(reverse("producto_lista")))
    assert "<select" not in t and 'class="chips"' in t
    assert re.search(r'<a class="chip" href="/productos/" aria-current="true">Todas</a>', t)
    for c in (c1, c2):
        assert f'<a class="chip" href="?categoria={c.pk}">{c.nombre}</a>' in t
    assert '<a class="chip" href="?categoria=ninguna">Sin categoría</a>' in t


def test_los_chips_conservan_la_busqueda_y_el_filtro_bajo_minimo(adm, surtido):
    c1, _ = surtido
    t = html(adm.get(reverse("producto_lista"), {"q": "a", "bajo_minimo": "1", "categoria": c1.pk}))
    assert f'href="?q=a&amp;bajo_minimo=1&amp;categoria={c1.pk}" aria-current="true">Aceites' in t
    assert 'href="?q=a&amp;bajo_minimo=1">Todas' in t
    assert f'<input type="hidden" name="categoria" value="{c1.pk}">' in t and '<input type="hidden" name="bajo_minimo" value="1">' in t


def test_chip_activo_y_valor_invalido(adm, surtido):
    r = adm.get(reverse("producto_lista"), {"categoria": "ninguna"})
    assert 'href="?categoria=ninguna" aria-current="true">Sin categoría' in html(r)
    r = adm.get(reverse("producto_lista"), {"categoria": "zzz"})
    assert r.status_code == 200 and "Categoría no válida" in html(r) and 'href="/productos/" aria-current="true">Todas' in html(r)


def test_los_chips_se_desplazan_en_horizontal_en_celular():
    movil = CSS.split("@media (max-width: 640px)", 1)[1]
    assert re.search(r"\.chips\s*\{[^}]*flex-wrap:\s*nowrap[^}]*overflow-x:\s*auto", movil)


# ------------------------------------------------------------------ sistema.js (D-58)
def test_sistema_js_se_sirve_y_no_depende_de_nada_externo():
    from django.contrib.staticfiles import finders

    assert finders.find("js/sistema.js")
    r = serve(RequestFactory().get("/static/js/sistema.js"), "js/sistema.js", insecure=True)
    cuerpo = b"".join(r.streaming_content).decode()
    assert r.status_code == 200 and "javascript" in r["Content-Type"]
    assert "keydown" in cuerpo and "con-js" in cuerpo and "window.print" in cuerpo
    assert not re.search(r"https?:|fetch\(|XMLHttpRequest|import\s|eval\(|document\.write|innerHTML", cuerpo)


def test_la_pagina_carga_un_solo_script_propio_con_defer(adm):
    t = html(adm.get(reverse("producto_lista")))
    assert re.findall(r"<script[^>]*>", t) == ['<script src="/static/js/sistema.js" defer>']
    assert "onclick=" not in t and "onchange=" not in t


def test_sin_js_nada_depende_de_el(adm):
    """Los elementos que solo tienen sentido con JS arrancan ocultos; el buscador tiene foco por HTML."""
    assert re.search(r"\.pista\s*\{\s*display:\s*none", CSS) and re.search(r"\.solo-js\s*\{\s*display:\s*none", CSS)
    assert ".con-js .pista" in CSS and ".con-js .solo-js" in CSS
    t = html(adm.get(reverse("producto_lista")))
    assert "autofocus" in t and 'type="search"' in t and "Presiona <kbd>/</kbd> para buscar" in t
    assert '<form method="get" class="herramientas" role="search">' in t          # la busqueda funciona con un GET normal


def test_atajo_de_busqueda_ignora_campos_de_texto():
    js = (RAIZ / "cuentas" / "static" / "js" / "sistema.js").read_text(encoding="utf-8")
    assert '"/"' in js and "INPUT" in js and "TEXTAREA" in js and "SELECT" in js and "isContentEditable" in js


# ------------------------------------------------------------------ formularios por secciones
def test_formulario_de_producto_por_secciones_y_sin_flechas(adm):
    t = html(adm.get(reverse("producto_nuevo")))
    assert t.count('<section class="tarjeta seccion">') == 3
    assert [h for h in re.findall(r"<h2>([^<]+)</h2>", t)] == ["Identificación", "Precios", "Stock"]
    assert 'type="number"' not in t and t.count('inputmode="decimal"') == 5
    assert t.count('<span class="prefijo" aria-hidden="true">$</span>') == 3
    assert "data-sufijo-unidad" in t and ">unidad</span>" in t


def test_los_campos_numericos_estan_alineados_sin_spinners_en_css():
    assert re.search(r"input\[inputmode=decimal\]\s*\{[^}]*text-align:\s*right", CSS)
    assert "-webkit-inner-spin-button" in CSS and "appearance: textfield" in CSS


def test_el_sufijo_usa_la_unidad_del_producto(adm):
    p = prod("K", unidad="litros")
    t = html(adm.get(reverse("producto_editar", args=[p.pk])))
    assert t.count('data-sufijo-unidad>litros</span>') == 2


def test_la_ayuda_del_codigo_queda_debajo_del_campo(adm):
    t = html(adm.get(reverse("producto_nuevo")))
    campo_sku = t.split('for="id_sku"')[1].split('<div class="campo')[0]
    assert "No se podrá cambiar después" in campo_sku and campo_sku.index("<input") < campo_sku.index("No se podrá cambiar")


@pytest.mark.parametrize("nombre,secciones", [("entrada", ["Mercadería", "Origen", "Detalle"]),
                                               ("salida", ["Cantidad", "Detalle"]), ("corregir", ["Cantidad", "Detalle"])])
def test_formularios_de_movimiento_por_secciones_sin_flechas(adm, nombre, secciones):
    p = prod("M")
    t = html(adm.get(reverse(nombre, args=[p.pk])))
    assert re.findall(r'<section class="tarjeta seccion">\s*<h2>([^<]+)</h2>', t) == secciones
    assert 'type="number"' not in t and 'inputmode="decimal"' in t
    if nombre == "entrada":
        assert '<span class="prefijo" aria-hidden="true">$</span>' in t


def test_formularios_de_proveedor_y_categoria_con_seccion(adm):
    assert '<section class="tarjeta seccion">' in html(adm.get(reverse("proveedor_nuevo")))
    assert '<section class="tarjeta seccion">' in html(adm.get(reverse("categoria_nueva")))


def test_los_formularios_siguen_validando_y_guardando(adm):
    datos = {"sku": " 0077 ", "nombre": "Con $", "categoria": "", "unidad": "unidad", "costo": "1234.50",
             "precio_venta": "2000", "precio_mayoreo": "1800", "stock_min": "2", "stock_max": "10"}
    r = adm.post(reverse("producto_nuevo"), datos)
    assert r.status_code == 302
    p = Producto.objects.get(sku="0077")
    assert p.costo == D("1234.50") and p.stock_max == D("10")
    datos.update(sku="OTRO", costo="-1")
    r = adm.post(reverse("producto_nuevo"), datos)
    assert r.status_code == 200 and "No puede ser negativo." in html(r)
    assert 'class="campo con-error"' in html(r)


# ------------------------------------------------------------------ estados vacios
def test_estados_vacios_con_boton_de_accion(adm, admin_user):
    assert "+ Agregar el primer proveedor" in html(adm.get(reverse("proveedor_lista")))
    assert "+ Agregar la primera categoría" in html(adm.get(reverse("categoria_lista")))
    assert "+ Agregar el primer producto" in html(adm.get(reverse("producto_lista")))
    p = prod("V")
    assert "Registrar la primera entrada" in html(adm.get(reverse("kardex", args=[p.pk])))


def test_sin_permiso_de_crear_no_aparece_el_boton_vacio():
    c = cliente(usuario("lector", permisos=["view_proveedor", "view_categoria", "view_producto"]))
    for nombre in ("proveedor_lista", "categoria_lista", "producto_lista"):
        t = html(c.get(reverse(nombre)))
        assert "Aún no hay" in t and "+ Agregar" not in t, nombre


def test_el_estado_vacio_de_una_busqueda_no_ofrece_crear(adm):
    prod("A")
    t = html(adm.get(reverse("producto_lista"), {"q": "zzzz"}))
    assert "No se encontraron productos" in t and "+ Agregar el primer producto" not in t


# ------------------------------------------------------------------ orden, espaciado e iconos
def test_escala_de_espaciado_en_multiplos_de_8():
    valores = re.findall(r"--s[1-6]:\s*(\d+)px", CSS)
    assert len(valores) == 6 and all(int(v) % 8 == 0 for v in valores)
    assert re.search(r"--ancho-contenido:\s*1280px", CSS) and "max-width: var(--ancho-contenido)" in CSS


def test_la_barra_lateral_tiene_un_icono_svg_en_linea_por_opcion(adm):
    t = html(adm.get(reverse("producto_lista")))
    lateral = t.split('<aside class="lateral">')[1].split("</aside>")[0]
    assert lateral.count('<svg class="icono"') == 4 and "http" not in lateral and "<img" not in lateral
    for texto in ("Productos", "Proveedores", "Categorías", "Importar desde Eleventa"):
        assert f"<span>{texto}</span>" in lateral


def test_encabezado_de_pagina_consistente_titulo_y_accion_a_la_derecha(adm):
    for nombre in ("producto_lista", "proveedor_lista", "categoria_lista"):
        t = html(adm.get(reverse(nombre)))
        cab = t.split('<div class="cabecera-pagina">')[1].split("</div>\n  ")[0]
        assert "<h1>" in cab and 'class="boton"' in cab, nombre
    assert re.search(r"\.cabecera-pagina\s*\{[^}]*justify-content:\s*space-between", CSS)


def test_buscadores_tablas_y_vacios_comparten_el_mismo_ancho():
    # todos son hijos de ancho completo del contenedor: sin max-width propio ni margenes laterales
    for regla in (".herramientas", ".tabla-scroll", ".vacio", ".tarjeta"):
        cuerpo = re.search(re.escape(regla) + r"\s*\{([^}]*)\}", CSS).group(1)
        assert "max-width" not in cuerpo and not re.search(r"margin:\s*[^;]*auto", cuerpo), regla


# ------------------------------------------------------------------ barra lateral a todo el alto e identificacion 2x2
def test_la_barra_lateral_ocupa_todo_el_alto_aunque_la_pagina_sea_mas_alta(adm):
    lateral = re.search(r"\n\.lateral\s*\{([^}]*)\}", CSS).group(1)
    assert "height" not in lateral and "position" not in lateral           # el fondo se estira con la pagina
    interior = re.search(r"\n\.lateral-interior\s*\{([^}]*)\}", CSS).group(1)
    assert "position: sticky" in interior and "top: 0" in interior and "height: 100vh" in interior
    assert "overflow-y: auto" in interior and "flex-direction: column" in interior   # desplazamiento propio
    t = html(adm.get("/"))
    assert '<aside class="lateral">\n    <div class="lateral-interior">' in t
    interior_html = t.split('<div class="lateral-interior">')[1].split("</aside>")[0]
    assert interior_html.index('<nav aria-label="Principal">') < interior_html.index('class="usuario"')   # usuario al final
    assert re.search(r"\.usuario\s*\{[^}]*margin-top:\s*auto", CSS)


def test_en_celular_la_barra_lateral_sigue_siendo_una_barra_superior():
    movil = CSS.split("@media (max-width: 800px)", 1)[1].split("@media", 1)[0]
    assert re.search(r"\.lateral-interior\s*\{[^}]*position:\s*static[^}]*height:\s*auto", movil)
    assert "flex-direction: row" in movil


def test_identificacion_codigo_y_nombre_en_la_primera_fila_categoria_y_unidad_en_la_segunda(adm):
    t = html(adm.get(reverse("producto_nuevo")))
    ident = t.split("<h2>Identificación</h2>")[1].split("</section>")[0]
    assert 'class="campos-fila dos-columnas"' in ident
    orden = [ident.index(f'for="id_{n}"') for n in ("sku", "nombre", "categoria", "unidad")]
    assert orden == sorted(orden)
    assert re.search(r"\.campos-fila\.dos-columnas\s*\{[^}]*repeat\(2", CSS)


def test_al_editar_se_muestra_el_codigo_fijo_en_la_primera_posicion(adm):
    p = prod("FIJO")
    t = html(adm.get(reverse("producto_editar", args=[p.pk])))
    ident = t.split("<h2>Identificación</h2>")[1].split("</section>")[0]
    assert '<div class="valor-fijo mono">FIJO</div>' in ident and 'name="sku"' not in t
    assert ident.index("valor-fijo") < ident.index('for="id_nombre"') < ident.index('for="id_categoria"') < ident.index('for="id_unidad"')
