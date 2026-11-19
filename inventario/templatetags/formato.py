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
