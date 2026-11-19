"""Migas de pan y vuelta a la lista conservando la busqueda (T-F1-16, ampliacion)."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from inventario import servicios
from inventario.models import Categoria, Movimiento, Producto

pytestmark = pytest.mark.django_db
CLAVE = "Zq7-mPx2vL-prueba"


@pytest.fixture
def adm(client):
    u = get_user_model().objects.create_user("adm", password=CLAVE)
    u.groups.add(Group.objects.get(name="Administrador"))
    client.force_login(u)
    client.user = u
    return client


@pytest.fixture
def prod():
    c = Categoria.objects.create(nombre="Filtros")
    return Producto.objects.create(sku="M1", nombre="Con migas", categoria=c)


def migas(r):
    m = re.search(r'<nav class="migas"[^>]*>(.*?)</nav>', r.content.decode(), re.S)
    assert m, "sin migas"
    return m.group(1)


def partes(html):
    """[(texto, href|None)] de cada elemento de las migas, sin las flechas separadoras."""
    out = []
    for m in re.finditer(r'<a href="([^"]*)">([^<]+)</a>|<span aria-current="page">([^<]+)</span>', html):
        out.append((m.group(2) or m.group(3), m.group(1)))
    return [(t.strip(), h) for t, h in out]


def test_migas_en_la_ficha(adm, prod):
    p = partes(migas(adm.get(reverse("producto_detalle", args=[prod.pk]))))
    assert p == [("← Productos", "/productos/"), ("M1", None)]


def test_migas_en_el_kardex(adm, prod):
    p = partes(migas(adm.get(reverse("kardex", args=[prod.pk]))))
    assert p == [("← Productos", "/productos/"), ("M1", f"/productos/{prod.pk}/"), ("Kardex", None)]


@pytest.mark.parametrize("nombre,titulo", [("entrada", "Registrar entrada"), ("salida", "Registrar salida"),
                                            ("corregir", "Corregir stock")])
def test_migas_en_los_formularios_de_movimiento(adm, prod, nombre, titulo):
    p = partes(migas(adm.get(reverse(nombre, args=[prod.pk]))))
    assert p == [("← Productos", "/productos/"), ("M1", f"/productos/{prod.pk}/"),
                 ("Kardex", f"/productos/{prod.pk}/kardex/"), (titulo, None)]


def test_migas_en_revertir(adm, prod):
    m = servicios.registrar_entrada(prod, 5, adm.user)
    p = partes(migas(adm.get(reverse("movimiento_revertir", args=[m.pk]))))
    assert p == [("← Productos", "/productos/"), ("M1", f"/productos/{prod.pk}/"),
                 ("Kardex", f"/productos/{prod.pk}/kardex/"), ("Revertir movimiento", None)]


def test_migas_en_editar_y_nuevo_producto(adm, prod):
    assert partes(migas(adm.get(reverse("producto_editar", args=[prod.pk])))) == [
        ("← Productos", "/productos/"), ("M1", f"/productos/{prod.pk}/"), ("Editar", None)]
    assert partes(migas(adm.get(reverse("producto_nuevo")))) == [("← Productos", "/productos/"), ("Nuevo producto", None)]


def test_el_ultimo_elemento_no_es_enlace(adm, prod):
    for url in (reverse("producto_detalle", args=[prod.pk]), reverse("kardex", args=[prod.pk]),
                reverse("entrada", args=[prod.pk])):
        assert partes(migas(adm.get(url)))[-1][1] is None


# ---- vuelta a la lista conservando busqueda y filtro
def test_la_lista_enlaza_la_ficha_con_la_busqueda_de_origen(adm, prod):
    r = adm.get(reverse("producto_lista"), {"q": "mi", "categoria": prod.categoria_id})
    assert f'href="/productos/{prod.pk}/?volver=q%3Dmi%26categoria%3D{prod.categoria_id}"' in r.content.decode()
    assert f'href="/productos/{prod.pk}/">' in adm.get(reverse("producto_lista")).content.decode()   # sin busqueda, enlace simple


def test_ficha_y_kardex_vuelven_a_la_lista_con_la_misma_busqueda(adm, prod):
    volver = f"q%3Dmi%26categoria%3D{prod.categoria_id}%26page%3D2"
    destino = f"/productos/?q=mi&categoria={prod.categoria_id}&page=2"
    f = adm.get(f"/productos/{prod.pk}/?volver={volver}")
    assert partes(migas(f))[0][1] in (destino, destino.replace("&", "&amp;"))
    t = f.content.decode()
    assert f"/productos/{prod.pk}/kardex/?volver=" in t                     # el kardex lo hereda
    k = adm.get(f"/productos/{prod.pk}/kardex/?volver={volver}")
    assert partes(migas(k))[0][1] in (destino, destino.replace("&", "&amp;"))
    assert partes(migas(k))[1][1].startswith(f"/productos/{prod.pk}/?volver=")   # y la ficha tambien


def test_el_filtro_de_fechas_del_kardex_conserva_el_origen(adm, prod):
    t = adm.get(f"/productos/{prod.pk}/kardex/?volver=q%3Dmi").content.decode()
    assert '<input type="hidden" name="volver" value="q=mi">' in t
    servicios.registrar_entrada(prod, 1, adm.user)
    t2 = adm.get(f"/productos/{prod.pk}/kardex/", {"desde": "2000-01-01", "volver": "q=mi"}).content.decode()
    assert '<input type="hidden" name="volver" value="q=mi">' in t2 and partes(migas(adm.get(
        f"/productos/{prod.pk}/kardex/", {"desde": "2000-01-01", "volver": "q=mi"})))[0][1] == "/productos/?q=mi"


@pytest.mark.parametrize("volver", ["https://evil.example/", "//evil.example", "javascript:alert(1)", "q=a&x=<script>",
                                    "evil=1", "page=", "q=" + "a" * 600])
def test_volver_solo_acepta_q_categoria_y_page_y_siempre_es_un_enlace_interno(adm, prod, volver):
    r = adm.get(reverse("producto_detalle", args=[prod.pk]), {"volver": volver})
    assert r.status_code == 200
    href = partes(migas(r))[0][1]
    assert href.startswith("/productos/") and "evil" not in href and "<" not in href and "javascript" not in href
    assert "<script>" not in r.content.decode().split("<main")[1]


def test_sin_parametro_las_migas_vuelven_a_la_lista_simple(adm, prod):
    assert partes(migas(adm.get(reverse("kardex", args=[prod.pk]))))[0][1] == "/productos/"
