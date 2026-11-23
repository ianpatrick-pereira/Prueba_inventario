"""Calculos de solo lectura para mostrar en pantalla (T-F1-17). No escriben nada."""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import F, Q

CERO = Decimal("0")


# ---- D-66 (final): regla UNICA de «bajo minimo», calculada sobre el stock MINIMO del producto:
#   minimo <= 0 -> nunca; minimo <= 1 -> solo con stock < minimo (con minimo 1, solo con 0);
#   minimo > 1  -> cuando el stock llega a la MITAD del minimo o menos (stock <= minimo / 2, exacto en Decimal).
# Todo el sistema (lista, filtro, panel, ficha, kardex, barra, hoja impresa) usa estas funciones: la de Python y
# la consulta equivalente para la base. Tener exactamente el minimo NUNCA es bajo minimo.
UNO = Decimal("1")
DOS = Decimal("2")


def es_bajo_minimo(stock, minimo):
    stock, minimo = Decimal(str(stock or 0)), Decimal(str(minimo or 0))
    if minimo <= 0:
        return False
    if minimo <= UNO:
        return stock < minimo
    return stock <= minimo / DOS


def umbral_aviso(minimo):
    """Stock hasta el que se avisa (inclusive) para minimo > 1: la mitad del minimo. Para minimo <= 1 el aviso es
    «stock menor que el minimo»; se devuelve el minimo (limite NO incluido). None si no hay minimo."""
    minimo = Decimal(str(minimo or 0))
    if minimo <= 0:
        return None
    return minimo if minimo <= UNO else minimo / DOS


def q_bajo_minimo():
    """Condicion equivalente a es_bajo_minimo para un queryset anotado con `stock` (productos_con_stock)."""
    return (Q(stock_min__gt=0, stock_min__lte=1, stock__lt=F("stock_min"))
            | Q(stock_min__gt=1, stock_min__gte=F("stock") * 2))   # sin dividir: SQLite divide enteros


def cantidad_sugerida(stock, minimo, maximo):
    """Cuanto reponer = objetivo - stock, con objetivo = el mayor entre maximo y minimo. Nunca negativa."""
    stock, minimo, maximo = (Decimal(str(v or 0)) for v in (stock, minimo, maximo))
    return max(CERO, max(maximo, minimo) - stock)


def calcular_margen(precio_venta, costo):
    """Margen = (precio de venta - costo) / precio de venta, en % sin decimales (mitad hacia arriba).

    Devuelve {"estado": "normal" | "bajo_costo" | "sin_costo", "pct": int | None}:
    - costo 0 -> «sin_costo» (no se calcula);
    - precio de venta menor que el costo -> «bajo_costo» (se informa el porcentaje, negativo);
    - si no, «normal» con el porcentaje."""
    precio, costo = Decimal(str(precio_venta or 0)), Decimal(str(costo or 0))
    if costo <= 0:
        return {"estado": "sin_costo", "pct": None}
    if precio <= 0:
        return {"estado": "bajo_costo", "pct": None}
    pct = ((precio - costo) / precio * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return {"estado": "bajo_costo" if precio < costo else "normal", "pct": int(pct)}


def calcular_barra_stock(stock, minimo, maximo):
    """Datos de la barra de stock entre 0 y el maximo. None si no hay maximo util (maximo <= 0).

    Color (D-66): rojo si es_bajo_minimo; ambar si no esta bajo pero aun no pasa el minimo (stock <= minimo);
    verde por encima del minimo. `marca` es la posicion (0-100) del limite del aviso: la mitad del minimo
    (para minimo <= 1, el propio minimo, que es el limite exclusivo)."""
    stock, minimo, maximo = (Decimal(str(v or 0)) for v in (stock, minimo, maximo))
    if maximo <= 0:
        return None
    umbral = umbral_aviso(minimo) or CERO
    pct = max(CERO, min(Decimal(100), stock / maximo * 100))
    marca = max(CERO, min(Decimal(100), umbral / maximo * 100))
    if es_bajo_minimo(stock, minimo):
        color = "rojo"
    elif minimo > 0 and stock <= minimo:
        color = "ambar"
    else:
        color = "verde"
    return {"pct": float(pct), "marca": float(marca), "color": color}
