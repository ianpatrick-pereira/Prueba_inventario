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
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import servicios
from .models import Categoria, Documento, ImportacionEleventa, Producto
from .validadores import clave_nombre, normalizar_codigo

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
MAX_DESCOMPRIMIDO = 50 * 1024 * 1024
MAX_FILAS_ENCABEZADO = 60
PELIGROSOS = ("=", "+", "-", "@")
MOTIVO_INICIAL = servicios.MOTIVO_INVENTARIO_INICIAL

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


MSG_OLE = ("Este archivo es Excel 97-2003, que el sistema no lee. "
           "Expórtalo de nuevo desde Eleventa o guárdalo como .xlsx")
MSG_BINARIO = ("El archivo no parece ser de texto (contiene datos binarios). "
               "Expórtalo de nuevo desde Eleventa o guárdalo como .xlsx o .csv.")
_FIRMA_OLE = b"\xd0\xcf\x11\xe0"
_CONTROL = bytes(set(range(32)) - {9, 10, 13})      # control que no es TAB, LF ni CR


def detectar_formato(contenido):
    """D-59: el formato se decide por el CONTENIDO, nunca por la extension.

    - «PK» -> "xlsx" (paquete zip de Excel);
    - firma D0 CF 11 E0 (Excel 97-2003 real, OLE) -> ArchivoInvalido con mensaje claro;
    - texto delimitado (TAB, «;» o «,») -> "texto"; con datos binarios -> ArchivoInvalido.
    Eleventa exporta texto separado por tabulaciones y lo guarda como «.xls»."""
    if contenido[:2] == b"PK":
        return "xlsx"
    if contenido[:4] == _FIRMA_OLE:
        raise ArchivoInvalido(MSG_OLE)
    if contenido.translate(None, _CONTROL) != contenido:
        raise ArchivoInvalido(MSG_BINARIO)
    return "texto"


def decodificar_texto(contenido):
    """UTF-8 (con o sin BOM); si falla, Windows-1252 (comillas curvas, «€»…); como ultimo
    recurso Latin-1, que acepta cualquier byte."""
    for codec in ("utf-8-sig", "cp1252"):
        try:
            return contenido.decode(codec)
        except UnicodeDecodeError:
            continue
    return contenido.decode("latin-1")


def _filas_texto(contenido):
    """Un solo camino para texto delimitado (.csv, .xls de Eleventa...): prueba TAB, «;» y «,»
    y entrega las filas de cada uno; leer_tabla se queda con el que trae los encabezados."""
    texto = decodificar_texto(contenido)
    for delim in ("\t", ";", ","):
        try:
            # newline="": respeta LF, CRLF y CR solo (sin traducir) para que csv los separe bien
            yield [list(f) for f in _acotar(csv.reader(io.StringIO(texto, newline=""), delimiter=delim))]
        except csv.Error:
            raise ArchivoInvalido("El archivo de texto está mal formado (por ejemplo, una celda enorme o comillas sin cerrar).")


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
    if len(contenido) > MAX_BYTES:
        raise ArchivoInvalido("El archivo supera el máximo de 5 MB.")
    candidatas = _filas_xlsx(contenido) if detectar_formato(contenido) == "xlsx" else _filas_texto(contenido)
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


# ----------------------------------------------------------------------------- numeros (D-50)
AMBIGUO = object()  # valor de texto que no se puede leer con certeza: la fila queda en error
_ENTERO = re.compile(r"[0-9]+", re.ASCII)
_GRUPOS_PUNTO = re.compile(r"[0-9]{1,3}(\.[0-9]{3})+", re.ASCII)
_GRUPOS_COMA = re.compile(r"[0-9]{1,3}(,[0-9]{3})+", re.ASCII)
_CARACTERES = re.compile(r"[0-9.,]+", re.ASCII)


def _limpiar_texto_numerico(valor):
    t = str(valor).strip().replace("$", "").replace(" ", "").replace("\xa0", "")
    signo = ""
    if t[:1] in ("+", "-"):
        signo, t = ("-" if t[0] == "-" else ""), t[1:]
    return signo, t


def _evidencia(t):
    """Que dice un texto numerico sobre el separador de su columna:
    «coma» (la coma es decimal, el punto es de miles), «punto» (el punto es decimal,
    la coma es de miles) o None (no dice nada)."""
    if not _CARACTERES.fullmatch(t):
        return None
    if "," in t and "." in t:
        return "coma" if t.rfind(",") > t.rfind(".") else "punto"
    if "," in t:
        if t.count(",") > 1:
            return None   # miles con coma: solo vale si OTRA celda de la columna lo confirma
        entero, dec = t.split(",")
        return "coma" if (len(dec) != 3 or (len(entero) > 1 and entero.startswith("0")) or entero in ("", "0")) else None
    if "." in t:
        if t.count(".") > 1:
            return None
        entero, dec = t.split(".")
        return "punto" if (len(dec) != 3 or (len(entero) > 1 and entero.startswith("0")) or entero in ("", "0")) else None
    return None


def detectar_modo(textos):
    """D-50: una vez por archivo y columna numerica. Devuelve «coma» (punto = miles),
    «punto» (punto = decimal) o None si no hay evidencia (o hay evidencias opuestas)."""
    ev = {_evidencia(_limpiar_texto_numerico(t)[1]) for t in textos if isinstance(t, str) and t.strip()}
    ev.discard(None)
    return ev.pop() if len(ev) == 1 else None


def parsear_decimal(valor, modo=None):
    """Convierte a Decimal SIN pasar por float para textos. Devuelve Decimal, None (no numerico)
    o AMBIGUO (un texto que podria leerse de dos formas y la columna no lo aclara).

    Los numeros nativos (int, float de Excel, Decimal) no cambian. Para textos, `modo` viene de
    detectar_modo(): «coma» (1.234,50), «punto» (1,234.50) o None. Un texto autoexplicativo
    («1,5», «2.75», «0.500», «1.234,50») se lee igual con cualquier modo."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, Decimal):
        return valor if valor.is_finite() else None
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        return Decimal(repr(valor)) if valor == valor and abs(valor) != float("inf") else None
    signo, t = _limpiar_texto_numerico(valor)
    if not t or not _CARACTERES.fullmatch(t):
        return None
    if _ENTERO.fullmatch(t):
        return Decimal(signo + t)
    ambiguo = False
    if "," in t and "." in t:
        decimal = "," if t.rfind(",") > t.rfind(".") else "."
        miles = "." if decimal == "," else ","
        resto_miles = t[: t.rfind(decimal)]
        if resto_miles.count(decimal) or not re.fullmatch(r"[0-9]{1,3}([.,][0-9]{3})*", resto_miles, re.ASCII):
            return None
        t = t.replace(miles, "").replace(decimal, ".")
    else:
        sep = "," if "," in t else "."
        if t.count(sep) > 1:  # solo puede ser separador de miles
            patron = _GRUPOS_COMA if sep == "," else _GRUPOS_PUNTO
            if not patron.fullmatch(t):
                return None
            # miles con coma exige que la columna lo indique; miles con punto no si la columna dice «punto»
            if (sep == "," and modo != "punto") or (sep == "." and modo == "punto"):
                return AMBIGUO
            t = t.replace(sep, "")
        else:
            entero, dec = t.split(sep)
            es_decimal = len(dec) != 3 or (len(entero) > 1 and entero.startswith("0")) or entero in ("", "0")
            if es_decimal:
                if entero == "":
                    entero = "0"
                t = f"{entero}.{dec}"
            elif (sep == "." and modo == "coma") or (sep == "," and modo == "punto"):
                t = entero + dec          # tres cifras: separador de miles segun la columna
            elif (sep == "." and modo == "punto") or (sep == "," and modo == "coma"):
                t = f"{entero}.{dec}"     # decimal con tres cifras (ej. 1.500 en una columna con punto decimal)
            else:
                ambiguo = True
        if ambiguo:
            return AMBIGUO
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

    CAMPOS_GUARDADOS = ("numero", "codigo", "descripcion", "departamento", "costo", "precio_venta",
                        "precio_mayoreo", "inventario", "minimo", "errores", "advertencias")

    def a_dict(self):
        """Forma guardable en JSON (los Decimal van como texto)."""
        d = {}
        for k in self.CAMPOS_GUARDADOS:
            v = getattr(self, k)
            d[k] = str(v) if isinstance(v, Decimal) else list(v) if isinstance(v, list) else v
        return d

    @classmethod
    def de_dict(cls, d):
        f = cls(numero=d["numero"])
        for k in cls.CAMPOS_GUARDADOS[1:]:
            v = d[k]
            if k in ("costo", "precio_venta", "precio_mayoreo", "minimo"):
                v = Decimal(v)
            elif k == "inventario":
                v = None if v is None else Decimal(v)
            setattr(f, k, v)
        return f


class Analisis(list):
    """Lista de Fila con las filas omitidas (totales) y la cantidad de filas vacias."""

    omitidas: list
    vacias: int

    def __init__(self, *a):
        super().__init__(*a)
        self.omitidas = []   # [{"fila": n, "texto": "..."}]
        self.vacias = 0

    def a_json(self):
        return {"filas": [f.a_dict() for f in self], "omitidas": self.omitidas, "vacias": self.vacias}

    @classmethod
    def de_json(cls, datos):
        a = cls(Fila.de_dict(d) for d in datos["filas"])
        a.omitidas, a.vacias = list(datos["omitidas"]), datos["vacias"]
        return a


def _texto(celda, advertencias, etiqueta):
    """Descripcion/departamento: se guardan tal cual (solo se recortan los extremos). Si empiezan
    con =, +, - o @ se advierte; la neutralizacion se aplica al EXPORTAR (D-49)."""
    if celda is None:
        return ""
    t = str(celda).strip()
    if t.startswith(PELIGROSOS):
        advertencias.append(
            f"{etiqueta} empieza con «{t[0]}»: se guarda igual; al exportar a planilla se neutralizará."
        )
    return t


def _codigo(celda, advertencias, errores):
    """D-49: los codigos NUNCA se alteran (solo se recortan los extremos). Booleanos y codigos
    que empiezan con =, +, - o @ son error de fila."""
    if celda is None:
        return ""
    if isinstance(celda, bool):
        errores.append("Código no válido (valor lógico): revisar en Eleventa.")
        return str(celda)
    if isinstance(celda, (int, float, Decimal)):
        d = Decimal(repr(celda)) if isinstance(celda, float) else Decimal(celda)
        texto = str(int(d)) if d == d.to_integral_value() else format(d, "f")
        advertencias.append(
            f"Código leído como número ({celda}): si tenía ceros al inicio (ej. «0077») se perdieron al abrirlo en Excel."
        )
        return normalizar_codigo(texto)
    t = normalizar_codigo(str(celda))
    if t.startswith(PELIGROSOS):
        errores.append("Código con símbolo inicial / no válido: revisar en Eleventa.")
    return t


def _monto(celda, modo, adv, errores, etiqueta, maximo=MAX_MONTO):
    if celda is None or (isinstance(celda, str) and not celda.strip()):
        return Decimal("0")
    d = parsear_decimal(celda, modo)
    if d is AMBIGUO:
        errores.append(f"{etiqueta}: valor ambiguo («{str(celda)[:30]}»): no se sabe si el punto o la coma es decimal.")
        return Decimal("0")
    if d is None or d < 0 or d > maximo:
        adv.append(f"{etiqueta}: valor no válido («{str(celda)[:30]}»), queda en 0.")
        return Decimal("0")
    r = _redondear(d)
    if r != d:
        adv.append(f"{etiqueta}: redondeado a 2 decimales ({celda}).")
    return r


def _valor(fila, mapa, clave):
    j = mapa.get(clave)
    return fila[j] if j is not None and j < len(fila) else None


def _es_vacia(fila):
    return all(c is None or str(c).strip() == "" for c in fila)


def _es_total(fila, mapa):
    """D-49/A3: fila de totales SOLO si el codigo esta vacio y la primera celda no vacia
    empieza con «total» (un producto con codigo «TOTAL5W30» o «Total» se importa)."""
    codigo = _valor(fila, mapa, "codigo")
    if codigo is not None and str(codigo).strip():
        return False
    primero = next((c for c in fila if c is not None and str(c).strip()), "")
    return _norm(primero).startswith("total")


COLUMNAS_NUMERICAS = ("costo", "precio_venta", "precio_mayoreo", "inventario", "minimo")


def analizar_contenido(contenido, nombre):
    """Lee el archivo y devuelve un Analisis (lista de Fila; no consulta la base)."""
    mapa, datos = leer_tabla(contenido, nombre)
    resultado = Analisis()
    utiles = []
    for numero, celdas in datos:
        if _es_vacia(celdas):
            resultado.vacias += 1
        elif _es_total(celdas, mapa):
            resultado.omitidas.append({"fila": numero, "texto": next(
                str(c).strip()[:60] for c in celdas if c is not None and str(c).strip())})
        else:
            utiles.append((numero, celdas))
    modos = {
        c: detectar_modo(_valor(celdas, mapa, c) for _, celdas in utiles) for c in COLUMNAS_NUMERICAS
    }
    for numero, celdas in utiles:
        f = Fila(numero=numero)
        adv, err = f.advertencias, f.errores
        f.codigo = _codigo(_valor(celdas, mapa, "codigo"), adv, err)
        f.descripcion = _texto(_valor(celdas, mapa, "descripcion"), adv, "Descripción")
        f.departamento = _texto(_valor(celdas, mapa, "departamento"), adv, "Departamento")
        f.costo = _monto(_valor(celdas, mapa, "costo"), modos["costo"], adv, err, "Precio costo")
        f.precio_venta = _monto(_valor(celdas, mapa, "precio_venta"), modos["precio_venta"], adv, err, "Precio venta")
        f.precio_mayoreo = _monto(_valor(celdas, mapa, "precio_mayoreo"), modos["precio_mayoreo"], adv, err, "Precio mayoreo")
        f.minimo = _monto(_valor(celdas, mapa, "minimo"), modos["minimo"], adv, err, "Inv. mínimo", MAX_CANTIDAD)
        inv_crudo = _valor(celdas, mapa, "inventario")
        inv = parsear_decimal(inv_crudo, modos["inventario"])
        if inv is AMBIGUO:
            err.append(f"Inventario: valor ambiguo («{str(inv_crudo)[:30]}»): no se sabe si el punto o la coma es decimal.")
            f.inventario = None
        elif inv is None or inv < 0 or inv > MAX_CANTIDAD:
            adv.append(f"Inventario no válido o negativo («{str(inv_crudo)[:30]}»): se importa sin stock.")
            f.inventario = None
        else:
            f.inventario = _redondear(inv)
            if f.inventario != inv:
                adv.append(f"Inventario redondeado a 2 decimales ({inv_crudo}).")
        if not f.codigo:
            err.append("Código vacío.")
        elif len(f.codigo) > 50:
            err.append("Código demasiado largo (máximo 50 caracteres).")
        if not f.descripcion:
            err.append("Descripción vacía.")
        elif len(f.descripcion) > 200:
            err.append("Descripción demasiado larga (máximo 200 caracteres).")
        if len(f.departamento) > 100:
            err.append("Departamento demasiado largo (máximo 100 caracteres).")
        if f.codigo and f.costo == 0 and not any("Precio costo" in e for e in err):
            adv.append("Costo en 0: revisar (el costo alimenta el pedido sugerido).")
        resultado.append(f)
    # duplicados dentro del archivo: ambas (todas) las filas quedan en error
    por_codigo = {}
    for f in resultado:
        if f.codigo:
            por_codigo.setdefault(f.codigo, []).append(f)
    for codigo, grupo in por_codigo.items():
        if len(grupo) > 1:
            donde = ", ".join(str(g.numero) for g in grupo)
            for g in grupo:
                g.errores.append(f"Código duplicado en el archivo (filas {donde}).")
    return resultado


def _en_lotes(valores, n=500):
    valores = list(valores)
    for i in range(0, len(valores), n):
        yield valores[i:i + n]


def clasificar(filas):
    """Marca cada fila como nueva/existente/error consultando la base (stock actual de las existentes)."""
    skus = [f.codigo for f in filas if f.codigo and f.valida]
    existentes = {}
    for lote in _en_lotes(skus):
        for p in servicios.productos_con_stock(Producto.objects.filter(sku__in=lote)):
            existentes[p.sku] = p
    for f in filas:
        f.estado, f.stock_sistema, f.diferencia = "nueva", None, None
        if not f.valida:
            f.estado = "error"
            continue
        p = existentes.get(f.codigo)
        if p is None:
            continue
        f.estado = "existente"
        f.stock_sistema = Decimal(p.stock).quantize(CENTAVO)
        if f.inventario is not None:
            f.diferencia = f.inventario - f.stock_sistema
    return filas


def resumir(filas):
    nuevas = [f for f in filas if f.estado == "nueva"]
    r = {
        "filas": len(filas),
        "nuevas": len(nuevas),
        "existentes": sum(f.estado == "existente" for f in filas),
        "con_advertencia": sum(bool(f.advertencias) and f.valida for f in filas),
        "con_error": sum(f.estado == "error" for f in filas),
        "unidades": str(sum((f.inventario or Decimal("0") for f in nuevas), Decimal("0"))),
    }
    if isinstance(filas, Analisis):
        r["omitidas"], r["vacias"] = len(filas.omitidas), filas.vacias
    return r


def _leer(importacion):
    with importacion.archivo.open("rb") as a:
        return a.read()


def analizar(importacion):
    """Lee el archivo guardado y lo analiza (la confirmacion siempre usa esto)."""
    return clasificar(analizar_contenido(_leer(importacion), importacion.nombre_original))


def cargar_analisis(importacion):
    """Vista previa: usa el analisis guardado al subir (sin releer el archivo)."""
    if importacion.analisis:
        return clasificar(Analisis.de_json(importacion.analisis))
    return analizar(importacion)


# ----------------------------------------------------------------------------- subida
def sha256(contenido):
    return hashlib.sha256(contenido).hexdigest()


def validar_subida(nombre, contenido):
    """Validaciones de la subida: extension, tamano, formato real y columnas. Devuelve el
    Analisis (para guardarlo). Lanza ArchivoInvalido con un mensaje claro."""
    n = (nombre or "").lower()
    if not n.endswith((".xls", ".xlsx", ".csv")):       # la extension solo filtra; el formato se decide por contenido
        raise ArchivoInvalido("Solo se aceptan archivos .xls, .xlsx o .csv.")
    if not contenido:
        raise ArchivoInvalido("El archivo está vacío.")
    if len(contenido) > MAX_BYTES:
        raise ArchivoInvalido("El archivo supera el máximo de 5 MB.")
    return analizar_contenido(contenido, nombre)


# ----------------------------------------------------------------------------- confirmacion
class ImportacionNoValida(ValidationError):
    pass


def _exigir_importar(usuario):
    if not (getattr(usuario, "is_authenticated", False) and usuario.is_active
            and usuario.has_perm("inventario.importar_eleventa")):
        raise PermissionDenied("No tienes permiso para importar desde Eleventa.")


def confirmar(importacion_id, usuario, actualizar_existentes=False):
    """Aplica la carga inicial (D-48, D-51). TODO O NADA: una sola transaccion de escritura.

    1. FUERA de la transaccion: se lee el archivo guardado, se comprueba su hash y se analiza.
    2. Dentro: se bloquea la importacion, se vuelve a comprobar que sku existen, y se crean
       categorias y productos con bulk_create (validando cada objeto) y el inventario inicial
       con servicios.registrar_inventario_inicial_en_lote (permiso D-39, nunca permitir_negativo).
    El numero de consultas no crece con las filas (solo con lotes de 500)."""
    _exigir_importar(usuario)
    imp = ImportacionEleventa.objects.get(pk=importacion_id)
    if imp.estado != ImportacionEleventa.VISTA_PREVIA:
        raise ImportacionNoValida("Esta importación ya no está en vista previa.")
    contenido = _leer(imp)
    if sha256(contenido) != imp.hash_sha256:
        raise ImportacionNoValida("El archivo guardado no coincide con el que se subió: vuelve a subirlo.")
    filas = analizar_contenido(contenido, imp.nombre_original)          # fuera de la transaccion
    try:
        return _aplicar(importacion_id, usuario, filas, actualizar_existentes)
    except IntegrityError:
        raise ImportacionNoValida(
            "Otro usuario creó uno de estos códigos mientras confirmabas: no se importó nada. "
            "Vuelve a revisar la vista previa."
        )


def _aplicar(importacion_id, usuario, filas, actualizar_existentes):
    with transaction.atomic():
        imp = ImportacionEleventa.objects.select_for_update().get(pk=importacion_id)
        if imp.estado != ImportacionEleventa.VISTA_PREVIA:
            raise ImportacionNoValida("Esta importación ya no está en vista previa.")
        if ImportacionEleventa.objects.filter(
            hash_sha256=imp.hash_sha256, estado=ImportacionEleventa.CONFIRMADA
        ).exists():
            raise ImportacionNoValida("Este archivo ya fue importado antes.")
        clasificar(filas)                                                 # que sku existen AHORA
        nuevas = [f for f in filas if f.estado == "nueva"]
        existentes = [f for f in filas if f.estado == "existente"]
        # categorias (comparadas sin mayusculas ni espacios)
        categorias = {clave_nombre(c.nombre): c for c in Categoria.objects.all()}
        faltan = {}
        for f in nuevas:
            if f.departamento:
                faltan.setdefault(clave_nombre(f.departamento), f.departamento)
        faltan = {k: v for k, v in faltan.items() if k not in categorias}
        if faltan:
            creadas = [Categoria(nombre=v) for v in faltan.values()]
            for c in creadas:
                c.full_clean(validate_unique=False, validate_constraints=False)
            for k, c in zip(faltan, Categoria.objects.bulk_create(creadas, batch_size=500)):
                categorias[k] = c
        # productos nuevos
        productos = []
        for f in nuevas:
            p = Producto(
                sku=f.codigo, nombre=f.descripcion, costo=f.costo, precio_venta=f.precio_venta,
                precio_mayoreo=f.precio_mayoreo, stock_min=f.minimo, stock_max=f.minimo,  # sin maximo en Eleventa
                categoria=categorias[clave_nombre(f.departamento)] if f.departamento else None,
            )
            p.full_clean(exclude=["categoria"], validate_unique=False, validate_constraints=False)
            productos.append(p)
        Producto.objects.bulk_create(productos, batch_size=500)
        # existentes: nunca se toca el stock; los datos solo si el usuario lo pidio
        actualizados = 0
        diferencias = [
            {"codigo": f.codigo, "archivo": str(f.inventario), "sistema": str(f.stock_sistema),
             "diferencia": str(f.diferencia)}
            for f in existentes if f.diferencia is not None and f.diferencia != 0
        ]
        if actualizar_existentes and existentes:
            por_sku = {}
            for lote in _en_lotes([f.codigo for f in existentes]):
                por_sku.update({p.sku: p for p in Producto.objects.filter(sku__in=lote)})
            cambiados = []
            for f in existentes:
                p = por_sku[f.codigo]
                p.nombre, p.costo = f.descripcion, f.costo
                p.precio_venta, p.precio_mayoreo, p.stock_min = f.precio_venta, f.precio_mayoreo, f.minimo
                if p.stock_max < p.stock_min:
                    p.stock_max = p.stock_min  # el maximo no puede quedar bajo el minimo
                p.full_clean(exclude=["categoria"], validate_unique=False, validate_constraints=False)
                cambiados.append(p)
            Producto.objects.bulk_update(
                cambiados, ["nombre", "costo", "precio_venta", "precio_mayoreo", "stock_min", "stock_max"],
                batch_size=500,
            )
            actualizados = len(cambiados)
        # inventario inicial (solo productos nuevos con inventario > 0)
        con_stock = [(p, f.inventario) for p, f in zip(productos, nuevas)
                     if f.inventario is not None and f.inventario > 0]
        documento = None
        if con_stock:
            documento = Documento.objects.create(
                tipo=Documento.INVENTARIO_INICIAL,
                nombre=f"Inventario inicial Eleventa - {imp.nombre_original[:150]}",
                fecha=timezone.localdate(),
            )
            servicios.registrar_inventario_inicial_en_lote(con_stock, usuario, documento)
        vistos = (imp.analisis or {}).get("existentes_vistos")
        pasaron = (len({f.codigo for f in existentes} - set(vistos)) if vistos is not None else 0)
        resumen = resumir(filas)
        resumen.update({
            "pasaron_a_existentes": pasaron,
            "creados": len(productos), "actualizados": actualizados, "productos_con_stock": len(con_stock),
            "unidades_cargadas": str(sum((c for _, c in con_stock), Decimal("0"))),
            "diferencias": diferencias,
            "errores": [{"fila": f.numero, "codigo": f.codigo, "mensajes": f.errores}
                        for f in filas if f.estado == "error"],
            "omitidas": filas.omitidas, "vacias": filas.vacias,
            "actualizar_existentes": bool(actualizar_existentes),
            "documento_id": documento.pk if documento else None,
        })
        imp.estado = ImportacionEleventa.CONFIRMADA
        imp.resumen = resumen
        imp.analisis = {}  # el detalle fila a fila ya no hace falta: el resumen conserva lo esencial
        imp.documento = documento
        imp.save()
        return resumen




def descartar(importacion_id, usuario):
    _exigir_importar(usuario)
    with transaction.atomic():
        imp = ImportacionEleventa.objects.select_for_update().get(pk=importacion_id)
        if imp.estado != ImportacionEleventa.VISTA_PREVIA:
            raise ImportacionNoValida("Esta importación ya no está en vista previa.")
        imp.estado = ImportacionEleventa.DESCARTADA
        imp.analisis = {}
        imp.save(update_fields=["estado", "analisis"])
