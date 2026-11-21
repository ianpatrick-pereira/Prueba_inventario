"""Validadores y normalizadores reutilizables del catalogo (D-42, D-43)."""
import re

from django.core.exceptions import ValidationError

_RUT = re.compile(r"(\d{6,8})-([0-9K])")


def normalizar_nombre(texto):
    """Quita espacios sobrantes (inicio, fin y repetidos) sin cambiar mayusculas."""
    return " ".join((texto or "").split())


def clave_nombre(texto):
    """Clave para detectar duplicados que solo difieren en mayusculas o espacios."""
    return normalizar_nombre(texto).casefold()


def normalizar_codigo(texto):
    """D-43: sin espacios al inicio y al final; NO cambia mayusculas ni ceros ("0077")."""
    return (texto or "").strip()


def calcular_dv(cuerpo):
    """Digito verificador (modulo 11) de un RUT chileno; `cuerpo` son solo digitos."""
    suma = sum(int(d) * f for d, f in zip(reversed(cuerpo), _factores()))
    resto = 11 - suma % 11
    return "0" if resto == 11 else "K" if resto == 10 else str(resto)


def _factores():
    while True:
        yield from (2, 3, 4, 5, 6, 7)


def normalizar_rut(texto):
    """D-42: "12.345.678-5" -> "12345678-5"; DV en mayuscula; vacio -> "".

    Lanza ValidationError si el formato o el digito verificador no son validos."""
    bruto = (texto or "").replace(".", "").replace(" ", "").upper()
    if not bruto:
        return ""
    if "-" not in bruto and len(bruto) > 1:
        bruto = f"{bruto[:-1]}-{bruto[-1]}"
    m = _RUT.fullmatch(bruto)
    if not m:
        raise ValidationError("RUT no válido. Usa el formato 12.345.678-5 (6 a 8 dígitos, guion y dígito verificador).")
    cuerpo, dv = m.groups()
    if calcular_dv(cuerpo) != dv:
        raise ValidationError("El dígito verificador del RUT no es correcto.")
    return f"{cuerpo}-{dv}"


_INICIOS_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def neutralizar_para_planilla(texto):
    """D-49: antepone «'» a un texto que una planilla (Excel) interpretaria como formula
    (empieza con =, +, - o @). Se usara al EXPORTAR a planilla; los datos se guardan sin alterar."""
    t = "" if texto is None else str(texto)
    return f"'{t}" if t.startswith(_INICIOS_DE_FORMULA) else t


_MAX_CODIGO_ALTERNATIVO = 100


def codigo_desde_descripcion(codigo, descripcion):
    """D-62: si la descripcion (sin espacios en los extremos) NO tiene espacios internos y es distinta del
    codigo, es un codigo real del producto y se guarda ademas como codigo alternativo.
    Devuelve ese valor o "" (descripcion igual al codigo, con espacios, vacia, demasiado larga o que
    empieza con =, +, -, @ o comillas: nunca se alteran ni se adivinan los codigos)."""
    d = (descripcion or "").strip()
    if not d or re.search(r"\s", d) or d.casefold() == (codigo or "").strip().casefold() or len(d) > _MAX_CODIGO_ALTERNATIVO:
        return ""
    if d.startswith(("=", "+", "-", "@", '"')) or d.endswith('"'):
        return ""
    return d
