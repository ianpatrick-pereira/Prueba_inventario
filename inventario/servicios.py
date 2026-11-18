"""Servicio de stock (T-F1-04).

El stock NO se guarda: es la suma de los movimientos (D-17). Toda escritura pasa por
aqui: se hace dentro de transaction.atomic y bloquea el producto con select_for_update
antes de leer el stock, para que dos operaciones simultaneas no lo dejen bajo cero.
(SQLite ignora el bloqueo de fila, pero serializa las escrituras; PostgreSQL si lo aplica.)

Reglas:
- D-31: una salida, ajuste o reversa que deje el stock bajo cero se rechaza con
  StockInsuficiente, salvo permitir_negativo=True (reservado a los importadores,
  que deben reflejar la realidad).
- D-32: un movimiento se corrige con una reversa: un ajuste de cantidad opuesta, con
  motivo y vinculo al original (Movimiento.revierte). No se revierte una reversa.

Excepciones:
- StockInsuficiente: subclase de ValidationError, a proposito: quien ya captura
  ValidationError (formularios, importadores) la trata como un rechazo de datos mas.
  Lleva stock_actual y cantidad_pedida para mostrar un mensaje claro.
- ValidationError: cantidad invalida (float, bool, <= 0, decimales de mas...), motivo
  faltante, reversa no permitida.
- MovimientoInmutable: solo si alguien intenta modificar/borrar un movimiento.

Limitacion: el control de negativos mira el stock total actual; un movimiento con fecha
pasada no revisa los saldos historicos intermedios.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce

from .models import Movimiento, Producto

CERO = Decimal("0")
_CENTAVO = Decimal("0.01")
_STOCK_FIELD = models.DecimalField(max_digits=14, decimal_places=2)


class StockInsuficiente(ValidationError):
    """La operacion dejaria el stock bajo cero (D-31)."""

    def __init__(self, producto, stock_actual, cantidad_pedida):
        self.producto = producto
        self.stock_actual = stock_actual
        self.cantidad_pedida = cantidad_pedida
        super().__init__(
            f"Stock insuficiente de {producto}: hay {stock_actual} y se pide {cantidad_pedida}.",
            code="stock_insuficiente",
        )


def _a_decimal(valor, nombre="cantidad"):
    """Acepta Decimal, int o str. Rechaza float y bool (un float ya perdio precision)."""
    if isinstance(valor, bool) or isinstance(valor, float) or valor is None:
        raise ValidationError({nombre: "Debe ser un Decimal, un entero o un texto numérico."})
    try:
        dec = Decimal(str(valor).strip()) if isinstance(valor, str) else Decimal(valor)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({nombre: "Valor numérico no válido."})
    if not dec.is_finite():
        raise ValidationError({nombre: "Valor numérico no válido."})
    return dec


def _sumar(qs):
    total = qs.aggregate(total=Sum("cantidad"))["total"] or CERO
    return Decimal(total).quantize(_CENTAVO)


def stock_de(producto, hasta=None):
    """Stock = suma de los movimientos (0 si no hay). `hasta`: datetime inclusivo."""
    qs = Movimiento.objects.filter(producto=producto)
    if hasta is not None:
        qs = qs.filter(fecha__lte=hasta)
    return _sumar(qs)


def productos_con_stock(queryset=None):
    """Productos anotados con `stock`, en una sola consulta."""
    if queryset is None:
        queryset = Producto.objects.all()
    return queryset.annotate(
        stock=Coalesce(
            Sum("movimiento__cantidad"), Value(CERO), output_field=_STOCK_FIELD
        )
    )


def kardex(producto):
    """Lista de (movimiento, saldo) en orden (fecha, id), con saldo acumulado."""
    filas, saldo = [], CERO
    for m in Movimiento.objects.filter(producto=producto).order_by("fecha", "id"):
        saldo += m.cantidad
        filas.append((m, saldo))
    return filas


def _bloquear(producto):
    """Bloquea el producto (debe llamarse dentro de atomic) y devuelve la fila fresca."""
    return Producto.objects.select_for_update().get(pk=producto.pk)


def _crear(producto, tipo, cantidad, usuario, *, documento=None, fecha=None, motivo="",
           costo_unitario=None, revierte=None, permitir_negativo=False):
    """Crea un movimiento con el producto ya bloqueado. `cantidad` ya lleva su signo."""
    campos = dict(
        producto=producto, tipo=tipo, cantidad=cantidad, usuario=usuario,
        documento=documento, motivo=motivo, costo_unitario=costo_unitario, revierte=revierte,
    )
    if fecha is not None:
        campos["fecha"] = fecha
    m = Movimiento(**campos)
    m.full_clean()  # campos, restricciones y clean(): ValidationError en vez de IntegrityError
    if cantidad < 0 and not permitir_negativo:
        actual = stock_de(producto)
        if actual + cantidad < 0:
            raise StockInsuficiente(producto, actual, -cantidad)
    m.save()
    return m


def _positiva(cantidad):
    dec = _a_decimal(cantidad)
    if dec <= 0:
        raise ValidationError({"cantidad": "La cantidad debe ser mayor que cero."})
    return dec


def registrar_entrada(producto, cantidad, usuario, documento=None, costo_unitario=None,
                      fecha=None, motivo=""):
    cantidad = _positiva(cantidad)
    if costo_unitario is not None:
        costo_unitario = _a_decimal(costo_unitario, "costo_unitario")
    with transaction.atomic():
        producto = _bloquear(producto)
        return _crear(producto, Movimiento.ENTRADA, cantidad, usuario, documento=documento,
                      fecha=fecha, motivo=motivo, costo_unitario=costo_unitario)


def registrar_salida(producto, cantidad, usuario, documento=None, fecha=None, motivo="",
                     permitir_negativo=False):
    """`cantidad` es positiva; se guarda negativa."""
    cantidad = _positiva(cantidad)
    with transaction.atomic():
        producto = _bloquear(producto)
        return _crear(producto, Movimiento.SALIDA, -cantidad, usuario, documento=documento,
                      fecha=fecha, motivo=motivo, permitir_negativo=permitir_negativo)


def registrar_ajuste(producto, cantidad_con_signo, usuario, motivo, documento=None,
                     fecha=None, permitir_negativo=False):
    cantidad = _a_decimal(cantidad_con_signo, "cantidad")
    with transaction.atomic():
        producto = _bloquear(producto)
        return _crear(producto, Movimiento.AJUSTE, cantidad, usuario, documento=documento,
                      fecha=fecha, motivo=motivo, permitir_negativo=permitir_negativo)


def ajustar_a_conteo(producto, cantidad_contada, usuario, motivo, documento=None):
    """Ajusta el stock a lo contado. Devuelve el ajuste, o None si no hay diferencia."""
    contada = _a_decimal(cantidad_contada, "cantidad_contada")
    if contada < 0:
        raise ValidationError({"cantidad_contada": "Lo contado no puede ser negativo."})
    with transaction.atomic():
        producto = _bloquear(producto)
        diferencia = contada - stock_de(producto)
        if diferencia == 0:
            return None
        return _crear(producto, Movimiento.AJUSTE, diferencia, usuario, documento=documento,
                      motivo=motivo)


def revertir(movimiento, usuario, motivo, permitir_negativo=False):
    """Corrige un movimiento con un ajuste de cantidad opuesta (D-32)."""
    with transaction.atomic():
        producto = _bloquear(movimiento.producto)
        # Se relee: el objeto recibido puede estar desactualizado.
        original = Movimiento.objects.get(pk=movimiento.pk)
        if original.revierte_id is not None:
            raise ValidationError("No se puede revertir una reversa.")
        if Movimiento.objects.filter(revierte=original).exists():
            raise ValidationError("Este movimiento ya fue revertido.")
        return _crear(producto, Movimiento.AJUSTE, -original.cantidad, usuario, motivo=motivo,
                      revierte=original, permitir_negativo=permitir_negativo)
