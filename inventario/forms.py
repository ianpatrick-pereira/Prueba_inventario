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


class _ProductoBaseForm(forms.ModelForm):
    def _no_negativo(self, campo):
        valor = self.cleaned_data.get(campo)
        if valor is not None and valor < 0:
            raise forms.ValidationError("No puede ser negativo.")
        return valor

    def clean_costo(self):
        return self._no_negativo("costo")

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


_CAMPOS_COMUNES = ["nombre", "categoria", "unidad", "costo", "stock_min", "stock_max"]


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
    return forms.DecimalField(label=label, max_digits=12, decimal_places=2, localize=True, **kw)


class SalidaForm(_MovimientoForm):
    cantidad = _cantidad("Cantidad que sale", min_value=0.01)
    motivo = forms.CharField(label="Motivo (opcional)", required=False, max_length=500)


class AjusteForm(_MovimientoForm):
    cantidad = _cantidad("Cantidad del ajuste (con signo: negativa resta, positiva suma)")
    motivo = forms.CharField(label="Motivo (obligatorio)", max_length=500)

    def clean_cantidad(self):
        c = self.cleaned_data["cantidad"]
        if c == 0:
            raise forms.ValidationError("El ajuste no puede ser cero.")
        return c


class ConteoForm(_MovimientoForm):
    cantidad = _cantidad("Cantidad contada", min_value=0)
    motivo = forms.CharField(label="Motivo (obligatorio)", max_length=500)


class ReversaForm(_MovimientoForm):
    motivo = forms.CharField(label="Motivo (obligatorio)", max_length=500)
