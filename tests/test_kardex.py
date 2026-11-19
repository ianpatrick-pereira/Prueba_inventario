"""Kardex y registro manual de salidas, ajustes, conteos y reversas (T-F1-07; D-44, D-45)."""
import datetime as dt
import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from inventario import servicios
from inventario.models import Documento, Movimiento, Producto
from inventario.templatetags.formato import formatear_numero

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
    return Producto.objects.create(sku="K-1", nombre="Producto kardex")


def html(r):
    return r.content.decode()


def token_de(r):
    return re.search(r'name="token" value="([^"]+)"', html(r)).group(1)


def enviar(client, nombre, prod, datos, token=None):
    """GET del formulario (para obtener el token) y POST."""
    url = reverse(nombre, args=[prod.pk])
    if token is None:
        token = token_de(client.get(url))
    return client.post(url, {"token": token, **datos})


def dia(dias_atras, hora=12):
    d = timezone.localdate() - dt.timedelta(days=dias_atras)
    return timezone.make_aware(dt.datetime.combine(d, dt.time(hora, 0)))


# ------------------------------------------------------------------ 1. kardex
def test_kardex_filas_en_orden_con_saldos_y_ultimo_saldo_igual_al_stock(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 10, admin_user, fecha=dia(1))     # insertado primero, fecha reciente
    servicios.registrar_entrada(prod, 5, admin_user, fecha=dia(3))      # fecha antigua
    servicios.registrar_salida(prod, 4, admin_user, fecha=dia(2))
    r = adm.get(reverse("kardex", args=[prod.pk]))
    filas = r.context["filas"]
    assert [f["m"].cantidad for f in filas] == [D("5"), D("-4"), D("10")]
    assert [f["saldo"] for f in filas] == [D("5"), D("1"), D("11")]
    assert filas[-1]["saldo"] == servicios.stock_de(prod) == r.context["stock"]
    assert "11,00" in html(r)


def test_kardex_filtro_por_fechas_con_saldo_inicial(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 100, admin_user, fecha=dia(10))
    servicios.registrar_salida(prod, 30, admin_user, fecha=dia(5, hora=0))   # medianoche exacta del dia 5
    servicios.registrar_salida(prod, 10, admin_user, fecha=dia(5, hora=23))
    servicios.registrar_entrada(prod, 7, admin_user, fecha=dia(2))
    url = reverse("kardex", args=[prod.pk])
    desde, hasta = dia(5).date().isoformat(), dia(5).date().isoformat()
    r = adm.get(url, {"desde": desde, "hasta": hasta})   # inclusivo por dia local
    assert [f["m"].cantidad for f in r.context["filas"]] == [D("-30"), D("-10")]
    assert r.context["saldo_inicial"] == D("100")
    assert [f["saldo"] for f in r.context["filas"]] == [D("70"), D("60")]
    r = adm.get(url, {"desde": dia(2).date().isoformat()})
    assert r.context["saldo_inicial"] == D("60") and [f["saldo"] for f in r.context["filas"]] == [D("67")]
    r = adm.get(url, {"hasta": dia(5).date().isoformat()})
    assert [f["saldo"] for f in r.context["filas"]] == [D("100"), D("70"), D("60")]
    assert r.context["saldo_inicial"] is None
    assert "Saldo inicial del período" in html(adm.get(url, {"desde": desde}))


def test_kardex_fecha_invalida_no_da_error(adm, prod):
    r = adm.get(reverse("kardex", args=[prod.pk]), {"desde": "basura"})
    assert r.status_code == 200 and "Fecha no válida" in html(r)


def _llenar(prod, usuario, n, desde=0):
    base = timezone.now() - dt.timedelta(days=400)
    Movimiento.objects.bulk_create([
        Movimiento(producto=prod, usuario=usuario, tipo="entrada", cantidad=D("1"),
                   fecha=base + dt.timedelta(minutes=i + desde))
        for i in range(n)
    ])


def test_kardex_pagina_de_a_100_con_saldos_continuos(adm, admin_user, prod):
    _llenar(prod, admin_user, 250)
    url = reverse("kardex", args=[prod.pk])
    saldos = []
    for pagina, esperadas in ((1, 100), (2, 100), (3, 50)):
        r = adm.get(url, {"page": pagina})
        assert len(r.context["filas"]) == esperadas
        saldos += [f["saldo"] for f in r.context["filas"]]
    assert saldos == [D(i) for i in range(1, 251)]
    assert saldos[-1] == servicios.stock_de(prod)


def test_kardex_paginado_con_filtro_mantiene_saldos(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 1000, admin_user, fecha=dia(300))
    _llenar(prod, admin_user, 130, desde=0)
    desde = (timezone.now() - dt.timedelta(days=401)).date().isoformat()
    r = adm.get(reverse("kardex", args=[prod.pk]), {"desde": desde, "page": 2})
    assert r.context["filas"][0]["saldo"] == D("101")  # 100 de la pagina 1 + 1


def _consultas(client, url):
    with CaptureQueriesContext(connection) as c:
        assert client.get(url).status_code == 200
    return len(c)


def test_kardex_consultas_acotadas(adm, admin_user, prod):
    url = reverse("kardex", args=[prod.pk])
    _llenar(prod, admin_user, 5)
    servicios.revertir(Movimiento.objects.first(), admin_user, "x", permitir_negativo=True)
    con_6 = _consultas(adm, url)
    _llenar(prod, admin_user, 145, desde=10)
    assert _consultas(adm, url) == con_6          # misma cantidad de consultas con 6 y con 151 movimientos
    assert len(adm.get(url).context["filas"]) == 100


# ------------------------------------------------------------------ 2. reversas enlazadas y boton
def test_reversa_y_revertido_enlazados_y_boton_revertir_solo_cuando_corresponde(adm, admin_user, prod):
    e1 = servicios.registrar_entrada(prod, 10, admin_user)
    e2 = servicios.registrar_entrada(prod, 3, admin_user)
    r1 = servicios.revertir(e1, admin_user, "error de digitacion")
    t = html(adm.get(reverse("kardex", args=[prod.pk])))
    assert f'href="#mov-{e1.pk}"' in t and f'href="#mov-{r1.pk}"' in t     # enlaces cruzados
    assert "Reversa del movimiento" in t and "Revertido por el movimiento" in t
    assert f"/movimientos/{e2.pk}/revertir/" in t                          # aun revertible
    assert f"/movimientos/{e1.pk}/revertir/" not in t                      # ya revertido
    assert f"/movimientos/{r1.pk}/revertir/" not in t                      # es reversa


def test_operador_no_ve_boton_revertir(op, admin_user, prod):
    servicios.registrar_entrada(prod, 10, admin_user)
    t = html(op.get(reverse("kardex", args=[prod.pk])))
    assert "/revertir/" not in t and "Revertir" not in t


# ------------------------------------------------------------------ 3. salida
def test_salida_registra_con_usuario_actual_y_fecha_ahora(op, operador, admin_user, prod):
    servicios.registrar_entrada(prod, 10, admin_user)
    antes = timezone.now()
    r = enviar(op, "salida", prod, {"cantidad": "2,5", "motivo": "venta mostrador"})
    assert r.status_code == 302 and r.url == reverse("kardex", args=[prod.pk])
    m = Movimiento.objects.filter(tipo="salida").get()
    assert m.cantidad == D("-2.50") and m.usuario == operador and m.motivo == "venta mostrador"
    assert antes <= m.fecha <= timezone.now()
    assert "Salida registrada" in html(op.get(r.url))


def test_salida_sin_motivo_es_valida(op, admin_user, prod):
    servicios.registrar_entrada(prod, 10, admin_user)
    enviar(op, "salida", prod, {"cantidad": "1", "motivo": ""})
    assert Movimiento.objects.filter(tipo="salida").count() == 1


def test_salida_mayor_al_stock_muestra_stock_actual_sin_movimiento(op, admin_user, prod):
    servicios.registrar_entrada(prod, 3, admin_user)
    r = enviar(op, "salida", prod, {"cantidad": "5", "motivo": ""})
    assert r.status_code == 200
    assert "Stock insuficiente: hay 3,00 y se pide 5,00." in html(r)
    assert Movimiento.objects.filter(tipo="salida").count() == 0
    # el formulario sigue utilizable: tiene un token nuevo y acepta la correccion
    assert token_de(r)
    r2 = op.post(reverse("salida", args=[prod.pk]), {"token": token_de(r), "cantidad": "3", "motivo": ""})
    assert r2.status_code == 302 and servicios.stock_de(prod) == D("0.00")


@pytest.mark.parametrize("cantidad", ["0", "-1", "abc", ""])
def test_salida_cantidad_invalida_es_error_de_campo_no_500(op, prod, cantidad):
    r = enviar(op, "salida", prod, {"cantidad": cantidad, "motivo": ""})
    assert r.status_code == 200 and "cantidad" in r.context["form"].errors


def test_sin_permiso_403_en_todas_las_pantallas_de_movimientos(client, prod, admin_user):
    e = servicios.registrar_entrada(prod, 5, admin_user)
    client.force_login(crear_usuario("sin"))
    for nombre, args in (("kardex", [prod.pk]), ("salida", [prod.pk]), ("ajuste", [prod.pk]),
                         ("conteo", [prod.pk]), ("movimiento_revertir", [e.pk])):
        url = reverse(nombre, args=args)
        assert client.get(url).status_code == 403, nombre
        assert client.post(url, {"token": "x", "cantidad": "1", "motivo": "m"}).status_code == 403, nombre


def test_anonimo_va_al_login(client, prod):
    for nombre in ("kardex", "salida", "ajuste", "conteo"):
        assert client.get(reverse(nombre, args=[prod.pk])).status_code == 302


# ------------------------------------------------------------------ 4. ajuste
def test_ajuste_exige_motivo(adm, prod):
    r = enviar(adm, "ajuste", prod, {"cantidad": "2", "motivo": ""})
    assert r.status_code == 200 and "motivo" in r.context["form"].errors
    r = enviar(adm, "ajuste", prod, {"cantidad": "2", "motivo": "   "})
    assert r.status_code == 200 and "motivo" in r.context["form"].errors
    assert Movimiento.objects.count() == 0


def test_ajuste_cero_rechazado(adm, prod):
    r = enviar(adm, "ajuste", prod, {"cantidad": "0", "motivo": "x"})
    assert r.status_code == 200 and "cantidad" in r.context["form"].errors


def test_operador_no_puede_ajustar_ni_por_post_directo(op, prod):
    url = reverse("ajuste", args=[prod.pk])
    assert op.get(url).status_code == 403
    assert op.post(url, {"token": "x", "cantidad": "5", "motivo": "m"}).status_code == 403
    assert Movimiento.objects.count() == 0
    t = html(op.get(reverse("producto_detalle", args=[prod.pk])))
    assert "/ajuste/" not in t and "/conteo/" not in t and "/salida/" in t


def test_administrador_ajusta_con_signo(adm, admin_user, prod):
    enviar(adm, "ajuste", prod, {"cantidad": "4", "motivo": "sobrante"})
    enviar(adm, "ajuste", prod, {"cantidad": "-1,5", "motivo": "merma"})
    assert servicios.stock_de(prod) == D("2.50")
    assert set(Movimiento.objects.values_list("usuario__username", flat=True)) == {"adm"}


def test_ajuste_que_deja_negativo_muestra_mensaje_sin_movimiento(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 2, admin_user)
    r = enviar(adm, "ajuste", prod, {"cantidad": "-5", "motivo": "merma"})
    assert r.status_code == 200 and "Stock insuficiente: hay 2,00 y se pide 5,00." in html(r)
    assert Movimiento.objects.filter(tipo="ajuste").count() == 0


# ------------------------------------------------------------------ 5. conteo
def test_conteo_muestra_stock_actual_y_diferencia(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 10, admin_user)
    assert "10,00" in html(adm.get(reverse("conteo", args=[prod.pk])))
    r = enviar(adm, "conteo", prod, {"cantidad": "12", "motivo": "conteo de enero"})
    assert servicios.stock_de(prod) == D("12.00")
    assert "diferencia aplicada de 2,00" in html(adm.get(r.url))
    r = enviar(adm, "conteo", prod, {"cantidad": "7,5", "motivo": "conteo"})
    assert servicios.stock_de(prod) == D("7.50")
    assert "diferencia aplicada de -4,50" in html(adm.get(r.url))


def test_conteo_sin_diferencia_no_crea_movimiento(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 10, admin_user)
    r = enviar(adm, "conteo", prod, {"cantidad": "10", "motivo": "conteo"})
    assert Movimiento.objects.count() == 1
    assert "Sin diferencia" in html(adm.get(r.url))


def test_conteo_exige_motivo_y_rechaza_negativo_y_operador_403(adm, prod, operador):
    assert "motivo" in enviar(adm, "conteo", prod, {"cantidad": "3", "motivo": ""}).context["form"].errors
    assert "cantidad" in enviar(adm, "conteo", prod, {"cantidad": "-1", "motivo": "x"}).context["form"].errors
    assert Movimiento.objects.count() == 0
    c2 = Client()
    c2.force_login(operador)
    assert c2.get(reverse("conteo", args=[prod.pk])).status_code == 403
    assert c2.post(reverse("conteo", args=[prod.pk]), {"token": "x", "cantidad": "1", "motivo": "m"}).status_code == 403


# ------------------------------------------------------------------ 6. revertir
def test_admin_revierte_y_el_stock_vuelve(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 10, admin_user)
    s = servicios.registrar_salida(prod, 4, admin_user)
    url = reverse("movimiento_revertir", args=[s.pk])
    assert servicios.stock_de(prod) == D("6.00")
    g = adm.get(url)
    assert g.status_code == 200 and "Movimiento n.º" in html(g)
    assert servicios.stock_de(prod) == D("6.00")  # el GET no revierte
    r = adm.post(url, {"token": token_de(g), "motivo": "salida por error"})
    assert r.status_code == 302 and r.url == reverse("kardex", args=[prod.pk])
    assert servicios.stock_de(prod) == D("10.00")
    rev = Movimiento.objects.get(revierte=s)
    assert rev.usuario == admin_user and rev.motivo == "salida por error"


def test_segunda_reversa_y_reversa_de_reversa_muestran_mensaje_sin_movimiento(adm, admin_user, prod):
    e = servicios.registrar_entrada(prod, 10, admin_user)
    r = servicios.revertir(e, admin_user, "primera")
    n = Movimiento.objects.count()
    for objetivo, texto in ((e, "ya fue revertido"), (r, "No se puede revertir una reversa")):
        url = reverse("movimiento_revertir", args=[objetivo.pk])
        resp = adm.post(url, {"token": token_de(adm.get(url)), "motivo": "otra vez"})
        assert resp.status_code == 200 and texto in html(resp)
    assert Movimiento.objects.count() == n


def test_reversa_que_deja_negativo_muestra_stock(adm, admin_user, prod):
    e = servicios.registrar_entrada(prod, 10, admin_user)
    servicios.registrar_salida(prod, 8, admin_user)
    url = reverse("movimiento_revertir", args=[e.pk])
    r = adm.post(url, {"token": token_de(adm.get(url)), "motivo": "x"})
    assert r.status_code == 200 and "Stock insuficiente: hay 2,00 y se pide 10,00." in html(r)


def test_operador_no_puede_revertir_ni_por_get_ni_por_post(op, admin_user, prod):
    e = servicios.registrar_entrada(prod, 10, admin_user)
    url = reverse("movimiento_revertir", args=[e.pk])
    assert op.get(url).status_code == 403
    assert op.post(url, {"token": "x", "motivo": "m"}).status_code == 403
    assert not Movimiento.objects.filter(revierte=e).exists()


def test_revertir_exige_motivo(adm, admin_user, prod):
    e = servicios.registrar_entrada(prod, 10, admin_user)
    url = reverse("movimiento_revertir", args=[e.pk])
    r = adm.post(url, {"token": token_de(adm.get(url)), "motivo": ""})
    assert r.status_code == 200 and "motivo" in r.context["form"].errors


# ------------------------------------------------------------------ 7. D-45 tokens de un solo uso
def _casos(prod, admin_user):
    servicios.registrar_entrada(prod, 50, admin_user)
    e = servicios.registrar_entrada(prod, 1, admin_user)
    return {
        "salida": (reverse("salida", args=[prod.pk]), {"cantidad": "1", "motivo": ""}, "salida"),
        "ajuste": (reverse("ajuste", args=[prod.pk]), {"cantidad": "1", "motivo": "m"}, "ajuste"),
        "conteo": (reverse("conteo", args=[prod.pk]), {"cantidad": "70", "motivo": "m"}, "ajuste"),
        "reversa": (reverse("movimiento_revertir", args=[e.pk]), {"motivo": "m"}, "ajuste"),
    }


@pytest.mark.parametrize("caso", ["salida", "ajuste", "conteo", "reversa"])
def test_mismo_token_dos_veces_registra_un_solo_movimiento(adm, admin_user, prod, caso):
    url, datos, tipo = _casos(prod, admin_user)[caso]
    n = Movimiento.objects.count()
    token = token_de(adm.get(url))
    r1 = adm.post(url, {"token": token, **datos})
    assert r1.status_code == 302
    assert Movimiento.objects.count() == n + 1
    r2 = adm.post(url, {"token": token, **datos}, follow=True)  # doble clic / recargar
    assert Movimiento.objects.count() == n + 1
    assert "no se registró nada nuevo" in html(r2)


@pytest.mark.parametrize("caso", ["salida", "ajuste", "conteo", "reversa"])
def test_token_inexistente_o_de_otra_sesion_se_rechaza(adm, admin_user, prod, caso):
    url, datos, _ = _casos(prod, admin_user)[caso]
    n = Movimiento.objects.count()
    r = adm.post(url, {"token": "inventado", **datos}, follow=True)
    assert "no se registró nada nuevo" in html(r) and Movimiento.objects.count() == n
    # token emitido a otra sesion (otro administrador)
    otro = Client()
    otro.force_login(crear_usuario("adm2", "Administrador"))
    ajeno = token_de(otro.get(url))
    adm.get(url)
    r = adm.post(url, {"token": ajeno, **datos}, follow=True)
    assert "no se registró nada nuevo" in html(r) and Movimiento.objects.count() == n


def test_token_de_otro_formulario_no_sirve(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 10, admin_user)
    token_salida = token_de(adm.get(reverse("salida", args=[prod.pk])))
    r = adm.post(reverse("ajuste", args=[prod.pk]), {"token": token_salida, "cantidad": "1", "motivo": "m"}, follow=True)
    assert "no se registró nada nuevo" in html(r) and Movimiento.objects.filter(tipo="ajuste").count() == 0


def test_formulario_invalido_conserva_el_token(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 10, admin_user)
    token = token_de(adm.get(reverse("salida", args=[prod.pk])))
    r = adm.post(reverse("salida", args=[prod.pk]), {"token": token, "cantidad": "", "motivo": ""})
    assert r.status_code == 200 and token_de(r) == token
    r = adm.post(reverse("salida", args=[prod.pk]), {"token": token, "cantidad": "1", "motivo": ""})
    assert r.status_code == 302


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_dos_envios_simultaneos_del_mismo_token_registran_uno():
    import threading

    from django.db import connection as con

    u = crear_usuario("hilos", "Administrador")
    p = Producto.objects.create(sku="TH-1", nombre="hilos")
    servicios.registrar_entrada(p, 50, u)
    c = Client()
    c.force_login(u)
    url = reverse("salida", args=[p.pk])
    token = token_de(c.get(url))
    cookie = c.cookies["sessionid"].value
    barrera = threading.Barrier(2)

    def enviar_hilo():
        try:
            cli = Client()
            cli.cookies["sessionid"] = cookie
            barrera.wait(timeout=10)
            cli.post(url, {"token": token, "cantidad": "1", "motivo": ""})
        finally:
            con.close()

    hilos = [threading.Thread(target=enviar_hilo) for _ in range(2)]
    [h.start() for h in hilos]
    [h.join(timeout=60) for h in hilos]
    assert Movimiento.objects.filter(tipo="salida").count() == 1


# ------------------------------------------------------------------ 8. D-44 fecha siempre ahora
def test_campo_fecha_enviado_por_post_se_ignora(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 20, admin_user)
    antes = timezone.now()
    enviar(adm, "salida", prod, {"cantidad": "1", "motivo": "", "fecha": "2020-01-01 10:00:00"})
    enviar(adm, "ajuste", prod, {"cantidad": "1", "motivo": "m", "fecha": "2020-01-01"})
    enviar(adm, "conteo", prod, {"cantidad": "5", "motivo": "m", "fecha": "2020-01-01"})
    for m in Movimiento.objects.exclude(tipo="entrada"):
        assert antes <= m.fecha <= timezone.now() and m.fecha.year >= 2026
    assert 'name="fecha"' not in html(adm.get(reverse("salida", args=[prod.pk])))


# ------------------------------------------------------------------ 9. nunca permitir_negativo
def test_ninguna_pantalla_pasa_permitir_negativo(adm, admin_user, prod, monkeypatch):
    llamadas = []
    for nombre in ("registrar_salida", "registrar_ajuste", "ajustar_a_conteo", "revertir"):
        real = getattr(servicios, nombre)

        def espia(*a, _real=real, _n=nombre, **k):
            llamadas.append((_n, k))
            return _real(*a, **k)

        monkeypatch.setattr(servicios, nombre, espia)
    e = servicios.registrar_entrada(prod, 20, admin_user)
    enviar(adm, "salida", prod, {"cantidad": "1", "motivo": ""})
    enviar(adm, "ajuste", prod, {"cantidad": "1", "motivo": "m"})
    enviar(adm, "conteo", prod, {"cantidad": "30", "motivo": "m"})
    url = reverse("movimiento_revertir", args=[e.pk])
    adm.post(url, {"token": token_de(adm.get(url)), "motivo": "m"})
    assert {n for n, _ in llamadas} == {"registrar_salida", "registrar_ajuste", "ajustar_a_conteo", "revertir"}
    assert all(not k.get("permitir_negativo") for _, k in llamadas)
    assert Movimiento.objects.filter(tipo="salida", usuario=admin_user).exists()


# ------------------------------------------------------------------ 10. formato es-CL
@pytest.mark.parametrize("valor,esperado", [
    (D("3500"), "3.500,00"), (D("-2.5"), "-2,50"), (0, "0,00"), (D("1234567.891"), "1.234.567,89"),
    (D("0.005"), "0,01"), (D("-0.001"), "0,00"), (None, ""), (D("999.999"), "1.000,00"),
])
def test_formato_es_cl(valor, esperado):
    assert formatear_numero(valor) == esperado


def test_ficha_y_lista_muestran_miles_con_punto_y_negativos(adm, admin_user):
    p = Producto.objects.create(sku="F1", nombre="Caro", costo=D("3500"), stock_min=D("2000"), stock_max=D("9000"))
    servicios.registrar_entrada(p, 3500, admin_user)
    ficha = html(adm.get(reverse("producto_detalle", args=[p.pk])))
    assert ficha.count("3.500,00") >= 2 and "2.000,00" in ficha and "9.000,00" in ficha
    assert "3.500,00" in html(adm.get(reverse("producto_lista")))
    servicios.registrar_salida(p, "2.5", admin_user)
    assert "-2,50" in html(adm.get(reverse("kardex", args=[p.pk])))


# ------------------------------------------------------------------ 11. CSRF y XSS
def test_csrf_en_todos_los_formularios(adm, admin_user, prod):
    e = servicios.registrar_entrada(prod, 5, admin_user)
    for nombre, args in (("salida", [prod.pk]), ("ajuste", [prod.pk]), ("conteo", [prod.pk]),
                         ("movimiento_revertir", [e.pk])):
        assert "csrfmiddlewaretoken" in html(adm.get(reverse(nombre, args=args))), nombre


def test_post_sin_csrf_es_rechazado(admin_user, prod):
    c = Client(enforce_csrf_checks=True)
    c.force_login(admin_user)
    assert c.post(reverse("salida", args=[prod.pk]), {"token": "x", "cantidad": "1"}).status_code == 403


def test_motivo_con_script_se_muestra_escapado_en_el_kardex(adm, admin_user, prod):
    servicios.registrar_entrada(prod, 5, admin_user)
    servicios.registrar_ajuste(prod, 1, admin_user, "<script>alert(1)</script>")
    t = html(adm.get(reverse("kardex", args=[prod.pk])))
    assert "<script>alert(1)" not in t and "&lt;script&gt;alert(1)&lt;/script&gt;" in t


def test_documento_y_usuario_en_el_kardex(adm, admin_user, prod):
    doc = Documento.objects.create(tipo="venta", numero="V-77")
    servicios.registrar_entrada(prod, 5, admin_user, documento=doc)
    t = html(adm.get(reverse("kardex", args=[prod.pk])))
    assert "V-77" in t and "adm" in t


def test_ficha_muestra_enlaces_segun_permisos(adm, prod, operador):
    t = html(adm.get(reverse("producto_detalle", args=[prod.pk])))
    for ruta in ("/kardex/", "/salida/", "/ajuste/", "/conteo/"):
        assert ruta in t
    c2 = Client()
    c2.force_login(operador)
    t2 = html(c2.get(reverse("producto_detalle", args=[prod.pk])))
    assert "/kardex/" in t2 and "/salida/" in t2 and "/ajuste/" not in t2
