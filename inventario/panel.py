"""Panel del dia para la pagina de inicio (T-F1-17): solo lectura, con un numero fijo de consultas."""
import datetime as dt
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from . import servicios
from .models import Movimiento


def rango_de_hoy():
    """[inicio, fin) del dia de hoy en la zona horaria del sistema (America/Santiago)."""
    hoy = timezone.localdate()
    inicio = timezone.make_aware(dt.datetime.combine(hoy, dt.time.min))
    return inicio, timezone.make_aware(dt.datetime.combine(hoy + dt.timedelta(days=1), dt.time.min))


def panel_del_dia(usuario):
    """Datos para las tarjetas del inicio segun los permisos del usuario."""
    datos = {"hay_panel": False}
    if usuario.has_perm("inventario.view_producto"):
        datos["hay_panel"] = True
        valor = ExpressionWrapper(F("stock") * F("costo"), output_field=DecimalField(max_digits=24, decimal_places=2))
        r = servicios.productos_con_stock().aggregate(
            bajo_minimo=Count("pk", filter=Q(stock_min__gt=0, stock__lte=F("stock_min"))),
            sin_costo=Count("pk", filter=Q(costo=0, stock__gt=0)),
            valor=Sum(valor),
        )
        datos["bajo_minimo"] = r["bajo_minimo"]
        datos["sin_costo"] = r["sin_costo"]
        # Con productos con stock y costo 0 el valor no es confiable: no se muestra.
        datos["valor_inventario"] = None if r["sin_costo"] else (r["valor"] or Decimal("0"))
    if usuario.has_perm("inventario.view_movimiento"):
        datos["hay_panel"] = True
        inicio, fin = rango_de_hoy()
        r = Movimiento.objects.filter(fecha__gte=inicio, fecha__lt=fin).aggregate(
            entradas=Count("pk", filter=Q(tipo=Movimiento.ENTRADA)),
            unidades_entradas=Sum("cantidad", filter=Q(tipo=Movimiento.ENTRADA)),
            salidas=Count("pk", filter=Q(tipo=Movimiento.SALIDA)),
            unidades_salidas=Sum("cantidad", filter=Q(tipo=Movimiento.SALIDA)),
        )
        datos.update({
            "entradas_hoy": r["entradas"], "unidades_entradas_hoy": r["unidades_entradas"] or Decimal("0"),
            "salidas_hoy": r["salidas"], "unidades_salidas_hoy": -(r["unidades_salidas"] or Decimal("0")),
            "ultimos": list(Movimiento.objects.select_related("producto", "usuario")[:10]),
        })
    return datos
