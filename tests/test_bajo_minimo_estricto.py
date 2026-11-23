"""T-F1-25 (D-66 final): «bajo minimo» se calcula sobre el stock minimo del producto:
minimo <= 0 nunca; minimo 1 solo con stock < 1; minimo > 1 cuando el stock llega a la MITAD del minimo o menos.
Lista, filtro, panel, ficha, kardex, barra e impresion usan la misma regla."""
from decimal import Decimal

import pytest
from django.urls import reverse

from inventario import servicios
from inventario.calculos import calcular_barra_stock, cantidad_sugerida, es_bajo_minimo
from inventario.models import Producto
from inventario.templatetags.formato import aviso_bajo, barra_stock
from tests.test_importar_eleventa import adm, admin_user, crear_usuario, media_tmp  # noqa: F401

pytestmark = pytest.mark.django_db
D = Decimal

CASOS = [   # stock, minimo, bajo
    (1, 1, False), (0, 1, True),
    (4, 6, False), (3, 6, True), (6, 6, False),
    (3, 5, False), (2, 5, True), (D("2.5"), 5, True),
    (2, 2, False), (1, 2, True),
    (0, 0, False), (5, 0, False),
]


@pytest.mark.parametrize("stock,minimo,bajo", CASOS)
def test_regla_unica(stock, minimo, bajo):
    assert es_bajo_minimo(stock, minimo) is bajo


def producto(sku, minimo, maximo, stock, usuario):
    p = Producto.objects.create(sku=sku, nombre=f"Prod {sku}", stock_min=D(minimo), stock_max=D(max(maximo, minimo)))
    if stock:
        servicios.registrar_entrada(p, D(stock), usuario)
    return p


@pytest.mark.parametrize("stock,minimo,bajo", CASOS)
def test_lista_filtro_panel_ficha_kardex_barra_coherentes(adm, admin_user, stock, minimo, bajo):
    p = producto("P1", minimo, minimo + 4, stock, admin_user)
    lista = adm.get(reverse("producto_lista")).content.decode()
    filtro = adm.get(reverse("producto_lista"), {"bajo_minimo": "1"})
    assert ('class="bajo"' in lista) is bajo and ("insignia-bajo" in lista) is bajo
    assert ([x.sku for x in filtro.context["productos"]] == ["P1"]) is bajo
    assert (adm.get("/").context["bajo_minimo"] == 1) is bajo
    ficha = adm.get(reverse("producto_detalle", args=[p.pk])).content.decode()
    assert ("(bajo el mínimo)" in ficha) is bajo and "en o bajo" not in ficha
    assert ("insignia-bajo" in adm.get(reverse("kardex", args=[p.pk])).content.decode()) is bajo
    assert (calcular_barra_stock(stock, minimo, minimo + 4)["color"] == "rojo") is bajo


def test_la_hoja_impresa_y_el_panel_cuentan_lo_mismo(adm, admin_user):
    producto("A", 1, 5, 1, admin_user)        # no
    producto("B", 1, 5, 0, admin_user)        # bajo
    producto("C", 6, 10, 3, admin_user)       # bajo (3 = mitad)
    producto("D", 6, 10, 4, admin_user)       # no
    producto("E", 0, 0, 0, admin_user)        # sin minimo: nunca
    filtro = adm.get(reverse("producto_lista"), {"bajo_minimo": "1"})
    assert sorted(x.sku for x in filtro.context["productos"]) == ["B", "C"]
    assert adm.get("/").context["bajo_minimo"] == 2


@pytest.mark.parametrize("stock,minimo,maximo,esperada", [
    (3, 6, 6, 3),          # maximo = minimo = 6 y stock 3: sugerida 3
    (0, 1, 1, 1),
    (0, 5, 3, 5),          # el objetivo es el mayor entre maximo y minimo
    (2, 5, 15, 13),
    (20, 5, 15, 0),        # nunca negativa
])
def test_cantidad_sugerida(stock, minimo, maximo, esperada):
    assert cantidad_sugerida(stock, minimo, maximo) == esperada


def test_sugerida_en_la_hoja_impresa_con_maximo_igual_al_minimo(adm, admin_user):
    producto("M", 6, 6, 3, admin_user)
    t = adm.get(reverse("producto_lista"), {"bajo_minimo": "1"}).content.decode()
    fila = next(f for f in t.split("<tr") if ">M<" in f)
    assert 'class="num solo-impresion">3</td>' in fila


def test_textos_de_aviso_y_texto_accesible_de_la_barra(adm, admin_user):
    assert aviso_bajo(D("6")) == "con 3 o menos" and aviso_bajo(D("1")) == "con 0"
    assert aviso_bajo(D("5")) == "con 2,50 o menos" and aviso_bajo(D("0")) == ""
    assert barra_stock(D("3"), D("6"), D("6"))["texto"] == "3 de 6, aviso con 3 o menos"
    p = producto("T", 6, 10, 3, admin_user)
    ficha = adm.get(reverse("producto_detalle", args=[p.pk])).content.decode()
    assert "Aviso de bajo stock" in ficha and "con 3 o menos" in ficha
    q = producto("U", 1, 5, 2, admin_user)
    assert "con 0" in adm.get(reverse("producto_detalle", args=[q.pk])).content.decode()


def test_la_marca_de_la_barra_indica_el_umbral_de_aviso():
    assert calcular_barra_stock(3, 6, 12)["marca"] == 25.0        # mitad del minimo (3 de 12)
    assert calcular_barra_stock(3, 1, 10)["marca"] == 10.0        # minimo 1: el limite es 1


def test_minimo_cero_nunca_es_bajo_ni_tiene_aviso(adm, admin_user):
    p = producto("Z", 0, 0, 0, admin_user)
    assert not es_bajo_minimo(0, 0) and "Aviso de bajo stock" not in adm.get(reverse("producto_detalle", args=[p.pk])).content.decode()
