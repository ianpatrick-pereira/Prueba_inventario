"""Pantallas del catalogo (T-F1-06; D-42, D-43)."""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from inventario import servicios
from inventario.models import Categoria, CodigoAlternativo, Producto, ProductoProveedor, Proveedor
from inventario.validadores import calcular_dv, normalizar_codigo, normalizar_rut

pytestmark = pytest.mark.django_db
D = Decimal
CLAVE = "Zq7-mPx2vL-prueba"


def crear_usuario(nombre, grupo=None):
    u = get_user_model().objects.create_user(nombre, password=CLAVE)
    if grupo:
        u.groups.add(Group.objects.get(name=grupo))
    return u


@pytest.fixture
def admin_user():
    return crear_usuario("adm", "Administrador")


@pytest.fixture
def operador():
    return crear_usuario("op", "Operador")


@pytest.fixture
def adm(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def op(client, operador):
    client.force_login(operador)
    return client


@pytest.fixture
def prod():
    return Producto.objects.create(sku="C934J", nombre="Filtro de aceite", stock_min=D("2"), stock_max=D("10"))


def html(r):
    return r.content.decode()


def mensajes(r):
    return html(r)


# ------------------------------------------------------------------ 1. busqueda
@pytest.fixture
def catalogo(admin_user):
    fsc = Proveedor.objects.create(nombre="Filtros SC", rut="76086428-5")
    otro = Proveedor.objects.create(nombre="Otro proveedor")
    p1 = Producto.objects.create(sku="C934J", nombre="Filtro de aceite")
    p2 = Producto.objects.create(sku="0077", nombre="Bujia")
    p3 = Producto.objects.create(sku="ABC", nombre="Cosa")
    p4 = Producto.objects.create(sku="ZZ9", nombre="ABC filtro de aire")
    ProductoProveedor.objects.create(producto=p1, proveedor=fsc, codigo_proveedor="FSC00057")
    ProductoProveedor.objects.create(producto=p1, proveedor=otro, codigo_proveedor="77-OTRO")
    CodigoAlternativo.objects.create(producto=p1, tipo="barras", valor="7801111111111")
    CodigoAlternativo.objects.create(producto=p1, tipo="fabricante", valor="77FAB")
    servicios.registrar_entrada(p1, 10, admin_user)
    servicios.registrar_salida(p1, 3, admin_user)
    return p1, p2, p3, p4


def skus(r):
    return [p.sku for p in r.context["productos"]]


def test_busqueda_por_sku_exacto_codigo_alternativo_proveedor_y_nombre(adm, catalogo):
    url = reverse("producto_lista")
    assert skus(adm.get(url, {"q": "C934J"})) == ["C934J"]
    assert skus(adm.get(url, {"q": "7801111111111"})) == ["C934J"]
    assert skus(adm.get(url, {"q": "FSC00057"})) == ["C934J"]
    assert skus(adm.get(url, {"q": "bujia"})) == ["0077"]
    assert skus(adm.get(url, {"q": "0077"})) == ["0077"]


def test_busqueda_sin_distinguir_mayusculas(adm, catalogo):
    url = reverse("producto_lista")
    assert skus(adm.get(url, {"q": "c934j"})) == ["C934J"]
    assert skus(adm.get(url, {"q": "fsc00057"})) == ["C934J"]
    assert skus(adm.get(url, {"q": "FILTRO DE ACEITE"})) == ["C934J"]


def test_coincidencia_exacta_primero(adm, catalogo):
    assert skus(adm.get(reverse("producto_lista"), {"q": "abc"})) == ["ABC", "ZZ9"]


def test_producto_con_varios_codigos_sale_una_vez_con_su_stock_real(adm, catalogo):
    r = adm.get(reverse("producto_lista"), {"q": "77"})
    filas = list(r.context["productos"])
    assert [p.sku for p in filas].count("C934J") == 1  # coincide por 2 codigos alt. y 1 de proveedor
    assert {p.sku: p.stock for p in filas}["C934J"] == D("7.00")
    assert 'class="num stock">7</td>' in html(r)      # la lista muestra el stock entero sin decimales


def test_busqueda_sin_resultados_mensaje(adm, catalogo):
    r = adm.get(reverse("producto_lista"), {"q": "inexistente-xyz"})
    assert "No se encontraron productos" in html(r)


def test_buscador_tiene_foco_automatico(adm):
    assert "autofocus" in html(adm.get(reverse("producto_lista")))


# ------------------------------------------------------------------ 2. consultas y paginacion
def _consultas(client, url, datos=None):
    with CaptureQueriesContext(connection) as c:
        assert client.get(url, datos or {}).status_code == 200
    return len(c)


def test_lista_usa_numero_fijo_de_consultas(adm, admin_user):
    cat = Categoria.objects.create(nombre="Filtros")
    url = reverse("producto_lista")
    Producto.objects.create(sku="A0", nombre="uno", categoria=cat)
    con_1 = _consultas(adm, url), _consultas(adm, url, {"q": "uno"})
    for i in range(1, 31):
        p = Producto.objects.create(sku=f"A{i}", nombre=f"uno {i}", categoria=cat)
        servicios.registrar_entrada(p, i, admin_user)
        CodigoAlternativo.objects.create(producto=p, tipo="barras", valor=f"B{i}")
    con_31 = _consultas(adm, url), _consultas(adm, url, {"q": "uno"})
    assert con_1 == con_31


def test_lista_pagina_de_a_50(adm):
    Producto.objects.bulk_create([Producto(sku=f"P{i:03d}", nombre=f"producto {i:03d}") for i in range(55)])
    r1 = adm.get(reverse("producto_lista"))
    assert len(r1.context["productos"]) == 50 and r1.context["is_paginated"]
    r2 = adm.get(reverse("producto_lista"), {"page": 2})
    assert len(r2.context["productos"]) == 5


# ------------------------------------------------------------------ 3. stock y minimo
def test_stock_con_dos_decimales_y_marca_bajo_el_minimo(adm, admin_user, prod):
    otro = Producto.objects.create(sku="OK1", nombre="Con stock", stock_min=D("2"), stock_max=D("9"))
    sin_minimo = Producto.objects.create(sku="SM", nombre="Sin minimo")
    servicios.registrar_entrada(prod, "2.5", admin_user)     # 2.50 > 2: sobre el minimo
    servicios.registrar_entrada(otro, "1.25", admin_user)    # 1.25 <= 2: bajo el minimo
    r = adm.get(reverse("producto_lista"))
    filas = {p.sku: p for p in r.context["productos"]}
    assert filas["C934J"].stock == D("2.50") and filas["OK1"].stock == D("1.25")
    texto = html(r)
    assert "2,50" in texto and "1,25" in texto and 'class="num stock">0</td>' in texto   # enteros sin decimales, fracciones con 2
    marcadas = [l for l in texto.splitlines() if 'class="bajo"' in l]
    assert len(marcadas) == 1 and "bajo mínimo" in texto
    assert "OK1" in texto.split('class="bajo"')[1][:300]
    assert sin_minimo.stock_min == 0  # sin minimo definido no se marca


def test_en_el_minimo_exacto_se_marca(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 2, admin_user)
    assert 'class="bajo"' in html(adm.get(reverse("producto_lista")))


# ------------------------------------------------------------------ 4. crear producto
def datos_producto(**kw):
    d = {"sku": "NUEVO1", "nombre": "Producto nuevo", "categoria": "", "unidad": "unidad",
         "costo": "0", "stock_min": "1", "stock_max": "5"}
    d.update(kw)
    return d


def test_crear_producto_guarda_codigo_sin_espacios_y_conserva_ceros(adm):
    r = adm.post(reverse("producto_nuevo"), datos_producto(sku=" 0077 "), follow=True)
    assert r.status_code == 200
    assert Producto.objects.get(nombre="Producto nuevo").sku == "0077"
    assert "Producto creado." in html(r)


def test_crear_producto_con_sku_duplicado_muestra_mensaje(adm, prod):
    r = adm.post(reverse("producto_nuevo"), datos_producto(sku="C934J"))
    assert r.status_code == 200
    assert "Ya existe un producto con el código «C934J»." in html(r)
    assert Producto.objects.count() == 1


def test_crear_producto_con_sku_duplicado_con_espacios_tambien(adm, prod):
    r = adm.post(reverse("producto_nuevo"), datos_producto(sku="  C934J "))
    assert r.status_code == 200 and "Ya existe un producto" in html(r)


def test_stock_max_menor_que_min_muestra_mensaje_no_500(adm):
    r = adm.post(reverse("producto_nuevo"), datos_producto(stock_min="10", stock_max="5"))
    assert r.status_code == 200
    assert "El stock máximo no puede ser menor que el mínimo." in html(r)
    assert Producto.objects.count() == 0


def test_formulario_de_producto_no_tiene_campo_de_stock(adm, prod):
    for url in (reverse("producto_nuevo"), reverse("producto_editar", args=[prod.pk])):
        assert 'name="stock"' not in html(adm.get(url))


# ------------------------------------------------------------------ 5. editar
def test_editar_no_permite_cambiar_el_sku(adm, prod):
    url = reverse("producto_editar", args=[prod.pk])
    assert 'name="sku"' not in html(adm.get(url))
    r = adm.post(url, datos_producto(sku="HACK", nombre="Nombre editado"), follow=True)
    prod.refresh_from_db()
    assert prod.sku == "C934J" and prod.nombre == "Nombre editado"
    assert "Producto actualizado." in html(r)


def test_editar_valida_stock_max(adm, prod):
    r = adm.post(reverse("producto_editar", args=[prod.pk]), datos_producto(stock_min="9", stock_max="1"))
    assert r.status_code == 200 and "El stock máximo no puede ser menor" in html(r)


# ------------------------------------------------------------------ 6. codigos
def test_agregar_codigo_alternativo_y_rechazar_duplicado(adm, prod):
    otro = Producto.objects.create(sku="O1", nombre="Otro")
    url = reverse("codigo_agregar", args=[prod.pk])
    r = adm.post(url, {"tipo": "barras", "valor": "  0123 "}, follow=True)
    assert CodigoAlternativo.objects.get(producto=prod).valor == "0123"
    assert "Código agregado." in html(r)
    r = adm.post(reverse("codigo_agregar", args=[otro.pk]), {"tipo": "barras", "valor": "0123"})
    assert r.status_code == 200 and "ya pertenece al producto" in html(r)
    assert CodigoAlternativo.objects.count() == 1


def test_codigo_de_proveedor_duplicado_rechazado_y_en_otro_proveedor_aceptado(adm, prod):
    a, b = Proveedor.objects.create(nombre="A"), Proveedor.objects.create(nombre="B")
    otro = Producto.objects.create(sku="O1", nombre="Otro")
    ProductoProveedor.objects.create(producto=otro, proveedor=a, codigo_proveedor="X1")
    url = reverse("codigo_proveedor_agregar", args=[prod.pk])
    r = adm.post(url, {"proveedor": a.pk, "codigo_proveedor": " X1 "})
    assert r.status_code == 200 and "ya usa el código «X1»" in html(r)
    assert not ProductoProveedor.objects.filter(producto=prod).exists()
    r = adm.post(url, {"proveedor": b.pk, "codigo_proveedor": "X1"}, follow=True)
    assert ProductoProveedor.objects.filter(producto=prod, proveedor=b, codigo_proveedor="X1").exists()
    assert "Código de proveedor agregado." in html(r)


def test_detalle_muestra_datos_stock_y_codigos(adm, admin_user, prod):
    servicios.registrar_entrada(prod, "4", admin_user)
    CodigoAlternativo.objects.create(producto=prod, tipo="barras", valor="999")
    ProductoProveedor.objects.create(
        producto=prod, proveedor=Proveedor.objects.create(nombre="Prov Uno"), codigo_proveedor="PU-1")
    t = html(adm.get(reverse("producto_detalle", args=[prod.pk])))
    for esperado in ("C934J", "4,00", "999", "PU-1", "Prov Uno", "Agregar código alternativo",
                     "Agregar código de proveedor"):
        assert esperado in t


# ------------------------------------------------------------------ 7. RUT
@pytest.mark.parametrize("entrada,esperado", [
    ("12.345.678-5", "12345678-5"), ("12345678-5", "12345678-5"), ("123456785", "12345678-5"),
    ("11.111.111-1", "11111111-1"), ("10.000.013-k", "10000013-K"), ("10000013-K", "10000013-K"),
    ("  76.086.428-5 ", "76086428-5"), ("", ""), (None, ""),
])
def test_normalizar_rut_validos(entrada, esperado):
    assert normalizar_rut(entrada) == esperado


@pytest.mark.parametrize("malo", ["12.345.678-4", "12345678-K", "1234", "abc", "12.345.678-", "123456789012-3"])
def test_normalizar_rut_invalidos(malo):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        normalizar_rut(malo)


def _dv_independiente(cuerpo):
    s, f = 0, 2
    for d in reversed(str(cuerpo)):
        s += int(d) * f
        f = 2 if f == 7 else f + 1
    r = 11 - s % 11
    return {11: "0", 10: "K"}.get(r, str(r))


def test_digito_verificador_coincide_con_calculo_independiente():
    cuerpos = [12345678, 7654321, 10000013, 11111111, 76086428, 6000000, 99999999, 5126663, 1234567]
    for c in cuerpos:
        assert calcular_dv(str(c)) == _dv_independiente(c)
        assert normalizar_rut(f"{c}-{_dv_independiente(c)}") == f"{c}-{_dv_independiente(c)}"
    dvs = {_dv_independiente(c) for c in range(1000000, 1000300)}
    assert "K" in dvs and "0" in dvs  # se cubren los casos especiales


def test_proveedor_rut_en_formulario(adm):
    url = reverse("proveedor_nuevo")
    r = adm.post(url, {"nombre": "Prov 1", "rut": "12.345.678-5", "contacto": ""}, follow=True)
    assert Proveedor.objects.get(nombre="Prov 1").rut == "12345678-5"
    r = adm.post(url, {"nombre": "Prov 2", "rut": "12.345.678-4", "contacto": ""})
    assert r.status_code == 200 and "dígito verificador" in html(r)
    r = adm.post(url, {"nombre": "Prov 3", "rut": "10.000.013-k", "contacto": ""})
    assert Proveedor.objects.get(nombre="Prov 3").rut == "10000013-K"
    adm.post(url, {"nombre": "Sin rut 1", "rut": "", "contacto": ""})
    adm.post(url, {"nombre": "Sin rut 2", "rut": "", "contacto": ""})
    assert Proveedor.objects.filter(rut__isnull=True).count() == 2
    r = adm.post(url, {"nombre": "Prov 4", "rut": "12345678-5", "contacto": ""})
    assert r.status_code == 200 and "Ya existe un proveedor con ese RUT" in html(r)


def test_buscar_proveedor_por_nombre_o_rut(adm):
    Proveedor.objects.create(nombre="Lubricantes Sur", rut="12345678-5")
    Proveedor.objects.create(nombre="Otro", rut="76086428-5")
    url = reverse("proveedor_lista")
    assert [p.nombre for p in adm.get(url, {"q": "lubric"}).context["proveedores"]] == ["Lubricantes Sur"]
    assert [p.nombre for p in adm.get(url, {"q": "12.345.678-5"}).context["proveedores"]] == ["Lubricantes Sur"]


def test_codigos_se_normalizan_sin_cambiar_mayusculas_ni_ceros():
    assert normalizar_codigo("  00aBc ") == "00aBc"


# ------------------------------------------------------------------ 8. nombres duplicados
@pytest.mark.parametrize("duplicado", ["aceites", "  ACEITES ", "Aceites  "])
def test_categoria_duplicada_por_mayusculas_o_espacios_rechazada(adm, duplicado):
    Categoria.objects.create(nombre="Aceites")
    r = adm.post(reverse("categoria_nueva"), {"nombre": duplicado})
    assert r.status_code == 200 and "Ya existe un categoría" in html(r)
    assert Categoria.objects.count() == 1


def test_proveedor_duplicado_por_mayusculas_o_espacios_rechazado(adm):
    Proveedor.objects.create(nombre="Filtros   Sur")
    r = adm.post(reverse("proveedor_nuevo"), {"nombre": " filtros sur ", "rut": "", "contacto": ""})
    assert r.status_code == 200 and "Ya existe un proveedor" in html(r)
    assert Proveedor.objects.count() == 1


def test_editar_el_propio_nombre_cambiando_mayusculas_es_valido(adm):
    c = Categoria.objects.create(nombre="aceites")
    r = adm.post(reverse("categoria_editar", args=[c.pk]), {"nombre": "Aceites"}, follow=True)
    c.refresh_from_db()
    assert c.nombre == "Aceites" and "Categoría actualizada." in html(r)


def test_crear_y_editar_proveedor_y_categoria(adm):
    adm.post(reverse("categoria_nueva"), {"nombre": "Filtros"}, follow=True)
    c = Categoria.objects.get()
    assert "Filtros" in html(adm.get(reverse("categoria_lista")))
    adm.post(reverse("proveedor_nuevo"), {"nombre": "Prov", "rut": "", "contacto": " 555  1234 "})
    p = Proveedor.objects.get()
    assert p.contacto == "555 1234"
    adm.post(reverse("proveedor_editar", args=[p.pk]), {"nombre": "Prov SA", "rut": "", "contacto": ""})
    p.refresh_from_db()
    assert p.nombre == "Prov SA" and c.nombre == "Filtros"


# ------------------------------------------------------------------ 9. permisos
def todas_las_urls_get(prod, cod, codp):
    return [
        reverse("producto_lista"), reverse("producto_nuevo"), reverse("producto_detalle", args=[prod.pk]),
        reverse("producto_editar", args=[prod.pk]), reverse("codigo_quitar", args=[cod.pk]),
        reverse("codigo_proveedor_quitar", args=[codp.pk]), reverse("proveedor_lista"),
        reverse("proveedor_nuevo"), reverse("categoria_lista"), reverse("categoria_nueva"),
    ]


@pytest.fixture
def con_codigos(prod):
    cod = CodigoAlternativo.objects.create(producto=prod, tipo="barras", valor="BC1")
    codp = ProductoProveedor.objects.create(
        producto=prod, proveedor=Proveedor.objects.create(nombre="PX"), codigo_proveedor="PX1")
    return cod, codp


def test_anonimo_va_al_login(client, prod, con_codigos):
    for url in todas_las_urls_get(prod, *con_codigos):
        r = client.get(url)
        assert r.status_code == 302 and r.url.startswith("/cuentas/login/"), url


def test_usuario_sin_grupo_recibe_403_en_todo(client, prod, con_codigos):
    client.force_login(crear_usuario("sin"))
    for url in todas_las_urls_get(prod, *con_codigos):
        assert client.get(url).status_code == 403, url
    cod, codp = con_codigos
    for url in (reverse("codigo_agregar", args=[prod.pk]), reverse("codigo_proveedor_agregar", args=[prod.pk]),
                reverse("codigo_quitar", args=[cod.pk]), reverse("codigo_proveedor_quitar", args=[codp.pk])):
        assert client.post(url, {}).status_code == 403, url
    assert client.get(reverse("proveedor_editar", args=[codp.proveedor_id])).status_code == 403
    assert client.get(reverse("categoria_editar", args=[Categoria.objects.create(nombre="x").pk])).status_code == 403


def test_operador_ve_crea_y_edita(op, prod):
    assert op.get(reverse("producto_lista")).status_code == 200
    assert op.get(reverse("producto_detalle", args=[prod.pk])).status_code == 200
    assert op.post(reverse("producto_nuevo"), datos_producto()).status_code == 302
    assert op.post(reverse("producto_editar", args=[prod.pk]), datos_producto(nombre="Editado")).status_code == 302
    assert op.post(reverse("proveedor_nuevo"), {"nombre": "P", "rut": "", "contacto": ""}).status_code == 302
    assert op.post(reverse("categoria_nueva"), {"nombre": "C"}).status_code == 302
    assert op.post(reverse("codigo_agregar", args=[prod.pk]), {"tipo": "barras", "valor": "Z1"}).status_code == 302


def test_operador_no_puede_quitar_codigos(op, prod, con_codigos):
    cod, codp = con_codigos
    assert op.post(reverse("codigo_quitar", args=[cod.pk])).status_code == 403
    assert op.post(reverse("codigo_proveedor_quitar", args=[codp.pk])).status_code == 403
    assert op.get(reverse("codigo_quitar", args=[cod.pk])).status_code == 403
    assert CodigoAlternativo.objects.filter(pk=cod.pk).exists()
    assert ProductoProveedor.objects.filter(pk=codp.pk).exists()
    detalle = html(op.get(reverse("producto_detalle", args=[prod.pk])))
    assert "Quitar" not in detalle  # el boton ni se muestra


def test_administrador_quita_con_confirmacion_y_get_no_borra(adm, prod, con_codigos):
    cod, codp = con_codigos
    assert "Quitar" in html(adm.get(reverse("producto_detalle", args=[prod.pk])))
    for url in (reverse("codigo_quitar", args=[cod.pk]), reverse("codigo_proveedor_quitar", args=[codp.pk])):
        r = adm.get(url)
        assert r.status_code == 200 and "¿Seguro" in html(r) and "csrfmiddlewaretoken" in html(r)
    assert CodigoAlternativo.objects.exists() and ProductoProveedor.objects.exists()  # GET no borro
    r = adm.post(reverse("codigo_quitar", args=[cod.pk]), follow=True)
    assert "Código quitado." in html(r)
    adm.post(reverse("codigo_proveedor_quitar", args=[codp.pk]))
    assert not CodigoAlternativo.objects.exists() and not ProductoProveedor.objects.exists()


def test_no_hay_rutas_para_borrar_productos_proveedores_ni_categorias(adm, prod):
    from django.urls import get_resolver

    nombres = {p.name for p in get_resolver().url_patterns if getattr(p, "name", None)}
    assert not {n for n in nombres if "borrar" in n or "eliminar" in n}
    assert adm.post(f"/productos/{prod.pk}/borrar/").status_code == 404


def test_inicio_muestra_enlaces_segun_permisos(client, operador):
    client.force_login(operador)
    t = html(client.get("/"))
    assert "Productos" in t and "Proveedores" in t and "Categorías" in t
    client.force_login(crear_usuario("sin"))
    t = html(client.get("/"))
    assert 'href="/productos/"' not in t


def test_csrf_en_formularios_de_catalogo(adm, prod):
    for url in (reverse("producto_nuevo"), reverse("producto_detalle", args=[prod.pk]),
                reverse("proveedor_nuevo"), reverse("categoria_nueva")):
        assert "csrfmiddlewaretoken" in html(adm.get(url))


# ------------------------------------------------------------------ 10. XSS
def test_nombre_con_script_se_muestra_escapado(adm, admin_user):
    p = Producto.objects.create(sku="XSS", nombre="<script>alert(1)</script>")
    for url in (reverse("producto_lista"), reverse("producto_detalle", args=[p.pk])):
        t = html(adm.get(url))
        assert "<script>alert(1)" not in t and "&lt;script&gt;alert(1)&lt;/script&gt;" in t, url
    Proveedor.objects.create(nombre="<img src=x onerror=alert(1)>")
    t = html(adm.get(reverse("proveedor_lista")))
    assert "<img src=x" not in t and "&lt;img" in t


def test_busqueda_con_html_se_escapa(adm):
    t = html(adm.get(reverse("producto_lista"), {"q": '"><script>x</script>'}))
    assert "<script>x</script>" not in t


@pytest.mark.parametrize("campo", ["costo", "stock_min", "stock_max"])
def test_formulario_de_producto_rechaza_negativos(adm, prod, campo):
    for url in (reverse("producto_nuevo"), reverse("producto_editar", args=[prod.pk])):
        r = adm.post(url, datos_producto(sku="NEG1", **{campo: "-1"}))
        assert r.status_code == 200 and campo in r.context["form"].errors
        assert "No puede ser negativo." in html(r)
    assert not Producto.objects.filter(sku="NEG1").exists()
