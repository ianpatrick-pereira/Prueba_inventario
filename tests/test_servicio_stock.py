"""Pruebas del servicio de stock (T-F1-04; decisiones D-17, D-31, D-32)."""
from datetime import datetime, time, timedelta
from datetime import timezone as dt_tz
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from inventario import servicios
from inventario.models import Movimiento, MovimientoInmutable, Producto
from inventario.servicios import StockInsuficiente

D = Decimal
pytestmark = pytest.mark.django_db


@pytest.fixture
def usuario():
    """Usuario del grupo Administrador (puede todas las operaciones de stock, D-38/D-39)."""
    u = get_user_model().objects.create_user("operador", password="x-prueba-123")
    u.groups.add(Group.objects.get(name="Administrador"))
    return u


@pytest.fixture
def producto():
    return Producto.objects.create(sku="S-1", nombre="Producto uno")


def entrada(p, u, c, **kw):
    return servicios.registrar_entrada(p, c, u, **kw)


# 1. stock = suma de movimientos
def test_producto_sin_movimientos_tiene_stock_cero(producto):
    assert servicios.stock_de(producto) == D("0.00")


def test_stock_es_la_suma_de_los_movimientos(producto, usuario):
    entrada(producto, usuario, "10")
    servicios.registrar_salida(producto, 3, usuario)
    servicios.registrar_ajuste(producto, D("-1.5"), usuario, "merma")
    assert servicios.stock_de(producto) == D("5.50")
    assert servicios.stock_de(producto) == sum(Movimiento.objects.values_list("cantidad", flat=True))


def test_stock_es_exacto_con_centavos(producto, usuario):
    for _ in range(10):
        entrada(producto, usuario, "0.10")
    assert servicios.stock_de(producto) == D("1.00")


def test_stock_con_hasta_datetime_excluye_lo_posterior(producto, usuario):
    ahora = timezone.now()
    entrada(producto, usuario, "10", fecha=ahora - timedelta(days=2))
    entrada(producto, usuario, "4", fecha=ahora - timedelta(days=1))
    entrada(producto, usuario, "100", fecha=ahora)
    assert servicios.stock_de(producto, hasta=ahora - timedelta(days=1)) == D("14.00")  # inclusivo
    assert servicios.stock_de(producto, hasta=ahora - timedelta(days=3)) == D("0.00")
    assert servicios.stock_de(producto) == D("114.00")


def test_hasta_datetime_con_zona_es_inclusivo_al_microsegundo(producto, usuario):
    t = timezone.localtime(timezone.now() - timedelta(days=1)).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )
    entrada(producto, usuario, "5", fecha=t)
    assert servicios.stock_de(producto, hasta=t) == D("5.00")
    assert servicios.stock_de(producto, hasta=t - timedelta(microseconds=1)) == D("0.00")


def test_hasta_date_incluye_todo_el_dia_local(producto, usuario):
    ayer = timezone.localdate() - timedelta(days=1)
    tarde = timezone.make_aware(datetime.combine(ayer, time(15, 0)))
    ultimo = timezone.make_aware(datetime.combine(ayer, time(23, 59, 59, 999999)))
    medianoche = timezone.make_aware(datetime.combine(ayer + timedelta(days=1), time(0, 0)))
    entrada(producto, usuario, "1", fecha=tarde)
    entrada(producto, usuario, "10", fecha=ultimo)
    entrada(producto, usuario, "100", fecha=medianoche)  # ya es el dia siguiente
    assert servicios.stock_de(producto, hasta=ayer) == D("11.00")
    assert servicios.stock_de(producto, hasta=ayer - timedelta(days=1)) == D("0.00")
    assert servicios.stock_de(producto, hasta=ayer + timedelta(days=1)) == D("111.00")


def test_hasta_date_de_hoy_incluye_un_movimiento_de_hoy(producto, usuario):
    entrada(producto, usuario, "5")  # fecha = ahora
    assert servicios.stock_de(producto, hasta=timezone.localdate()) == D("5.00")


def test_hasta_datetime_sin_zona_y_otros_tipos_rechazados(producto):
    with pytest.raises(ValidationError):
        servicios.stock_de(producto, hasta=datetime(2026, 1, 1, 12, 0))
    with pytest.raises(ValidationError):
        servicios.stock_de(producto, hasta="2026-01-01")


# 2. productos_con_stock
def test_productos_con_stock_coincide_y_usa_una_consulta(usuario, django_assert_num_queries):
    ps = [Producto.objects.create(sku=f"M-{i}", nombre=f"p{i}") for i in range(4)]
    entrada(ps[0], usuario, "10")
    servicios.registrar_salida(ps[0], "4", usuario)
    entrada(ps[1], usuario, "2.5")
    entrada(ps[2], usuario, "7")  # ps[3] queda sin movimientos
    esperado = {p.pk: servicios.stock_de(p) for p in ps}
    with django_assert_num_queries(1):
        obtenido = {p.pk: p.stock for p in servicios.productos_con_stock()}
    assert obtenido == esperado
    assert obtenido[ps[3].pk] == D("0")


def test_productos_con_stock_acepta_queryset(producto, usuario):
    entrada(producto, usuario, "3")
    Producto.objects.create(sku="OTRO", nombre="otro")
    r = list(servicios.productos_con_stock(Producto.objects.filter(sku="S-1")))
    assert len(r) == 1 and r[0].stock == D("3")


# 3. kardex
def test_kardex_orden_por_fecha_e_id_con_saldos(producto, usuario):
    ahora = timezone.now().replace(microsecond=0)
    m3 = entrada(producto, usuario, "10", fecha=ahora)                       # id 1, fecha mas reciente
    m1 = entrada(producto, usuario, "5", fecha=ahora - timedelta(days=2))    # id 2, la mas antigua
    m2a = servicios.registrar_salida(producto, "3", usuario, fecha=ahora - timedelta(days=1))
    m2b = servicios.registrar_ajuste(producto, D("-1"), usuario, "merma", fecha=ahora - timedelta(days=1))
    filas = servicios.kardex(producto)
    assert [f[0].pk for f in filas] == [m1.pk, m2a.pk, m2b.pk, m3.pk]
    assert [f[1] for f in filas] == [D("5"), D("2"), D("1"), D("11")]
    assert filas[-1][1] == servicios.stock_de(producto)


# 4. signos y validaciones
def test_signos_guardados(producto, usuario):
    e = entrada(producto, usuario, "5")
    s = servicios.registrar_salida(producto, "2", usuario)
    a1 = servicios.registrar_ajuste(producto, "1", usuario, "sobrante")
    a2 = servicios.registrar_ajuste(producto, "-1", usuario, "merma")
    assert (e.cantidad, s.cantidad, a1.cantidad, a2.cantidad) == (D("5"), D("-2"), D("1"), D("-1"))
    assert (e.tipo, s.tipo, a1.tipo) == ("entrada", "salida", "ajuste")


def test_entrada_guarda_costo_documento_y_usuario(producto, usuario):
    e = entrada(producto, usuario, "5", costo_unitario="12.50")
    e.refresh_from_db()
    assert e.costo_unitario == D("12.50") and e.usuario == usuario


@pytest.mark.parametrize("cantidad", ["0", "-1", 0, -3])
def test_entrada_y_salida_rechazan_cantidad_no_positiva(producto, usuario, cantidad):
    with pytest.raises(ValidationError) as e:
        servicios.registrar_entrada(producto, cantidad, usuario)
    assert "cantidad" in e.value.error_dict
    with pytest.raises(ValidationError) as e:
        servicios.registrar_salida(producto, cantidad, usuario)
    assert "cantidad" in e.value.error_dict
    assert Movimiento.objects.count() == 0


def test_ajuste_cero_rechazado(producto, usuario):
    with pytest.raises(ValidationError):
        servicios.registrar_ajuste(producto, 0, usuario, "nada")
    assert Movimiento.objects.count() == 0


@pytest.mark.parametrize("motivo", ["", "   ", "\t"])
def test_ajuste_sin_motivo_rechazado(producto, usuario, motivo):
    with pytest.raises(ValidationError) as e:
        servicios.registrar_ajuste(producto, 1, usuario, motivo)
    assert "motivo" in e.value.error_dict
    assert Movimiento.objects.count() == 0


# 5. stock negativo (D-31)
def test_salida_bajo_cero_rechazada_y_no_crea_movimiento(producto, usuario):
    entrada(producto, usuario, "5")
    with pytest.raises(StockInsuficiente) as e:
        servicios.registrar_salida(producto, "5.01", usuario)
    assert e.value.stock_actual == D("5.00") and e.value.cantidad_pedida == D("5.01")
    assert isinstance(e.value, ValidationError)
    assert Movimiento.objects.count() == 1


def test_ajuste_bajo_cero_rechazado(producto, usuario):
    with pytest.raises(StockInsuficiente):
        servicios.registrar_ajuste(producto, "-1", usuario, "merma")
    assert Movimiento.objects.count() == 0


def test_salida_que_deja_exactamente_cero_se_acepta(producto, usuario):
    entrada(producto, usuario, "5")
    servicios.registrar_salida(producto, "5", usuario)
    assert servicios.stock_de(producto) == D("0.00")


def test_permitir_negativo_acepta_salida_y_ajuste(producto, usuario):
    servicios.registrar_salida(producto, "3", usuario, permitir_negativo=True)
    servicios.registrar_ajuste(producto, "-2", usuario, "merma", permitir_negativo=True)
    assert servicios.stock_de(producto) == D("-5.00")


# 6. ajustar_a_conteo
def test_conteo_sube_baja_y_sin_diferencia(producto, usuario):
    entrada(producto, usuario, "10")
    a = servicios.ajustar_a_conteo(producto, "12", usuario, "conteo")
    assert a.cantidad == D("2") and servicios.stock_de(producto) == D("12.00")
    a = servicios.ajustar_a_conteo(producto, D("7.5"), usuario, "conteo")
    assert a.cantidad == D("-4.5") and servicios.stock_de(producto) == D("7.50")
    n = Movimiento.objects.count()
    assert servicios.ajustar_a_conteo(producto, "7.50", usuario, "conteo") is None
    assert Movimiento.objects.count() == n


def test_conteo_a_cero_y_negativo_rechazado(producto, usuario):
    entrada(producto, usuario, "4")
    servicios.ajustar_a_conteo(producto, 0, usuario, "vacio")
    assert servicios.stock_de(producto) == D("0.00")
    with pytest.raises(ValidationError):
        servicios.ajustar_a_conteo(producto, "-1", usuario, "mal")
    assert servicios.stock_de(producto) == D("0.00")


def test_conteo_sin_motivo_rechazado(producto, usuario):
    with pytest.raises(ValidationError):
        servicios.ajustar_a_conteo(producto, "3", usuario, "")
    assert Movimiento.objects.count() == 0


# 7. reversas (D-32)
def test_revertir_deja_el_stock_como_antes(producto, usuario):
    entrada(producto, usuario, "10")
    s = servicios.registrar_salida(producto, "4", usuario)
    r = servicios.revertir(s, usuario, "salida por error")
    assert r.tipo == "ajuste" and r.cantidad == D("4.00") and r.revierte == s
    assert s.reversa == r
    assert servicios.stock_de(producto) == D("10.00")


def test_revertir_una_entrada(producto, usuario):
    e = entrada(producto, usuario, "10")
    servicios.revertir(e, usuario, "entrada duplicada")
    assert servicios.stock_de(producto) == D("0.00")


def test_segunda_reversa_rechazada(producto, usuario):
    entrada(producto, usuario, "10")
    s = servicios.registrar_salida(producto, "4", usuario)
    servicios.revertir(s, usuario, "error")
    with pytest.raises(ValidationError, match="ya fue revertido"):
        servicios.revertir(s, usuario, "otra vez")
    assert Movimiento.objects.count() == 3


def test_base_impide_dos_reversas_del_mismo_movimiento(producto, usuario):
    e = entrada(producto, usuario, "10")
    servicios.revertir(e, usuario, "uno")
    with pytest.raises(IntegrityError), transaction.atomic():
        Movimiento.objects.bulk_create([
            Movimiento(producto=producto, usuario=usuario, tipo="ajuste", cantidad=D("-10"),
                       motivo="doble", revierte=e)
        ])


def test_revertir_una_reversa_rechazado(producto, usuario):
    e = entrada(producto, usuario, "10")
    r = servicios.revertir(e, usuario, "error")
    with pytest.raises(ValidationError, match="reversa"):
        servicios.revertir(r, usuario, "revertir la reversa")
    assert Movimiento.objects.count() == 2


def test_reversa_que_deja_negativo_rechazada_por_defecto(producto, usuario):
    e = entrada(producto, usuario, "10")
    servicios.registrar_salida(producto, "8", usuario)
    with pytest.raises(StockInsuficiente):
        servicios.revertir(e, usuario, "error")
    assert Movimiento.objects.filter(revierte__isnull=False).count() == 0
    r = servicios.revertir(e, usuario, "forzado", permitir_negativo=True)
    assert servicios.stock_de(producto) == D("-8.00")  # 10 - 8 - 10
    assert r.cantidad == D("-10.00")


def test_reversa_exige_motivo(producto, usuario):
    e = entrada(producto, usuario, "10")
    with pytest.raises(ValidationError) as ex:
        servicios.revertir(e, usuario, "  ")
    assert "motivo" in ex.value.error_dict


def test_el_original_sigue_inmutable(producto, usuario):
    e = entrada(producto, usuario, "10")
    servicios.revertir(e, usuario, "error")
    e.cantidad = D("1")
    with pytest.raises(MovimientoInmutable):
        e.save()


# 8. tipos de cantidad
@pytest.mark.parametrize("valor", [1.5, 1.0, True, False, None])
def test_float_bool_y_none_rechazados(producto, usuario, valor):
    with pytest.raises(ValidationError):
        servicios.registrar_entrada(producto, valor, usuario)
    with pytest.raises(ValidationError):
        servicios.registrar_salida(producto, valor, usuario)
    with pytest.raises(ValidationError):
        servicios.registrar_ajuste(producto, valor, usuario, "m")
    with pytest.raises(ValidationError):
        servicios.ajustar_a_conteo(producto, valor, usuario, "m")
    assert Movimiento.objects.count() == 0


def test_float_en_costo_unitario_rechazado(producto, usuario):
    with pytest.raises(ValidationError):
        servicios.registrar_entrada(producto, 1, usuario, costo_unitario=1.5)


@pytest.mark.parametrize("valor", ["1.25", D("1.25"), " 1.25 "])
def test_str_y_decimal_aceptados(producto, usuario, valor):
    assert servicios.registrar_entrada(producto, valor, usuario).cantidad == D("1.25")


def test_int_aceptado(producto, usuario):
    assert servicios.registrar_entrada(producto, 3, usuario).cantidad == D("3")


@pytest.mark.parametrize("valor", ["0.001", "1.005", "abc", "", "NaN", "1e30"])
def test_decimales_de_mas_y_basura_rechazados(producto, usuario, valor):
    with pytest.raises(ValidationError):
        servicios.registrar_entrada(producto, valor, usuario)
    assert Movimiento.objects.count() == 0


# 9. atomicidad y bloqueo
def test_si_falla_despues_de_escribir_no_queda_nada(producto, usuario, monkeypatch):
    entrada(producto, usuario, "5")
    original = Movimiento.save

    def guarda_y_luego_falla(self, *a, **k):
        original(self, *a, **k)  # la fila SI se escribe...
        assert Movimiento.objects.filter(pk=self.pk).exists()
        raise IntegrityError("simulado despues de escribir")

    monkeypatch.setattr(Movimiento, "save", guarda_y_luego_falla)
    with pytest.raises(IntegrityError):
        servicios.registrar_salida(producto, "1", usuario)
    with pytest.raises(IntegrityError):
        servicios.ajustar_a_conteo(producto, "9", usuario, "conteo")
    monkeypatch.undo()
    assert Movimiento.objects.count() == 1  # ...pero la transaccion la deshizo
    assert servicios.stock_de(producto) == D("5.00")


def test_error_de_validacion_dentro_de_transaccion_externa_no_deja_residuos(producto, usuario):
    with transaction.atomic():
        entrada(producto, usuario, "5")
        with pytest.raises(StockInsuficiente):
            servicios.registrar_salida(producto, "9", usuario)
    assert Movimiento.objects.count() == 1
    assert servicios.stock_de(producto) == D("5.00")


def test_se_bloquea_el_producto_antes_de_leer_el_stock(producto, usuario, monkeypatch):
    eventos = []
    bloquear, stock = servicios._bloquear, servicios.stock_de

    def espia_bloquear(p):
        assert transaction.get_connection().in_atomic_block
        eventos.append("bloquear")
        return bloquear(p)

    def espia_stock(*a, **k):
        eventos.append("stock")
        return stock(*a, **k)

    monkeypatch.setattr(servicios, "_bloquear", espia_bloquear)
    monkeypatch.setattr(servicios, "stock_de", espia_stock)
    e = entrada(producto, usuario, "5")
    assert eventos == ["bloquear"]
    entrada(producto, usuario, "50")
    for operacion in (
        lambda: servicios.registrar_salida(producto, "1", usuario),
        lambda: servicios.registrar_ajuste(producto, "-1", usuario, "m"),
        lambda: servicios.ajustar_a_conteo(producto, "9", usuario, "m"),
        lambda: servicios.revertir(e, usuario, "m"),
    ):
        eventos.clear()
        operacion()
        assert eventos[0] == "bloquear" and "stock" in eventos, eventos


def test_sqlite_configurada_con_transacciones_immediate():
    from django.conf import settings

    assert settings.DATABASES["default"]["OPTIONS"]["transaction_mode"] == "IMMEDIATE"
    assert settings.DATABASES["default"]["OPTIONS"]["timeout"] >= 10


@pytest.mark.django_db(transaction=True)
def test_dos_salidas_simultaneas_una_pasa_y_la_otra_es_stock_insuficiente(monkeypatch):
    import threading
    import time

    from django.db import connection

    u = get_user_model().objects.create_user("hilos", password="x-prueba-123")
    u.groups.add(Group.objects.get(name="Administrador"))
    p = Producto.objects.create(sku="H-1", nombre="hilos")
    servicios.registrar_entrada(p, 10, u)
    barrera = threading.Barrier(2)
    resultados = []
    stock_real = servicios.stock_de

    def stock_lento(*a, **k):
        valor = stock_real(*a, **k)
        time.sleep(0.4)  # espera DENTRO de la seccion critica, justo despues de leer el stock
        return valor

    monkeypatch.setattr(servicios, "stock_de", stock_lento)

    def salida():
        try:
            barrera.wait(timeout=10)
            servicios.registrar_salida(Producto.objects.get(pk=p.pk), 10, u)
            resultados.append("ok")
        except StockInsuficiente:
            resultados.append("insuficiente")
        except Exception as exc:  # noqa: BLE001 - se informa en la asercion
            resultados.append(repr(exc))
        finally:
            connection.close()

    hilos = [threading.Thread(target=salida) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=60)
    assert sorted(resultados) == ["insuficiente", "ok"], resultados
    monkeypatch.undo()
    assert servicios.stock_de(p) == D("0.00")


# 10. productos_con_stock no depende de los joins del queryset recibido
@pytest.fixture
def catalogo(usuario):
    from inventario.models import CodigoAlternativo, ProductoProveedor, Proveedor

    pa, pb = Proveedor.objects.create(nombre="A"), Proveedor.objects.create(nombre="B")
    p1 = Producto.objects.create(sku="K-1", nombre="uno")
    p2 = Producto.objects.create(sku="K-2", nombre="dos")
    p3 = Producto.objects.create(sku="K-3", nombre="tres")
    for prov in (pa, pb):  # p1 con los dos proveedores (duplicaria filas en el join)
        ProductoProveedor.objects.create(producto=p1, proveedor=prov, codigo_proveedor="X1")
    ProductoProveedor.objects.create(producto=p2, proveedor=pa, codigo_proveedor="X2")
    for v in ("770001", "770002"):
        CodigoAlternativo.objects.create(producto=p1, tipo="barras", valor=v)
    CodigoAlternativo.objects.create(producto=p2, tipo="barras", valor="770003")
    entrada(p1, usuario, "10")
    servicios.registrar_salida(p1, "3", usuario)
    entrada(p2, usuario, "5")
    return pa, pb, p1, p2, p3


def _real(qs):
    return {p.pk: (p.stock, servicios.stock_de(p)) for p in qs}


def _comprobar(qs, esperados, django_assert_num_queries):
    with django_assert_num_queries(1):
        filas = list(qs)
    assert {p.pk for p in filas} == {p.pk for p in esperados}
    for p in filas:
        assert p.stock == servicios.stock_de(p)
        assert isinstance(p.stock, D) and p.stock == p.stock.quantize(D("0.01"))


def test_stock_con_filtro_por_dos_proveedores(catalogo, django_assert_num_queries):
    pa, pb, p1, p2, _ = catalogo
    qs = servicios.productos_con_stock(
        Producto.objects.filter(productoproveedor__proveedor__in=[pa, pb]).distinct()
    )
    _comprobar(qs, [p1, p2], django_assert_num_queries)
    assert {p.pk: p.stock for p in qs}[p1.pk] == D("7.00")


def test_stock_con_filtro_por_proveedores_sin_distinct(catalogo):
    pa, pb, p1, p2, _ = catalogo
    qs = servicios.productos_con_stock(Producto.objects.filter(productoproveedor__proveedor__in=[pa, pb]))
    for p in qs:  # p1 aparece repetido por el join, pero con el stock real cada vez
        assert p.stock == servicios.stock_de(p)


def test_stock_con_filtro_por_codigo_alternativo(catalogo, django_assert_num_queries):
    _, _, p1, p2, _ = catalogo
    qs = servicios.productos_con_stock(
        Producto.objects.filter(codigoalternativo__valor__startswith="77").distinct()
    )
    _comprobar(qs, [p1, p2], django_assert_num_queries)


def test_stock_con_filtro_por_tipo_de_movimiento(catalogo, django_assert_num_queries):
    _, _, p1, p2, _ = catalogo
    qs = servicios.productos_con_stock(Producto.objects.filter(movimiento__tipo="entrada").distinct())
    _comprobar(qs, [p1, p2], django_assert_num_queries)
    assert {p.pk: p.stock for p in qs}[p1.pk] == D("7.00")  # no solo las entradas


def test_stock_con_distinct_y_con_annotate_previo(catalogo, django_assert_num_queries):
    from django.db.models import Count

    _, _, p1, p2, p3 = catalogo
    qs = servicios.productos_con_stock(Producto.objects.annotate(n=Count("movimiento")))
    _comprobar(qs, [p1, p2, p3], django_assert_num_queries)
    assert {p.pk: p.n for p in qs}[p1.pk] == 2
    _comprobar(servicios.productos_con_stock().distinct(), [p1, p2, p3], django_assert_num_queries)


# 11. fecha futura (D-33)
@pytest.mark.parametrize("operacion", ["entrada", "salida", "ajuste"])
def test_fecha_futura_rechazada_en_el_servicio(producto, usuario, operacion):
    entrada(producto, usuario, "50")
    futura = timezone.now() + timedelta(hours=1)
    with pytest.raises(ValidationError) as e:
        if operacion == "entrada":
            servicios.registrar_entrada(producto, 1, usuario, fecha=futura)
        elif operacion == "salida":
            servicios.registrar_salida(producto, 1, usuario, fecha=futura)
        else:
            servicios.registrar_ajuste(producto, 1, usuario, "m", fecha=futura)
    assert "fecha" in e.value.error_dict
    assert Movimiento.objects.count() == 1


def test_fecha_futura_rechazada_en_bulk_create_y_save(producto, usuario):
    futura = timezone.now() + timedelta(days=1)
    m = Movimiento(producto=producto, usuario=usuario, tipo="entrada", cantidad=D("1"), fecha=futura)
    with pytest.raises(ValidationError) as e:
        Movimiento.objects.bulk_create([m])
    assert "fecha" in e.value.error_dict
    with pytest.raises(ValidationError):
        m.save()
    assert Movimiento.objects.count() == 0


def test_fecha_dentro_de_la_tolerancia_se_acepta(producto, usuario):
    entrada(producto, usuario, "1", fecha=timezone.now() + timedelta(minutes=2))
    with pytest.raises(ValidationError):
        entrada(producto, usuario, "1", fecha=timezone.now() + timedelta(minutes=6))
    assert Movimiento.objects.count() == 1


# 12. integridad de `revierte`
def _reversa(producto, usuario, original, **cambios):
    datos = dict(producto=producto, usuario=usuario, tipo="ajuste", cantidad=-original.cantidad,
                 motivo="reversa", revierte=original)
    datos.update(cambios)
    return Movimiento(**datos)


@pytest.fixture
def original(producto, usuario):
    return entrada(producto, usuario, "10")


def _casos_invalidos(producto, usuario, original):
    otro = Producto.objects.create(sku="OTRO-R", nombre="otro")
    ya_revertido = servicios.revertir(original, usuario, "primera")  # es una reversa
    return {
        "reversa_de_reversa": _reversa(producto, usuario, ya_revertido),
        "tipo_entrada": _reversa(producto, usuario, original, tipo="entrada", cantidad=D("10")),
        "otro_producto": _reversa(otro, usuario, original),
        "cantidad_distinta": _reversa(producto, usuario, original, cantidad=D("-9")),
        "misma_cantidad_signo_igual": _reversa(producto, usuario, original, cantidad=D("10")),
        "autorreferencia": Movimiento(pk=987, producto=producto, usuario=usuario, tipo="ajuste",
                                      cantidad=D("1"), motivo="x", revierte_id=987),
        "inexistente": Movimiento(producto=producto, usuario=usuario, tipo="ajuste",
                                  cantidad=D("1"), motivo="x", revierte_id=424242),
    }


CASOS = ["reversa_de_reversa", "tipo_entrada", "otro_producto", "cantidad_distinta",
         "misma_cantidad_signo_igual", "autorreferencia", "inexistente"]


@pytest.mark.parametrize("caso", CASOS)
def test_reversa_invalida_rechazada_por_save(producto, usuario, original, caso):
    m = _casos_invalidos(producto, usuario, original)[caso]
    n = Movimiento.objects.count()
    with pytest.raises(ValidationError):
        m.save()
    assert Movimiento.objects.count() == n


@pytest.mark.parametrize("caso", CASOS)
def test_reversa_invalida_rechazada_por_bulk_create(producto, usuario, original, caso):
    m = _casos_invalidos(producto, usuario, original)[caso]
    n = Movimiento.objects.count()
    with pytest.raises(ValidationError):
        Movimiento.objects.bulk_create([m])
    assert Movimiento.objects.count() == n


@pytest.mark.parametrize("caso", ["reversa_de_reversa", "tipo_entrada", "otro_producto", "cantidad_distinta"])
def test_reversa_invalida_rechazada_por_objects_create(producto, usuario, original, caso):
    m = _casos_invalidos(producto, usuario, original)[caso]
    with pytest.raises(ValidationError):
        Movimiento.objects.create(
            producto=m.producto, usuario=usuario, tipo=m.tipo, cantidad=m.cantidad,
            motivo=m.motivo, revierte=m.revierte,
        )


def test_reversa_con_tipo_entrada_rechazada_por_la_base(producto, usuario, original):
    from django.db import connection

    with pytest.raises(IntegrityError) as e, transaction.atomic(), connection.cursor() as c:
        c.execute(
            "INSERT INTO inventario_movimiento"
            " (producto_id, usuario_id, fecha, tipo, cantidad, motivo, revierte_id)"
            " VALUES (%s, %s, '2026-01-01 00:00:00', 'entrada', 10, '', %s)",
            [producto.pk, usuario.pk, original.pk],
        )
    assert "movimiento_revierte_solo_ajuste" in str(e.value)


def test_reversa_valida_se_acepta(producto, usuario, original):
    r = _reversa(producto, usuario, original)
    r.save()
    assert original.reversa == r


# 13. la reversa hereda el documento (D-32)
def test_reversa_hereda_el_documento_del_original(producto, usuario):
    from inventario.models import Documento

    doc = Documento.objects.create(tipo="venta", numero="T-1")
    s = servicios.registrar_entrada(producto, 5, usuario, documento=doc)
    r = servicios.revertir(s, usuario, "error")
    assert r.documento == doc


# 14. _a_decimal estricto
@pytest.mark.parametrize("valor", [(0, (1, 5), -1), [1], "1e3", "1E-2", "١٢", "１２", "1.", ".5",
                                   "1,5", "--1", "0x10", "Infinity", "NaN", b"1", {"a": 1}, 1 + 2j])
def test_a_decimal_rechaza_tipos_y_formatos_raros(valor):
    with pytest.raises(ValidationError):
        servicios._a_decimal(valor)


@pytest.mark.parametrize("valor,esperado", [("1.25", D("1.25")), ("+3", D("3")), ("-2.5", D("-2.5")),
                                            (" 7 ", D("7")), (4, D("4")), (D("1.5"), D("1.5")), ("0077", D("77"))])
def test_a_decimal_acepta_lo_permitido(valor, esperado):
    assert servicios._a_decimal(valor) == esperado


def test_stock_insuficiente_no_tiene_error_dict_pero_si_messages(producto, usuario):
    with pytest.raises(StockInsuficiente) as e:
        servicios.registrar_salida(producto, 1, usuario)
    assert not hasattr(e.value, "error_dict")
    assert "Stock insuficiente" in e.value.messages[0]


# R1. fecha sin zona horaria o en texto (complemento de D-33)
def test_fecha_sin_zona_rechazada_por_el_servicio_y_por_create(producto, usuario):
    ingenua = datetime(2026, 1, 1, 12, 0)
    with pytest.raises(ValidationError) as e:
        servicios.registrar_entrada(producto, 1, usuario, fecha=ingenua)
    assert "fecha" in e.value.error_dict
    with pytest.raises(ValidationError) as e:
        Movimiento.objects.create(producto=producto, usuario=usuario, tipo="entrada",
                                  cantidad=D("1"), fecha=ingenua)
    assert "fecha" in e.value.error_dict
    with pytest.raises(ValidationError):
        Movimiento.objects.create(producto=producto, usuario=usuario, tipo="entrada",
                                  cantidad=D("1"), fecha="2026-01-01T12:00:00")
    with pytest.raises(ValidationError):
        Movimiento.objects.create(producto=producto, usuario=usuario, tipo="entrada",
                                  cantidad=D("1"), fecha="no es una fecha")
    assert Movimiento.objects.count() == 0


def test_fecha_en_texto_con_zona_se_acepta(producto, usuario):
    m = Movimiento.objects.create(producto=producto, usuario=usuario, tipo="entrada",
                                  cantidad=D("1"), fecha="2026-01-01T12:00:00Z")
    m.refresh_from_db()
    assert m.fecha == datetime(2026, 1, 1, 12, 0, tzinfo=dt_tz.utc)


@pytest.mark.parametrize("fecha,aceptada", [("2026-01-01T12:00:00", False), ("2026-01-01T12:00:00Z", True)])
def test_carga_cruda_con_y_sin_zona(producto, usuario, fecha, aceptada):
    from django.core import serializers

    m = Movimiento(pk=900, producto=producto, usuario=usuario, tipo="entrada", cantidad=D("2"),
                   fecha=datetime(2026, 1, 1, 12, 0, tzinfo=dt_tz.utc))
    datos = serializers.serialize("json", [m]).replace("2026-01-01T12:00:00Z", fecha)
    assert fecha in datos
    if aceptada:
        for obj in serializers.deserialize("json", datos):
            obj.save()
        assert Movimiento.objects.get(pk=900).cantidad == D("2.00")
    else:
        with pytest.raises(ValidationError) as e:
            for obj in serializers.deserialize("json", datos):
                obj.save()
        assert "fecha" in e.value.error_dict
        assert Movimiento.objects.count() == 0


# O1. la reversa debe tener el mismo documento que el original
def test_reversa_con_documento_distinto_rechazada(producto, usuario):
    from inventario.models import Documento

    d1 = Documento.objects.create(tipo="venta", numero="D-1")
    d2 = Documento.objects.create(tipo="venta", numero="D-2")
    original = servicios.registrar_entrada(producto, 5, usuario, documento=d1)
    for doc in (d2, None):
        with pytest.raises(ValidationError) as e:
            Movimiento.objects.create(producto=producto, usuario=usuario, tipo="ajuste",
                                      cantidad=D("-5"), motivo="x", revierte=original, documento=doc)
        assert "documento" in e.value.error_dict
    ok = Movimiento.objects.create(producto=producto, usuario=usuario, tipo="ajuste",
                                   cantidad=D("-5"), motivo="x", revierte=original, documento=d1)
    assert ok.revierte == original
