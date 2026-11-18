from django.contrib import admin

from .models import (
    Categoria,
    CodigoAlternativo,
    Documento,
    Movimiento,
    Producto,
    ProductoProveedor,
    Proveedor,
)

admin.site.register(Categoria)
admin.site.register(Proveedor)
admin.site.register(Producto)
admin.site.register(CodigoAlternativo)
admin.site.register(ProductoProveedor)
admin.site.register(Documento)


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
