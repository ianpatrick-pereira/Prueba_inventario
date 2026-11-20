"""El importador acepta la exportacion real de Eleventa: texto separado por tabulaciones con extension
.xls (T-F1-19, D-59). Todos los archivos son FICTICIOS y se generan aqui."""
import io
import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook

from inventario import importar_eleventa as imp
from inventario import servicios
from inventario.models import ImportacionEleventa, Producto

pytestmark = pytest.mark.django_db
D = Decimal
# Linea de encabezados REAL de Eleventa (dato del responsable): 8 columnas, sin tildes, TAB.
ENCABEZADO_REAL = "Codigo\tDescripcion\tPrecio Costo\tPrecio Venta\tPrecio Mayoreo\tInventario\tInv. Minimo\tDepartamento"
FILAS = [
    ["0077", "Filtro de aceite ñandú 0°", "1.234,50", "2.500,00", "2.000,00", "10", "2", "Filtros"],
    ["A100", "Aceite “sintético” 5W30", "5.990,00", "9.990,00", "8.500,00", "5", "1", "Aceites"],
    ["B200", "Bujía 3€", "800,00", "1.500,00", "1.200,00", "0", "0", "Bujías"],
]


@pytest.fixture(autouse=True)
def media_tmp(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def adm(client):
    u = get_user_model().objects.create_user("adm", password="Zq7-mPx2vL-prueba")
    u.groups.add(Group.objects.get(name="Administrador"))
    client.force_login(u)
    return client


def texto(filas=FILAS, encabezado=ENCABEZADO_REAL, nl="\n", sep="\t", titulo=True, totales=True):
    lineas = (["REPORTE DE INVENTARIO (ficticio)", ""] if titulo else []) + [encabezado.replace("\t", sep)]
    lineas += [sep.join(f) for f in filas]
    if totales:
        lineas += ["", sep.join(["", "Totales", "", "", "", "99", "", ""])]
    return nl.join(lineas) + nl


def subir(client, contenido, nombre="FILTROS.xls"):
    return client.post(reverse("importar_eleventa"), {"archivo": SimpleUploadedFile(nombre, contenido)})


def xlsx_bytes():
    wb = Workbook()
    ws = wb.active
    ws.append(ENCABEZADO_REAL.split("\t"))
    for f in FILAS:
        ws.append(f)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def esperar_filas(filas):
    assert [f.codigo for f in filas] == ["0077", "A100", "B200"]
    assert filas[0].descripcion.startswith("Filtro de aceite ñandú") and filas[0].costo == D("1234.50")
    assert filas[1].precio_venta == D("9990") and filas[0].inventario == D("10") and filas[2].inventario == D("0")
    assert filas[0].departamento == "Filtros" and filas[0].minimo == D("2")


# ------------------------------------------------------------------ encabezados reales
def test_la_linea_de_encabezados_real_de_eleventa_se_reconoce_tal_cual():
    contenido = (ENCABEZADO_REAL + "\n" + "\t".join(FILAS[0]) + "\n").encode("cp1252")
    mapa, datos = imp.leer_tabla(contenido, "FILTROS.xls")
    assert mapa == {"codigo": 0, "descripcion": 1, "costo": 2, "precio_venta": 3, "precio_mayoreo": 4,
                    "inventario": 5, "minimo": 6, "departamento": 7}
    assert len(datos) == 1 and ENCABEZADO_REAL.count("\t") == 7                       # 8 columnas


@pytest.mark.parametrize("encabezado", [
    ENCABEZADO_REAL, ENCABEZADO_REAL.upper(), ENCABEZADO_REAL.lower(),
    "Codigo\tDescripcion\tPrecio  Costo\tPrecio venta\tPrecio mayoreo\tInventario\tInv Minimo\tDepartamento",
    "Código\tDescripción\tPrecio Costo\tPrecio Venta\tPrecio Mayoreo\tInventario\tInv. Mínimo\tDepartamento",
    " Codigo \tDescripcion\tPrecio.Costo\tPrecio-Venta\tPrecio Mayoreo\tInventario\tInv.Minimo\tDepartamento ",
])
def test_variantes_de_encabezado_sin_tilde_mayusculas_espacios_y_puntos(encabezado):
    filas = imp.analizar_contenido(texto(encabezado=encabezado).encode("utf-8"), "x.xls")
    esperar_filas(filas)


# ------------------------------------------------------------------ codificaciones y saltos de linea
@pytest.mark.parametrize("codec", ["utf-8", "utf-8-sig", "cp1252"])
def test_xls_de_texto_en_utf8_con_bom_y_windows_1252(codec):
    filas = imp.analizar_contenido(texto().encode(codec), "FILTROS.xls")
    esperar_filas(filas)
    assert filas[1].descripcion == "Aceite “sintético” 5W30" and filas[2].descripcion == "Bujía 3€"


def test_cp1252_se_decodifica_con_comillas_curvas_y_euro_no_como_latin1():
    assert imp.decodificar_texto("“x” €".encode("cp1252")) == "“x” €"
    assert imp.decodificar_texto("ñ°".encode("utf-8")) == "ñ°" and imp.decodificar_texto("ñ".encode("utf-8-sig")) == "ñ"
    assert imp.decodificar_texto(b"a\x81b") == "a\x81b"          # byte sin cp1252: ultimo recurso Latin-1, no falla


@pytest.mark.parametrize("nl", ["\n", "\r\n", "\r"])
def test_saltos_de_linea_lf_crlf_y_cr_solo(nl):
    esperar_filas(imp.analizar_contenido(texto(nl=nl).encode("cp1252"), "FILTROS.xls"))


@pytest.mark.parametrize("nl", ["\n", "\r\n", "\r"])
def test_cr_solo_tambien_funciona_en_csv_con_punto_y_coma(nl):
    filas = imp.analizar_contenido(texto(nl=nl, sep=";").encode("utf-8"), "x.csv")
    esperar_filas(filas)


@pytest.mark.parametrize("sep", ["\t", ";", ","])
def test_el_separador_se_detecta_entre_tab_punto_y_coma_y_coma(sep):
    contenido = texto(sep=sep, filas=[[c.replace(",", ".") if sep == "," else c for c in f] for f in FILAS])
    filas = imp.analizar_contenido(contenido.encode("utf-8"), "x.csv")
    assert [f.codigo for f in filas] == ["0077", "A100", "B200"]


def test_csv_con_tabulaciones_se_acepta(adm):
    r = subir(adm, texto().encode("cp1252"), "inventario.csv")
    assert r.status_code == 302 and ImportacionEleventa.objects.count() == 1


# ------------------------------------------------------------------ deteccion por contenido
def test_deteccion_de_formato_por_contenido():
    assert imp.detectar_formato(xlsx_bytes()) == "xlsx"
    assert imp.detectar_formato(texto().encode("utf-8")) == "texto"
    assert imp.detectar_formato(b"") == "texto"
    with pytest.raises(imp.ArchivoInvalido) as e:
        imp.detectar_formato(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
    assert "Excel 97-2003" in e.value.messages[0]


def test_xls_con_firma_ole_da_mensaje_claro_y_no_crea_importacion(adm):
    r = subir(adm, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 1024, "REAL.xls")
    t = r.content.decode()
    assert r.status_code == 200 and "Este archivo es Excel 97-2003, que el sistema no lee." in t
    assert "Expórtalo de nuevo desde Eleventa o guárdalo como .xlsx" in t
    assert ImportacionEleventa.objects.count() == 0


@pytest.mark.parametrize("contenido", [bytes(range(256)) * 8, b"Codigo\tDescripcion\x00\x01\x02", b"a\tb\x07c\n", b"\xff\xfeC\x00o\x00"])
def test_xls_binario_o_con_basura_se_rechaza(adm, contenido):
    r = subir(adm, contenido, "BASURA.xls")
    assert r.status_code == 200 and "no parece ser de texto" in r.content.decode()
    assert ImportacionEleventa.objects.count() == 0


def test_tab_cr_y_lf_no_cuentan_como_binario():
    assert imp.detectar_formato(b"a\tb\r\nc\td\r") == "texto"


def test_xlsx_renombrado_a_xls_se_lee_como_xlsx(adm):
    r = subir(adm, xlsx_bytes(), "RENOMBRADO.xls")
    assert r.status_code == 302
    i = ImportacionEleventa.objects.get()
    assert i.archivo.path.endswith(".xlsx") and len(i.analisis["filas"]) == 3
    esperar_filas(imp.analizar_contenido(xlsx_bytes(), "x.xls"))


def test_el_formato_se_decide_por_contenido_aunque_la_extension_mienta():
    esperar_filas(imp.analizar_contenido(texto().encode("utf-8"), "falso.xlsx"))       # texto con extension .xlsx
    esperar_filas(imp.analizar_contenido(xlsx_bytes(), "falso.csv"))                   # xlsx con extension .csv


# ------------------------------------------------------------------ subida, nombre guardado y confirmacion
def test_subida_de_xls_de_texto_guarda_con_nombre_aleatorio_y_extension_del_formato(adm):
    r = subir(adm, texto().encode("cp1252"), "FILTROS.xls")
    assert r.status_code == 302
    i = ImportacionEleventa.objects.get()
    assert re.search(r"importaciones[/\\][0-9a-f]{32}\.csv$", i.archivo.path)
    assert "FILTROS" not in i.archivo.path and not i.archivo.path.lower().endswith(".xls")
    assert i.nombre_original == "FILTROS.xls"                       # solo para mostrar
    assert i.analisis["filas"][0]["codigo"] == "0077"


def test_el_codigo_0077_se_conserva_en_el_texto(adm):
    r = subir(adm, texto().encode("cp1252"))
    i = ImportacionEleventa.objects.get()
    assert i.analisis["filas"][0]["codigo"] == "0077" and r.status_code == 302


def test_confirmar_una_importacion_de_xls_de_texto_crea_los_productos(adm):
    subir(adm, texto().encode("cp1252"))
    i = ImportacionEleventa.objects.get()
    url = reverse("importar_vista_previa", args=[i.pk])
    token = re.search(r'name="token" value="([^"]+)"', adm.get(url).content.decode()).group(1)
    r = adm.post(url, {"token": token, "accion": "confirmar"})
    assert r.status_code == 302
    assert sorted(Producto.objects.values_list("sku", flat=True)) == ["0077", "A100", "B200"]
    p = Producto.objects.get(sku="0077")
    assert p.nombre.startswith("Filtro de aceite ñandú") and p.costo == D("1234.50")
    assert servicios.stock_de(p) == D("10.00") and p.categoria.nombre == "Filtros"
    assert Producto.objects.get(sku="A100").nombre == "Aceite “sintético” 5W30"


def test_extensiones_permitidas_y_rechazadas(adm):
    for nombre in ("A.XLS", "a.xlsx", "a.csv"):
        assert subir(adm, texto().encode("utf-8") + nombre.encode(), nombre).status_code == 302, nombre
    r = subir(adm, texto().encode("utf-8"), "datos.txt")
    assert "Solo se aceptan archivos .xls, .xlsx o .csv" in r.content.decode()


def test_la_pantalla_de_subida_dice_xls_xlsx_o_csv(adm):
    assert "Archivo exportado de Eleventa (.xls, .xlsx o .csv, máximo 5 MB)" in adm.get(reverse("importar_eleventa")).content.decode()


# ------------------------------------------------------------------ D-48 a D-51 siguen valiendo
def test_d50_separador_decimal_por_columna_en_el_texto():
    filas = [["C1", "x", "1.500", "10", "10", "1", "0", ""], ["C2", "y", "2,5", "10", "10", "1", "0", ""]]
    a = imp.analizar_contenido(texto(filas=filas).encode("cp1252"), "F.xls")
    assert a[0].costo == D("1500") and a[1].costo == D("2.5") and a[0].valida                 # evidencia en la columna
    solo = [["C1", "x", "1.500", "10", "10", "1", "0", ""]]
    amb = imp.analizar_contenido(texto(filas=solo).encode("cp1252"), "F.xls")[0]
    assert not amb.valida and "ambiguo" in amb.errores[0]                                  # sin evidencia: error


def test_los_codigos_nunca_se_alteran_y_el_saneo_sigue_igual():
    filas = [["-123", "Con menos", "1", "1", "1", "1", "0", ""], ["AB  12", "Doble espacio", "1", "1", "1", "1", "0", ""],
             ["X1", "=SUMA(A1)", "1", "1", "1", "1", "0", ""]]
    a = imp.analizar_contenido(texto(filas=filas).encode("cp1252"), "F.xls")
    assert not a[0].valida and a[0].codigo == "-123"
    assert a[1].codigo == "AB  12" and a[1].valida
    assert a[2].descripcion == "=SUMA(A1)" and any("se guarda igual" in x for x in a[2].advertencias)


def test_filas_de_totales_y_vacias_se_omiten_en_el_texto():
    a = imp.analizar_contenido(texto().encode("cp1252"), "F.xls")
    assert [o["texto"] for o in a.omitidas] == ["Totales"] and a.vacias == 1


def test_texto_enorme_en_una_celda_es_error_no_500(adm):
    grande = (ENCABEZADO_REAL + "\nA\t" + "x" * 200000 + "\t1\t1\t1\t1\t0\tD\n").encode("utf-8")
    r = subir(adm, grande, "G.xls")
    assert r.status_code == 200 and "mal formado" in r.content.decode() and ImportacionEleventa.objects.count() == 0


def test_mas_de_5_mb_sigue_rechazado_en_xls(adm):
    r = subir(adm, b"a" * (5 * 1024 * 1024 + 1), "G.xls")
    assert "5 MB" in r.content.decode() and ImportacionEleventa.objects.count() == 0
