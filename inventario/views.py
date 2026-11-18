"""Pantallas del catalogo (T-F1-06). Cada vista exige el permiso de Django que le corresponde;
sin permiso -> 403 y sin sesion -> login (LoginRequiredMiddleware)."""
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Case, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from . import servicios
from .forms import (
    CategoriaForm,
    CodigoAlternativoForm,
    ProductoCrearForm,
    ProductoEditarForm,
    ProductoProveedorForm,
    ProveedorForm,
)
from .models import Categoria, CodigoAlternativo, Producto, ProductoProveedor, Proveedor
from .validadores import normalizar_codigo


class Permiso(PermissionRequiredMixin):
    raise_exception = True  # 403 en vez de redirigir al login


class MensajeExito:
    """Muestra un mensaje de exito despues de guardar un formulario valido."""

    mensaje_exito = "Guardado."

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, self.mensaje_exito)
        return respuesta


# ---------------------------------------------------------------- productos
class ProductoLista(Permiso, ListView):
    permission_required = "inventario.view_producto"
    template_name = "inventario/producto_lista.html"
    context_object_name = "productos"
    paginate_by = 50

    def get_queryset(self):
        q = normalizar_codigo(self.request.GET.get("q"))
        qs = servicios.productos_con_stock(Producto.objects.select_related("categoria"))
        if not q:
            return qs.order_by("nombre", "pk")
        # Subconsultas (no joins): un producto nunca sale repetido ni altera su stock.
        por_alt = CodigoAlternativo.objects.filter(valor__istartswith=q).values("producto")
        por_prov = ProductoProveedor.objects.filter(codigo_proveedor__istartswith=q).values("producto")
        exactos = Q(sku__iexact=q) | Q(
            pk__in=CodigoAlternativo.objects.filter(valor__iexact=q).values("producto")
        ) | Q(pk__in=ProductoProveedor.objects.filter(codigo_proveedor__iexact=q).values("producto"))
        return (
            qs.filter(
                Q(sku__istartswith=q) | Q(nombre__icontains=q) | Q(pk__in=por_alt) | Q(pk__in=por_prov)
            )
            .annotate(
                orden_exacto=Case(When(exactos, then=Value(0)), default=Value(1), output_field=IntegerField())
            )
            .order_by("orden_exacto", "nombre", "pk")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = normalizar_codigo(self.request.GET.get("q"))
        return ctx


class ProductoNuevo(Permiso, MensajeExito, CreateView):
    permission_required = "inventario.add_producto"
    form_class = ProductoCrearForm
    template_name = "inventario/producto_form.html"
    mensaje_exito = "Producto creado."

    def get_success_url(self):
        return reverse("producto_detalle", args=[self.object.pk])


class ProductoEditar(Permiso, MensajeExito, UpdateView):
    permission_required = "inventario.change_producto"
    model = Producto
    form_class = ProductoEditarForm
    template_name = "inventario/producto_form.html"
    mensaje_exito = "Producto actualizado."

    def get_success_url(self):
        return reverse("producto_detalle", args=[self.object.pk])


class ProductoDetalle(Permiso, DetailView):
    permission_required = "inventario.view_producto"
    model = Producto
    template_name = "inventario/producto_detalle.html"
    context_object_name = "producto"
    form_codigo = None
    form_proveedor = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        p = self.object
        user = self.request.user
        ctx["stock"] = servicios.stock_de(p)
        ctx["codigos"] = p.codigoalternativo_set.order_by("valor")
        ctx["codigos_proveedor"] = p.productoproveedor_set.select_related("proveedor").order_by(
            "proveedor__nombre", "codigo_proveedor"
        )
        if user.has_perm("inventario.add_codigoalternativo"):
            ctx["form_codigo"] = self.form_codigo or CodigoAlternativoForm(producto=p)
        if user.has_perm("inventario.add_productoproveedor"):
            ctx["form_proveedor"] = self.form_proveedor or ProductoProveedorForm(producto=p)
        return ctx


class _AgregarCodigo(Permiso, View):
    """POST: agrega un codigo; si hay errores vuelve a mostrar el detalle con ellos."""

    form_class = None
    campo = None
    mensaje = ""

    def post(self, request, pk):
        producto = get_object_or_404(Producto, pk=pk)
        form = self.form_class(request.POST, producto=producto)
        if form.is_valid():
            form.save()
            messages.success(request, self.mensaje)
            return redirect("producto_detalle", pk=pk)
        vista = ProductoDetalle()
        vista.setup(request, pk=pk)
        vista.object = producto
        vista.form_codigo = form if self.campo == "codigo" else None
        vista.form_proveedor = form if self.campo == "proveedor" else None
        return vista.render_to_response(vista.get_context_data(object=producto))


class CodigoAgregar(_AgregarCodigo):
    permission_required = "inventario.add_codigoalternativo"
    form_class = CodigoAlternativoForm
    campo = "codigo"
    mensaje = "Código agregado."


class CodigoProveedorAgregar(_AgregarCodigo):
    permission_required = "inventario.add_productoproveedor"
    form_class = ProductoProveedorForm
    campo = "proveedor"
    mensaje = "Código de proveedor agregado."


class _QuitarCodigo(Permiso, View):
    """GET muestra la confirmacion (no borra nada); POST borra y vuelve al producto."""

    model = None
    mensaje = ""
    template_name = "inventario/confirmar_quitar.html"

    def _objeto(self, pk):
        return get_object_or_404(self.model.objects.select_related("producto"), pk=pk)

    def get(self, request, pk):
        from django.shortcuts import render

        return render(request, self.template_name, {"objeto": self._objeto(pk), "tipo": self.etiqueta})

    def post(self, request, pk):
        objeto = self._objeto(pk)
        producto_pk = objeto.producto_id
        objeto.delete()
        messages.success(request, self.mensaje)
        return redirect("producto_detalle", pk=producto_pk)


class CodigoQuitar(_QuitarCodigo):
    permission_required = "inventario.delete_codigoalternativo"
    model = CodigoAlternativo
    etiqueta = "código alternativo"
    mensaje = "Código quitado."


class CodigoProveedorQuitar(_QuitarCodigo):
    permission_required = "inventario.delete_productoproveedor"
    model = ProductoProveedor
    etiqueta = "código de proveedor"
    mensaje = "Código de proveedor quitado."


# ---------------------------------------------------------------- proveedores
class ProveedorLista(Permiso, ListView):
    permission_required = "inventario.view_proveedor"
    template_name = "inventario/proveedor_lista.html"
    context_object_name = "proveedores"
    paginate_by = 50

    def get_queryset(self):
        q = normalizar_codigo(self.request.GET.get("q"))
        qs = Proveedor.objects.order_by("nombre")
        if q:
            sin_formato = q.replace(".", "").replace(" ", "")
            qs = qs.filter(Q(nombre__icontains=q) | Q(rut__icontains=sin_formato))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = normalizar_codigo(self.request.GET.get("q"))
        return ctx


class ProveedorNuevo(Permiso, MensajeExito, CreateView):
    permission_required = "inventario.add_proveedor"
    form_class = ProveedorForm
    template_name = "inventario/form_simple.html"
    success_url = reverse_lazy("proveedor_lista")
    mensaje_exito = "Proveedor creado."
    extra_context = {"titulo": "Nuevo proveedor", "volver": reverse_lazy("proveedor_lista")}


class ProveedorEditar(Permiso, MensajeExito, UpdateView):
    permission_required = "inventario.change_proveedor"
    model = Proveedor
    form_class = ProveedorForm
    template_name = "inventario/form_simple.html"
    success_url = reverse_lazy("proveedor_lista")
    mensaje_exito = "Proveedor actualizado."
    extra_context = {"titulo": "Editar proveedor", "volver": reverse_lazy("proveedor_lista")}


# ---------------------------------------------------------------- categorias
class CategoriaLista(Permiso, ListView):
    permission_required = "inventario.view_categoria"
    template_name = "inventario/categoria_lista.html"
    context_object_name = "categorias"
    queryset = Categoria.objects.order_by("nombre")


class CategoriaNueva(Permiso, MensajeExito, CreateView):
    permission_required = "inventario.add_categoria"
    form_class = CategoriaForm
    template_name = "inventario/form_simple.html"
    success_url = reverse_lazy("categoria_lista")
    mensaje_exito = "Categoría creada."
    extra_context = {"titulo": "Nueva categoría", "volver": reverse_lazy("categoria_lista")}


class CategoriaEditar(Permiso, MensajeExito, UpdateView):
    permission_required = "inventario.change_categoria"
    model = Categoria
    form_class = CategoriaForm
    template_name = "inventario/form_simple.html"
    success_url = reverse_lazy("categoria_lista")
    mensaje_exito = "Categoría actualizada."
    extra_context = {"titulo": "Editar categoría", "volver": reverse_lazy("categoria_lista")}
