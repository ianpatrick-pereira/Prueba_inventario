"""Pantallas del catalogo (T-F1-06). Cada vista exige el permiso de Django que le corresponde;
sin permiso -> 403 y sin sesion -> login (LoginRequiredMiddleware)."""
import datetime as _dt
from decimal import Decimal
from urllib.parse import urlencode

from django import forms as _forms
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from . import servicios, tokens
from .forms import (
    AjusteForm,
    CategoriaForm,
    CodigoAlternativoForm,
    ConteoForm,
    ProductoCrearForm,
    ProductoEditarForm,
    ProductoProveedorForm,
    ProveedorForm,
    ReversaForm,
    SalidaForm,
)
from .models import Categoria, CodigoAlternativo, Movimiento, Producto, ProductoProveedor, Proveedor
from .servicios import StockInsuficiente
from .templatetags.formato import formatear_numero
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

    def _filtro_categoria(self):
        """Lee ?categoria=<pk>|ninguna. Un valor invalido o inexistente no da error: avisa y no filtra."""
        if hasattr(self, "_cat"):
            return self._cat
        self.categorias = list(Categoria.objects.order_by("nombre"))     # tambien alimenta el selector
        crudo = (self.request.GET.get("categoria") or "").strip()
        self._cat, self.aviso_categoria = None, ""
        if crudo == "ninguna":
            self._cat = "ninguna"
        elif crudo:
            validas = {str(c.pk) for c in self.categorias}
            if crudo in validas:
                self._cat = int(crudo)
            else:
                self.aviso_categoria = "Categoría no válida: se muestran los productos de todas las categorías."
        return self._cat

    def get_queryset(self):
        q = normalizar_codigo(self.request.GET.get("q"))
        qs = servicios.productos_con_stock(Producto.objects.select_related("categoria"))
        cat = self._filtro_categoria()
        if cat == "ninguna":
            qs = qs.filter(categoria__isnull=True)
        elif cat is not None:
            qs = qs.filter(categoria_id=cat)
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
        q = normalizar_codigo(self.request.GET.get("q"))
        cat = self._filtro_categoria()
        ctx.update({
            "q": q, "categorias": self.categorias, "categoria_sel": "ninguna" if cat == "ninguna" else (cat or ""),
            "aviso_categoria": self.aviso_categoria,
            # los enlaces de pagina conservan la busqueda y la categoria
            "filtros_qs": urlencode({k: v for k, v in (("q", q), ("categoria", "" if cat is None else cat)) if v != ""}),
        })
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
    queryset = Categoria.objects.annotate(n_productos=Count("producto")).order_by("nombre")


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


# ---------------------------------------------------------------- kardex y movimientos (T-F1-07)
KARDEX_POR_PAGINA = 100


FECHA_MINIMA = _dt.date(2000, 1, 1)
FECHA_MAXIMA = _dt.date(2100, 12, 31)


class _FiltroFechas(_forms.Form):
    desde = _forms.DateField(required=False, label="Desde")
    hasta = _forms.DateField(required=False, label="Hasta")

    def _en_rango(self, campo):
        f = self.cleaned_data.get(campo)
        if f is not None and not (FECHA_MINIMA <= f <= FECHA_MAXIMA):
            raise _forms.ValidationError("Fecha fuera de rango.")
        return f

    def clean_desde(self):
        return self._en_rango("desde")

    def clean_hasta(self):
        return self._en_rango("hasta")


def _inicio_del_dia(fecha):
    return timezone.make_aware(_dt.datetime.combine(fecha, _dt.time.min))


class Kardex(Permiso, View):
    permission_required = "inventario.view_movimiento"

    def get(self, request, pk):
        producto = get_object_or_404(Producto, pk=pk)
        filtro = _FiltroFechas(request.GET)
        desde = hasta = None
        if filtro.is_valid():
            desde, hasta = filtro.cleaned_data["desde"], filtro.cleaned_data["hasta"]
        qs = Movimiento.objects.filter(producto=producto)
        saldo = Decimal("0")
        if desde:
            # Saldo inicial del periodo: todo lo anterior al comienzo del dia «desde».
            limite = _inicio_del_dia(desde)
            saldo = qs.filter(fecha__lt=limite).aggregate(t=Sum("cantidad"))["t"] or Decimal("0")
            qs = qs.filter(fecha__gte=limite)
        saldo_inicial = saldo
        if hasta:
            qs = qs.filter(fecha__lt=_inicio_del_dia(hasta + _dt.timedelta(days=1)))
        qs = qs.select_related("usuario", "documento").order_by("fecha", "id")
        paginador = Paginator(qs, KARDEX_POR_PAGINA)
        # Sin pagina pedida se abre la ULTIMA (lo mas reciente); los saldos siguen continuos.
        pagina = paginador.get_page(request.GET.get("page") or paginador.num_pages)
        previos = (pagina.number - 1) * KARDEX_POR_PAGINA
        if previos:  # el saldo continua desde las paginas anteriores
            saldo += sum(qs.values_list("cantidad", flat=True)[:previos], Decimal("0"))
        ids = [m.pk for m in pagina]
        revertidos = dict(
            Movimiento.objects.filter(revierte_id__in=ids).values_list("revierte_id", "id")
        )
        filas = []
        for m in pagina:
            saldo += m.cantidad
            filas.append({
                "m": m, "saldo": saldo, "reversa_id": revertidos.get(m.pk),
                "puede_revertir": m.revierte_id is None and m.pk not in revertidos,
            })
        sufijo = "&".join(f"{k}={v}" for k, v in (("desde", desde), ("hasta", hasta)) if v)
        return render(request, "inventario/kardex.html", {
            "producto": producto, "stock": servicios.stock_de(producto), "filas": filas,
            "pagina": pagina, "filtro": filtro, "saldo_inicial": saldo_inicial if desde else None,
            "filtro_qs": sufijo,
        })


def _texto_stock_insuficiente(e):
    return (f"Stock insuficiente: hay {formatear_numero(e.stock_actual)} y se pide "
            f"{formatear_numero(e.cantidad_pedida)}.")


class _FormularioMovimiento(Permiso, View):
    """Patron GET (formulario con token) -> POST (consume el token) -> redireccion (D-45).
    Las pantallas siempre usan request.user y nunca permitir_negativo (D-40); la fecha es
    siempre «ahora» (D-44: no hay campo de fecha, y cualquier fecha enviada se ignora)."""

    accion = ""
    form_class = None
    titulo = ""
    template_name = "inventario/movimiento_form.html"

    def objetivo(self, pk):
        return get_object_or_404(Producto, pk=pk)

    def producto_de(self, objetivo):
        return objetivo

    def clave(self, objetivo):
        return f"{self.accion}:{objetivo.pk}"

    def contexto_extra(self, objetivo):
        return {}

    def ejecutar(self, request, objetivo, datos):
        raise NotImplementedError

    def render_form(self, request, objetivo, form, status=200):
        producto = self.producto_de(objetivo)
        ctx = {"form": form, "titulo": self.titulo, "objetivo": objetivo, "producto": producto,
               "stock": servicios.stock_de(producto)}
        ctx.update(self.contexto_extra(objetivo))
        return render(request, self.template_name, ctx, status=status)

    def get(self, request, pk):
        objetivo = self.objetivo(pk)
        form = self.form_class(initial={"token": tokens.emitir(request, self.clave(objetivo))})
        return self.render_form(request, objetivo, form)

    def post(self, request, pk):
        objetivo = self.objetivo(pk)
        destino = redirect("kardex", pk=self.producto_de(objetivo).pk)
        form = self.form_class(request.POST)
        if not form.is_valid():
            return self.render_form(request, objetivo, form)
        if not tokens.consumir(request, form.cleaned_data["token"], self.clave(objetivo)):
            messages.warning(
                request,
                "Este formulario ya se había enviado (o venció): no se registró nada nuevo. "
                "Revisa el kardex antes de repetirlo.",
            )
            return destino
        try:
            mensaje = self.ejecutar(request, objetivo, form.cleaned_data)
        except StockInsuficiente as e:
            return self._con_error(request, objetivo, None, _texto_stock_insuficiente(e))
        except ValidationError as e:
            return self._con_error(request, objetivo, e, None)
        # PermissionDenied sube tal cual: Django responde 403.
        messages.success(request, mensaje)
        return destino

    def _con_error(self, request, objetivo, excepcion, texto):
        # El token ya se consumio: se emite uno nuevo para poder corregir y reenviar.
        datos = request.POST.copy()
        datos["token"] = tokens.emitir(request, self.clave(objetivo))
        form = self.form_class(datos)
        form.is_valid()
        if texto:
            form.add_error(None, texto)
        elif hasattr(excepcion, "error_dict"):
            for campo, errores in excepcion.message_dict.items():
                form.add_error(campo if campo in form.fields else None, errores)
        else:
            form.add_error(None, excepcion.messages)
        return self.render_form(request, objetivo, form)


class Salida(_FormularioMovimiento):
    permission_required = "inventario.registrar_salida"
    accion, form_class, titulo = "salida", SalidaForm, "Registrar salida"

    def ejecutar(self, request, producto, d):
        m = servicios.registrar_salida(producto, d["cantidad"], request.user, motivo=d["motivo"])
        return f"Salida registrada: {formatear_numero(-m.cantidad)} de {producto.sku}."


class Ajuste(_FormularioMovimiento):
    permission_required = "inventario.registrar_ajuste"
    accion, form_class, titulo = "ajuste", AjusteForm, "Registrar ajuste"

    def ejecutar(self, request, producto, d):
        m = servicios.registrar_ajuste(producto, d["cantidad"], request.user, d["motivo"])
        return f"Ajuste registrado: {formatear_numero(m.cantidad)} en {producto.sku}."


class Conteo(_FormularioMovimiento):
    permission_required = "inventario.registrar_ajuste"
    accion, form_class, titulo = "conteo", ConteoForm, "Registrar conteo"

    def ejecutar(self, request, producto, d):
        m = servicios.ajustar_a_conteo(producto, d["cantidad"], request.user, d["motivo"])
        if m is None:
            return f"Sin diferencia: el stock ya era {formatear_numero(d['cantidad'])}."
        return (f"Conteo registrado: diferencia aplicada de {formatear_numero(m.cantidad)}; "
                f"el stock ahora es {formatear_numero(d['cantidad'])}.")


class Revertir(_FormularioMovimiento):
    permission_required = "inventario.revertir_movimiento"
    accion, form_class, titulo = "revertir", ReversaForm, "Revertir movimiento"
    template_name = "inventario/revertir.html"

    def objetivo(self, pk):
        return get_object_or_404(Movimiento.objects.select_related("producto", "usuario"), pk=pk)

    def producto_de(self, movimiento):
        return movimiento.producto

    def contexto_extra(self, m):
        ya = Movimiento.objects.filter(revierte=m).first()
        return {"m": m, "es_reversa": m.revierte_id is not None, "reversa": ya}

    def ejecutar(self, request, m, d):
        servicios.revertir(m, request.user, d["motivo"])
        return f"Movimiento revertido: el stock de {m.producto.sku} volvió a lo que era antes."


# ---------------------------------------------------------------- importacion desde Eleventa (T-F1-08)
from django.core.files.base import ContentFile  # noqa: E402
from django.core.paginator import Paginator as _Paginator  # noqa: E402
from django.db import IntegrityError  # noqa: E402

from . import importar_eleventa  # noqa: E402
from .forms import SubirEleventaForm  # noqa: E402
from .models import ImportacionEleventa  # noqa: E402


class _ImportarBase(Permiso):
    permission_required = "inventario.importar_eleventa"


class ImportarSubir(_ImportarBase, View):
    template_name = "inventario/importar_subir.html"

    def get(self, request):
        return render(request, self.template_name, {"form": SubirEleventaForm()})

    def post(self, request):
        form = SubirEleventaForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})
        contenido = form.contenido
        huella = importar_eleventa.sha256(contenido)
        previa = ImportacionEleventa.objects.filter(hash_sha256=huella).first()
        if previa and previa.estado == ImportacionEleventa.CONFIRMADA:
            form.add_error("archivo", f"Este archivo ya fue importado el {timezone.localtime(previa.fecha):%d-%m-%Y %H:%M}.")
            return render(request, self.template_name, {"form": form})
        if previa and previa.estado == ImportacionEleventa.VISTA_PREVIA:
            messages.info(request, "Este archivo ya estaba subido: continúa desde su vista previa.")
            return redirect("importar_vista_previa", pk=previa.pk)
        if previa:  # descartada: se reemplaza por una nueva vista previa
            previa.archivo.delete(save=False)
            previa.delete()
        nombre = (request.FILES["archivo"].name or "archivo").replace("\\", "/").rsplit("/", 1)[-1][:255]
        imp = ImportacionEleventa(usuario=request.user, hash_sha256=huella, nombre_original=nombre,
                                  analisis=form.analisis.a_json())
        imp.archivo.save(nombre, ContentFile(contenido), save=False)
        try:
            imp.save()
        except IntegrityError:
            imp.archivo.delete(save=False)
            form.add_error("archivo", "Este archivo ya fue subido por otra persona; reintenta.")
            return render(request, self.template_name, {"form": form})
        return redirect("importar_vista_previa", pk=imp.pk)


class ImportarVistaPrevia(_ImportarBase, View):
    template_name = "inventario/importar_vista_previa.html"

    def _cargar(self, pk):
        return get_object_or_404(ImportacionEleventa, pk=pk)

    def get(self, request, pk):
        imp = self._cargar(pk)
        if imp.estado == ImportacionEleventa.CONFIRMADA:
            return redirect("importar_resumen", pk=pk)
        if imp.estado == ImportacionEleventa.DESCARTADA:
            messages.info(request, "Esa importación fue descartada.")
            return redirect("importar_eleventa")
        try:
            filas = importar_eleventa.cargar_analisis(imp)
        except importar_eleventa.ArchivoInvalido as e:
            messages.error(request, " ".join(e.messages))
            return redirect("importar_eleventa")
        vistos = [f.codigo for f in filas if f.estado == "existente"]
        if imp.analisis and imp.analisis.get("existentes_vistos") != vistos:
            # Lo que el usuario vio como «existente»: al confirmar se avisa si cambio.
            imp.analisis["existentes_vistos"] = vistos
            imp.save(update_fields=["analisis"])
        pagina = _Paginator(filas, 100).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "imp": imp, "resumen": importar_eleventa.resumir(filas), "pagina": pagina,
            "omitidas": filas.omitidas,
            "token": tokens.emitir(request, f"importar:{pk}"),
        })

    def post(self, request, pk):
        self._cargar(pk)  # 404 si no existe
        accion = request.POST.get("accion")
        if not tokens.consumir(request, request.POST.get("token", ""), f"importar:{pk}"):
            messages.warning(
                request,
                "Este formulario ya se había enviado (o venció): no se hizo nada nuevo. "
                "Revisa el estado de la importación.",
            )
            return redirect("importar_vista_previa", pk=pk)
        try:
            if accion == "confirmar":
                importar_eleventa.confirmar(
                    pk, request.user, actualizar_existentes=request.POST.get("actualizar_existentes") == "on"
                )
                messages.success(request, "Importación confirmada.")
                return redirect("importar_resumen", pk=pk)
            if accion == "descartar":
                importar_eleventa.descartar(pk, request.user)
                messages.success(request, "Importación descartada: no se cambió nada.")
                return redirect("importar_eleventa")
        except ValidationError as e:
            messages.error(request, "No se importó nada: " + " ".join(e.messages))
            return redirect("importar_vista_previa", pk=pk)
        messages.error(request, "Acción no válida.")
        return redirect("importar_vista_previa", pk=pk)


class ImportarResumen(_ImportarBase, View):
    def get(self, request, pk):
        imp = get_object_or_404(ImportacionEleventa.objects.select_related("documento", "usuario"), pk=pk)
        if imp.estado != ImportacionEleventa.CONFIRMADA:
            return redirect("importar_vista_previa", pk=pk)
        return render(request, "inventario/importar_resumen.html", {"imp": imp, "r": imp.resumen})
