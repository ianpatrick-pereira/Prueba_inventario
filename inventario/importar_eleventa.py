"""Carga inicial del catalogo desde la exportacion de inventario de Eleventa (T-F1-08, D-46..D-48).

El archivo es una ENTRADA NO CONFIABLE (CLAUDE.md §7-8): nunca se evaluan formulas
(openpyxl en modo read_only y data_only), las celdas de texto se sanean y todo se valida.
Solo se LEE el archivo exportado; no se toca Eleventa.

El mapeo de columnas esta en COLUMNAS: es el unico lugar a ajustar cuando llegue el archivo real.
"""
import csv
import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from . import servicios
from .models import Categoria, Documento, ImportacionEleventa, Producto
from .validadores import clave_nombre, normalizar_codigo, normalizar_nombre

# ----------------------------------------------------------------------------- mapeo de columnas
# clave interna -> nombres posibles del encabezado (ya normalizados: ver _norm).
COLUMNAS = {
    "codigo": ["codigo"],
    "descripcion": ["descripcion"],
    "costo": ["precio costo"],
    "precio_venta": ["precio venta"],
    "precio_mayoreo": ["precio mayoreo"],
    "inventario": ["inventario"],
    "minimo": ["inv minimo"],
    "departamento": ["departamento"],
}
OBLIGATORIAS = ("codigo", "descripcion", "inventario")
NOMBRES_VISIBLES = {"codigo": "Código", "descripcion": "Descripción", "inventario": "Inventario"}

MAX_BYTES = 5 * 1024 * 1024
MAX_FILAS = 50000
MAX_DESCOMPRIMIDO = 200 * 1024 * 1024
MAX_FILAS_ENCABEZADO = 60
PELIGROSOS = ("=", "+", "-", "@")
MOTIVO_INICIAL = "Inventario inicial desde Eleventa"

MAX_CANTIDAD = Decimal("9999999999.99")
MAX_MONTO = Decimal("999999999999.99")
CENTAVO = Decimal("0.01")


class ArchivoInvalido(ValidationError):
    """El archivo no se puede leer como exportacion de Eleventa (mensaje claro para el usuario)."""


def _norm(texto):
    """Minusculas, sin tildes, sin puntuacion y con espacios colapsados."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


# ----------------------------------------------------------------------------- lectura
def _filas_xlsx(contenido):
    try:
        if not zipfile.is_zipfile(io.BytesIO(contenido)):
            raise ArchivoInvalido("El archivo no es un .xlsx válido (no es un paquete Excel).")
        with zipfile.ZipFile(io.BytesIO(contenido)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_DESCOMPRIMIDO:
                raise ArchivoInvalido("El archivo .xlsx es demasiado grande al descomprimirse.")
        from openpyxl import load_workbook

        libro = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    except ArchivoInvalido:
        raise
    except Exception:
        raise ArchivoInvalido("No se pudo abrir el archivo .xlsx: está dañado o no es un Excel válido.")
    try:
        for hoja in libro.worksheets:
            yield [list(f) for f in _acotar(hoja.iter_rows(values_only=True))]
    finally:
        libro.close()


def _acotar(filas):
    for i, fila in enumerate(filas):
        if i > MAX_FILAS + MAX_FILAS_ENCABEZADO + 1000:
            raise ArchivoInvalido(f"El archivo tiene demasiadas filas (máximo {MAX_FILAS}).")
        yield fila


def _filas_csv(contenido):
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = contenido.decode("latin-1")
    for delim in (";", ","):
        yield [list(f) for f in _acotar(csv.reader(io.StringIO(texto), delimiter=delim))]


def _ubicar_encabezados(filas):
    """Devuelve (indice_fila_encabezado, {clave: indice_columna}) o None."""
    for i, fila in enumerate(filas[:MAX_FILAS_ENCABEZADO]):
        normas = [_norm(c) for c in fila]
        mapa = {}
        for clave, nombres in COLUMNAS.items():
            for j, n in enumerate(normas):
                if n in nombres and clave not in mapa:
                    mapa[clave] = j
        if all(o in mapa for o in OBLIGATORIAS):
            return i, mapa
    return None


def leer_tabla(contenido, nombre):
    """Devuelve (mapa, filas): `filas` = [(numero_de_fila_en_el_archivo, [celdas])]."""
    es_csv = nombre.lower().endswith(".csv")
    if len(contenido) > MAX_BYTES:
        raise ArchivoInvalido("El archivo supera el máximo de 5 MB.")
    candidatas = _filas_csv(contenido) if es_csv else _filas_xlsx(contenido)
    for filas in candidatas:
        ubicado = _ubicar_encabezados(filas)
        if ubicado:
            i, mapa = ubicado
            datos = [(n, f) for n, f in enumerate(filas[i + 1:], start=i + 2)]
            if len(datos) > MAX_FILAS:
                raise ArchivoInvalido(f"El archivo tiene demasiadas filas (máximo {MAX_FILAS}).")
            return mapa, datos
    faltan = ", ".join(NOMBRES_VISIBLES[o] for o in OBLIGATORIAS)
    raise ArchivoInvalido(
        f"No se encontraron los encabezados obligatorios ({faltan}) en el archivo. "
        "Revisa que sea la exportación del reporte de inventario de Eleventa."
    )


# ----------------------------------------------------------------------------- numeros
_MILES = re.compile(r"\d{1,3}(\.\d{3})+")


def parsear_decimal(valor):
    """Convierte a Decimal SIN pasar por float para montos de texto. Devuelve Decimal o None.

    Acepta numeros (int, float de Excel, Decimal) y textos: «$1.234», «1.234,50», «1234.5».
    Con un solo separador «.»: grupos de tres cifras se leen como miles («1.234» = 1234)."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, Decimal):
        return valor if valor.is_finite() else None
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        return Decimal(repr(valor)) if valor == valor and abs(valor) != float("inf") else None
    t = str(valor).strip().replace("$", "").replace(" ", "").replace("\xa0", "")
    if not t:
        return None
    signo = ""
    if t[0] in "+-":
        signo, t = ("-" if t[0] == "-" else ""), t[1:]
    if not t or not re.fullmatch(r"[\d.,]+", t):
        return None
    if "," in t and "." in t:
        decimal = "," if t.rfind(",") > t.rfind(".") else "."
        miles = "." if decimal == "," else ","
        t = t.replace(miles, "").replace(decimal, ".")
    elif "," in t:
        if t.count(",") > 1:
            return None
        t = t.replace(",", ".")
    elif "." in t:
        if _MILES.fullmatch(t):
            t = t.replace(".", "")
        elif t.count(".") > 1:
            return None
    try:
        return Decimal(signo + t)
    except InvalidOperation:
        return None


def _redondear(d):
    return d.quantize(CENTAVO, rounding=ROUND_HALF_UP)


# ----------------------------------------------------------------------------- analisis
@dataclass
class Fila:
    numero: int
    codigo: str = ""
    descripcion: str = ""
    departamento: str = ""
    costo: Decimal = Decimal("0")
    precio_venta: Decimal = Decimal("0")
    precio_mayoreo: Decimal = Decimal("0")
    inventario: Decimal | None = None  # None: sin stock (negativo, vacio o no numerico)
    minimo: Decimal = Decimal("0")
    estado: str = "nueva"  # nueva | existente | error
    errores: list = field(default_factory=list)
    advertencias: list = field(default_factory=list)
    stock_sistema: Decimal | None = None
    diferencia: Decimal | None = None

    @property
    def valida(self):
        return not self.errores


def _texto(celda, advertencias, etiqueta):
    """Texto saneado: sin espacios extra y sin =, +, -, @ al inicio (inyeccion de formulas)."""
    if celda is None:
        return ""
    t = normalizar_nombre(str(celda))
    limpio = t.lstrip("".join(PELIGROSOS) + " ")
    if limpio != t:
        advertencias.append(f"{etiqueta}: se quitó un símbolo peligroso al inicio («{t[:1]}»), valor leído «{t[:40]}».")
        t = limpio
    return t


def _codigo(celda, advertencias):
    if celda is None:
        return ""
    if isinstance(celda, bool):
        return str(celda)
    if isinstance(celda, (int, float, Decimal)):
        d = Decimal(repr(celda)) if isinstance(celda, float) else Decimal(celda)
        if d == d.to_integral_value():
            texto = str(int(d))
        else:
            texto = format(d, "f")
        advertencias.append(
            f"Código leído como número ({celda}): si tenía ceros al inicio (ej. «0077») se perdieron al abrirlo en Excel."
        )
        return normalizar_codigo(texto)
    return _texto(celda, advertencias, "Código")


def _monto(celda, advertencias, etiqueta, maximo=MAX_MONTO):
    if celda is None or (isinstance(celda, str) and not celda.strip()):
        return Decimal("0")
    d = parsear_decimal(celda)
    if d is None or d < 0 or d > maximo:
        advertencias.append(f"{etiqueta}: valor no válido («{str(celda)[:30]}»), queda en 0.")
        return Decimal("0")
    r = _redondear(d)
    if r != d:
        advertencias.append(f"{etiqueta}: redondeado a 2 decimales ({celda}).")
    return r


def _valor(fila, mapa, clave):
    j = mapa.get(clave)
    return fila[j] if j is not None and j < len(fila) else None


def _es_vacia(fila):
    return all(c is None or str(c).strip() == "" for c in fila)


def _es_total(fila, mapa):
    codigo = _valor(fila, mapa, "codigo")
    primero = next((c for c in fila if c is not None and str(c).strip()), "")
    return (codigo is None or str(codigo).strip() == "" or _norm(codigo).startswith("total")) and \
        _norm(primero).startswith("total")


def analizar_contenido(contenido, nombre):
    """Lee el archivo y devuelve la lista de Fila (sin consultar la base)."""
    mapa, datos = leer_tabla(contenido, nombre)
    filas = []
    for numero, celdas in datos:
        if _es_vacia(celdas) or _es_total(celdas, mapa):
            continue
        f = Fila(numero=numero)
        adv = f.advertencias
        f.codigo = _codigo(_valor(celdas, mapa, "codigo"), adv)
        f.descripcion = _texto(_valor(celdas, mapa, "descripcion"), adv, "Descripción")
        f.departamento = _texto(_valor(celdas, mapa, "departamento"), adv, "Departamento")
        f.costo = _monto(_valor(celdas, mapa, "costo"), adv, "Precio costo")
        f.precio_venta = _monto(_valor(celdas, mapa, "precio_venta"), adv, "Precio venta")
        f.precio_mayoreo = _monto(_valor(celdas, mapa, "precio_mayoreo"), adv, "Precio mayoreo")
        f.minimo = _monto(_valor(celdas, mapa, "minimo"), adv, "Inv. mínimo", MAX_CANTIDAD)
        inv_crudo = _valor(celdas, mapa, "inventario")
        inv = parsear_decimal(inv_crudo)
        if inv is None or inv < 0 or inv > MAX_CANTIDAD:
            adv.append(f"Inventario no válido o negativo («{str(inv_crudo)[:30]}»): se importa sin stock.")
            f.inventario = None
        else:
            f.inventario = _redondear(inv)
            if f.inventario != inv:
                adv.append(f"Inventario redondeado a 2 decimales ({inv_crudo}).")
        if not f.codigo:
            f.errores.append("Código vacío.")
        elif len(f.codigo) > 50:
            f.errores.append("Código demasiado largo (máximo 50 caracteres).")
        if not f.descripcion:
            f.errores.append("Descripción vacía.")
        elif len(f.descripcion) > 200:
            f.errores.append("Descripción demasiado larga (máximo 200 caracteres).")
        if len(f.departamento) > 100:
            f.errores.append("Departamento demasiado largo (máximo 100 caracteres).")
        if f.codigo and f.costo == 0:
            adv.append("Costo en 0: revisar (el costo alimenta el pedido sugerido).")
        filas.append(f)
    # duplicados dentro del archivo: ambas (todas) las filas quedan en error
    por_codigo = {}
    for f in filas:
        if f.codigo:
            por_codigo.setdefault(f.codigo, []).append(f)
    for codigo, grupo in por_codigo.items():
        if len(grupo) > 1:
            donde = ", ".join(str(g.numero) for g in grupo)
            for g in grupo:
                g.errores.append(f"Código duplicado en el archivo (filas {donde}).")
    return filas


def _en_lotes(valores, n=500):
    valores = list(valores)
    for i in range(0, len(valores), n):
        yield valores[i:i + n]


def clasificar(filas):
    """Marca cada fila como nueva/existente/error consultando la base (stock actual de las existentes)."""
    skus = [f.codigo for f in filas if f.codigo]
    existentes = {}
    for lote in _en_lotes(skus):
        for p in servicios.productos_con_stock(Producto.objects.filter(sku__in=lote)):
            existentes[p.sku] = p
    for f in filas:
        if not f.valida:
            f.estado = "error"
            continue
        p = existentes.get(f.codigo)
        if p is None:
            f.estado = "nueva"
            continue
        f.estado = "existente"
        f.stock_sistema = Decimal(p.stock).quantize(CENTAVO)
        if f.inventario is not None:
            f.diferencia = f.inventario - f.stock_sistema
    return filas


def resumir(filas):
    nuevas = [f for f in filas if f.estado == "nueva"]
    return {
        "filas": len(filas),
        "nuevas": len(nuevas),
        "existentes": sum(f.estado == "existente" for f in filas),
        "con_advertencia": sum(bool(f.advertencias) and f.valida for f in filas),
        "con_error": sum(f.estado == "error" for f in filas),
        "unidades": str(sum((f.inventario or Decimal("0") for f in nuevas), Decimal("0"))),
    }


def analizar(importacion):
    with importacion.archivo.open("rb") as a:
        contenido = a.read()
    return clasificar(analizar_contenido(contenido, importacion.nombre_original))


# ----------------------------------------------------------------------------- subida
def sha256(contenido):
    return hashlib.sha256(contenido).hexdigest()


def validar_subida(nombre, contenido):
    """Validaciones de la subida: extension, tamano, formato real y columnas. Lanza ArchivoInvalido."""
    n = (nombre or "").lower()
    if not (n.endswith(".xlsx") or n.endswith(".csv")):
        raise ArchivoInvalido("Solo se aceptan archivos .xlsx o .csv.")
    if not contenido:
        raise ArchivoInvalido("El archivo está vacío.")
    if len(contenido) > MAX_BYTES:
        raise ArchivoInvalido("El archivo supera el máximo de 5 MB.")
    if n.endswith(".csv") and b"\x00" in contenido[:4096]:
        raise ArchivoInvalido("El archivo .csv no parece ser de texto.")
    leer_tabla(contenido, nombre)  # lanza ArchivoInvalido si faltan columnas o esta corrupto


# ----------------------------------------------------------------------------- confirmacion
class ImportacionNoValida(ValidationError):
    pass


def confirmar(importacion_id, usuario, actualizar_existentes=False):
    """Aplica la carga inicial (D-48). TODO O NADA: una sola transaccion.

    Devuelve el resumen guardado. Requiere el permiso importar_eleventa y usa el servicio de
    stock (registrar_ajuste), que exige sus propios permisos (D-39) y nunca permitir_negativo."""
    if not (getattr(usuario, "is_authenticated", False) and usuario.is_active
            and usuario.has_perm("inventario.importar_eleventa")):
        raise PermissionDenied("No tienes permiso para importar desde Eleventa.")
    with transaction.atomic():
        imp = ImportacionEleventa.objects.select_for_update().get(pk=importacion_id)
        if imp.estado != ImportacionEleventa.VISTA_PREVIA:
            raise ImportacionNoValida("Esta importación ya no está en vista previa.")
        if ImportacionEleventa.objects.filter(
            hash_sha256=imp.hash_sha256, estado=ImportacionEleventa.CONFIRMADA
        ).exists():
            raise ImportacionNoValida("Este archivo ya fue importado antes.")
        filas = analizar(imp)
        categorias = {clave_nombre(c.nombre): c for c in Categoria.objects.all()}
        documento = None
        creados = actualizados = con_stock = 0
        unidades = Decimal("0")
        diferencias, errores = [], []
        for f in filas:
            if f.estado == "error":
                errores.append({"fila": f.numero, "codigo": f.codigo, "mensajes": f.errores})
                continue
            if f.estado == "existente":
                if f.diferencia is not None and f.diferencia != 0:
                    diferencias.append({"codigo": f.codigo, "archivo": str(f.inventario),
                                        "sistema": str(f.stock_sistema), "diferencia": str(f.diferencia)})
                if actualizar_existentes:
                    _actualizar(f)
                    actualizados += 1
                continue
            categoria = None
            if f.departamento:
                clave = clave_nombre(f.departamento)
                categoria = categorias.get(clave)
                if categoria is None:
                    categoria = categorias[clave] = Categoria.objects.create(nombre=f.departamento)
            producto = Producto(
                sku=f.codigo, nombre=f.descripcion, categoria=categoria, costo=f.costo,
                precio_venta=f.precio_venta, precio_mayoreo=f.precio_mayoreo,
                stock_min=f.minimo, stock_max=f.minimo,  # Eleventa no trae maximo: igual al minimo
            )
            producto.full_clean()
            producto.save()
            creados += 1
            if f.inventario is not None and f.inventario > 0:
                if documento is None:
                    documento = Documento.objects.create(
                        tipo=Documento.INVENTARIO_INICIAL,
                        nombre=f"Inventario inicial Eleventa - {imp.nombre_original[:150]}",
                        fecha=timezone.localdate(),
                    )
                servicios.registrar_ajuste(
                    producto, f.inventario, usuario, MOTIVO_INICIAL, documento=documento
                )
                con_stock += 1
                unidades += f.inventario
        resumen = resumir(filas)
        resumen.update({
            "creados": creados, "actualizados": actualizados, "productos_con_stock": con_stock,
            "unidades_cargadas": str(unidades), "diferencias": diferencias, "errores": errores,
            "actualizar_existentes": bool(actualizar_existentes),
            "documento_id": documento.pk if documento else None,
        })
        imp.estado = ImportacionEleventa.CONFIRMADA
        imp.resumen = resumen
        imp.documento = documento
        imp.save()
        return resumen


def _actualizar(f):
    p = Producto.objects.get(sku=f.codigo)
    p.nombre, p.costo = f.descripcion, f.costo
    p.precio_venta, p.precio_mayoreo, p.stock_min = f.precio_venta, f.precio_mayoreo, f.minimo
    if p.stock_max < p.stock_min:
        p.stock_max = p.stock_min  # el maximo no puede quedar bajo el minimo
    p.full_clean()
    p.save()


def descartar(importacion_id, usuario):
    if not (getattr(usuario, "is_authenticated", False) and usuario.is_active
            and usuario.has_perm("inventario.importar_eleventa")):
        raise PermissionDenied("No tienes permiso para importar desde Eleventa.")
    with transaction.atomic():
        imp = ImportacionEleventa.objects.select_for_update().get(pk=importacion_id)
        if imp.estado != ImportacionEleventa.VISTA_PREVIA:
            raise ImportacionNoValida("Esta importación ya no está en vista previa.")
        imp.estado = ImportacionEleventa.DESCARTADA
        imp.save(update_fields=["estado"])
