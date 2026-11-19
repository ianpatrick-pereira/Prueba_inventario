"""Calculos de solo lectura para mostrar en pantalla (T-F1-17). No escriben nada."""
from decimal import ROUND_HALF_UP, Decimal

CERO = Decimal("0")


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

    Color: rojo si stock <= minimo; ambar si queda entre el minimo y la mitad del rango (maximo / 2);
    verde por encima de eso. `marca` es la posicion del minimo (0-100)."""
    stock, minimo, maximo = (Decimal(str(v or 0)) for v in (stock, minimo, maximo))
    if maximo <= 0:
        return None
    pct = max(CERO, min(Decimal(100), stock / maximo * 100))
    marca = max(CERO, min(Decimal(100), minimo / maximo * 100))
    if stock <= minimo:
        color = "rojo"
    elif stock <= maximo / 2:
        color = "ambar"
    else:
        color = "verde"
    return {"pct": float(pct), "marca": float(marca), "color": color}
