"""Modelos del inventario (decisión D-17).

Reglas clave:
- No existe una columna de stock: el stock se calcula sumando movimientos (T-F1-04).
- Los códigos (sku, códigos alternativos, códigos de proveedor) son SIEMPRE texto:
  en Eleventa existen códigos como «0077» que un número destruiría.
- Los movimientos son inmutables: un error se corrige con un movimiento inverso.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


def _cantidad(**kw):
    return models.DecimalField(max_digits=12, decimal_places=2, **kw)


def _monto(**kw):
    return models.DecimalField(max_digits=14, decimal_places=2, **kw)


class Categoria(models.Model):
    nombre = models.CharField("nombre", max_length=100, unique=True)

    class Meta:
        verbose_name = "categoría"
        verbose_name_plural = "categorías"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Proveedor(models.Model):
    nombre = models.CharField("nombre", max_length=150, unique=True)
    rut = models.CharField("RUT", max_length=20, unique=True, null=True, blank=True)
    contacto = models.CharField("contacto", max_length=200, blank=True)

    class Meta:
        verbose_name = "proveedor"
        verbose_name_plural = "proveedores"
        ordering = ["nombre"]

    def save(self, *args, **kwargs):
        # El RUT vacío se guarda como NULL para que "sin RUT" no choque con la unicidad.
        if self.rut is not None and not self.rut.strip():
            self.rut = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    sku = models.CharField("SKU (código en Eleventa)", max_length=50, unique=True)
    nombre = models.CharField("nombre", max_length=200)
    categoria = models.ForeignKey(
        Categoria, verbose_name="categoría", on_delete=models.PROTECT, null=True, blank=True
    )
    unidad = models.CharField("unidad", max_length=30, default="unidad")
    costo = _monto(verbose_name="costo de referencia", default=Decimal("0"))
    stock_min = _cantidad(verbose_name="stock mínimo", default=Decimal("0"))
    stock_max = _cantidad(verbose_name="stock máximo", default=Decimal("0"))

    class Meta:
        verbose_name = "producto"
        verbose_name_plural = "productos"
        ordering = ["nombre"]
        constraints = [
            models.CheckConstraint(
                condition=Q(stock_max__gte=models.F("stock_min")),
                name="producto_stock_max_gte_min",
                violation_error_message="El stock máximo no puede ser menor que el mínimo.",
            ),
        ]

    def __str__(self):
        return f"{self.sku} - {self.nombre}"


class CodigoAlternativo(models.Model):
    BARRAS = "barras"
    FABRICANTE = "fabricante"
    TIPOS = [(BARRAS, "Código de barras"), (FABRICANTE, "Código de fabricante")]

    producto = models.ForeignKey(Producto, verbose_name="producto", on_delete=models.PROTECT)
    tipo = models.CharField("tipo", max_length=20, choices=TIPOS)
    valor = models.CharField("valor", max_length=100, unique=True)

    class Meta:
        verbose_name = "código alternativo"
        verbose_name_plural = "códigos alternativos"
        ordering = ["valor"]

    def __str__(self):
        return f"{self.valor} ({self.get_tipo_display()})"


class ProductoProveedor(models.Model):
    """Qué código usa cada proveedor para cada producto (muchos a muchos)."""

    producto = models.ForeignKey(Producto, verbose_name="producto", on_delete=models.PROTECT)
    proveedor = models.ForeignKey(Proveedor, verbose_name="proveedor", on_delete=models.PROTECT)
    codigo_proveedor = models.CharField("código del proveedor", max_length=100)

    class Meta:
        verbose_name = "código de producto por proveedor"
        verbose_name_plural = "códigos de producto por proveedor"
        constraints = [
            models.UniqueConstraint(
                fields=["proveedor", "codigo_proveedor"],
                name="productoproveedor_codigo_unico_por_proveedor",
                violation_error_message="Ese código ya existe para este proveedor.",
            ),
        ]

    def __str__(self):
        return f"{self.proveedor} / {self.codigo_proveedor} -> {self.producto}"


class Documento(models.Model):
    FACTURA = "factura"
    VENTA = "venta"
    CONTEO = "conteo"
    INVENTARIO_INICIAL = "inventario_inicial"
    AJUSTE = "ajuste"
    TIPOS = [
        (FACTURA, "Factura"),
        (VENTA, "Venta"),
        (CONTEO, "Conteo"),
        (INVENTARIO_INICIAL, "Inventario inicial"),
        (AJUSTE, "Ajuste"),
    ]

    tipo = models.CharField("tipo", max_length=30, choices=TIPOS)
    numero = models.CharField("número", max_length=50, blank=True)
    nombre = models.CharField("nombre", max_length=200, blank=True)
    fecha = models.DateField("fecha", default=timezone.localdate)
    proveedor = models.ForeignKey(
        Proveedor, verbose_name="proveedor", on_delete=models.PROTECT, null=True, blank=True
    )
    patente = models.CharField("patente", max_length=20, blank=True)
    foto = models.FileField("foto", upload_to="facturas/", blank=True)
    descuento_global = models.DecimalField(
        "descuento global (%)", max_digits=5, decimal_places=2, default=Decimal("0")
    )

    class Meta:
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        ordering = ["-fecha", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(descuento_global__gte=0) & Q(descuento_global__lte=100),
                name="documento_descuento_0_100",
                violation_error_message="El descuento global debe estar entre 0 y 100.",
            ),
            models.UniqueConstraint(
                fields=["proveedor", "numero"],
                condition=Q(tipo="factura") & ~Q(numero=""),
                name="documento_factura_unica_por_proveedor",
                violation_error_message="Esa factura ya fue registrada para este proveedor.",
            ),
        ]

    def __str__(self):
        etiqueta = self.numero or self.nombre or f"#{self.pk}"
        return f"{self.get_tipo_display()} {etiqueta}"


class MovimientoInmutable(PermissionDenied):
    """Se intentó modificar o borrar un movimiento ya guardado."""


MENSAJE_INMUTABLE = (
    "Los movimientos son inmutables: no se pueden modificar ni borrar. "
    "Para corregir un error, registra un movimiento inverso."
)


class MovimientoQuerySet(models.QuerySet):
    """update() y delete() masivos están prohibidos (el kardex es un registro histórico)."""

    def update(self, *args, **kwargs):
        raise MovimientoInmutable(MENSAJE_INMUTABLE)

    def delete(self):
        raise MovimientoInmutable(MENSAJE_INMUTABLE)

    # bulk_update llama a update() internamente, pero se bloquea explícitamente.
    def bulk_update(self, *args, **kwargs):
        raise MovimientoInmutable(MENSAJE_INMUTABLE)


class Movimiento(models.Model):
    """Una fila del kardex. Nunca se modifica ni se borra: un error se corrige
    con un movimiento inverso (por ejemplo, un ajuste de signo contrario)."""

    ENTRADA = "entrada"
    SALIDA = "salida"
    AJUSTE = "ajuste"
    TIPOS = [(ENTRADA, "Entrada"), (SALIDA, "Salida"), (AJUSTE, "Ajuste")]

    producto = models.ForeignKey(Producto, verbose_name="producto", on_delete=models.PROTECT)
    documento = models.ForeignKey(
        Documento, verbose_name="documento", on_delete=models.PROTECT, null=True, blank=True
    )
    fecha = models.DateTimeField("fecha", default=timezone.now)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="usuario", on_delete=models.PROTECT
    )
    tipo = models.CharField("tipo", max_length=20, choices=TIPOS)
    cantidad = _cantidad(verbose_name="cantidad (con signo)")
    costo_unitario = _monto(verbose_name="costo unitario", null=True, blank=True)
    motivo = models.TextField("motivo", blank=True, default="")

    objects = MovimientoQuerySet.as_manager()

    class Meta:
        verbose_name = "movimiento"
        verbose_name_plural = "movimientos"
        ordering = ["-fecha", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(tipo="entrada") | Q(cantidad__gt=0),
                name="movimiento_entrada_positiva",
                violation_error_message="Una entrada debe tener cantidad mayor que cero.",
            ),
            models.CheckConstraint(
                condition=~Q(tipo="salida") | Q(cantidad__lt=0),
                name="movimiento_salida_negativa",
                violation_error_message="Una salida debe tener cantidad menor que cero.",
            ),
            models.CheckConstraint(
                condition=~Q(tipo="ajuste") | (~Q(cantidad=0) & ~Q(motivo="")),
                name="movimiento_ajuste_con_motivo",
                violation_error_message="Un ajuste necesita cantidad distinta de cero y un motivo.",
            ),
        ]

    def clean(self):
        super().clean()
        # La base de datos solo ve motivo == ""; aquí también se rechaza el motivo de solo espacios.
        if self.tipo == self.AJUSTE and not (self.motivo or "").strip():
            raise ValidationError({"motivo": "Un ajuste necesita un motivo."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise MovimientoInmutable(MENSAJE_INMUTABLE)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise MovimientoInmutable(MENSAJE_INMUTABLE)

    def __str__(self):
        return f"{self.get_tipo_display()} {self.cantidad} de {self.producto_id} ({self.fecha:%Y-%m-%d})"
