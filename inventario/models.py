"""Modelos del inventario (decisión D-17).

Reglas clave:
- No existe una columna de stock: el stock se calcula sumando movimientos (T-F1-04).
- Los códigos (sku, códigos alternativos, códigos de proveedor) son SIEMPRE texto:
  en Eleventa existen códigos como «0077» que un número destruiría.
- Los movimientos son inmutables: un error se corrige con un movimiento inverso.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.models import Q, Value
from django.db.models.functions import Lower, Trim
from django.db.models.lookups import Exact
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone

# Precision de los campos numericos. Cambiarla aqui cambia el campo, la validacion
# de Movimiento.save() y el CHECK de rango de la base de datos a la vez.
CANTIDAD_MAX_DIGITS = 12
CANTIDAD_DECIMAL_PLACES = 2
MONTO_MAX_DIGITS = 14
MONTO_DECIMAL_PLACES = 2
# Maximo representable de la cantidad en valor absoluto (9999999999.99 con 12/2).
# El rango del CHECK es inclusivo: un valor mayor (p. ej. 9999999999.999) se
# redondearia al leerlo y romperia la consulta.
CANTIDAD_MAXIMO = Decimal(10) ** (CANTIDAD_MAX_DIGITS - CANTIDAD_DECIMAL_PLACES) - Decimal(
    1
).scaleb(-CANTIDAD_DECIMAL_PLACES)


def _cantidad(**kw):
    return models.DecimalField(
        max_digits=CANTIDAD_MAX_DIGITS, decimal_places=CANTIDAD_DECIMAL_PLACES, **kw
    )


def _monto(**kw):
    return models.DecimalField(
        max_digits=MONTO_MAX_DIGITS, decimal_places=MONTO_DECIMAL_PLACES, **kw
    )


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
    precio_venta = _monto(verbose_name="precio de venta", default=Decimal("0"))
    precio_mayoreo = _monto(verbose_name="precio mayoreo", default=Decimal("0"))
    stock_min = _cantidad(verbose_name="stock mínimo", default=Decimal("0"))
    stock_max = _cantidad(verbose_name="stock máximo", default=Decimal("0"))

    class Meta:
        verbose_name = "producto"
        verbose_name_plural = "productos"
        ordering = ["nombre"]
        constraints = [
            models.CheckConstraint(
                condition=Q(costo__gte=0), name="producto_costo_no_negativo",
                violation_error_message="El costo no puede ser negativo.",
            ),
            models.CheckConstraint(
                condition=Q(precio_venta__gte=0), name="producto_precio_venta_no_negativo",
                violation_error_message="El precio de venta no puede ser negativo.",
            ),
            models.CheckConstraint(
                condition=Q(precio_mayoreo__gte=0), name="producto_precio_mayoreo_no_negativo",
                violation_error_message="El precio mayoreo no puede ser negativo.",
            ),
            models.CheckConstraint(
                condition=Q(stock_min__gte=0), name="producto_stock_min_no_negativo",
                violation_error_message="El stock mínimo no puede ser negativo.",
            ),
            models.CheckConstraint(
                condition=Q(stock_max__gte=0), name="producto_stock_max_no_negativo",
                violation_error_message="El stock máximo no puede ser negativo.",
            ),
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
        constraints = [
            # K1: «a0204» y «A0204» son el mismo codigo para la busqueda; la base no deja que convivan.
            models.UniqueConstraint(
                Lower("valor"), name="codigoalternativo_valor_unico_sin_mayusculas",
                violation_error_message="Ese código alternativo ya existe (sin distinguir mayúsculas).",
            ),
        ]

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


TIPOS_DOCUMENTO = ["factura", "venta", "conteo", "inventario_inicial", "ajuste"]
TIPOS_MOVIMIENTO = ["entrada", "salida", "ajuste"]


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
                condition=Q(tipo__in=TIPOS_DOCUMENTO),
                name="documento_tipo_valido",
                violation_error_message="Tipo de documento no válido.",
            ),
            models.CheckConstraint(
                condition=~Q(tipo="factura") | Q(proveedor__isnull=False),
                name="documento_factura_con_proveedor",
                violation_error_message="Una factura debe tener proveedor.",
            ),
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


TOLERANCIA_FECHA_FUTURA = timedelta(minutes=5)

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

    def bulk_create(
        self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
        update_fields=None, unique_fields=None,
    ):
        # Solo se permite insertar filas nuevas: "upsert" e "ignorar conflictos"
        # actualizarian o descartarian movimientos existentes en silencio.
        if update_conflicts or ignore_conflicts:
            raise MovimientoInmutable(MENSAJE_INMUTABLE)
        objs = list(objs)
        for obj in objs:
            obj.validar_datos()
        return super().bulk_create(
            objs, batch_size=batch_size, update_fields=update_fields, unique_fields=unique_fields,
        )


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
    # D-32: una reversa es un ajuste de cantidad opuesta que apunta al movimiento que
    # corrige. OneToOne => la base garantiza que un movimiento se revierta una sola vez.
    revierte = models.OneToOneField(
        "self", verbose_name="revierte a", on_delete=models.PROTECT,
        null=True, blank=True, related_name="reversa",
    )

    objects = MovimientoQuerySet.as_manager()

    class Meta:
        verbose_name = "movimiento"
        verbose_name_plural = "movimientos"
        ordering = ["-fecha", "-id"]
        # Django usa el base manager en operaciones internas: que sea el manager inmutable.
        base_manager_name = "objects"
        # D-39: permisos que exige inventario/servicios.py (defensa en profundidad).
        permissions = [
            ("registrar_entrada", "Puede registrar entradas de stock"),
            ("registrar_salida", "Puede registrar salidas de stock"),
            ("registrar_ajuste", "Puede registrar ajustes y conteos de stock"),
            ("revertir_movimiento", "Puede revertir movimientos"),
            # D-40: usar permitir_negativo=True en el servicio de stock.
            ("permitir_stock_negativo", "Puede dejar el stock bajo cero (permitir_negativo)"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(tipo__in=TIPOS_MOVIMIENTO),
                name="movimiento_tipo_valido",
                violation_error_message="Tipo de movimiento no válido.",
            ),
            models.CheckConstraint(
                condition=Q(cantidad__gte=-CANTIDAD_MAXIMO) & Q(cantidad__lte=CANTIDAD_MAXIMO),
                name="movimiento_cantidad_en_rango",
                violation_error_message="La cantidad está fuera del rango permitido.",
            ),
            models.CheckConstraint(
                condition=Q(revierte__isnull=True) | Q(tipo="ajuste"),
                name="movimiento_revierte_solo_ajuste",
                violation_error_message="Solo un ajuste puede revertir otro movimiento.",
            ),
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
                condition=~Q(tipo="ajuste")
                | (~Q(cantidad=0) & ~Q(Exact(Trim("motivo"), Value("")))),
                name="movimiento_ajuste_con_motivo",
                violation_error_message="Un ajuste necesita cantidad distinta de cero y un motivo.",
            ),
        ]

    def clean(self):
        super().clean()
        # Mismas reglas que save/bulk_create/carga cruda (motivo sin espacios, fecha, reversa...).
        self.validar_datos()

    def validar_datos(self):
        """Valida decimales y motivo. Corre en save(), bulk_create y en el receptor
        pre_save (que tambien cubre loaddata y save_base), para que ninguna via
        guarde datos que luego no cuadren al sumar."""
        self._validar_decimales()
        if self.tipo == self.AJUSTE and not (self.motivo or "").strip():
            raise ValidationError({"motivo": "Un ajuste necesita un motivo."})
        # D-33: sin fechas futuras (5 minutos de tolerancia por desfase de reloj).
        if self.fecha is not None:
            try:
                fecha = self._meta.get_field("fecha").to_python(self.fecha)
            except ValidationError:
                raise ValidationError({"fecha": "Fecha no válida."})
            # Sin zona horaria no se adivina: el importador debe convertir explicitamente.
            if fecha is None or timezone.is_naive(fecha):
                raise ValidationError({"fecha": "La fecha debe incluir zona horaria."})
            self.fecha = fecha
            if fecha > timezone.now() + TOLERANCIA_FECHA_FUTURA:
                raise ValidationError({"fecha": "La fecha del movimiento no puede ser futura."})
        if self.revierte_id is not None:
            self._validar_reversa()

    def _validar_reversa(self):
        """D-32: una reversa es un ajuste de cantidad opuesta, del mismo producto,
        sobre un movimiento que no es a su vez una reversa."""
        if self.pk is not None and self.revierte_id == self.pk:
            raise ValidationError({"revierte": "Un movimiento no puede revertirse a sí mismo."})
        original = Movimiento.objects.filter(pk=self.revierte_id).first()
        if original is None:
            raise ValidationError({"revierte": "El movimiento a revertir no existe."})
        if self.tipo != self.AJUSTE:
            raise ValidationError({"tipo": "Solo un ajuste puede revertir otro movimiento."})
        if original.revierte_id is not None:
            raise ValidationError({"revierte": "No se puede revertir una reversa."})
        if original.documento_id != self.documento_id:
            raise ValidationError({"documento": "La reversa debe tener el mismo documento."})
        if original.producto_id != self.producto_id:
            raise ValidationError({"revierte": "La reversa debe ser del mismo producto."})
        if self.cantidad is None or Decimal(str(self.cantidad)) != -original.cantidad:
            raise ValidationError({"cantidad": "La reversa debe tener la cantidad opuesta."})

    def _validar_decimales(self):
        """Rechaza (sin redondear) valores con mas decimales o digitos que el campo."""
        errores = {}
        for nombre in ("cantidad", "costo_unitario"):
            valor = getattr(self, nombre)
            if valor is None:
                continue
            campo = self._meta.get_field(nombre)
            try:
                dec = Decimal(repr(valor)) if isinstance(valor, float) else Decimal(str(valor))
            except Exception:
                errores[nombre] = "Valor numérico no válido."
                continue
            if not dec.is_finite():
                errores[nombre] = "Valor numérico no válido."
                continue
            paso = Decimal(1).scaleb(-campo.decimal_places)
            maximo = Decimal(10) ** (campo.max_digits - campo.decimal_places) - paso
            # El rango se comprueba antes de quantize(): un valor enorme lo haria fallar.
            if abs(dec) > maximo:
                errores[nombre] = (
                    f"Admite como máximo {campo.max_digits} dígitos en total "
                    f"({campo.decimal_places} decimales)."
                )
            elif dec != dec.quantize(paso):
                errores[nombre] = f"Admite como máximo {campo.decimal_places} decimales."
        if errores:
            raise ValidationError(errores)

    def save(self, *args, **kwargs):
        if args or not self._state.adding or kwargs.get("force_update") or kwargs.get("update_fields"):
            raise MovimientoInmutable(MENSAJE_INMUTABLE)
        self.validar_datos()
        # Siempre INSERT: un pk ya existente falla en vez de convertirse en UPDATE.
        kwargs["force_insert"] = True
        super().save(**kwargs)

    def delete(self, *args, **kwargs):
        raise MovimientoInmutable(MENSAJE_INMUTABLE)

    def __str__(self):
        return f"{self.get_tipo_display()} {self.cantidad} de {self.producto_id} ({self.fecha:%Y-%m-%d})"


@receiver(pre_save, sender=Movimiento)
def _proteger_movimiento_en_pre_save(sender, instance, raw=False, using=None, **kwargs):
    """Ultima barrera: corre en save(), loaddata/deserialize y save_base() directo.
    Rechaza un pk ya existente (seria una sobrescritura) y datos que no cumplan
    la precision o el motivo."""
    if instance.pk is not None and Movimiento.objects.using(using).filter(pk=instance.pk).exists():
        raise MovimientoInmutable(MENSAJE_INMUTABLE)
    instance.validar_datos()


def ruta_importacion(instance, filename):
    """El archivo subido se guarda con nombre aleatorio: el nombre original nunca va en la ruta."""
    extension = ".csv" if str(filename).lower().endswith(".csv") else ".xlsx"
    return f"importaciones/{uuid.uuid4().hex}{extension}"


class ImportacionEleventa(models.Model):
    """Una carga inicial del catalogo desde la exportacion de inventario de Eleventa (T-F1-08).
    El archivo es un dato del negocio: vive en media/ (fuera de Git)."""

    VISTA_PREVIA = "vista_previa"
    CONFIRMADA = "confirmada"
    DESCARTADA = "descartada"
    ESTADOS = [(VISTA_PREVIA, "Vista previa"), (CONFIRMADA, "Confirmada"), (DESCARTADA, "Descartada")]

    archivo = models.FileField("archivo", upload_to=ruta_importacion)
    hash_sha256 = models.CharField("hash SHA-256", max_length=64, unique=True)
    nombre_original = models.CharField("nombre original", max_length=255)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="usuario", on_delete=models.PROTECT
    )
    fecha = models.DateTimeField("fecha", default=timezone.now)
    estado = models.CharField("estado", max_length=20, choices=ESTADOS, default=VISTA_PREVIA)
    resumen = models.JSONField("resumen", default=dict, blank=True)
    # Resultado de leer el archivo al subirlo (filas, advertencias, errores): la vista previa
    # se pagina con esto sin volver a leer el archivo. La confirmacion vuelve a leerlo.
    analisis = models.JSONField("análisis del archivo", default=dict, blank=True)
    documento = models.ForeignKey(
        Documento, verbose_name="documento", on_delete=models.PROTECT, null=True, blank=True
    )

    class Meta:
        verbose_name = "importación desde Eleventa"
        verbose_name_plural = "importaciones desde Eleventa"
        ordering = ["-fecha", "-id"]
        permissions = [("importar_eleventa", "Puede importar la carga inicial desde Eleventa")]

    def __str__(self):
        return f"Importación {self.pk} ({self.get_estado_display()})"
