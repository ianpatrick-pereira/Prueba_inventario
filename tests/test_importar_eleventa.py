"""Carga inicial desde Eleventa (T-F1-08; D-46, D-47, D-48). TODOS los archivos son ficticios y se
generan aqui; nunca se lee ni se sube un archivo real."""
import io
import re
import subprocess
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
        ws.append(["Totales", None, None, None, None, 99])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def csv_bytes(filas, sep=";", codec="utf-8", encabezados=ENCABEZADOS):
    lineas = ["REPORTE FICTICIO", ""] + [sep.join(encabezados)] + [
        sep.join("" if c is None else (f'"{c}"' if sep in str(c) else str(c)) for c in f) for f in filas
    ] + ["", sep.join(["Totales", "", "", "", "", "99"])]
    return "\n".join(lineas).encode(codec)


FILAS = [
    ["0077", "Filtro de aceite", "$1.234", "2500", "2000", 10, 2, "Filtros"],
    ["A100", "Aceite 10W40", "1.234,50", "$5.990", "5.500", "5", "1", "Aceites"],
    ["B200", "Bujía", 800, 1500, 1200, 0, 0, "filtros "],
]


def subir(client, contenido, nombre="inventario.xlsx"):
    return client.post(reverse("importar_eleventa"), {"archivo": SimpleUploadedFile(nombre, contenido)})


def token_de(r):
    return re.search(r'name="token" value="([^"]+)"', r.content.decode()).group(1)


def preparar(client, contenido=None, nombre="inventario.xlsx"):
    r = subir(client, contenido or xlsx(FILAS), nombre)
    assert r.status_code == 302, r.content.decode()[:500]
    return ImportacionEleventa.objects.get()


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
@pytest.mark.parametrize("entrada,esperado", [
    ("$1.234", D("1234")), ("1.234,50", D("1234.50")), ("1234.5", D("1234.5")), (1234, D("1234")),
    ("$ 12.345.678", D("12345678")), ("12,5", D("12.5")), ("0,5", D("0.5")), ("1,234.56", D("1234.56")),
    (D("7.25"), D("7.25")), (1234.5, D("1234.5")), ("-5", D("-5")), ("  42 ", D("42")),
])
def test_parsear_decimal(entrada, esperado):
    r = imp.parsear_decimal(entrada)
    assert r == esperado and isinstance(r, Decimal)


@pytest.mark.parametrize("malo", ["abc", "", "  ", None, True, "1.2.3,4,5", "12a", float("nan"), float("inf")])
def test_parsear_decimal_invalido(malo):
    assert imp.parsear_decimal(malo) is None


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
@pytest.mark.parametrize("peligroso,limpio", [("=SUMA(A1)", "SUMA(A1)"), ("+cmd", "cmd"), ("-cmd", "cmd"),
                                              ("@cmd", "cmd"), ("  =HYPERLINK(\"x\")", "HYPERLINK(\"x\")")])
def test_celdas_de_texto_que_empiezan_con_simbolo_peligroso_se_sanean(peligroso, limpio):
    f = imp.analizar_contenido(csv_bytes([["X1", peligroso, 1, 1, 1, 1, 0, peligroso]]), "x.csv")[0]
    assert f.descripcion == limpio and f.departamento == limpio
    assert any("peligroso" in a for a in f.advertencias)


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
    assert f2.descripcion == "2+2" and any("peligroso" in a for a in f2.advertencias)


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


def test_no_usa_permitir_negativo(adm, monkeypatch):
    llamadas = []
    real = servicios.registrar_ajuste

    def espia(*a, **k):
        llamadas.append(k)
        return real(*a, **k)

    monkeypatch.setattr(servicios, "registrar_ajuste", espia)
    confirmar_http(adm, preparar(adm))
    assert llamadas and all(not k.get("permitir_negativo") for k in llamadas)


# ------------------------------------------------------------------ 8. todo o nada
def test_un_fallo_a_mitad_no_deja_nada(adm, monkeypatch):
    i = preparar(adm)
    real = servicios.registrar_ajuste
    n = {"c": 0}

    def falla_en_el_segundo(*a, **k):
        n["c"] += 1
        if n["c"] == 2:
            raise RuntimeError("fallo simulado")
        return real(*a, **k)

    monkeypatch.setattr(servicios, "registrar_ajuste", falla_en_el_segundo)
    adm.raise_request_exception = False
    r = confirmar_http(adm, i)
    assert r.status_code == 500 or r.status_code == 302
    assert n["c"] == 2                                          # llego a escribir el primero
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
    assert "5.990,00" in t and "5.000,50" in t and "3.500,00" in t
    for campo in ("precio_venta", "precio_mayoreo"):
        r = adm.post(reverse("producto_editar", args=[p.pk]), {**datos, campo: "-1"})
        assert r.status_code == 200 and campo in r.context["form"].errors
    sin = {k: v for k, v in datos.items() if not k.startswith("precio")}
    adm.post(reverse("producto_editar", args=[p.pk]), {**sin, "nombre": "Sin precios"})
    p.refresh_from_db()
    assert p.nombre == "Sin precios" and p.precio_venta == 0     # vacio = 0


# ------------------------------------------------------------------ 12. archivo con nombre aleatorio
def test_archivo_guardado_con_nombre_aleatorio_en_importaciones(adm, settings):
    i = preparar(adm, xlsx(FILAS), "mi inventario secreto.xlsx")
    ruta = i.archivo.path
    assert re.fullmatch(r".*/importaciones/[0-9a-f]{32}\.xlsx", ruta)
    assert "secreto" not in ruta and i.nombre_original == "mi inventario secreto.xlsx"
    assert str(settings.MEDIA_ROOT) in ruta


def test_nombre_con_ruta_se_reduce_al_nombre_final(adm):
    i = preparar(adm, csv_bytes(FILAS), "..\\..\\etc\\pasw.csv")
    assert i.nombre_original == "pasw.csv" and i.archivo.path.endswith(".csv")


def test_media_esta_fuera_de_git():
    r = subprocess.run(["git", "check-ignore", "-q", "media/importaciones/x.xlsx"], capture_output=True)
    assert r.returncode == 0


def test_requirements_fija_openpyxl_y_et_xmlfile():
    t = open("requirements.txt").read()
    assert "openpyxl==3.1.5" in t and re.search(r"^et_xmlfile==\d", t, re.M)
