"""Formato de numeros es-CL en pantalla: 3.500,00 (miles con punto, decimales con coma).
Solo cambia como se muestra; no cambia como se guarda."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()


def formatear_numero(valor, decimales=2):
    if valor is None or valor == "":
        return ""
    try:
        d = Decimal(str(valor)).quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return str(valor)
    signo = "-" if d < 0 else ""
    texto = f"{abs(d):,.{decimales}f}"
    texto = texto.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"{signo}{texto}"


@register.filter(name="cl")
def cl(valor, decimales=2):
    return formatear_numero(valor, int(decimales))


@register.filter(name="empieza_con")
def empieza_con(valor, prefijo):
    """Para marcar la pagina actual en el menu: url_name|empieza_con:"producto"."""
    return str(valor or "").startswith(prefijo)


def formatear_pesos(valor):
    """Monto entero en pesos: 6990 -> «$6.990». Los centavos se redondean al peso mas cercano
    (la mitad se redondea alejandose del cero: 1234,50 -> $1.235 y -1500,50 -> -$1.501). Solo cambia como se muestra, no como se guarda."""
    if valor is None or valor == "":
        return ""
    try:
        d = Decimal(str(valor)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return str(valor)
    signo = "-" if d < 0 else ""
    return f"{signo}${abs(d):,.0f}".replace(",", ".")


@register.filter(name="pesos")
def pesos(valor):
    return formatear_pesos(valor)


def formatear_cantidad(valor):
    """Cantidad de stock: sin decimales si es un numero entero (3 -> «3»), con 2 si no (2,5 -> «2,50»)."""
    if valor is None or valor == "":
        return ""
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return str(valor)
    return formatear_numero(d, 0 if d == d.to_integral_value() else 2)


@register.filter(name="cantidad")
def cantidad(valor):
    return formatear_cantidad(valor)


@register.inclusion_tag("inventario/_barra_stock.html")
def barra_stock(stock, minimo, maximo):
    """Barra de stock entre 0 y el maximo, con marca del minimo (no se dibuja sin maximo util)."""
    from ..calculos import calcular_barra_stock

    datos = calcular_barra_stock(stock, minimo, maximo)
    if datos is None:
        return {"dibujar": False}
    datos.update({
        "dibujar": True,
        "texto": f"{formatear_cantidad(stock)} de {formatear_cantidad(maximo)}, mínimo {formatear_cantidad(minimo)}",
    })
    return datos


@register.filter(name="sugerida")
def sugerida(producto):
    """Cantidad sugerida a reponer = maximo - stock, solo si es mayor que cero (hoja impresa)."""
    try:
        diferencia = Decimal(str(producto.stock_max)) - Decimal(str(producto.stock))
    except (InvalidOperation, AttributeError, ValueError):
        return ""
    return formatear_cantidad(diferencia) if diferencia > 0 else ""
