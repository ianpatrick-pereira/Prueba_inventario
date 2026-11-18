"""Servicio de stock (T-F1-04).

El stock NO se guarda: es la suma de los movimientos (D-17). Toda escritura pasa por
aqui: se hace dentro de transaction.atomic y bloquea el producto con select_for_update
antes de leer el stock. En PostgreSQL eso bloquea la fila del producto. SQLite no tiene
bloqueo de fila: select_for_update no hace nada, y la seguridad ante escrituras
simultaneas viene de BEGIN IMMEDIATE (D-35, config/settings.py), que hace que cada
transaccion de escritura espere a la anterior (hasta el timeout configurado).

Reglas:
- D-31: una salida, ajuste o reversa que deje el stock bajo cero se rechaza con
  StockInsuficiente, salvo permitir_negativo=True (reservado a los importadores,
  que deben reflejar la realidad).
- D-32: un movimiento se corrige con una reversa: un ajuste de cantidad opuesta, con
  motivo, vinculo al original (Movimiento.revierte) y el mismo documento. No se
  revierte una reversa ni se revierte dos veces el mismo movimiento.
- D-33: no se aceptan movimientos con fecha futura (lo valida el modelo).
- D-34: D-31 controla SOLO el stock actual (la suma de todos los movimientos). Un
  movimiento con fecha pasada (inventario inicial, tickets atrasados) puede dejar
  saldos historicos negativos en el kardex; es aceptado y no se controla.

Excepciones:
- StockInsuficiente: subclase de ValidationError, a proposito: quien ya captura
  ValidationError (formularios, importadores) la trata como un rechazo de datos mas.
  Lleva stock_actual y cantidad_pedida. OJO: se construye con un mensaje simple, asi
  que NO tiene error_dict; para leer el texto usa .messages.
- ValidationError: cantidad invalida (float, bool, <= 0, decimales de mas...), motivo
  faltante, fecha futura, reversa no permitida.
- PermissionDenied (de Django): el usuario no tiene el permiso de la operacion, esta
  inactivo o es anonimo (D-39). Se comprueba ANTES de validar o tocar nada.
- MovimientoInmutable: solo si alguien intenta modificar/borrar un movimiento.
"""
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

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


def _exigir_permiso(usuario, permiso):
    """D-39: defensa en profundidad. Ninguna pantalla o importador futuro puede saltarse
    los permisos. `permiso` es el codename de Movimiento (p. ej. "registrar_entrada")."""
    if (
        usuario is None
        or not getattr(usuario, "is_authenticated", False)
        or not getattr(usuario, "is_active", False)
        or not usuario.has_perm(f"inventario.{permiso}")
    ):
        raise PermissionDenied(f"No tienes permiso para esta operación ({permiso}).")


_NUMERO = re.compile(r"[+-]?\d+(\.\d+)?", re.ASCII)


def _a_decimal(valor, nombre="cantidad"):
    """Acepta Decimal, int (no bool) y str decimal simple en ASCII (sin notacion
    cientifica ni digitos no ASCII). Rechaza float, bool, tuplas y None."""
    if isinstance(valor, Decimal):
        dec = valor
    elif isinstance(valor, int) and not isinstance(valor, bool):
        dec = Decimal(valor)
    elif isinstance(valor, str) and _NUMERO.fullmatch(valor.strip()):
        dec = Decimal(valor.strip())
    else:
        raise ValidationError(
            {nombre: "Debe ser un Decimal, un entero o un texto numérico simple (ej. 1.25)."}
        )
    if not dec.is_finite():
        raise ValidationError({nombre: "Valor numérico no válido."})
    return dec


def _sumar(qs):
    total = qs.aggregate(total=Sum("cantidad"))["total"] or CERO
    return Decimal(total).quantize(_CENTAVO)


def _limite_hasta(hasta):
    """Devuelve (filtro, valor). date -> fin del dia local, inclusivo; datetime con zona
    -> ese instante, inclusivo; datetime sin zona -> ValidationError."""
    if isinstance(hasta, datetime):
        if timezone.is_naive(hasta):
            raise ValidationError({"hasta": "El datetime debe tener zona horaria."})
        return "fecha__lte", hasta
    if isinstance(hasta, date):
        manana = datetime.combine(hasta + timedelta(days=1), time.min)
        return "fecha__lt", timezone.make_aware(manana)
    raise ValidationError({"hasta": "Debe ser una fecha o un datetime con zona horaria."})


def stock_de(producto, hasta=None):
    """Stock = suma de los movimientos (0 si no hay). `hasta` inclusivo: un datetime con
    zona horaria, o una date (se toma hasta el fin de ese dia en la zona local)."""
    qs = Movimiento.objects.filter(producto=producto)
    if hasta is not None:
        filtro, limite = _limite_hasta(hasta)
        qs = qs.filter(**{filtro: limite})
    return _sumar(qs)


def productos_con_stock(queryset=None):
    """Productos anotados con `stock`, en una sola consulta.

    Usa una subconsulta correlacionada: no depende de joins del queryset recibido, asi que
    filtros por proveedor, codigos, movimientos, distinct o annotate previos no duplican la suma.
    """
    if queryset is None:
        queryset = Producto.objects.all()
    suma = (
        Movimiento.objects.filter(producto=OuterRef("pk"))
        .order_by()
        .values("producto")
        .annotate(s=Sum("cantidad"))
        .values("s")
    )
    return queryset.annotate(
        stock=Coalesce(Subquery(suma, output_field=_STOCK_FIELD), Value(CERO), output_field=_STOCK_FIELD)
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
    _exigir_permiso(usuario, "registrar_entrada")
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
    _exigir_permiso(usuario, "registrar_salida")
    cantidad = _positiva(cantidad)
    with transaction.atomic():
        producto = _bloquear(producto)
        return _crear(producto, Movimiento.SALIDA, -cantidad, usuario, documento=documento,
                      fecha=fecha, motivo=motivo, permitir_negativo=permitir_negativo)


def registrar_ajuste(producto, cantidad_con_signo, usuario, motivo, documento=None,
                     fecha=None, permitir_negativo=False):
    _exigir_permiso(usuario, "registrar_ajuste")
    cantidad = _a_decimal(cantidad_con_signo, "cantidad")
    with transaction.atomic():
        producto = _bloquear(producto)
        return _crear(producto, Movimiento.AJUSTE, cantidad, usuario, documento=documento,
                      fecha=fecha, motivo=motivo, permitir_negativo=permitir_negativo)


def ajustar_a_conteo(producto, cantidad_contada, usuario, motivo, documento=None):
    """Ajusta el stock a lo contado. Devuelve el ajuste, o None si no hay diferencia."""
    _exigir_permiso(usuario, "registrar_ajuste")
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
    _exigir_permiso(usuario, "revertir_movimiento")
    with transaction.atomic():
        producto = _bloquear(movimiento.producto)
        # Se relee: el objeto recibido puede estar desactualizado.
        original = Movimiento.objects.get(pk=movimiento.pk)
        if original.revierte_id is not None:
            raise ValidationError("No se puede revertir una reversa.")
        if Movimiento.objects.filter(revierte=original).exists():
            raise ValidationError("Este movimiento ya fue revertido.")
        return _crear(producto, Movimiento.AJUSTE, -original.cantidad, usuario, motivo=motivo,
                      documento=original.documento, revierte=original,
                      permitir_negativo=permitir_negativo)
