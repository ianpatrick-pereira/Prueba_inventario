"""Pruebas de los modelos del inventario (T-F1-03, decisión D-17)."""
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from inventario.models import (
    CodigoAlternativo,
    Documento,
    Movimiento,
    MovimientoInmutable,
    Producto,
    ProductoProveedor,
    Proveedor,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def usuario():
    return get_user_model().objects.create_user("operador", password="x-prueba-123")


@pytest.fixture
def producto():
    return Producto.objects.create(sku="P-001", nombre="Producto de prueba")


@pytest.fixture
def prov_a():
    return Proveedor.objects.create(nombre="Proveedor A")


@pytest.fixture
def prov_b():
    return Proveedor.objects.create(nombre="Proveedor B")


def mov(producto, usuario, tipo, cantidad, motivo=""):
    return Movimiento(producto=producto, usuario=usuario, tipo=tipo, cantidad=Decimal(cantidad), motivo=motivo)


def test_sku_unico(producto):
    with pytest.raises(IntegrityError), transaction.atomic():
        Producto.objects.create(sku="P-001", nombre="Otro")


def test_codigo_alternativo_unico(producto):
    otro = Producto.objects.create(sku="P-002", nombre="Otro")
    CodigoAlternativo.objects.create(producto=producto, tipo="barras", valor="7800001")
    with pytest.raises(IntegrityError), transaction.atomic():
        CodigoAlternativo.objects.create(producto=otro, tipo="fabricante", valor="7800001")


def test_codigo_proveedor_unico_dentro_del_proveedor(producto, prov_a):
    otro = Producto.objects.create(sku="P-002", nombre="Otro")
    ProductoProveedor.objects.create(producto=producto, proveedor=prov_a, codigo_proveedor="X1")
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductoProveedor.objects.create(producto=otro, proveedor=prov_a, codigo_proveedor="X1")


def test_mismo_codigo_en_dos_proveedores_se_permite(producto, prov_a, prov_b):
    otro = Producto.objects.create(sku="P-002", nombre="Otro")
    ProductoProveedor.objects.create(producto=producto, proveedor=prov_a, codigo_proveedor="X1")
    ProductoProveedor.objects.create(producto=otro, proveedor=prov_b, codigo_proveedor="X1")
    assert ProductoProveedor.objects.filter(codigo_proveedor="X1").count() == 2


def test_producto_con_dos_proveedores(producto, prov_a, prov_b):
    ProductoProveedor.objects.create(producto=producto, proveedor=prov_a, codigo_proveedor="A-1")
    ProductoProveedor.objects.create(producto=producto, proveedor=prov_b, codigo_proveedor="B-9")
    assert producto.productoproveedor_set.count() == 2


def test_stock_max_menor_que_min_rechazado_por_la_base():
    with pytest.raises(IntegrityError), transaction.atomic():
        Producto.objects.create(sku="P-9", nombre="x", stock_min=Decimal("10"), stock_max=Decimal("5"))


def test_stock_max_menor_que_min_rechazado_por_validacion():
    p = Producto(sku="P-9", nombre="x", stock_min=Decimal("10"), stock_max=Decimal("5"))
    with pytest.raises(ValidationError):
        p.full_clean()


@pytest.mark.parametrize("valor", ["-0.01", "100.01", "150"])
def test_descuento_fuera_de_rango_rechazado(valor):
    with pytest.raises(IntegrityError), transaction.atomic():
        Documento.objects.create(tipo="venta", descuento_global=Decimal(valor))


@pytest.mark.parametrize("valor", ["0", "100", "12.5"])
def test_descuento_en_rango_aceptado(valor):
    Documento.objects.create(tipo="venta", descuento_global=Decimal(valor))


def test_factura_repetida_rechazada(prov_a):
    Documento.objects.create(tipo="factura", proveedor=prov_a, numero="1001")
    with pytest.raises(IntegrityError), transaction.atomic():
        Documento.objects.create(tipo="factura", proveedor=prov_a, numero="1001")


def test_factura_mismo_numero_otro_proveedor_se_permite(prov_a, prov_b):
    Documento.objects.create(tipo="factura", proveedor=prov_a, numero="1001")
    Documento.objects.create(tipo="factura", proveedor=prov_b, numero="1001")


def test_otro_tipo_con_mismo_numero_se_permite(prov_a):
    Documento.objects.create(tipo="factura", proveedor=prov_a, numero="1001")
    Documento.objects.create(tipo="conteo", proveedor=prov_a, numero="1001")
    Documento.objects.create(tipo="conteo", proveedor=prov_a, numero="1001")


@pytest.mark.parametrize("cantidad", ["0", "-1"])
def test_entrada_no_positiva_rechazada(producto, usuario, cantidad):
    with pytest.raises(IntegrityError), transaction.atomic():
        mov(producto, usuario, "entrada", cantidad).save()


@pytest.mark.parametrize("cantidad", ["0", "1"])
def test_salida_no_negativa_rechazada(producto, usuario, cantidad):
    with pytest.raises(IntegrityError), transaction.atomic():
        mov(producto, usuario, "salida", cantidad).save()


def test_movimientos_validos_se_guardan(producto, usuario):
    mov(producto, usuario, "entrada", "5").save()
    mov(producto, usuario, "salida", "-2").save()
    mov(producto, usuario, "ajuste", "-1", "merma").save()
    assert Movimiento.objects.count() == 3


def test_ajuste_sin_motivo_rechazado_por_la_base_de_datos(producto, usuario):
    # bulk_create esquiva save() y full_clean(): solo la restriccion de la base puede frenarlo.
    with pytest.raises(IntegrityError), transaction.atomic():
        Movimiento.objects.bulk_create([mov(producto, usuario, "ajuste", "3", "")])
    assert Movimiento.objects.count() == 0


def test_ajuste_con_cantidad_cero_rechazado_por_la_base_de_datos(producto, usuario):
    with pytest.raises(IntegrityError), transaction.atomic():
        Movimiento.objects.bulk_create([mov(producto, usuario, "ajuste", "0", "conteo")])


def test_ajuste_sin_motivo_rechazado_por_validacion(producto, usuario):
    with pytest.raises(ValidationError):
        mov(producto, usuario, "ajuste", "3", "   ").full_clean()


def test_modificar_movimiento_guardado_rechazado(producto, usuario):
    m = mov(producto, usuario, "entrada", "5")
    m.save()
    m.cantidad = Decimal("99")
    with pytest.raises(MovimientoInmutable):
        m.save()
    assert Movimiento.objects.get(pk=m.pk).cantidad == Decimal("5.00")


def test_modificar_movimiento_por_queryset_rechazado(producto, usuario):
    m = mov(producto, usuario, "entrada", "5")
    m.save()
    with pytest.raises(MovimientoInmutable):
        Movimiento.objects.filter(pk=m.pk).update(cantidad=Decimal("99"))
    with pytest.raises(MovimientoInmutable):
        Movimiento.objects.bulk_update([m], ["cantidad"])
    assert Movimiento.objects.get(pk=m.pk).cantidad == Decimal("5.00")


def test_borrar_movimiento_objeto_rechazado(producto, usuario):
    m = mov(producto, usuario, "entrada", "5")
    m.save()
    with pytest.raises(MovimientoInmutable):
        m.delete()
    assert Movimiento.objects.filter(pk=m.pk).exists()


def test_borrar_movimientos_queryset_rechazado(producto, usuario):
    mov(producto, usuario, "entrada", "5").save()
    with pytest.raises(MovimientoInmutable):
        Movimiento.objects.all().delete()
    with pytest.raises(MovimientoInmutable):
        producto.movimiento_set.all().delete()
    assert Movimiento.objects.count() == 1


def test_borrar_producto_con_movimientos_rechazado(producto, usuario):
    from django.db.models import ProtectedError

    mov(producto, usuario, "entrada", "5").save()
    with pytest.raises(ProtectedError):
        producto.delete()
    assert Movimiento.objects.count() == 1


def test_borrar_usuario_y_documento_con_movimientos_rechazado(producto, usuario, prov_a):
    from django.db.models import ProtectedError

    doc = Documento.objects.create(tipo="factura", proveedor=prov_a, numero="7")
    m = mov(producto, usuario, "entrada", "5")
    m.documento = doc
    m.save()
    with pytest.raises(ProtectedError):
        usuario.delete()
    with pytest.raises(ProtectedError):
        doc.delete()


def test_codigo_0077_se_guarda_como_texto():
    p = Producto.objects.create(sku="0077", nombre="Codigo con ceros")
    CodigoAlternativo.objects.create(producto=p, tipo="barras", valor="0077")
    ProductoProveedor.objects.create(
        producto=p, proveedor=Proveedor.objects.create(nombre="P"), codigo_proveedor="0077"
    )
    assert Producto.objects.get(pk=p.pk).sku == "0077"
    assert CodigoAlternativo.objects.get(producto=p).valor == "0077"
    assert ProductoProveedor.objects.get(producto=p).codigo_proveedor == "0077"
    assert Producto.objects.filter(sku="0077").exists()
    assert not Producto.objects.filter(sku="77").exists()


def test_producto_no_tiene_columna_de_stock():
    nombres = {f.name for f in Producto._meta.get_fields()}
    assert not {"stock", "stock_actual", "cantidad"} & nombres


def test_proveedor_sin_rut_multiple_y_rut_unico():
    Proveedor.objects.create(nombre="A", rut="")
    Proveedor.objects.create(nombre="B", rut="")
    Proveedor.objects.create(nombre="C", rut="11.111.111-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        Proveedor.objects.create(nombre="D", rut="11.111.111-1")


def test_documento_acepta_fecha_y_str():
    d = Documento.objects.create(tipo="venta", nombre="Ticket 5", fecha=date(2026, 1, 2))
    assert "Venta" in str(d)
