from django.urls import path

from . import views

urlpatterns = [
    path("productos/", views.ProductoLista.as_view(), name="producto_lista"),
    path("productos/nuevo/", views.ProductoNuevo.as_view(), name="producto_nuevo"),
    path("productos/<int:pk>/", views.ProductoDetalle.as_view(), name="producto_detalle"),
    path("productos/<int:pk>/editar/", views.ProductoEditar.as_view(), name="producto_editar"),
    path("productos/<int:pk>/codigos/agregar/", views.CodigoAgregar.as_view(), name="codigo_agregar"),
    path("productos/<int:pk>/proveedores/agregar/", views.CodigoProveedorAgregar.as_view(),
         name="codigo_proveedor_agregar"),
    path("codigos/<int:pk>/quitar/", views.CodigoQuitar.as_view(), name="codigo_quitar"),
    path("codigos-proveedor/<int:pk>/quitar/", views.CodigoProveedorQuitar.as_view(),
         name="codigo_proveedor_quitar"),
    path("proveedores/", views.ProveedorLista.as_view(), name="proveedor_lista"),
    path("proveedores/nuevo/", views.ProveedorNuevo.as_view(), name="proveedor_nuevo"),
    path("proveedores/<int:pk>/editar/", views.ProveedorEditar.as_view(), name="proveedor_editar"),
    path("categorias/", views.CategoriaLista.as_view(), name="categoria_lista"),
    path("categorias/nueva/", views.CategoriaNueva.as_view(), name="categoria_nueva"),
    path("categorias/<int:pk>/editar/", views.CategoriaEditar.as_view(), name="categoria_editar"),
]
