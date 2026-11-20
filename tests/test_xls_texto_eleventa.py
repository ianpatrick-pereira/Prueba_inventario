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
    with pytest.raises(imp.ArchivoInvalido, match="Codificación no reconocida"):
        imp.decodificar_texto(b"a\x81b")                          # byte sin definir en cp1252: se rechaza (sin Latin-1)


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


@pytest.mark.parametrize("contenido", [bytes(range(256)) * 8, b"Codigo\tDescripcion\x00\x01\x02", b"a\tb\x07c\n" ])
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


# ------------------------------------------------------------------ comillas de pulgadas (QUOTE_NONE solo con TAB)
DESCRIPCIONES = ['MANGUERA 1/2"', '"OFERTA" FILTRO DE AIRE', 'FILTRO 3/4" X 16', '"sin cerrar', 'TUBO 1" y 2" doble', 'CON "COMILLA" EN MEDIO']


def _filas_con_comillas():
    return [[f"Q{i}", d, "1.234,50", "2.500,00", "2.000,00", str(i + 1), "1", "Mangueras"] for i, d in enumerate(DESCRIPCIONES)]


@pytest.mark.parametrize("nl", ["\n", "\r\n", "\r"])
def test_xls_con_tab_y_comillas_sueltas_o_en_pares_separa_cada_fila_y_conserva_el_texto(nl):
    filas = imp.analizar_contenido(texto(filas=_filas_con_comillas(), nl=nl).encode("cp1252"), "FILTROS.xls")
    assert [f.codigo for f in filas] == [f"Q{i}" for i in range(len(DESCRIPCIONES))]          # ninguna fila se junto
    assert [f.descripcion for f in filas] == DESCRIPCIONES                                    # texto tal cual, comillas incluidas
    assert [f.inventario for f in filas] == [D(i + 1) for i in range(len(DESCRIPCIONES))]
    assert all(f.valida and f.costo == D("1234.50") for f in filas)


def test_la_comilla_suelta_no_se_come_caracteres_ni_filas_siguientes():
    contenido = (ENCABEZADO_REAL + '\nA1\tMANGUERA 1/2"\t1\t1\t1\t5\t0\tMang\nA2\tOTRA\t1\t1\t1\t6\t0\tMang\nA3\t"abierta\t1\t1\t1\t7\t0\tMang\nA4\tFINAL\t1\t1\t1\t8\t0\tMang\n').encode("utf-8")
    filas = imp.analizar_contenido(contenido, "F.xls")
    assert [(f.codigo, f.descripcion, f.inventario) for f in filas] == [
        ("A1", 'MANGUERA 1/2"', D(5)), ("A2", "OTRA", D(6)), ("A3", '"abierta', D(7)), ("A4", "FINAL", D(8))]


def test_subida_confirmacion_de_un_xls_con_comillas_de_pulgadas(adm):
    r = subir(adm, texto(filas=_filas_con_comillas()).encode("cp1252"), "FILTROS.xls")
    assert r.status_code == 302
    i = ImportacionEleventa.objects.get()
    url = reverse("importar_vista_previa", args=[i.pk])
    token = re.search(r'name="token" value="([^"]+)"', adm.get(url).content.decode()).group(1)
    adm.post(url, {"token": token, "accion": "confirmar"})
    assert dict(Producto.objects.values_list("sku", "nombre")) == {f"Q{i}": d for i, d in enumerate(DESCRIPCIONES)}


def test_csv_con_coma_y_campos_entre_comillas_sigue_funcionando():
    contenido = (
        "Codigo,Descripcion,Precio Costo,Precio Venta,Precio Mayoreo,Inventario,Inv. Minimo,Departamento\n"
        '0077,"Filtro, de aceite",1234.50,2500.00,2000.00,10,2,"Filtros, varios"\n'
        'A100,"Manguera ""reforzada"" 1/2",5990.00,9990.00,8500.00,5,1,Aceites\n'
    ).encode("utf-8")
    filas = imp.analizar_contenido(contenido, "x.csv")
    assert [f.codigo for f in filas] == ["0077", "A100"]
    assert filas[0].descripcion == "Filtro, de aceite" and filas[0].departamento == "Filtros, varios"
    assert filas[1].descripcion == 'Manguera "reforzada" 1/2'                      # comillas dobles escapadas


def test_csv_con_punto_y_coma_y_campos_entre_comillas_sigue_funcionando():
    contenido = (
        "Codigo;Descripcion;Precio Costo;Precio Venta;Precio Mayoreo;Inventario;Inv. Minimo;Departamento\n"
        '0077;"Filtro; de aceite";1.234,50;2.500,00;2.000,00;10;2;Filtros\n'
    ).encode("utf-8")
    filas = imp.analizar_contenido(contenido, "x.csv")
    assert filas[0].descripcion == "Filtro; de aceite" and filas[0].costo == D("1234.50")


def test_con_tab_las_comillas_son_literales_y_con_punto_y_coma_se_respetan():
    tab = (ENCABEZADO_REAL + '\nA1\t"abierta\t1\t1\t1\t5\t0\tM\nA2\tOTRA 1/2"\t1\t1\t1\t6\t0\tM\n').encode("utf-8")
    filas = imp.analizar_contenido(tab, "F.xls")
    assert [(f.codigo, f.descripcion) for f in filas] == [("A1", '"abierta'), ("A2", 'OTRA 1/2"')]      # una comilla no junta filas
    pc = (ENCABEZADO_REAL.replace("\t", ";") + '\nA1;"con; punto y coma";1;1;1;5;0;M\nA2;"abierta;1;1;1;6;0;M\n').encode("utf-8")
    ok = imp.analizar_contenido(pc, "F.csv")[0]
    assert ok.codigo == "A1" and ok.descripcion == "con; punto y coma" and ok.inventario == D(5)      # «;» entre comillas: una celda


# ------------------------------------------------------------------ A1: nunca se corren valores (columnas de mas)
def test_tab_dentro_de_la_descripcion_es_error_y_no_carga_el_precio_como_inventario(adm):
    fila = "0077\tMANGUERA\t1/2 REFORZADA\t1.234,50\t2.500,00\t2.000,00\t10\t2\tFiltros"      # 9 celdas
    contenido = (ENCABEZADO_REAL + "\n" + fila + "\nB1\tNORMAL\t1.000,00\t2.000,00\t1.500,00\t3\t1\tAceites\n").encode("cp1252")
    a = imp.analizar_contenido(contenido, "F.xls")
    assert not a[0].valida and "La fila tiene 9 columnas y el encabezado 8: revisa la descripción en Eleventa." in a[0].errores
    assert a[1].valida
    subir(adm, contenido)
    i = ImportacionEleventa.objects.get()
    url = reverse("importar_vista_previa", args=[i.pk])
    token = re.search(r'name="token" value="([^"]+)"', adm.get(url).content.decode()).group(1)
    adm.post(url, {"token": token, "accion": "confirmar"})
    assert list(Producto.objects.values_list("sku", flat=True)) == ["B1"]                    # la fila corrida NO se cargo
    assert not Producto.objects.filter(sku="0077").exists()


def test_punto_y_coma_sin_comillas_dentro_de_la_descripcion_en_csv_es_error():
    cab = ENCABEZADO_REAL.replace("\t", ";")
    contenido = f"{cab}\n0077;MANGUERA; REFORZADA;1.234,50;2.500,00;2.000,00;10;2;Filtros\n".encode("utf-8")
    f = imp.analizar_contenido(contenido, "F.csv")[0]
    assert not f.valida and "La fila tiene 9 columnas y el encabezado 8" in f.errores[0]


def test_columnas_extra_vacias_al_final_se_aceptan():
    lineas = [ENCABEZADO_REAL, "0077\tFiltro\t1.234,50\t2.500,00\t2.000,00\t10\t2\tFiltros\t",
              "A100\tAceite\t1.000,00\t2.000,00\t1.500,00\t5\t1\tAceites\t\t\t"]
    filas = imp.analizar_contenido(("\n".join(lineas) + "\n").encode("cp1252"), "F.xls")
    assert [f.valida for f in filas] == [True, True] and filas[1].inventario == D(5)


def test_fila_mas_corta_se_acepta_con_lo_que_hay_si_estan_las_obligatorias():
    lineas = [ENCABEZADO_REAL, "0077\tFiltro\t1.234,50\t2.500,00\t2.000,00\t10", "A100\tAceite\t1.000,00\t2.000,00\t1.500,00\t5\t1"]
    f1, f2 = imp.analizar_contenido(("\n".join(lineas) + "\n").encode("cp1252"), "F.xls")
    assert f1.valida and f1.inventario == D(10) and f1.departamento == "" and f1.minimo == D(0)
    assert f2.valida and f2.minimo == D(1) and f2.departamento == ""
    corta = imp.analizar_contenido((ENCABEZADO_REAL + "\n0077\tFiltro\n").encode("cp1252"), "F.xls")[0]
    assert corta.valida is True and corta.inventario is None                                  # falta Inventario: sin stock, con aviso


def test_las_celdas_de_mas_en_xlsx_no_cambian_el_comportamiento():
    wb = Workbook()
    ws = wb.active
    ws.append(ENCABEZADO_REAL.split("\t"))
    ws.append(["0077", "Filtro", 1, 2, 3, 10, 2, "Filtros", "nota fuera de la tabla"])
    b = io.BytesIO()
    wb.save(b)
    assert imp.analizar_contenido(b.getvalue(), "F.xlsx")[0].valida


# ------------------------------------------------------------------ M1: codigos entre comillas y aviso de Excel
CASO_AUDITOR = '"0077"\t"MANGUERA 1/2"""\t"1,500.00"\t"2,500.00"\t"2,000.00"\t"7"\t"1"\t"Mangueras"'


def test_codigo_que_empieza_o_termina_con_comillas_es_error_de_fila():
    for codigo in ('"0077"', '"0077', '0077"'):
        f = imp.analizar_contenido((ENCABEZADO_REAL + f"\n{codigo}\tX\t1\t1\t1\t1\t0\tD\n").encode("utf-8"), "F.xls")[0]
        assert not f.valida and f.codigo == codigo
        assert "Código entre comillas: el archivo pasó por Excel; expórtalo de nuevo desde Eleventa." in f.errores
    ok = imp.analizar_contenido((ENCABEZADO_REAL + '\n0077\tX "1/2"\t1\t1\t1\t1\t0\tD\n').encode("utf-8"), "F.xls")[0]
    assert ok.valida                                                                          # comillas en la descripcion: normal


def test_el_caso_del_auditor_da_error_de_fila_y_aviso_general_de_excel(adm):
    contenido = (ENCABEZADO_REAL + "\n" + CASO_AUDITOR + "\n").encode("utf-8")
    a = imp.analizar_contenido(contenido, "F.xls")
    assert not a[0].valida and a[0].codigo == '"0077"' and a.avisos == [imp.AVISO_EXCEL]
    subir(adm, contenido)
    t = adm.get(reverse("importar_vista_previa", args=[ImportacionEleventa.objects.get().pk])).content.decode()
    assert "Parece que este archivo fue abierto y guardado en Excel. Para evitar errores, expórtalo de nuevo desde Eleventa y súbelo sin abrirlo." in t
    assert "Código entre comillas" in t


def test_el_aviso_general_solo_si_mas_de_la_mitad_viene_entre_comillas():
    normal = imp.analizar_contenido(texto().encode("utf-8"), "F.xls")
    assert normal.avisos == []
    mitad = "\t".join(['"A1"', '"Desc"', '"1"', '"1"', "1", "5", "0", "D"])                  # 4 de 8: justo la mitad, no mas
    assert imp.analizar_contenido((ENCABEZADO_REAL + "\n" + mitad + "\n").encode("utf-8"), "F.xls").avisos == []
    mas = "\t".join(['"A1"', '"Desc"', '"1"', '"1"', '"1"', "5", "0", "D"])                 # 5 de 8
    assert imp.analizar_contenido((ENCABEZADO_REAL + "\n" + mas + "\n").encode("utf-8"), "F.xls").avisos == [imp.AVISO_EXCEL]


def test_el_aviso_y_las_filas_se_guardan_con_el_analisis(adm):
    subir(adm, (ENCABEZADO_REAL + "\n" + CASO_AUDITOR + "\n").encode("utf-8"))
    i = ImportacionEleventa.objects.get()
    assert i.analisis["avisos"] == [imp.AVISO_EXCEL] and i.analisis["codificacion"] == "UTF-8"


# ------------------------------------------------------------------ M2: codificacion detectada y mojibake
def test_la_vista_previa_muestra_la_codificacion_detectada(adm):
    subir(adm, texto().encode("utf-8"), "A.xls")
    t = adm.get(reverse("importar_vista_previa", args=[ImportacionEleventa.objects.get().pk])).content.decode()
    assert "Codificación detectada: <strong>UTF-8</strong>" in t
    ImportacionEleventa.objects.all().delete()
    subir(adm, texto().encode("cp1252"), "B.xls")
    t = adm.get(reverse("importar_vista_previa", args=[ImportacionEleventa.objects.get().pk])).content.decode()
    assert "Codificación detectada: <strong>Windows-1252</strong>" in t


def test_archivo_mixto_marca_las_filas_con_posible_mala_decodificacion():
    cab = ENCABEZADO_REAL + "\n"
    fila_cp1252 = "A1\tAceite ñandú 5W30\t1\t1\t1\t5\t0\tD\n".encode("cp1252")                 # Windows-1252 puro
    fila_utf8 = "A2\tBujía iridio\t1\t1\t1\t6\t0\tD\n".encode("utf-8")                       # UTF-8 mezclado
    a = imp.analizar_contenido(cab.encode("cp1252") + fila_cp1252 + fila_utf8, "F.xls")
    assert a.codificacion == "Windows-1252"
    assert not any("mala decodificación" in x for x in a[0].advertencias)
    assert any("posible mala decodificación" in x for x in a[1].advertencias) and a[1].valida
    puro = imp.analizar_contenido(texto().encode("cp1252"), "F.xls")
    assert not any("mala decodificación" in x for f in puro for x in f.advertencias)


def test_con_utf8_no_se_busca_mala_decodificacion():
    a = imp.analizar_contenido((ENCABEZADO_REAL + "\nA1\tÃ© literal\t1\t1\t1\t5\t0\tD\n").encode("utf-8"), "F.xls")
    assert a.codificacion == "UTF-8" and not any("mala decodificación" in x for x in a[0].advertencias)


# ------------------------------------------------------------------ B1: controles C1 y bytes sin definir
@pytest.mark.parametrize("byte", [b"\x81", b"\x8d", b"\x8f", b"\x90", b"\x9d"])
def test_bytes_sin_definir_en_windows_1252_se_rechazan(adm, byte):
    contenido = ENCABEZADO_REAL.encode("cp1252") + b"\nA1\tx" + byte + b"y\t1\t1\t1\t5\t0\tD\n"
    r = subir(adm, contenido, "X.xls")
    assert r.status_code == 200 and "Codificación no reconocida" in r.content.decode()
    assert ImportacionEleventa.objects.count() == 0


def test_controles_c1_en_utf8_se_rechazan():
    contenido = (ENCABEZADO_REAL + "\nA1\tdesc\u0085con control\t1\t1\t1\t5\t0\tD\n").encode("utf-8")
    with pytest.raises(imp.ArchivoInvalido, match="Codificación no reconocida"):
        imp.analizar_contenido(contenido, "F.xls")


def test_los_caracteres_validos_de_windows_1252_no_se_rechazan():
    todo = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ¡¿ñÑ°"
    assert imp.decodificar_con_codificacion(todo.encode("cp1252")) == (todo, "Windows-1252")


# ------------------------------------------------------------------ B2: UTF-16 y relleno al final
@pytest.mark.parametrize("bom", [b"\xff\xfe", b"\xfe\xff"])
def test_utf16_con_bom_da_el_mensaje_especifico(adm, bom):
    codec = "utf-16-le" if bom == b"\xff\xfe" else "utf-16-be"
    r = subir(adm, bom + texto().encode(codec), "U.xls")
    assert "El archivo está en formato Unicode (UTF-16). Expórtalo de nuevo desde Eleventa sin abrirlo en Excel." in r.content.decode()
    assert ImportacionEleventa.objects.count() == 0


@pytest.mark.parametrize("final", [b"\x00", b"\x00\x00\x00", b"\x1a", b"\x1a\x00", b"\x00\x1a\x00"])
def test_nul_o_ctrl_z_al_final_se_quitan_y_se_sigue(adm, final):
    contenido = texto().encode("cp1252") + final
    esperar_filas(imp.analizar_contenido(contenido, "F.xls"))
    assert subir(adm, contenido, "R.xls").status_code == 302


def test_nul_o_ctrl_z_en_medio_siguen_siendo_binario():
    for malo in (b"\x00", b"\x1a"):
        con = texto().encode("cp1252").replace(b"Filtro", b"Fil" + malo + b"tro", 1)
        with pytest.raises(imp.ArchivoInvalido, match="no parece ser de texto"):
            imp.analizar_contenido(con, "F.xls")
