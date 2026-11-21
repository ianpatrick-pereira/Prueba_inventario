from django.contrib import admin

from .models import (
    Categoria,
    CodigoAlternativo,
    Documento,
    ImportacionEleventa,
    Movimiento,
    Producto,
    ProductoProveedor,
    Proveedor,
)

from .forms import (
    CategoriaForm,
    CodigoAlternativoAdminForm,
    ProductoAdminForm,
    ProductoProveedorAdminForm,
    ProveedorForm,
)

admin.site.register(Documento)


class _SoloLecturaAlEditar(admin.ModelAdmin):
    """Campos clave que no se cambian una vez creados (para cambiarlos: quitar y volver a agregar)."""

    campos_fijos = ()

    def get_readonly_fields(self, request, obj=None):
        base = tuple(super().get_readonly_fields(request, obj))
        return base + tuple(self.campos_fijos) if obj is not None else base


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    form = CategoriaForm        # rechaza nombres que solo difieren en mayusculas o espacios


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    form = ProveedorForm        # nombre unico sin mayusculas/espacios y RUT normalizado (D-42)


@admin.register(Producto)
class ProductoAdmin(_SoloLecturaAlEditar):
    form = ProductoAdminForm    # no negativos, maximo >= minimo, sku normalizado
    campos_fijos = ("sku",)     # D-43: llave de sincronizacion con Eleventa
    list_display = ("sku", "nombre", "categoria", "activo", "requiere_revision")
    list_filter = ("activo", "requiere_revision")
    search_fields = ("sku", "nombre")

    def has_delete_permission(self, request, obj=None):
        """D-64: ningun producto se borra (ni desde la ficha ni con la accion masiva); se archiva."""
        return False


@admin.register(CodigoAlternativo)
class CodigoAlternativoAdmin(_SoloLecturaAlEditar):
    form = CodigoAlternativoAdminForm
    campos_fijos = ("valor",)
    list_display = ("valor", "tipo", "producto")


@admin.register(ProductoProveedor)
class ProductoProveedorAdmin(_SoloLecturaAlEditar):
    form = ProductoProveedorAdminForm
    campos_fijos = ("proveedor", "codigo_proveedor")
    list_display = ("proveedor", "codigo_proveedor", "producto")


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    """Solo lectura: el kardex no se edita ni se borra (se corrige con un movimiento inverso)."""

    list_display = ("fecha", "producto", "tipo", "cantidad", "usuario")
    list_filter = ("tipo",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportacionEleventa)
class ImportacionEleventaAdmin(admin.ModelAdmin):
    """Solo lectura: una importación confirmada es un registro histórico."""

    list_display = ("id", "fecha", "usuario", "estado", "nombre_original")
    list_filter = ("estado",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
