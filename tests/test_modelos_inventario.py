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


# ---------------------------------------------------------------------------
# Correcciones de la auditoria de T-F1-03
# ---------------------------------------------------------------------------
from django.core import serializers  # noqa: E402
from django.contrib import admin as django_admin  # noqa: E402
from django.db.models import ProtectedError  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from inventario.admin import MovimientoAdmin  # noqa: E402
from inventario.models import Categoria  # noqa: E402


def _constraint(excinfo, nombre):
    assert nombre in str(excinfo.value), str(excinfo.value)


# 1. save() siempre INSERT
def test_save_con_pk_existente_falla_y_no_cambia_la_fila(producto, usuario):
    original = mov(producto, usuario, "entrada", "5")
    original.save()
    nuevo = mov(producto, usuario, "entrada", "99")
    nuevo.pk = original.pk
    with pytest.raises(IntegrityError), transaction.atomic():
        nuevo.save()
    assert Movimiento.objects.get(pk=original.pk).cantidad == Decimal("5.00")
    assert Movimiento.objects.count() == 1


# 2. tipo valido en la base
@pytest.mark.parametrize("tipo", ["ENTRADA", "", "otro"])
def test_movimiento_tipo_invalido_rechazado_por_la_base(producto, usuario, tipo):
    with pytest.raises(IntegrityError) as e, transaction.atomic():
        Movimiento.objects.bulk_create([mov(producto, usuario, tipo, "5", "m")])
    _constraint(e, "movimiento_tipo_valido")


@pytest.mark.parametrize("tipo", ["FACTURA", "", "otro"])
def test_documento_tipo_invalido_rechazado_por_la_base(tipo):
    with pytest.raises(IntegrityError) as e, transaction.atomic():
        Documento.objects.bulk_create([Documento(tipo=tipo, fecha=date(2026, 1, 1))])
    _constraint(e, "documento_tipo_valido")


# 3 y 4. precision
@pytest.mark.parametrize("cantidad", ["0.001", "1.005", "99999999999999.999", "10000000000.00"])
def test_cantidad_con_exceso_de_precision_rechazada(producto, usuario, cantidad):
    with pytest.raises(ValidationError) as e:
        mov(producto, usuario, "entrada", cantidad).save()
    assert "cantidad" in e.value.error_dict
    assert Movimiento.objects.count() == 0


def test_costo_unitario_con_exceso_de_precision_rechazado(producto, usuario):
    m = mov(producto, usuario, "entrada", "1")
    m.costo_unitario = Decimal("1.005")
    with pytest.raises(ValidationError) as e:
        m.save()
    assert "costo_unitario" in e.value.error_dict
    m.costo_unitario = Decimal("123456789012.345")
    with pytest.raises(ValidationError) as e:
        m.save()
    assert "costo_unitario" in e.value.error_dict


def test_cantidad_valida_se_acepta_sin_redondear(producto, usuario):
    m = mov(producto, usuario, "entrada", "1.25")
    m.costo_unitario = Decimal("10.50")
    m.save()
    m.refresh_from_db()
    assert m.cantidad == Decimal("1.25")
    assert m.costo_unitario == Decimal("10.50")
    mov(producto, usuario, "entrada", "9999999999.99").save()


def test_cantidad_fuera_de_rango_rechazada_por_la_base(producto, usuario):
    with pytest.raises(IntegrityError) as e, transaction.atomic():
        Movimiento.objects.bulk_create([mov(producto, usuario, "entrada", "99999999999999.99")])
    _constraint(e, "movimiento_cantidad_en_rango")
    with pytest.raises(IntegrityError) as e, transaction.atomic():
        Movimiento.objects.bulk_create([mov(producto, usuario, "salida", "-10000000000")])
    _constraint(e, "movimiento_cantidad_en_rango")


# 5. bulk_create sin upsert ni ignorar conflictos
def test_bulk_create_rechaza_update_conflicts(producto, usuario):
    m = mov(producto, usuario, "entrada", "5")
    m.save()
    otro = mov(producto, usuario, "entrada", "77")
    otro.pk = m.pk
    with pytest.raises(MovimientoInmutable):
        Movimiento.objects.bulk_create(
            [otro], update_conflicts=True, update_fields=["cantidad"], unique_fields=["id"]
        )
    assert Movimiento.objects.get(pk=m.pk).cantidad == Decimal("5.00")


def test_bulk_create_rechaza_ignore_conflicts(producto, usuario):
    m = mov(producto, usuario, "entrada", "5")
    m.save()
    otro = mov(producto, usuario, "entrada", "77")
    otro.pk = m.pk
    with pytest.raises(MovimientoInmutable):
        Movimiento.objects.bulk_create([otro], ignore_conflicts=True)
    assert Movimiento.objects.count() == 1


# 6. carga cruda (loaddata / deserialize)
def test_carga_cruda_sobre_pk_existente_rechazada(producto, usuario):
    m = mov(producto, usuario, "entrada", "5")
    m.save()
    datos = serializers.serialize("json", [m])
    modificado = datos.replace('"cantidad": "5"', '"cantidad": "99"')
    assert modificado != datos
    with pytest.raises(MovimientoInmutable):
        for obj in serializers.deserialize("json", modificado):
            obj.save()
    assert Movimiento.objects.get(pk=m.pk).cantidad == Decimal("5.00")


def test_carga_cruda_de_pk_nuevo_se_permite(producto, usuario):
    m = mov(producto, usuario, "entrada", "5")
    m.save()
    datos = serializers.serialize("json", [m])
    Movimiento.objects.all()._raw_delete(Movimiento.objects.db)  # solo para preparar el escenario
    for obj in serializers.deserialize("json", datos):
        obj.save()
    assert Movimiento.objects.get(pk=m.pk).cantidad == Decimal("5.00")


# 7. base manager inmutable
def test_base_manager_es_inmutable(producto, usuario):
    m = mov(producto, usuario, "entrada", "5")
    m.save()
    with pytest.raises(MovimientoInmutable):
        Movimiento._base_manager.update(cantidad=Decimal("1"))
    with pytest.raises(MovimientoInmutable):
        Movimiento._base_manager.all().delete()
    assert Movimiento.objects.get(pk=m.pk).cantidad == Decimal("5.00")


# 9. factura con proveedor
def test_factura_sin_proveedor_rechazada_por_la_base():
    with pytest.raises(IntegrityError) as e, transaction.atomic():
        Documento.objects.create(tipo="factura", numero="1")
    _constraint(e, "documento_factura_con_proveedor")
    Documento.objects.create(tipo="venta", numero="1")  # otros tipos no la exigen


def test_factura_sin_proveedor_rechazada_por_validacion():
    with pytest.raises(ValidationError) as e:
        Documento(tipo="factura", numero="1").full_clean()
    assert "Una factura debe tener proveedor." in str(e.value)


# 11. motivo vacio tras Trim
def test_ajuste_con_motivo_de_espacios_rechazado_por_la_base(producto, usuario):
    with pytest.raises(IntegrityError) as e, transaction.atomic():
        Movimiento.objects.bulk_create([mov(producto, usuario, "ajuste", "3", "   ")])
    _constraint(e, "movimiento_ajuste_con_motivo")


def test_ajuste_con_motivo_de_espacios_rechazado_por_save(producto, usuario):
    with pytest.raises(ValidationError) as e:
        mov(producto, usuario, "ajuste", "3", " \n\t ").save()
    assert "motivo" in e.value.error_dict


def test_ajuste_positivo_con_motivo_aceptado(producto, usuario):
    mov(producto, usuario, "ajuste", "3", "sobrante en conteo").save()
    assert Movimiento.objects.get().cantidad == Decimal("3.00")


# Admin de solo lectura
def test_admin_movimiento_es_solo_lectura(usuario):
    su = get_user_model().objects.create_superuser("jefe", password="x-prueba-123")
    request = RequestFactory().get("/admin/")
    request.user = su
    ma = MovimientoAdmin(Movimiento, django_admin.site)
    assert ma.has_add_permission(request) is False
    assert ma.has_change_permission(request) is False
    assert ma.has_change_permission(request, obj=object()) is False
    assert ma.has_delete_permission(request) is False
    assert ma.has_delete_permission(request, obj=object()) is False
    assert "delete_selected" not in ma.get_actions(request)


# PROTECT en el resto de las relaciones
def test_borrar_categoria_con_productos_rechazado():
    c = Categoria.objects.create(nombre="Aceites")
    Producto.objects.create(sku="C-1", nombre="x", categoria=c)
    with pytest.raises(ProtectedError):
        c.delete()


def test_borrar_proveedor_con_codigos_o_documentos_rechazado(producto, prov_a):
    ProductoProveedor.objects.create(producto=producto, proveedor=prov_a, codigo_proveedor="Z")
    with pytest.raises(ProtectedError):
        prov_a.delete()
    prov = Proveedor.objects.create(nombre="Con documento")
    Documento.objects.create(tipo="factura", proveedor=prov, numero="1")
    with pytest.raises(ProtectedError):
        prov.delete()


def test_borrar_producto_con_codigo_alternativo_rechazado(producto):
    CodigoAlternativo.objects.create(producto=producto, tipo="barras", valor="123")
    with pytest.raises(ProtectedError):
        producto.delete()


# ValidationError identifica la restriccion que falla
def test_validation_error_nombra_la_restriccion_o_el_campo(prov_a):
    with pytest.raises(ValidationError) as e:
        Documento(tipo="venta", descuento_global=Decimal("101")).full_clean()
    assert "El descuento global debe estar entre 0 y 100." in str(e.value)
    with pytest.raises(ValidationError) as e:
        Documento(tipo="factura", proveedor=prov_a, numero="9").full_clean()
        Documento.objects.create(tipo="factura", proveedor=prov_a, numero="9")
        Documento(tipo="factura", proveedor=prov_a, numero="9").full_clean()
    assert "Esa factura ya fue registrada" in str(e.value)
