"""Pruebas del servicio de stock (T-F1-04; decisiones D-17, D-31, D-32)."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
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
    return get_user_model().objects.create_user("operador", password="x-prueba-123")


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


def test_stock_con_hasta_excluye_lo_posterior(producto, usuario):
    ahora = timezone.now()
    entrada(producto, usuario, "10", fecha=ahora - timedelta(days=2))
    entrada(producto, usuario, "4", fecha=ahora - timedelta(days=1))
    entrada(producto, usuario, "100", fecha=ahora + timedelta(days=1))
    assert servicios.stock_de(producto, hasta=ahora - timedelta(days=1)) == D("14.00")  # inclusivo
    assert servicios.stock_de(producto, hasta=ahora - timedelta(days=3)) == D("0.00")
    assert servicios.stock_de(producto) == D("114.00")


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


# 9. atomicidad
def test_si_el_guardado_falla_no_queda_movimiento_a_medias(producto, usuario, monkeypatch):
    entrada(producto, usuario, "5")

    def falla(self, *a, **k):
        raise IntegrityError("simulado")

    monkeypatch.setattr(Movimiento, "save", falla)
    with pytest.raises(IntegrityError):
        servicios.registrar_salida(producto, "1", usuario)
    monkeypatch.undo()
    assert Movimiento.objects.count() == 1
    assert servicios.stock_de(producto) == D("5.00")


def test_error_de_validacion_dentro_de_transaccion_externa_no_deja_residuos(producto, usuario):
    with transaction.atomic():
        entrada(producto, usuario, "5")
        with pytest.raises(StockInsuficiente):
            servicios.registrar_salida(producto, "9", usuario)
    assert Movimiento.objects.count() == 1
    assert servicios.stock_de(producto) == D("5.00")


def test_escrituras_bloquean_el_producto(producto, usuario, monkeypatch):
    llamadas = []
    original = servicios._bloquear

    def espia(p):
        assert transaction.get_connection().in_atomic_block
        llamadas.append(p.pk)
        return original(p)

    monkeypatch.setattr(servicios, "_bloquear", espia)
    e = entrada(producto, usuario, "5")
    servicios.registrar_salida(producto, "1", usuario)
    servicios.registrar_ajuste(producto, "1", usuario, "m")
    servicios.ajustar_a_conteo(producto, "9", usuario, "m")
    servicios.revertir(e, usuario, "m", permitir_negativo=True)
    assert len(llamadas) == 5
