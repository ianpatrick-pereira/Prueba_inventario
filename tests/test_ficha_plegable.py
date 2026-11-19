"""Secciones plegables de la ficha del producto (T-F1-16)."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

from inventario.models import CodigoAlternativo, Producto, ProductoProveedor, Proveedor

pytestmark = pytest.mark.django_db
CLAVE = "Zq7-mPx2vL-prueba"


def crear_usuario(nombre, grupo):
    u = get_user_model().objects.create_user(nombre, password=CLAVE)
    u.groups.add(Group.objects.get(name=grupo))
    return u


@pytest.fixture
def adm(client):
    client.force_login(crear_usuario("adm", "Administrador"))
    return client


@pytest.fixture
def prod():
    p = Producto.objects.create(sku="F1", nombre="Con codigos")
    CodigoAlternativo.objects.create(producto=p, tipo="barras", valor="B1")
    CodigoAlternativo.objects.create(producto=p, tipo="fabricante", valor="M1")
    ProductoProveedor.objects.create(producto=p, proveedor=Proveedor.objects.create(nombre="Prov"), codigo_proveedor="PX1")
    return p


def html(r):
    return r.content.decode()


def secciones(t):
    """{titulo: (abierta?, texto de la seccion)} para los <details> de la ficha."""
    out = {}
    for m in re.finditer(r"<details([^>]*)>\s*<summary>([^<]+)</summary>(.*?)</details>", t, re.S):
        out[m.group(2).strip()] = (" open" in m.group(1), m.group(3))
    return out


def test_las_dos_secciones_son_details_cerrados_con_el_conteo(adm, prod):
    s = secciones(html(adm.get(reverse("producto_detalle", args=[prod.pk]))))
    assert set(s) == {"Códigos alternativos (2)", "Códigos por proveedor (1)"}
    assert not any(abierta for abierta, _ in s.values())


def test_las_secciones_usan_las_clases_del_historial_y_no_necesitan_js(adm, prod):
    t = html(adm.get(reverse("producto_detalle", args=[prod.pk])))
    assert t.count('<details class="tarjeta historial"') == 2 and "<script" not in t


def test_conteo_en_cero_y_actualizado(adm):
    p = Producto.objects.create(sku="F0", nombre="Sin codigos")
    assert set(secciones(html(adm.get(reverse("producto_detalle", args=[p.pk]))))) == {
        "Códigos alternativos (0)", "Códigos por proveedor (0)"}


def test_al_abrir_se_ven_la_tabla_y_el_formulario_de_agregar(adm, prod):
    s = secciones(html(adm.get(reverse("producto_detalle", args=[prod.pk]))))
    alt, prv = s["Códigos alternativos (2)"][1], s["Códigos por proveedor (1)"][1]
    assert "B1" in alt and "M1" in alt and "Agregar código alternativo" in alt and "csrfmiddlewaretoken" in alt
    assert "PX1" in prv and "Prov" in prv and "Agregar código de proveedor" in prv and "Quitar" in prv


def test_se_abre_sola_la_seccion_cuyo_formulario_volvio_con_errores(adm, prod):
    r = adm.post(reverse("codigo_agregar", args=[prod.pk]), {"tipo": "barras", "valor": "B1"})   # duplicado
    s = secciones(html(r))
    assert r.status_code == 200 and s["Códigos alternativos (2)"][0] and not s["Códigos por proveedor (1)"][0]
    assert "ya pertenece al producto" in html(r)
    prov = Proveedor.objects.get(nombre="Prov")
    r = adm.post(reverse("codigo_proveedor_agregar", args=[prod.pk]), {"proveedor": prov.pk, "codigo_proveedor": "PX1"})
    s = secciones(html(r))
    assert r.status_code == 200 and s["Códigos por proveedor (1)"][0] and not s["Códigos alternativos (2)"][0]


def test_se_abre_la_seccion_despues_de_agregar(adm, prod):
    r = adm.post(reverse("codigo_agregar", args=[prod.pk]), {"tipo": "barras", "valor": "NUEVO"})
    assert r.status_code == 302 and r.url == f"{reverse('producto_detalle', args=[prod.pk])}?abrir=alternativos"
    s = secciones(html(adm.get(r.url)))
    assert s["Códigos alternativos (3)"][0] and not s["Códigos por proveedor (1)"][0]
    prov = Proveedor.objects.create(nombre="Otro")
    r = adm.post(reverse("codigo_proveedor_agregar", args=[prod.pk]), {"proveedor": prov.pk, "codigo_proveedor": "Z9"})
    assert r.url.endswith("?abrir=proveedores")
    s = secciones(html(adm.get(r.url)))
    assert s["Códigos por proveedor (2)"][0] and not s["Códigos alternativos (3)"][0]


def test_se_abre_la_seccion_despues_de_quitar(adm, prod):
    c = CodigoAlternativo.objects.get(valor="B1")
    r = adm.post(reverse("codigo_quitar", args=[c.pk]))
    assert r.status_code == 302 and r.url.endswith("?abrir=alternativos")
    assert secciones(html(adm.get(r.url)))["Códigos alternativos (1)"][0]
    pp = ProductoProveedor.objects.get()
    r = adm.post(reverse("codigo_proveedor_quitar", args=[pp.pk]), follow=True)
    s = secciones(html(r))
    assert s["Códigos por proveedor (0)"][0] and not s["Códigos alternativos (1)"][0]
    assert "Código de proveedor quitado." in html(r)


def test_un_parametro_abrir_desconocido_no_abre_nada_ni_falla(adm, prod):
    r = adm.get(reverse("producto_detalle", args=[prod.pk]), {"abrir": "<script>"})
    assert r.status_code == 200 and not any(a for a, _ in secciones(html(r)).values()) and "<script>" not in html(r)


def test_permisos_de_agregar_y_quitar_no_cambian(prod):
    op = Client()
    op.force_login(crear_usuario("op", "Operador"))
    t = html(op.get(reverse("producto_detalle", args=[prod.pk])))
    s = secciones(t)
    assert "Agregar código alternativo" in s["Códigos alternativos (2)"][1] and "Quitar" not in t   # puede agregar, no quitar
    c = CodigoAlternativo.objects.get(valor="B1")
    assert op.post(reverse("codigo_quitar", args=[c.pk])).status_code == 403
    assert op.post(reverse("codigo_agregar", args=[prod.pk]), {"tipo": "barras", "valor": "OP1"}).status_code == 302
    sin = Client()
    sin.force_login(get_user_model().objects.create_user("sin", password=CLAVE))
    assert sin.get(reverse("producto_detalle", args=[prod.pk])).status_code == 403
    assert sin.post(reverse("codigo_agregar", args=[prod.pk]), {"tipo": "barras", "valor": "X"}).status_code == 403
