"""Entrada manual de mercaderia y kardex como unico lugar para mover el stock (T-F1-14; D-53..D-56)."""
import datetime as dt
import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from inventario import servicios
from inventario.models import Documento, Movimiento, Producto, Proveedor

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
def adm(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def op(operador):
    c = Client()
    c.force_login(operador)
    return c


@pytest.fixture
def operador():
    return crear_usuario("op", "Operador")


@pytest.fixture
def prod():
    return Producto.objects.create(sku="E-1", nombre="Producto entrada")


@pytest.fixture
def prov():
    return Proveedor.objects.create(nombre="Filtros Sur")


def html(r):
    return r.content.decode()


def token_de(r):
    return re.search(r'name="token" value="([^"]+)"', html(r)).group(1)


def entrada(client, prod, datos, token=None):
    url = reverse("entrada", args=[prod.pk])
    return client.post(url, {"token": token or token_de(client.get(url)), **datos})


# ------------------------------------------------------------------ D-55: registrar entrada
def test_entrada_registra_positiva_con_usuario_actual_y_hora_de_ahora(adm, admin_user, prod):
    antes = timezone.now()
    r = entrada(adm, prod, {"cantidad": "12,5", "motivo": "llego camion"})
    assert r.status_code == 302 and r.url == reverse("kardex", args=[prod.pk])
    m = Movimiento.objects.get()
    assert (m.tipo, m.cantidad, m.usuario, m.motivo) == ("entrada", D("12.50"), admin_user, "llego camion")
    assert antes <= m.fecha <= timezone.now() and m.documento is None and m.costo_unitario is None
    assert "Entrada registrada: 12,50 de E-1." in html(adm.get(r.url))
    assert servicios.stock_de(prod) == D("12.50")


def test_entrada_con_costo_unitario(adm, prod):
    entrada(adm, prod, {"cantidad": "3", "costo_unitario": "1.234,56".replace(".", "").replace(",", ".")})
    assert Movimiento.objects.get().costo_unitario == D("1234.56")
    entrada(adm, prod, {"cantidad": "1", "costo_unitario": "10,5"})
    assert Movimiento.objects.latest("pk").costo_unitario == D("10.50")


@pytest.mark.parametrize("campo,valor", [
    ("cantidad", "0"), ("cantidad", "-1"), ("cantidad", "abc"), ("cantidad", ""),
    ("costo_unitario", "-1"), ("costo_unitario", "1.234"), ("costo_unitario", "10,123"),
])
def test_entrada_con_datos_invalidos_da_error_en_el_campo(adm, prod, campo, valor):
    datos = {"cantidad": "1", "costo_unitario": ""}
    datos[campo] = valor
    r = entrada(adm, prod, datos)
    assert r.status_code == 200 and campo in r.context["form"].errors
    assert Movimiento.objects.count() == 0


def test_entrada_con_proveedor_y_numero_crea_una_factura_y_se_ve_en_el_kardex(adm, prod, prov):
    r = entrada(adm, prod, {"cantidad": "5", "proveedor": prov.pk, "numero": " F-100 "})
    assert r.status_code == 302
    d = Documento.objects.get()
    assert (d.tipo, d.proveedor, d.numero) == ("factura", prov, "F-100")
    assert Movimiento.objects.get().documento == d
    assert "F-100" in html(adm.get(reverse("kardex", args=[prod.pk]), {"page": 1}))


def test_entrada_con_la_misma_factura_la_reutiliza_sin_fallar(adm, prod, prov):
    otro = Producto.objects.create(sku="E-2", nombre="otro")
    entrada(adm, prod, {"cantidad": "5", "proveedor": prov.pk, "numero": "F-100"})
    r = entrada(adm, otro, {"cantidad": "2", "proveedor": prov.pk, "numero": "F-100"})
    assert r.status_code == 302
    assert Documento.objects.count() == 1
    assert {m.documento_id for m in Movimiento.objects.all()} == {Documento.objects.get().pk}


def test_misma_factura_en_otro_proveedor_es_otro_documento(adm, prod, prov):
    b = Proveedor.objects.create(nombre="Otro")
    entrada(adm, prod, {"cantidad": "1", "proveedor": prov.pk, "numero": "F-1"})
    entrada(adm, prod, {"cantidad": "1", "proveedor": b.pk, "numero": "F-1"})
    assert Documento.objects.count() == 2


def test_proveedor_sin_numero_crea_documento_sin_numero(adm, prod, prov):
    entrada(adm, prod, {"cantidad": "4", "proveedor": prov.pk, "numero": ""})
    d = Documento.objects.get()
    assert d.tipo == "factura" and d.numero == "" and d.proveedor == prov and Movimiento.objects.get().documento == d


def test_numero_sin_proveedor_es_error_en_el_campo(adm, prod):
    r = entrada(adm, prod, {"cantidad": "5", "numero": "F-9"})
    assert r.status_code == 200 and "numero" in r.context["form"].errors
    assert "elegir el proveedor" in html(r)
    assert Movimiento.objects.count() == 0 and not Documento.objects.exists()


def test_si_la_entrada_falla_no_queda_el_documento(adm, prod, prov, monkeypatch):
    def falla(*a, **k):
        raise servicios.ValidationError("simulado")

    monkeypatch.setattr(servicios, "registrar_entrada", falla)
    r = entrada(adm, prod, {"cantidad": "5", "proveedor": prov.pk, "numero": "F-7"})
    assert r.status_code == 200 and not Documento.objects.exists() and Movimiento.objects.count() == 0


def test_el_operador_puede_registrar_entradas(op, operador, prod):
    r = entrada(op, prod, {"cantidad": "2"})
    assert r.status_code == 302 and Movimiento.objects.get().usuario == operador


def test_sin_permiso_403_y_anonimo_al_login(client, prod):
    url = reverse("entrada", args=[prod.pk])
    assert client.get(url).status_code == 302
    client.force_login(crear_usuario("sin"))
    assert client.get(url).status_code == 403
    assert client.post(url, {"token": "x", "cantidad": "1"}).status_code == 403
    assert Movimiento.objects.count() == 0


def test_doble_envio_de_la_entrada_registra_un_solo_movimiento(adm, prod):
    url = reverse("entrada", args=[prod.pk])
    token = token_de(adm.get(url))
    adm.post(url, {"token": token, "cantidad": "3"})
    r = adm.post(url, {"token": token, "cantidad": "3"}, follow=True)
    assert Movimiento.objects.count() == 1 and "no se registró nada nuevo" in html(r)


def test_fecha_enviada_por_post_en_la_entrada_se_ignora(adm, prod):
    antes = timezone.now()
    entrada(adm, prod, {"cantidad": "1", "fecha": "2020-01-01 10:00:00"})
    assert Movimiento.objects.get().fecha >= antes and 'name="fecha"' not in html(adm.get(reverse("entrada", args=[prod.pk])))


def test_la_entrada_nunca_pasa_permitir_negativo(adm, prod, monkeypatch):
    llamadas = []
    real = servicios.registrar_entrada

    def espia(*a, **k):
        llamadas.append(k)
        return real(*a, **k)

    monkeypatch.setattr(servicios, "registrar_entrada", espia)
    entrada(adm, prod, {"cantidad": "1"})
    assert len(llamadas) == 1 and "permitir_negativo" not in llamadas[0]


def test_formulario_de_entrada_tiene_csrf_y_selector_de_proveedor(adm, prod, prov):
    t = html(adm.get(reverse("entrada", args=[prod.pk])))
    assert "csrfmiddlewaretoken" in t and 'name="proveedor"' in t and "Filtros Sur" in t and "Sin proveedor" in t
    assert "Registrar entrada" in t


def test_entrada_escapa_el_motivo_en_el_kardex(adm, prod):
    entrada(adm, prod, {"cantidad": "1", "motivo": "<script>alert(1)</script>"})
    t = html(adm.get(reverse("kardex", args=[prod.pk])))
    assert "<script>alert(1)" not in t and "&lt;script&gt;" in t


# ------------------------------------------------------------------ D-56: historial plegable
def _llenar(prod, usuario, n):
    base = timezone.now() - dt.timedelta(days=300)
    Movimiento.objects.bulk_create([
        Movimiento(producto=prod, usuario=usuario, tipo="entrada", cantidad=D("1"), fecha=base + dt.timedelta(minutes=i))
        for i in range(n)
    ])


def _details(t):
    return re.search(r"<details[^>]*>", t).group(0)


def test_historial_cerrado_sin_parametros_y_con_el_total_correcto(adm, admin_user, prod):
    _llenar(prod, admin_user, 7)
    t = html(adm.get(reverse("kardex", args=[prod.pk])))
    assert "Historial de movimientos (7)" in t and " open" not in _details(t) and "<summary>" in t
    assert t.index("Registrar entrada") < t.index("<details")           # botones arriba, historial plegado debajo
    assert all("sistema.js" in x for x in re.findall(r"<script[^>]*>", t))


@pytest.mark.parametrize("params", [{"desde": "2000-01-01"}, {"hasta": "2100-01-01"}, {"page": 1}, {"desde": "basura"}])
def test_historial_abierto_con_filtro_de_fechas_o_pagina(adm, admin_user, prod, params):
    _llenar(prod, admin_user, 3)
    t = html(adm.get(reverse("kardex", args=[prod.pk]), params))
    assert " open" in _details(t)


def test_el_total_del_historial_respeta_el_filtro(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 1, admin_user, fecha=timezone.now() - dt.timedelta(days=30))
    servicios.registrar_entrada(prod, 1, admin_user)
    servicios.registrar_entrada(prod, 1, admin_user)
    hoy = timezone.localdate().isoformat()
    r = adm.get(reverse("kardex", args=[prod.pk]), {"desde": hoy})
    assert "Historial de movimientos (2)" in html(r)
    assert "Historial de movimientos (3)" in html(adm.get(reverse("kardex", args=[prod.pk])))


def test_historial_sin_movimientos_dice_cero(adm, prod):
    assert "Historial de movimientos (0)" in html(adm.get(reverse("kardex", args=[prod.pk])))


def test_el_boton_revertir_sigue_en_cada_linea(adm, admin_user, prod):
    e = servicios.registrar_entrada(prod, 5, admin_user)
    t = html(adm.get(reverse("kardex", args=[prod.pk])))
    assert f"/movimientos/{e.pk}/revertir/" in t


def test_stock_actual_destacado_en_el_kardex(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 5, admin_user)
    t = html(adm.get(reverse("kardex", args=[prod.pk])))
    assert 'class="cifra' in t and "5,00" in t and t.index("Stock actual") < t.index("<details")


# ------------------------------------------------------------------ crear producto -> kardex
def test_crear_producto_redirige_al_kardex_con_el_mensaje(adm):
    datos = {"sku": "NUEVO1", "nombre": "Producto nuevo", "categoria": "", "unidad": "unidad", "costo": "0",
             "precio_venta": "0", "precio_mayoreo": "0", "stock_min": "0", "stock_max": "0"}
    r = adm.post(reverse("producto_nuevo"), datos)
    p = Producto.objects.get(sku="NUEVO1")
    assert r.status_code == 302 and r.url == reverse("kardex", args=[p.pk])
    t = html(adm.get(r.url))
    assert "Producto creado. Si llegó con mercadería, registra la entrada." in t


def test_el_operador_tambien_cae_en_el_kardex_al_crear(op, operador):
    datos = {"sku": "NUEVO2", "nombre": "Otro", "categoria": "", "unidad": "unidad", "costo": "0",
             "precio_venta": "0", "precio_mayoreo": "0", "stock_min": "0", "stock_max": "0"}
    r = op.post(reverse("producto_nuevo"), datos)
    assert r.url == reverse("kardex", args=[Producto.objects.get(sku="NUEVO2").pk])


def test_menu_marca_productos_en_las_pantallas_de_movimientos(adm, prod):
    for nombre in ("entrada", "salida", "corregir"):
        t = html(adm.get(reverse(nombre, args=[prod.pk])))
        assert re.search(r'href="/productos/" aria-current="page">.*?<span>Productos</span>', t), nombre
