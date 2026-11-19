"""Carga inicial desde Eleventa (T-F1-08; D-46, D-47, D-48). TODOS los archivos son ficticios y se
generan aqui; nunca se lee ni se sube un archivo real."""
import io
import re
import subprocess
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.urls import reverse
from openpyxl import Workbook

from inventario import importar_eleventa as imp
from inventario import servicios
from inventario.models import Categoria, Documento, ImportacionEleventa, Movimiento, Producto

pytestmark = pytest.mark.django_db
D = Decimal
CLAVE = "Zq7-mPx2vL-prueba"
ENCABEZADOS = ["Código", "Descripción", "Precio costo", "Precio venta", "Precio mayoreo",
               "Inventario", "Inv. mínimo", "Departamento"]


def crear_usuario(nombre, grupo=None):
    u = get_user_model().objects.create_user(nombre, password=CLAVE)
    if grupo:
        u.groups.add(Group.objects.get(name=grupo))
    return u


@pytest.fixture(autouse=True)
def media_tmp(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def admin_user():
    return crear_usuario("adm", "Administrador")


@pytest.fixture
def adm(client, admin_user):
    client.force_login(admin_user)
    return client


def xlsx(filas, encabezados=ENCABEZADOS, titulo=("REPORTE DE INVENTARIO (ficticio)", "", "Fecha: 01/01/2026"),
         totales=True, hoja="Hoja1"):
    wb = Workbook()
    ws = wb.active
    ws.title = hoja
    for t in titulo:
        ws.append([t])
    ws.append(encabezados)
    for f in filas:
        ws.append(f)
    if totales:
        ws.append([])
        ws.append([None, "Totales", None, None, None, 99])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def csv_bytes(filas, sep=";", codec="utf-8", encabezados=ENCABEZADOS):
    lineas = ["REPORTE FICTICIO", ""] + [sep.join(encabezados)] + [
        sep.join("" if c is None else (f'"{c}"' if sep in str(c) else str(c)) for c in f) for f in filas
    ] + ["", sep.join(["", "Totales", "", "", "", "99"])]
    return "\n".join(lineas).encode(codec)


FILAS = [
    ["0077", "Filtro de aceite", "$1.234", "2500", "2000", 10, 2, "Filtros"],
    ["A100", "Aceite 10W40", "1.234,50", "$5.990,00", "5.500,00", "5", "1", "Aceites"],
    ["B200", "Bujía", 800, 1500, 1200, 0, 0, "filtros "],
]


def subir(client, contenido, nombre="inventario.xlsx"):
    return client.post(reverse("importar_eleventa"), {"archivo": SimpleUploadedFile(nombre, contenido)})


def token_de(r):
    return re.search(r'name="token" value="([^"]+)"', r.content.decode()).group(1)


def preparar(client, contenido=None, nombre="inventario.xlsx"):
    r = subir(client, contenido or xlsx(FILAS), nombre)
    assert r.status_code == 302, r.content.decode()[:500]
    return ImportacionEleventa.objects.order_by("-pk").first()


def confirmar_http(client, i, actualizar=False):
    url = reverse("importar_vista_previa", args=[i.pk])
    datos = {"token": token_de(client.get(url)), "accion": "confirmar"}
    if actualizar:
        datos["actualizar_existentes"] = "on"
    return client.post(url, datos)


# ------------------------------------------------------------------ 1. lectura de xlsx y csv
@pytest.mark.parametrize("contenido,nombre", [
    (xlsx(FILAS), "a.xlsx"),
    (csv_bytes(FILAS), "a.csv"),
    (csv_bytes(FILAS, sep=","), "b.csv"),
    (csv_bytes(FILAS, codec="latin-1"), "c.csv"),
    (csv_bytes(FILAS, sep=",", codec="latin-1"), "d.csv"),
    (csv_bytes(FILAS, codec="utf-8-sig"), "e.csv"),
])
def test_lee_xlsx_y_csv_con_titulos_y_totales(contenido, nombre):
    filas = imp.analizar_contenido(contenido, nombre)
    assert [f.codigo for f in filas] == ["0077", "A100", "B200"]      # sin fila de titulo, vacia ni totales
    assert filas[0].descripcion == "Filtro de aceite" and filas[2].descripcion == "Bujía"
    assert filas[0].costo == D("1234") and filas[1].costo == D("1234.50")
    assert filas[0].inventario == D("10") and filas[1].inventario == D("5") and filas[2].inventario == D("0")
    assert filas[0].minimo == D("2") and filas[0].departamento == "Filtros"
    assert filas[0].numero > 3  # numero de fila real del archivo


def test_ubica_encabezados_con_variaciones():
    raros = ["  CODIGO ", "descripción", "PRECIO  COSTO", "Precio Venta", "precio mayoreo", "INVENTARIO",
             "inv minimo", "DEPARTAMENTO"]
    filas = imp.analizar_contenido(xlsx(FILAS, encabezados=raros), "x.xlsx")
    assert len(filas) == 3 and filas[1].codigo == "A100"


def test_encabezados_al_final_de_muchas_filas_de_titulo():
    filas = imp.analizar_contenido(
        xlsx(FILAS, titulo=[f"linea de titulo {i}" for i in range(20)]), "x.xlsx")
    assert len(filas) == 3


def test_xlsx_toma_la_hoja_que_tiene_los_encabezados():
    wb = Workbook()
    wb.active.title = "Portada"
    wb.active.append(["nada"])
    ws = wb.create_sheet("Datos")
    ws.append(ENCABEZADOS)
    ws.append(["Z1", "Cosa", 1, 2, 3, 4, 0, "D"])
    b = io.BytesIO()
    wb.save(b)
    assert [f.codigo for f in imp.analizar_contenido(b.getvalue(), "x.xlsx")] == ["Z1"]


# ------------------------------------------------------------------ 2. numeros
@pytest.mark.parametrize("entrada,modo,esperado", [
    ("$1.234", "coma", D("1234")), ("1.234,50", None, D("1234.50")), ("1234.5", None, D("1234.5")),
    (1234, None, D("1234")), ("$ 12.345.678", None, D("12345678")), ("12,5", None, D("12.5")),
    ("0,5", None, D("0.5")), ("1,234.56", None, D("1234.56")), (D("7.25"), None, D("7.25")),
    (1234.5, None, D("1234.5")), ("-5", None, D("-5")), ("  42 ", None, D("42")),
    ("0.500", None, D("0.5")), ("0.500", "coma", D("0.5")), (".5", None, D("0.5")), ("2.75", None, D("2.75")),
    ("1.005", "coma", D("1005")), ("1.005", "punto", D("1.005")),
    ("1,234", "punto", D("1234")), ("1,234", "coma", D("1.234")),
    ("1,234,567", "punto", D("1234567")), ("1.234.567", "coma", D("1234567")), ("1.234.567", None, D("1234567")),
])
def test_parsear_decimal(entrada, modo, esperado):
    r = imp.parsear_decimal(entrada, modo)
    assert r == esperado and isinstance(r, Decimal)


@pytest.mark.parametrize("texto,modo", [
    ("1.500", None), ("1.005", None), ("1,234", None), ("1,234,567", None), ("1,234,567", "coma"),
    ("1.234.567", "punto"), ("$1.234", None),
])
def test_parsear_decimal_ambiguo_es_error_nunca_conversion_silenciosa(texto, modo):
    assert imp.parsear_decimal(texto, modo) is imp.AMBIGUO


@pytest.mark.parametrize("columna,modo", [
    (["1,5", "2.000"], "coma"), (["1.234,50"], "coma"), (["2,5"], "coma"), (["2.75"], "punto"),
    (["0.500"], "punto"), (["1,234,567"], None), (["1,234,567", "2.75"], "punto"), (["1,234.5"], "punto"), (["1.500", "2.000"], None),
    (["1,5", "2.75"], None), ([], None), (["10", "20"], None),
])
def test_detectar_modo_por_columna(columna, modo):
    assert imp.detectar_modo(columna) == modo


def test_decimal_de_float_no_arrastra_error_binario():
    assert imp.parsear_decimal(0.1 + 0.2) != D("0.3")  # el float ya perdio precision antes de leerlo...
    assert imp.parsear_decimal(0.1) == D("0.1")        # ...pero un valor de Excel se lee sin ruido


# ------------------------------------------------------------------ 3. codigos
def test_codigo_numerico_de_excel_se_convierte_con_advertencia_y_texto_se_conserva():
    filas = imp.analizar_contenido(xlsx([[77, "Numerico", 1, 1, 1, 1, 0, ""], [77.0, "Float", 1, 1, 1, 1, 0, ""],
                                         ["0077", "Texto", 1, 1, 1, 1, 0, ""]]), "x.xlsx")
    num, flt, texto = filas
    assert num.codigo == "77" and flt.codigo == "77"
    assert any("ceros" in a and "77" in a for a in num.advertencias)
    assert texto.codigo == "0077" and not any("ceros" in a for a in texto.advertencias)
    assert num.errores == [] or "duplicado" in num.errores[0]  # 77 y 77.0 chocan entre si, es esperado


def test_codigo_texto_con_ceros_en_csv_se_conserva():
    f = imp.analizar_contenido(csv_bytes([["0077", "x", 1, 1, 1, 1, 0, ""]]), "x.csv")[0]
    assert f.codigo == "0077"


# ------------------------------------------------------------------ 4. saneo y formulas
@pytest.mark.parametrize("peligroso", ["=SUMA(A1)", "+56 9", "-123", "@SUM", "-20% dto."])
def test_descripcion_y_departamento_se_guardan_tal_cual_con_advertencia(peligroso):
    f = imp.analizar_contenido(csv_bytes([["X1", peligroso, 1, 1, 1, 1, 0, peligroso]]), "x.csv")[0]
    assert f.descripcion == peligroso and f.departamento == peligroso and f.valida
    assert sum("se guarda igual" in a for a in f.advertencias) == 2


@pytest.mark.parametrize("codigo", ["-123", "+56 9", "@SUM", "=1+1", "  -9"])
def test_codigo_que_empieza_con_simbolo_es_error_y_no_se_altera(codigo):
    f = imp.analizar_contenido(csv_bytes([[codigo, "Algo", 1, 1, 1, 1, 0, ""]]), "x.csv")[0]
    assert f.codigo == codigo.strip() and not f.valida and f.errores[0].startswith("Código con símbolo inicial")


def test_codigo_booleano_de_xlsx_es_error():
    f = imp.analizar_contenido(xlsx([[True, "Verdadero", 1, 1, 1, 1, 0, ""]]), "x.xlsx")[0]
    assert not f.valida and "valor lógico" in f.errores[0]


def test_codigo_conserva_espacios_internos_y_solo_se_recorta():
    f = imp.analizar_contenido(csv_bytes([["  AB  12 ", "Doble espacio", 1, 1, 1, 1, 0, ""]]), "x.csv")[0]
    assert f.codigo == "AB  12" and f.valida


@pytest.mark.parametrize("actualizar", [False, True])
def test_codigo_con_menos_no_pisa_al_codigo_existente(adm, actualizar):
    p = Producto.objects.create(sku="123", nombre="Original", costo=D("5"))
    i = preparar(adm, xlsx([["-123", "Intento de pisar", 99, 99, 99, 50, 0, "Dep"]]))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    assert r.context["resumen"]["con_error"] == 1
    confirmar_http(adm, i, actualizar=actualizar)
    p.refresh_from_db()
    assert p.nombre == "Original" and p.costo == D("5") and Movimiento.objects.count() == 0
    assert Producto.objects.count() == 1 and not Categoria.objects.exists()
    assert ImportacionEleventa.objects.get().resumen["errores"][0]["codigo"] == "-123"


def test_neutralizar_para_planilla():
    from inventario.validadores import neutralizar_para_planilla as n

    assert n("=SUMA(A1)") == "'=SUMA(A1)" and n("+1") == "'+1" and n("-x") == "'-x" and n("@a") == "'@a"
    assert n("\tTAB") == "'\tTAB" and n("normal") == "normal" and n("") == "" and n(None) == "" and n(12) == "12"


def test_formula_en_xlsx_no_se_evalua():
    wb = Workbook()
    ws = wb.active
    ws.append(ENCABEZADOS)
    ws.append(["F1", "=1+1", 1, 1, 1, "=2+2", 0, ""])        # formulas reales (sin valor en cache)
    ws.append(["F2", "texto", 1, 1, 1, 1, 0, ""])
    c = ws.cell(row=3, column=2)
    c.value = "=2+2"
    c.data_type = "s"                                         # texto que SOLO parece formula
    b = io.BytesIO()
    wb.save(b)
    f1, f2 = imp.analizar_contenido(b.getvalue(), "x.xlsx")
    assert f1.descripcion == "" and "Descripción vacía." in f1.errores   # la formula no se calculo a "2"
    assert f1.inventario is None                                       # tampoco "=2+2" -> 4
    assert f2.descripcion == "=2+2" and any("se guarda igual" in a for a in f2.advertencias)  # texto, sin evaluar


def test_el_codigo_no_ejecuta_nada_con_data_only(monkeypatch):
    import openpyxl

    llamadas = []
    real = openpyxl.load_workbook

    def espia(*a, **k):
        llamadas.append(k)
        return real(*a, **k)

    monkeypatch.setattr(openpyxl, "load_workbook", espia)
    imp.analizar_contenido(xlsx(FILAS), "x.xlsx")
    assert llamadas and all(k.get("data_only") is True and k.get("read_only") is True for k in llamadas)


# ------------------------------------------------------------------ 5. validacion de la subida
def test_subida_con_extension_incorrecta(adm):
    r = subir(adm, b"x", "datos.txt")
    assert r.status_code == 200 and "Solo se aceptan archivos .xlsx o .csv" in r.content.decode()
    assert ImportacionEleventa.objects.count() == 0


def test_subida_de_mas_de_5_mb(adm):
    r = subir(adm, b"a" * (5 * 1024 * 1024 + 1), "grande.csv")
    assert r.status_code == 200 and "5 MB" in r.content.decode() and ImportacionEleventa.objects.count() == 0


@pytest.mark.parametrize("contenido", [b"esto no es un zip", b"PK\x03\x04roto" + b"\x00" * 50])
def test_subida_de_xlsx_corrupto(adm, contenido):
    r = subir(adm, contenido, "malo.xlsx")
    assert r.status_code == 200 and ("no es un .xlsx válido" in r.content.decode()
                                     or "No se pudo abrir" in r.content.decode())
    assert ImportacionEleventa.objects.count() == 0


def test_subida_de_zip_que_no_es_excel(adm):
    import zipfile

    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("hola.txt", "no soy un excel")
    r = subir(adm, b.getvalue(), "zip.xlsx")
    assert r.status_code == 200 and ImportacionEleventa.objects.count() == 0


def test_subida_sin_columnas_obligatorias(adm):
    r = subir(adm, xlsx([["a", "b"]], encabezados=["Foo", "Bar"]), "x.xlsx")
    assert r.status_code == 200 and "No se encontraron los encabezados obligatorios" in r.content.decode()
    r = subir(adm, csv_bytes([["a"]], encabezados=["Codigo", "Precio costo"]), "x.csv")
    assert "encabezados obligatorios" in r.content.decode() and ImportacionEleventa.objects.count() == 0


def test_subida_csv_binario_y_vacio(adm):
    assert subir(adm, b"", "v.csv").status_code == 200
    assert subir(adm, b"a;b\x00\x00;c", "b.csv").status_code == 200
    assert ImportacionEleventa.objects.count() == 0


# ------------------------------------------------------------------ 6. vista previa
def test_vista_previa_conteos_y_mensajes(adm):
    Producto.objects.create(sku="A100", nombre="Ya existe")
    filas = [
        ["N1", "Nuevo ok", 100, 200, 150, 5, 1, "Dep"],
        ["N2", "Costo cero", 0, 200, 150, 3, 0, "Dep"],
        ["DUP", "Uno", 1, 1, 1, 1, 0, ""], ["DUP", "Dos", 1, 1, 1, 1, 0, ""],
        ["", "Sin codigo", 1, 1, 1, 1, 0, ""],
        ["N3", "", 1, 1, 1, 1, 0, ""],
        ["N4", "Inventario malo", 1, 1, 1, "abc", 0, ""],
        ["N5", "Inventario negativo", 1, 1, 1, -4, 0, ""],
        ["A100", "Existente", 1, 1, 1, 8, 0, ""],
    ]
    i = preparar(adm, xlsx(filas))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    res = r.context["resumen"]
    assert res["filas"] == 9 and res["con_error"] == 4            # 2 duplicadas + codigo vacio + desc vacia
    assert res["existentes"] == 1 and res["nuevas"] == 4
    assert res["con_advertencia"] == 3                           # costo 0, inventario malo, inventario negativo (+N2.. ver abajo)
    assert D(res["unidades"]) == D("8")                          # solo N1 (5) y N2 (3)
    por = {(f.codigo, f.numero): f for f in r.context["pagina"]}
    dup = [f for f in r.context["pagina"] if f.codigo == "DUP"]
    assert len(dup) == 2 and all(f.estado == "error" and "duplicado" in f.errores[0] for f in dup)
    assert any("Código vacío." in f.errores for f in r.context["pagina"])
    assert any("Descripción vacía." in f.errores for f in r.context["pagina"])
    n2 = next(f for f in r.context["pagina"] if f.codigo == "N2")
    assert any("Costo en 0" in a for a in n2.advertencias)
    for cod in ("N4", "N5"):
        f = next(f for f in r.context["pagina"] if f.codigo == cod)
        assert f.inventario is None and any("sin stock" in a for a in f.advertencias)
    ex = next(f for f in r.context["pagina"] if f.codigo == "A100")
    assert ex.estado == "existente" and ex.diferencia == D("8")
    assert Producto.objects.count() == 1 and Movimiento.objects.count() == 0   # la vista previa no guarda nada
    assert por


def test_vista_previa_pagina_de_a_100(adm):
    filas = [[f"P{i}", f"Producto {i}", 1, 1, 1, 1, 0, ""] for i in range(130)]
    i = preparar(adm, xlsx(filas))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    assert len(r.context["pagina"]) == 100 and r.context["pagina"].paginator.num_pages == 2


def test_vista_previa_escapa_html(adm):
    i = preparar(adm, xlsx([["X1", "<script>alert(1)</script>", 1, 1, 1, 1, 0, ""]]), "<b>x</b>.xlsx")
    t = adm.get(reverse("importar_vista_previa", args=[i.pk])).content.decode()
    assert "<script>alert(1)" not in t and "&lt;script&gt;" in t and "<b>x</b>" not in t


# ------------------------------------------------------------------ 7. confirmar
def test_confirmar_crea_productos_categorias_documento_y_ajustes(adm, admin_user):
    i = preparar(adm)
    r = confirmar_http(adm, i)
    assert r.status_code == 302 and r.url == reverse("importar_resumen", args=[i.pk])
    assert sorted(Producto.objects.values_list("sku", flat=True)) == ["0077", "A100", "B200"]
    assert sorted(Categoria.objects.values_list("nombre", flat=True)) == ["Aceites", "Filtros"]  # "filtros " no duplica
    assert Documento.objects.filter(tipo="inventario_inicial").count() == 1
    ajustes = Movimiento.objects.all()
    assert ajustes.count() == 2                                   # B200 tiene inventario 0: sin ajuste
    assert {m.tipo for m in ajustes} == {"ajuste"} and {m.motivo for m in ajustes} == {imp.MOTIVO_INICIAL}
    assert {m.documento_id for m in ajustes} == {Documento.objects.get().pk}
    assert {m.usuario for m in ajustes} == {admin_user}
    p = Producto.objects.get(sku="0077")
    assert servicios.stock_de(p) == D("10.00") and p.costo == D("1234") and p.precio_venta == D("2500")
    assert p.precio_mayoreo == D("2000") and p.stock_min == D("2") and p.categoria.nombre == "Filtros"
    assert servicios.stock_de(Producto.objects.get(sku="A100")) == D("5.00")
    assert Producto.objects.get(sku="B200").categoria.nombre == "Filtros"
    i.refresh_from_db()
    assert i.estado == "confirmada" and i.documento is not None and i.resumen["creados"] == 3
    t = adm.get(r.url).content.decode()
    assert "Importación confirmada" in t and "Productos creados" in t


def test_existente_no_cambia_stock_y_se_informa_la_diferencia(adm, admin_user):
    p = Producto.objects.create(sku="0077", nombre="Nombre viejo", costo=D("1"), stock_min=D("0"), stock_max=D("50"))
    servicios.registrar_ajuste(p, 4, admin_user, "previo")
    i = preparar(adm)
    confirmar_http(adm, i)
    p.refresh_from_db()
    assert servicios.stock_de(p) == D("4.00")                    # NO se toco
    assert p.nombre == "Nombre viejo" and p.costo == D("1")      # sin la opcion, tampoco los datos
    dif = ImportacionEleventa.objects.get().resumen["diferencias"]
    assert dif == [{"codigo": "0077", "archivo": "10.00", "sistema": "4.00", "diferencia": "6.00"}]
    assert "6,00" in adm.get(reverse("importar_resumen", args=[i.pk])).content.decode()


def test_con_la_opcion_actualiza_datos_de_existentes_pero_no_el_stock(adm, admin_user):
    p = Producto.objects.create(sku="0077", nombre="Nombre viejo", costo=D("1"), stock_min=D("0"), stock_max=D("1"))
    servicios.registrar_ajuste(p, 4, admin_user, "previo")
    i = preparar(adm)
    confirmar_http(adm, i, actualizar=True)
    p.refresh_from_db()
    assert p.nombre == "Filtro de aceite" and p.costo == D("1234") and p.precio_venta == D("2500")
    assert p.precio_mayoreo == D("2000") and p.stock_min == D("2") and p.stock_max == D("2")  # max sube al minimo
    assert servicios.stock_de(p) == D("4.00")


def test_filas_con_error_no_se_importan_pero_se_listan(adm):
    filas = [["OK1", "Bien", 1, 1, 1, 2, 0, ""], ["", "Sin codigo", 1, 1, 1, 1, 0, ""]]
    i = preparar(adm, xlsx(filas))
    confirmar_http(adm, i)
    assert list(Producto.objects.values_list("sku", flat=True)) == ["OK1"]
    i.refresh_from_db()
    assert i.resumen["errores"][0]["mensajes"] == ["Código vacío."]
    assert "Código vacío." in adm.get(reverse("importar_resumen", args=[i.pk])).content.decode()


def test_inventario_negativo_o_no_numerico_se_importa_sin_stock(adm):
    i = preparar(adm, xlsx([["N1", "Neg", 1, 1, 1, -3, 0, ""], ["N2", "Txt", 1, 1, 1, "x", 0, ""]]))
    confirmar_http(adm, i)
    assert Producto.objects.count() == 2 and Movimiento.objects.count() == 0
    assert not Documento.objects.exists()


def test_sin_stock_no_se_crea_documento(adm):
    confirmar_http(adm, preparar(adm, xlsx([["N1", "Cero", 1, 1, 1, 0, 0, ""]])))
    assert Producto.objects.count() == 1 and not Documento.objects.exists()


def test_no_usa_ajustes_uno_a_uno_ni_permitir_negativo(adm, monkeypatch):
    llamadas = []
    real = servicios.registrar_inventario_inicial_en_lote

    def espia(*a, **k):
        llamadas.append((a, k))
        return real(*a, **k)

    def prohibido(*a, **k):
        raise AssertionError("no debe usar registrar_ajuste uno a uno")

    monkeypatch.setattr(servicios, "registrar_inventario_inicial_en_lote", espia)
    monkeypatch.setattr(servicios, "registrar_ajuste", prohibido)
    confirmar_http(adm, preparar(adm))
    assert len(llamadas) == 1 and not any("permitir_negativo" in k for _, k in llamadas)


# ------------------------------------------------------------------ 8. todo o nada
def test_un_fallo_a_mitad_no_deja_nada(adm, monkeypatch):
    i = preparar(adm)
    real = servicios.registrar_inventario_inicial_en_lote
    visto = {}

    def escribe_y_falla(*a, **k):
        real(*a, **k)                                # los movimientos SI se escribieron...
        visto["movimientos"] = Movimiento.objects.count()
        visto["productos"] = Producto.objects.count()
        raise RuntimeError("fallo simulado")         # ...y luego algo falla

    monkeypatch.setattr(servicios, "registrar_inventario_inicial_en_lote", escribe_y_falla)
    adm.raise_request_exception = False
    r = confirmar_http(adm, i)
    assert r.status_code == 500
    assert visto == {"movimientos": 2, "productos": 3}           # habia escrito de todo
    assert Producto.objects.count() == 0 and Categoria.objects.count() == 0
    assert Documento.objects.count() == 0 and Movimiento.objects.count() == 0
    i.refresh_from_db()
    assert i.estado == "vista_previa" and i.resumen == {}


def test_un_error_de_validacion_a_mitad_tampoco_deja_nada(adm, admin_user, monkeypatch):
    i = preparar(adm)
    from django.core.exceptions import ValidationError

    real = Producto.full_clean
    contador = {"c": 0}

    def falla(self, *a, **k):
        contador["c"] += 1
        if contador["c"] == 3:
            raise ValidationError("producto invalido simulado")
        return real(self, *a, **k)

    monkeypatch.setattr(Producto, "full_clean", falla)
    r = confirmar_http(adm, i)
    assert r.status_code == 302 and r.url == reverse("importar_vista_previa", args=[i.pk])
    assert Producto.objects.count() == 0 and Movimiento.objects.count() == 0 and not Documento.objects.exists()
    assert "No se importó nada" in adm.get(r.url).content.decode()


# ------------------------------------------------------------------ 9. hash y doble envio
def test_el_mismo_archivo_no_se_confirma_dos_veces(adm):
    contenido = xlsx(FILAS)
    i = preparar(adm, contenido)
    confirmar_http(adm, i)
    r = subir(adm, contenido)
    assert r.status_code == 200 and "ya fue importado" in r.content.decode()
    assert ImportacionEleventa.objects.count() == 1
    # y la confirmacion directa sobre la misma importacion tampoco repite nada
    with pytest.raises(imp.ImportacionNoValida):
        imp.confirmar(i.pk, ImportacionEleventa.objects.get().usuario)
    assert Producto.objects.count() == 3 and Movimiento.objects.count() == 2


def test_dos_importaciones_con_el_mismo_hash_son_imposibles_en_la_base(adm, admin_user):
    i = preparar(adm)
    with pytest.raises(IntegrityError), transaction.atomic():
        ImportacionEleventa.objects.create(archivo="x", hash_sha256=i.hash_sha256, nombre_original="y",
                                           usuario=admin_user)


def test_subir_el_mismo_archivo_en_vista_previa_lleva_a_la_misma_vista_previa(adm):
    contenido = xlsx(FILAS)
    i = preparar(adm, contenido)
    r = subir(adm, contenido)
    assert r.status_code == 302 and r.url == reverse("importar_vista_previa", args=[i.pk])
    assert ImportacionEleventa.objects.count() == 1


def test_doble_envio_del_formulario_registra_una_vez(adm):
    i = preparar(adm)
    url = reverse("importar_vista_previa", args=[i.pk])
    token = token_de(adm.get(url))
    adm.post(url, {"token": token, "accion": "confirmar"})
    n = Movimiento.objects.count()
    r = adm.post(url, {"token": token, "accion": "confirmar"}, follow=True)
    assert Movimiento.objects.count() == n == 2 and Producto.objects.count() == 3
    assert "no se hizo nada nuevo" in r.content.decode() or "Importación confirmada" in r.content.decode()


def test_token_inventado_no_confirma(adm):
    i = preparar(adm)
    adm.post(reverse("importar_vista_previa", args=[i.pk]), {"token": "inventado", "accion": "confirmar"})
    assert Producto.objects.count() == 0
    assert ImportacionEleventa.objects.get().estado == "vista_previa"


def test_descartar_no_cambia_nada_y_permite_resubir(adm):
    contenido = xlsx(FILAS)
    i = preparar(adm, contenido)
    url = reverse("importar_vista_previa", args=[i.pk])
    adm.post(url, {"token": token_de(adm.get(url)), "accion": "descartar"})
    i.refresh_from_db()
    assert i.estado == "descartada" and Producto.objects.count() == 0
    r = subir(adm, contenido)                       # se puede volver a subir
    assert r.status_code == 302 and ImportacionEleventa.objects.get().estado == "vista_previa"


# ------------------------------------------------------------------ 10. permisos
def todas_las_urls(i):
    return [reverse("importar_eleventa"), reverse("importar_vista_previa", args=[i.pk]),
            reverse("importar_resumen", args=[i.pk])]


@pytest.mark.parametrize("grupo", ["Operador", None])
def test_operador_y_sin_grupo_reciben_403_en_las_tres_pantallas(adm, client, grupo, django_user_model):
    i = preparar(adm)
    otro = Client()
    otro.force_login(crear_usuario("otro", grupo))
    for url in todas_las_urls(i):
        assert otro.get(url).status_code == 403, url
    assert otro.post(reverse("importar_eleventa"), {"archivo": SimpleUploadedFile("a.xlsx", xlsx(FILAS))}).status_code == 403
    assert otro.post(reverse("importar_vista_previa", args=[i.pk]), {"token": "x", "accion": "confirmar"}).status_code == 403
    assert Producto.objects.count() == 0


def test_anonimo_va_al_login(client, adm):
    i = preparar(adm)
    c = Client()
    for url in todas_las_urls(i):
        assert c.get(url).status_code == 302


def test_el_servicio_tambien_exige_el_permiso(adm):
    i = preparar(adm)
    with pytest.raises(Exception) as e:
        imp.confirmar(i.pk, crear_usuario("op", "Operador"))
    assert e.typename == "PermissionDenied"
    assert Producto.objects.count() == 0


def test_menu_solo_para_quien_tiene_el_permiso(adm):
    assert "Importar desde Eleventa" in adm.get("/").content.decode()
    c = Client()
    c.force_login(crear_usuario("op2", "Operador"))
    assert "Importar desde Eleventa" not in c.get("/").content.decode()


def test_permiso_nuevo_en_administrador_y_no_en_operador():
    assert Group.objects.get(name="Administrador").permissions.filter(codename="importar_eleventa").exists()
    assert not Group.objects.get(name="Operador").permissions.filter(
        content_type__app_label="inventario", codename__contains="importa").exists()


def test_admin_de_importaciones_es_de_solo_lectura(adm, admin_user):
    from django.contrib import admin as dj_admin
    from django.test import RequestFactory

    ma = dj_admin.site._registry[ImportacionEleventa]
    req = RequestFactory().get("/admin/")
    req.user = get_user_model().objects.create_superuser("su", password="x")
    assert not (ma.has_add_permission(req) or ma.has_change_permission(req) or ma.has_delete_permission(req))


# ------------------------------------------------------------------ 11. D-47 precios y CHECKs
@pytest.mark.parametrize("campo", ["costo", "precio_venta", "precio_mayoreo", "stock_min", "stock_max"])
def test_checks_de_no_negativos_en_la_base(campo):
    valores = {"sku": "NEG", "nombre": "x", "unidad": "u", "costo": 0, "precio_venta": 0,
               "precio_mayoreo": 0, "stock_min": 0, "stock_max": 0}
    valores[campo] = -1
    cols = ", ".join(valores)
    with pytest.raises(IntegrityError) as e, transaction.atomic(), connection.cursor() as c:
        c.execute(f"INSERT INTO inventario_producto ({cols}) VALUES ({', '.join(['%s'] * len(valores))})",
                  list(valores.values()))
    assert "no_negativo" in str(e.value) or "stock_max_gte_min" in str(e.value)


def test_precios_en_formularios_y_ficha(adm):
    datos = {"sku": "PR1", "nombre": "Con precios", "categoria": "", "unidad": "unidad", "costo": "3500",
             "precio_venta": "5990", "precio_mayoreo": "5000.50", "stock_min": "1", "stock_max": "5"}
    adm.post(reverse("producto_nuevo"), datos)
    p = Producto.objects.get(sku="PR1")
    assert p.precio_venta == D("5990") and p.precio_mayoreo == D("5000.50")
    t = adm.get(reverse("producto_detalle", args=[p.pk])).content.decode()
    assert "$5.990" in t and "$5.001" in t and "$3.500" in t        # pesos enteros; 5000,50 se redondea a $5.001
    for campo in ("precio_venta", "precio_mayoreo"):
        r = adm.post(reverse("producto_editar", args=[p.pk]), {**datos, campo: "-1"})
        assert r.status_code == 200 and campo in r.context["form"].errors
    sin = {k: v for k, v in datos.items() if not k.startswith("precio")}
    adm.post(reverse("producto_editar", args=[p.pk]), {**sin, "nombre": "Sin precios"})
    p.refresh_from_db()
    assert p.nombre == "Sin precios" and p.precio_venta == 0     # vacio = 0


# ------------------------------------------------------------------ 12. archivo con nombre aleatorio
def es_ruta_de_importacion(ruta, extension="xlsx", clase=PurePath):
    """True si `ruta` termina en importaciones/<32 hex>.<extension>, con el separador de la
    plataforma (/ en Linux, \\ en Windows). Se compara por partes, no como texto."""
    p = clase(str(ruta))
    return p.parent.name == "importaciones" and re.fullmatch(rf"[0-9a-f]{{32}}\.{extension}", p.name) is not None


@pytest.mark.parametrize("ruta,clase,esperado", [
    ("/srv/app/media/importaciones/0123456789abcdef0123456789abcdef.xlsx", PurePosixPath, True),
    ("C:\\Users\\ana\\inv\\media\\importaciones\\019b9b55aaaabbbbccccddddeeeeffff.xlsx", PureWindowsPath, True),
    ("C:\\Users\\ana\\media\\importaciones\\mi inventario.xlsx", PureWindowsPath, False),
    ("C:\\Users\\ana\\media\\otra\\0123456789abcdef0123456789abcdef.xlsx", PureWindowsPath, False),
    ("/srv/media/importaciones/0123456789abcdef0123456789abcde.xlsx", PurePosixPath, False),
    ("/srv/media/importaciones/0123456789ABCDEF0123456789abcdef.xlsx", PurePosixPath, False),
])
def test_comprobacion_de_ruta_funciona_con_separadores_de_linux_y_de_windows(ruta, clase, esperado):
    # Simula Windows sin estar en Windows: PureWindowsPath interpreta «\\» como separador.
    assert es_ruta_de_importacion(ruta, clase=clase) is esperado


def test_archivo_guardado_con_nombre_aleatorio_en_importaciones(adm, settings):
    i = preparar(adm, xlsx(FILAS), "mi inventario secreto.xlsx")
    ruta = Path(i.archivo.path)
    assert es_ruta_de_importacion(ruta)
    assert "secreto" not in str(ruta) and i.nombre_original == "mi inventario secreto.xlsx"
    assert ruta.resolve().is_relative_to(Path(settings.MEDIA_ROOT).resolve())   # sin comparar texto de rutas
    assert ruta.is_file()


def test_nombre_con_ruta_se_reduce_al_nombre_final(adm):
    i = preparar(adm, csv_bytes(FILAS), "..\\..\\etc\\pasw.csv")
    assert i.nombre_original == "pasw.csv" and es_ruta_de_importacion(i.archivo.path, "csv")


RAIZ = Path(__file__).resolve().parent.parent


def test_media_esta_fuera_de_git():
    lineas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "media/" in [l.strip() for l in lineas]
    try:
        r = subprocess.run(["git", "check-ignore", "-q", "media/importaciones/x.xlsx"],
                           capture_output=True, cwd=RAIZ)
    except FileNotFoundError:        # git no esta en el PATH (p. ej. algunos equipos Windows)
        return
    assert r.returncode == 0


def test_requirements_fija_openpyxl_y_et_xmlfile():
    t = (RAIZ / "requirements.txt").read_text(encoding="utf-8")
    assert "openpyxl==3.1.5" in t and re.search(r"^et_xmlfile==\d", t, re.M)


# ------------------------------------------------------------------ D-50: separador decimal por columna
def _costos(valores):
    filas = [[f"C{i}", f"Cosa {i}", v, 1, 1, 1, 0, ""] for i, v in enumerate(valores)]
    return imp.analizar_contenido(csv_bytes(filas), "x.csv")


def test_columna_con_evidencia_de_coma_decimal_lee_el_punto_como_miles():
    filas = _costos(["1.500", "2,5", "1.234,50"])
    assert [f.costo for f in filas] == [D("1500"), D("2.5"), D("1234.5")] and all(f.valida for f in filas)


def test_columna_con_evidencia_de_punto_decimal_lee_el_punto_como_decimal():
    filas = _costos(["1.005", "2.75", "0.500"])
    assert [f.costo for f in filas] == [D("1.01"), D("2.75"), D("0.50")] and all(f.valida for f in filas)
    assert any("redondeado" in a for a in filas[0].advertencias)


def test_valor_ambiguo_sin_evidencia_es_error_de_fila_en_esa_columna():
    f1, f2 = _costos(["1.500", "2000"])
    assert not f1.valida and "ambiguo" in f1.errores[0] and "Precio costo" in f1.errores[0]
    assert f2.valida and f2.costo == D("2000")


def test_cero_punto_quinientos_nunca_es_miles():
    assert _costos(["0.500"])[0].costo == D("0.50")


def test_coma_de_miles_solo_si_la_columna_lo_indica():
    asi = _costos(["1,234,567", "2.75"])
    assert asi[0].costo == D("1234567") and asi[0].valida
    sin = _costos(["1,234,567", "10"])[0]
    assert not sin.valida and "ambiguo" in sin.errores[0]
    conmilcoma = _costos(["1,234", "2.75"])[0]
    assert conmilcoma.costo == D("1234")
    con_coma_decimal = _costos(["1,234", "2,5"])[0]
    assert con_coma_decimal.costo == D("1.23") and any("redondeado" in a for a in con_coma_decimal.advertencias)


def test_inventario_ambiguo_es_error_y_numeros_nativos_de_xlsx_no_cambian():
    f = imp.analizar_contenido(csv_bytes([["I1", "x", 1, 1, 1, "1.500", 0, ""]]), "x.csv")[0]
    assert not f.valida and "Inventario: valor ambiguo" in f.errores[0]
    nativos = imp.analizar_contenido(xlsx([["I2", "x", 1500, 1, 1, 1500, 0, ""], ["I3", "y", 1.5, 1, 1, 0.5, 0, ""]]), "x.xlsx")
    assert [f.valida for f in nativos] == [True, True]
    assert nativos[0].costo == D("1500") and nativos[1].costo == D("1.5") and nativos[1].inventario == D("0.5")


def test_vista_previa_muestra_el_error_de_ambiguedad_y_no_importa_la_fila(adm):
    i = preparar(adm, xlsx([["OK", "Bien", "2,5", 1, 1, 1, 0, ""], ["AMB", "Ambigua", 1, 1, 1, "1.500", 0, ""]]))
    t = adm.get(reverse("importar_vista_previa", args=[i.pk])).content.decode()
    assert "valor ambiguo" in t
    confirmar_http(adm, i)
    assert list(Producto.objects.values_list("sku", flat=True)) == ["OK"]


# ------------------------------------------------------------------ A3: filas de totales
def test_fila_de_totales_solo_si_el_codigo_esta_vacio_y_empieza_con_total():
    filas = [["TOTAL5W30", "Aceite sintetico", 1, 1, 1, 1, 0, ""], ["Total", "Producto raro", 1, 1, 1, 1, 0, ""],
             ["", "Total general", None, None, None, 99, None, ""], ["", "Sin total aqui", 1, 1, 1, 1, 0, ""]]
    a = imp.analizar_contenido(xlsx(filas, totales=False), "x.xlsx")
    assert [f.codigo for f in a] == ["TOTAL5W30", "Total", ""]          # el 3.o es un error de codigo vacio
    assert [o["texto"] for o in a.omitidas] == ["Total general"] and a.omitidas[0]["fila"] == 7
    assert a[2].errores == ["Código vacío."]


def test_vista_previa_muestra_las_filas_omitidas_con_su_numero(adm):
    i = preparar(adm, xlsx(FILAS))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    assert r.context["resumen"]["omitidas"] == 1 and r.context["resumen"]["vacias"] == 1
    assert r.context["omitidas"][0]["texto"] == "Totales"
    n = r.context["omitidas"][0]["fila"]
    assert f"fila {n} (" in r.content.decode()


def test_codigos_total_se_importan(adm):
    i = preparar(adm, xlsx([["TOTAL5W30", "Aceite", 1, 1, 1, 2, 0, ""], ["Total", "Otro", 1, 1, 1, 0, 0, ""]], totales=False))
    confirmar_http(adm, i)
    assert sorted(Producto.objects.values_list("sku", flat=True)) == ["TOTAL5W30", "Total"]


# ------------------------------------------------------------------ M1: csv.Error
def test_csv_con_campo_enorme_es_archivo_invalido_no_500(adm):
    grande = (";".join(ENCABEZADOS) + "\nA;" + "x" * 200000 + ";1;1;1;1;0;D\n").encode()
    with pytest.raises(imp.ArchivoInvalido):
        imp.analizar_contenido(grande, "x.csv")
    r = subir(adm, grande, "enorme.csv")
    assert r.status_code == 200 and "mal formado" in r.content.decode()
    assert ImportacionEleventa.objects.count() == 0


# ------------------------------------------------------------------ M3: analisis guardado, hash al confirmar
def test_el_analisis_se_guarda_al_subir_y_la_vista_previa_no_relee_el_archivo(adm, monkeypatch):
    i = preparar(adm)
    assert i.analisis["filas"] and len(i.analisis["filas"]) == 3 and i.analisis["omitidas"]
    monkeypatch.setattr(imp, "_leer", lambda *_: pytest.fail("la vista previa no debe releer el archivo"))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    assert r.status_code == 200 and r.context["resumen"]["filas"] == 3
    assert [f.codigo for f in r.context["pagina"]] == ["0077", "A100", "B200"]


def test_la_vista_previa_con_analisis_guardado_refleja_el_estado_actual_de_la_base(adm):
    i = preparar(adm)
    Producto.objects.create(sku="A100", nombre="Aparecio despues")
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    assert r.context["resumen"]["existentes"] == 1 and r.context["resumen"]["nuevas"] == 2


def test_la_confirmacion_vuelve_a_leer_el_archivo_y_compara_el_hash(adm):
    i = preparar(adm)
    with open(i.archivo.path, "ab") as f:
        f.write(b"manipulado")
    r = confirmar_http(adm, i)
    assert r.status_code == 302 and r.url == reverse("importar_vista_previa", args=[i.pk])
    assert Producto.objects.count() == 0
    assert "no coincide" in adm.get(r.url).content.decode()


def test_la_confirmacion_usa_el_archivo_y_no_el_analisis_guardado(adm):
    i = preparar(adm)
    i.analisis = {"filas": [], "omitidas": [], "vacias": 0}      # un analisis guardado adulterado no importa
    i.save()
    confirmar_http(adm, i)
    assert Producto.objects.count() == 3


def test_limite_descomprimido_es_de_50_mb():
    assert imp.MAX_DESCOMPRIMIDO == 50 * 1024 * 1024


# ------------------------------------------------------------------ M2: rendimiento de la confirmacion (D-51)
def _archivo_grande(n):
    filas = [[f"P{i:05d}", f"Producto ficticio {i}", "1.250,50", "2.500,00", "2.000,00", i % 7 + 1, 2, f"Dep{i % 20}"]
             for i in range(n)]
    return xlsx(filas)


def _medir_confirmacion(admin_user, n):
    from django.test.utils import CaptureQueriesContext
    import time

    contenido = _archivo_grande(n)
    c = Client()
    c.force_login(admin_user)
    r = c.post(reverse("importar_eleventa"), {"archivo": SimpleUploadedFile(f"g{n}.xlsx", contenido)})
    i = ImportacionEleventa.objects.get(pk=int(r.url.strip("/").split("/")[-1]))
    t0 = time.perf_counter()
    with CaptureQueriesContext(connection) as q:
        resumen = imp.confirmar(i.pk, admin_user)
    return len(q), time.perf_counter() - t0, resumen


def test_confirmacion_de_5000_filas_es_rapida_y_con_consultas_acotadas(admin_user, capsys):
    q50, t50, r50 = _medir_confirmacion(admin_user, 50)
    ImportacionEleventa.objects.all().delete()  # el archivo distinto de 5.000 filas es otra importacion
    q5000, t5000, r5000 = _medir_confirmacion(admin_user, 5000)
    with capsys.disabled():
        print(f"\n[M2] confirmar 50 filas: {q50} consultas, {t50:.2f}s; 5.000 filas: {q5000} consultas, {t5000:.2f}s")
    assert r5000["creados"] == 5000 - 50 and r5000["existentes"] == 50       # las 50 primeras ya existian
    lotes = -(-5000 // 500)
    assert q5000 <= q50 + 14 * lotes              # solo crece por lotes de 500 (no por fila)
    assert q5000 < 150
    assert t5000 < 5
    assert Movimiento.objects.count() == 50 + (5000 - 50)  # 50 del primer lote + el resto nuevo
    assert servicios.stock_de(Producto.objects.get(sku="P00003")) == D("4.00")


def test_la_vista_previa_de_5000_filas_pagina_y_no_relee(adm):
    i = preparar(adm, _archivo_grande(5000))
    import time

    t0 = time.perf_counter()
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]), {"page": 50})
    assert r.status_code == 200 and len(r.context["pagina"]) == 100 and time.perf_counter() - t0 < 5


# ------------------------------------------------------------------ ajustes finales de la re-auditoria
def test_sku_creado_por_otro_entre_la_reclasificacion_y_el_guardado_no_da_500(adm, monkeypatch):
    i = preparar(adm)
    url = reverse("importar_vista_previa", args=[i.pk])
    token = token_de(adm.get(url))            # la vista previa se ve ANTES de que alguien cree el sku
    real = imp.clasificar

    def clasifica_y_alguien_crea(filas):
        r = real(filas)
        Producto.objects.create(sku="A100", nombre="Creado por otro usuario")   # justo despues de clasificar
        return r

    monkeypatch.setattr(imp, "clasificar", clasifica_y_alguien_crea)
    r = adm.post(url, {"token": token, "accion": "confirmar"})
    assert r.status_code == 302 and r.url == url
    monkeypatch.undo()
    t = adm.get(r.url).content.decode()
    assert "Otro usuario creó uno de estos códigos" in t
    assert Producto.objects.count() == 0 and Movimiento.objects.count() == 0 and not Documento.objects.exists()
    assert ImportacionEleventa.objects.get().estado == "vista_previa"


def test_resumen_avisa_de_filas_que_pasaron_a_existentes_desde_la_vista_previa(adm):
    i = preparar(adm)
    url = reverse("importar_vista_previa", args=[i.pk])
    token = token_de(adm.get(url))                                # el usuario ve la vista previa
    assert ImportacionEleventa.objects.get().analisis["existentes_vistos"] == []
    otro = Producto.objects.create(sku="A100", nombre="Creado despues")
    adm.post(url, {"token": token, "accion": "confirmar"})
    i.refresh_from_db()
    assert i.resumen["pasaron_a_existentes"] == 1
    assert servicios.stock_de(otro) == D("0.00")                  # no recibio stock
    t = adm.get(reverse("importar_resumen", args=[i.pk])).content.decode()
    assert "1 fila pasaron a existentes desde la vista previa y no recibieron stock" in t


def test_sin_cambios_no_hay_aviso_de_pasaron_a_existentes(adm):
    i = preparar(adm)
    adm.get(reverse("importar_vista_previa", args=[i.pk]))
    confirmar_http(adm, i)
    i.refresh_from_db()
    assert i.resumen["pasaron_a_existentes"] == 0
    assert "pasaron a existentes" not in adm.get(reverse("importar_resumen", args=[i.pk])).content.decode()


def test_el_analisis_se_vacia_al_confirmar_y_al_descartar(adm):
    i = preparar(adm)
    assert i.analisis
    confirmar_http(adm, i)
    i.refresh_from_db()
    assert i.analisis == {} and i.resumen["creados"] == 3 and i.resumen["filas"] == 3
    assert "Importación confirmada" in adm.get(reverse("importar_resumen", args=[i.pk])).content.decode()
    j = preparar(adm, xlsx([["Z1", "Otro", 1, 1, 1, 1, 0, ""]]))
    assert j.analisis
    url = reverse("importar_vista_previa", args=[j.pk])
    adm.post(url, {"token": token_de(adm.get(url)), "accion": "descartar"})
    j.refresh_from_db()
    assert j.estado == "descartada" and j.analisis == {}


def test_csv_con_saltos_de_linea_de_windows_crlf_se_lee_igual():
    lf = csv_bytes(FILAS)
    crlf = lf.replace(b"\n", b"\r\n")
    assert b"\r\n" in crlf and b"\r\r\n" not in crlf
    a, b = imp.analizar_contenido(lf, "x.csv"), imp.analizar_contenido(crlf, "x.csv")
    assert [f.a_dict() for f in a] == [f.a_dict() for f in b] and len(b) == 3
    con_bom = imp.analizar_contenido(b"\xef\xbb\xbf" + crlf, "x.csv")        # Excel en Windows agrega BOM
    assert [f.codigo for f in con_bom] == ["0077", "A100", "B200"]
