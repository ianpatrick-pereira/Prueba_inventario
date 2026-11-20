from decimal import Decimal

from django import forms

from .models import Categoria, CodigoAlternativo, Producto, ProductoProveedor, Proveedor
from .validadores import clave_nombre, normalizar_codigo, normalizar_nombre, normalizar_rut


class _NombreUnicoMixin:
    """Rechaza nombres que solo difieren en mayusculas o espacios (modelo con campo `nombre`)."""

    etiqueta = "registro"

    def clean_nombre(self):
        nombre = normalizar_nombre(self.cleaned_data.get("nombre"))
        if not nombre:
            raise forms.ValidationError("Este campo es obligatorio.")
        clave = clave_nombre(nombre)
        otros = self._meta.model.objects.exclude(pk=self.instance.pk).values_list("nombre", flat=True)
        if any(clave_nombre(o) == clave for o in otros):
            raise forms.ValidationError(
                f"Ya existe un {self.etiqueta} con ese nombre (no se distinguen mayúsculas ni espacios)."
            )
        return nombre


class CategoriaForm(_NombreUnicoMixin, forms.ModelForm):
    etiqueta = "categoría"

    class Meta:
        model = Categoria
        fields = ["nombre"]


class ProveedorForm(_NombreUnicoMixin, forms.ModelForm):
    etiqueta = "proveedor"

    class Meta:
        model = Proveedor
        fields = ["nombre", "rut", "contacto"]
        help_texts = {"rut": "Opcional. Ejemplo: 12.345.678-5 (se guarda como 12345678-5)."}

    def clean_rut(self):
        rut = normalizar_rut(self.cleaned_data.get("rut"))  # ValidationError si es invalido
        if rut and Proveedor.objects.exclude(pk=self.instance.pk).filter(rut=rut).exists():
            raise forms.ValidationError("Ya existe un proveedor con ese RUT.")
        return rut or None

    def clean_contacto(self):
        return normalizar_nombre(self.cleaned_data.get("contacto"))


def _numerico(campo):
    """Campo numerico sin flechitas del navegador: texto con teclado decimal, alineado a la derecha."""
    if isinstance(campo.widget, forms.NumberInput):
        campo.widget = forms.TextInput()
    campo.widget.attrs.update({"inputmode": "decimal", "autocomplete": "off"})
    return campo


class _ProductoBaseForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre in ("costo", "precio_venta", "precio_mayoreo", "stock_min", "stock_max"):
            _numerico(self.fields[nombre])
        # Los precios son opcionales: vacio = 0.
        for campo in ("precio_venta", "precio_mayoreo"):
            self.fields[campo].required = False

    def _vacio_es_cero(self, campo):
        v = self.cleaned_data.get(campo)
        return Decimal("0") if v is None else v

    def _no_negativo(self, campo):
        valor = self.cleaned_data.get(campo)
        if valor is not None and valor < 0:
            raise forms.ValidationError("No puede ser negativo.")
        return valor

    def clean_costo(self):
        return self._no_negativo("costo")

    def clean_precio_venta(self):
        self.cleaned_data["precio_venta"] = self._vacio_es_cero("precio_venta")
        return self._no_negativo("precio_venta")

    def clean_precio_mayoreo(self):
        self.cleaned_data["precio_mayoreo"] = self._vacio_es_cero("precio_mayoreo")
        return self._no_negativo("precio_mayoreo")

    def clean_stock_min(self):
        return self._no_negativo("stock_min")

    def clean_stock_max(self):
        return self._no_negativo("stock_max")

    def clean(self):
        datos = super().clean()
        minimo, maximo = datos.get("stock_min"), datos.get("stock_max")
        if minimo is not None and maximo is not None and maximo < minimo:
            # Se asigna al campo para mostrar un mensaje claro (y no un error 500).
            self.add_error("stock_max", "El stock máximo no puede ser menor que el mínimo.")
        return datos

    def clean_nombre(self):
        nombre = normalizar_nombre(self.cleaned_data.get("nombre"))
        if not nombre:
            raise forms.ValidationError("Este campo es obligatorio.")
        return nombre


_CAMPOS_COMUNES = ["nombre", "categoria", "unidad", "costo", "precio_venta", "precio_mayoreo",
                   "stock_min", "stock_max"]


class ProductoCrearForm(_ProductoBaseForm):
    class Meta:
        model = Producto
        fields = ["sku"] + _CAMPOS_COMUNES
        labels = {"sku": "Código (el mismo de Eleventa)"}
        help_texts = {"sku": "No se podrá cambiar después desde las pantallas."}

    def clean_sku(self):
        sku = normalizar_codigo(self.cleaned_data.get("sku"))
        if not sku:
            raise forms.ValidationError("Este campo es obligatorio.")
        if Producto.objects.filter(sku=sku).exists():
            raise forms.ValidationError(f"Ya existe un producto con el código «{sku}».")
        return sku


class ProductoAdminForm(ProductoCrearForm):
    """Formulario del panel de administracion: mismas reglas que las pantallas (no negativos,
    maximo >= minimo, sku sin espacios y unico). En edicion el sku es de solo lectura (D-43):
    el admin lo saca del formulario y esta validacion no corre."""


class ProductoEditarForm(_ProductoBaseForm):
    """D-43: sin campo sku; aunque llegue por POST, se ignora."""

    class Meta:
        model = Producto
        fields = _CAMPOS_COMUNES


class CodigoAlternativoForm(forms.ModelForm):
    class Meta:
        model = CodigoAlternativo
        fields = ["tipo", "valor"]
        labels = {"valor": "Código"}

    def __init__(self, *args, producto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.producto = producto

    def clean_valor(self):
        valor = normalizar_codigo(self.cleaned_data.get("valor"))
        if not valor:
            raise forms.ValidationError("Este campo es obligatorio.")
        existente = CodigoAlternativo.objects.select_related("producto").filter(valor=valor).first()
        if existente:
            raise forms.ValidationError(
                f"El código «{valor}» ya pertenece al producto {existente.producto}."
            )
        return valor

    def save(self, commit=True):
        self.instance.producto = self.producto
        return super().save(commit)


class ProductoProveedorForm(forms.ModelForm):
    class Meta:
        model = ProductoProveedor
        fields = ["proveedor", "codigo_proveedor"]
        labels = {"codigo_proveedor": "Código del proveedor"}

    def __init__(self, *args, producto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.producto = producto

    def clean_codigo_proveedor(self):
        codigo = normalizar_codigo(self.cleaned_data.get("codigo_proveedor"))
        if not codigo:
            raise forms.ValidationError("Este campo es obligatorio.")
        return codigo

    def clean(self):
        datos = super().clean()
        proveedor, codigo = datos.get("proveedor"), datos.get("codigo_proveedor")
        if proveedor and codigo:
            existente = ProductoProveedor.objects.select_related("producto").filter(
                proveedor=proveedor, codigo_proveedor=codigo
            ).first()
            if existente:
                self.add_error(
                    "codigo_proveedor",
                    f"{proveedor} ya usa el código «{codigo}» para el producto {existente.producto}.",
                )
        return datos

    def save(self, commit=True):
        self.instance.producto = self.producto
        return super().save(commit)


# ---------------------------------------------------------------- movimientos (T-F1-07)
class _MovimientoForm(forms.Form):
    """Base: token de un solo uso (D-45). No hay campo de fecha (D-44): siempre es «ahora»."""

    token = forms.CharField(widget=forms.HiddenInput)


def _cantidad(label, **kw):
    # localize=True: acepta «2,5» (coma) ademas de «2.5».
    return _numerico(forms.DecimalField(label=label, max_digits=12, decimal_places=2, localize=True, **kw))


class SalidaForm(_MovimientoForm):
    cantidad = _cantidad("Cantidad que sale", min_value=0.01)
    motivo = forms.CharField(label="Motivo (opcional)", required=False, max_length=500)


class EntradaForm(_MovimientoForm):
    """Mercaderia que llega. Proveedor y numero de factura/guia son opcionales."""

    cantidad = _cantidad("Cantidad que llega", min_value=0.01)
    costo_unitario = _numerico(forms.DecimalField(
        label="Costo unitario (opcional)", required=False, max_digits=14, decimal_places=2,
        min_value=0, localize=True,
    ))
    proveedor = forms.ModelChoiceField(label="Proveedor (opcional)", queryset=Proveedor.objects.order_by("nombre"),
                                       required=False, empty_label="- Sin proveedor -")
    numero = forms.CharField(label="Número de factura o guía (opcional)", required=False, max_length=50)
    motivo = forms.CharField(label="Motivo o comentario (opcional)", required=False, max_length=500)

    def clean_numero(self):
        return normalizar_codigo(self.cleaned_data.get("numero"))

    def clean(self):
        datos = super().clean()
        if datos.get("numero") and not datos.get("proveedor") and "numero" not in self.errors:
            self.add_error("numero", "Para registrar un número de factura o guía hay que elegir el proveedor.")
        return datos


class ConteoForm(_MovimientoForm):
    """«Corregir stock»: se escribe lo que hay en la bodega; el sistema registra la diferencia."""

    cantidad = _cantidad("¿Cuántas unidades hay ahora en la bodega?", min_value=0)
    motivo = forms.CharField(label="Motivo de la corrección (obligatorio)", max_length=500)


class ReversaForm(_MovimientoForm):
    motivo = forms.CharField(label="Motivo (obligatorio)", max_length=500)


# ---------------------------------------------------------------- importacion desde Eleventa (T-F1-08)
from . import importar_eleventa as _imp  # noqa: E402


class SubirEleventaForm(forms.Form):
    archivo = forms.FileField(label="Archivo exportado de Eleventa (.xls, .xlsx o .csv, máximo 5 MB)")

    def clean_archivo(self):
        f = self.cleaned_data["archivo"]
        if f.size > _imp.MAX_BYTES:
            raise forms.ValidationError("El archivo supera el máximo de 5 MB.")
        contenido = f.read(_imp.MAX_BYTES + 1)
        try:
            self.analisis = _imp.validar_subida(f.name, contenido)
            self.formato = _imp.detectar_formato(contenido)
        except _imp.ArchivoInvalido as e:
            raise forms.ValidationError(e.messages)
        self.contenido = contenido
        return f


# ---------------------------------------------------------------- formularios del panel de administracion
class CodigoAlternativoAdminForm(forms.ModelForm):
    class Meta:
        model = CodigoAlternativo
        fields = ["producto", "tipo", "valor"]

    def clean_valor(self):
        valor = normalizar_codigo(self.cleaned_data.get("valor"))
        if not valor:
            raise forms.ValidationError("Este campo es obligatorio.")
        otro = CodigoAlternativo.objects.select_related("producto").exclude(pk=self.instance.pk).filter(valor=valor).first()
        if otro:
            raise forms.ValidationError(f"El código «{valor}» ya pertenece al producto {otro.producto}.")
        return valor


class ProductoProveedorAdminForm(forms.ModelForm):
    class Meta:
        model = ProductoProveedor
        fields = ["producto", "proveedor", "codigo_proveedor"]

    def clean_codigo_proveedor(self):
        codigo = normalizar_codigo(self.cleaned_data.get("codigo_proveedor"))
        if not codigo:
            raise forms.ValidationError("Este campo es obligatorio.")
        return codigo

    def clean(self):
        datos = super().clean()
        proveedor, codigo = datos.get("proveedor"), datos.get("codigo_proveedor")
        if proveedor and codigo:
            otro = ProductoProveedor.objects.select_related("producto").exclude(
                pk=self.instance.pk).filter(proveedor=proveedor, codigo_proveedor=codigo).first()
            if otro:
                self.add_error("codigo_proveedor",
                               f"{proveedor} ya usa el código «{codigo}» para el producto {otro.producto}.")
        return datos
