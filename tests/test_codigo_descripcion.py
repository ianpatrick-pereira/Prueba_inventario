"""T-F1-22 (D-62): codigo de la descripcion como alternativo, precio en la vista previa, re-analisis y comando.
Archivos ficticios generados aqui."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse

from inventario import importar_eleventa as imp
from inventario.models import CodigoAlternativo, Producto
from tests.test_importar_eleventa import (  # noqa: F401  (fixtures y ayudas compartidas)
    adm, admin_user, confirmar_http, media_tmp, preparar, xlsx,
)

pytestmark = pytest.mark.django_db


def fila(codigo, desc, costo=100, venta=200, inv=5):
    return [codigo, desc, costo, venta, 0, inv, 0, "Dep"]


def alts():
    return {a.valor: (a.producto.sku, a.tipo) for a in CodigoAlternativo.objects.select_related("producto")}


def importar(adm, filas, actualizar=False):
    i = preparar(adm, xlsx(filas))
    r = confirmar_http(adm, i, actualizar)
    assert r.status_code == 302, r.content.decode()[:300]
    return i


def previa(adm, filas):
    i = preparar(adm, xlsx(filas))
    return i, adm.get(reverse("importar_vista_previa", args=[i.pk]))


def test_descripcion_igual_al_codigo_no_crea_alternativo(adm):
    importar(adm, [fila("X1", "X1")])
    assert alts() == {}


def test_descripcion_con_codigo_distinto_crea_fabricante_y_busqueda_exacta_primero(adm):
    importar(adm, [fila("566", "A0204"), fila("900", "Otro producto A0204x")])
    assert alts() == {"A0204": ("566", "fabricante")}
    r = adm.get(reverse("producto_lista"), {"q": "A0204"})
    assert r.status_code == 200
    skus = [p.sku for p in r.context["object_list"]]
    assert skus[0] == "566" and "900" in skus
    assert Producto.objects.get(sku="566").nombre == "A0204"


def test_descripcion_con_espacios_no_crea(adm):
    importar(adm, [fila("1", "Filtro de aceite")])
    assert alts() == {}


def test_conflicto_con_alternativo_de_otro_producto(adm):
    p = Producto.objects.create(sku="P1", nombre="Previo")
    CodigoAlternativo.objects.create(producto=p, tipo="fabricante", valor="ZZ9")
    i, r = previa(adm, [fila("N1", "ZZ9")])
    f = r.context["pagina"][0]
    assert any("el código de la descripción ZZ9 ya pertenece a P1" in a for a in f.advertencias)
    confirmar_http(adm, i)
    assert alts() == {"ZZ9": ("P1", "fabricante")} and Producto.objects.filter(sku="N1").exists()


def test_conflicto_con_sku_de_otro_producto(adm):
    Producto.objects.create(sku="K7", nombre="Previo")
    i, r = previa(adm, [fila("N1", "K7")])
    assert any("ya pertenece a K7" in a for a in r.context["pagina"][0].advertencias)
    confirmar_http(adm, i)
    assert alts() == {}


def test_mismo_archivo_dos_filas_con_el_mismo_codigo_de_descripcion(adm):
    i, r = previa(adm, [fila("A", "Q55"), fila("B", "Q55")])
    avisos = {f.codigo: f.advertencias for f in r.context["pagina"]}
    assert not any("código de la descripción" in a for a in avisos["A"])
    assert any("ya pertenece a A" in a for a in avisos["B"])
    confirmar_http(adm, i)
    assert alts() == {"Q55": ("A", "fabricante")}


def test_actualizar_existentes_agrega_sin_borrar(adm):
    p = Producto.objects.create(sku="566", nombre="Viejo")
    CodigoAlternativo.objects.create(producto=p, tipo="barras", valor="7890")
    importar(adm, [fila("566", "A0204")], actualizar=True)
    assert alts() == {"7890": ("566", "barras"), "A0204": ("566", "fabricante")}
    assert Producto.objects.get(sku="566").sku == "566"


def test_sin_actualizar_el_existente_no_recibe_alternativo(adm):
    Producto.objects.create(sku="566", nombre="Viejo")
    importar(adm, [fila("566", "A0204")], actualizar=False)
    assert alts() == {}


def test_vista_previa_muestra_precio_venta_y_conteo(adm):
    i, r = previa(adm, [fila("566", "A0204", venta=1990), fila("2", "Dos palabras")])
    assert r.context["resumen"]["alternativos"] == 1
    html = r.content.decode()
    assert "Precio venta" in html and "1.990" in html
    assert "Códigos alternativos que se crearán" in html and "A0204" in html


def test_reanalisis_por_version_vieja_al_abrir_y_al_resubir(adm):
    contenido = xlsx([fila("566", "A0204")])
    i = preparar(adm, contenido)
    datos = i.analisis
    datos["version"] = 1
    for f in datos["filas"]:
        f.pop("codigo_alt", None)
    i.analisis = datos
    i.save()
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    assert r.context["resumen"]["alternativos"] == 1
    i.refresh_from_db()
    assert i.analisis["version"] == imp.ANALIZADOR_VERSION
    # al re-subir el mismo archivo tambien se re-analiza
    i.analisis = {**i.analisis, "version": 1}
    i.save()
    r2 = adm.post(reverse("importar_eleventa"),
                  {"archivo": __import__("django.core.files.uploadedfile", fromlist=["x"]).SimpleUploadedFile("inventario.xlsx", contenido)})
    assert r2.status_code == 302
    i.refresh_from_db()
    assert i.analisis["version"] == imp.ANALIZADOR_VERSION


def test_comando_simular_aplicar_e_idempotencia():
    Producto.objects.create(sku="566", nombre="A0204")
    Producto.objects.create(sku="2", nombre="Dos palabras")
    Producto.objects.create(sku="3", nombre="3")
    Producto.objects.create(sku="4", nombre="A0204")             # conflicto con el primero
    salida = StringIO()
    call_command("codigos_desde_descripcion", stdout=salida)
    assert CodigoAlternativo.objects.count() == 0
    assert "SIMULACIÓN" in salida.getvalue() and "que se crearían: 1" in salida.getvalue()
    salida = StringIO()
    call_command("codigos_desde_descripcion", "--aplicar", stdout=salida)
    assert alts() == {"A0204": ("566", "fabricante")}
    assert "Conflictos (no se crean): 1" in salida.getvalue()
    call_command("codigos_desde_descripcion", "--aplicar", stdout=StringIO())
    assert CodigoAlternativo.objects.count() == 1
    assert set(Producto.objects.values_list("sku", flat=True)) == {"566", "2", "3", "4"}


# ------------------------------------------------------------------ K1: sin distinguir mayusculas
def test_k1_minusculas_frente_a_sku_de_la_base(adm):
    Producto.objects.create(sku="A0204", nombre="Previo")
    i, r = previa(adm, [fila("N1", "a0204")])
    assert any("ya pertenece a A0204" in a for a in r.context["pagina"][0].advertencias)
    confirmar_http(adm, i)
    assert alts() == {}


def test_k1_minusculas_frente_a_alternativo_de_la_base(adm):
    p = Producto.objects.create(sku="P1", nombre="Previo")
    CodigoAlternativo.objects.create(producto=p, tipo="fabricante", valor="B100")
    i, r = previa(adm, [fila("N1", "b100")])
    assert any("ya pertenece a P1" in a for a in r.context["pagina"][0].advertencias)
    confirmar_http(adm, i)
    assert alts() == {"B100": ("P1", "fabricante")}


def test_k1_repetido_en_el_archivo_con_otras_mayusculas(adm):
    i, r = previa(adm, [fila("A", "C300"), fila("B", "c300")])
    avisos = {f.codigo: f.advertencias for f in r.context["pagina"]}
    assert any("ya pertenece a A" in a for a in avisos["B"])
    confirmar_http(adm, i)
    assert alts() == {"C300": ("A", "fabricante")}


def test_k1_descripcion_que_solo_difiere_del_propio_sku_en_mayusculas_no_crea(adm):
    i, r = previa(adm, [fila("ab12", "AB12")])
    assert not r.context["pagina"][0].advertencias or not any(
        "código de la descripción" in a for a in r.context["pagina"][0].advertencias)
    confirmar_http(adm, i)
    assert alts() == {}


def test_k1_comando_sin_distinguir_mayusculas():
    Producto.objects.create(sku="566", nombre="A0204")
    Producto.objects.create(sku="567", nombre="a0204")           # conflicto con el alternativo que se creara
    Producto.objects.create(sku="X9", nombre="x9")               # mismo sku con otras mayusculas: nada
    Producto.objects.create(sku="K1", nombre="k1")
    Producto.objects.create(sku="otro", nombre="K1")             # conflicto con el sku K1
    salida = StringIO()
    call_command("codigos_desde_descripcion", "--aplicar", stdout=salida)
    assert alts() == {"A0204": ("566", "fabricante")}
    assert "Conflictos (no se crean): 2" in salida.getvalue()


def test_k1_la_base_rechaza_alternativos_que_solo_difieren_en_mayusculas():
    from django.db import IntegrityError, transaction
    p = Producto.objects.create(sku="P1", nombre="Previo")
    CodigoAlternativo.objects.create(producto=p, tipo="fabricante", valor="A0204")
    with pytest.raises(IntegrityError), transaction.atomic():
        CodigoAlternativo.objects.create(producto=p, tipo="barras", valor="a0204")


def test_k1_la_migracion_se_detiene_y_lista_los_duplicados_sin_borrar():
    from importlib import import_module
    from django.apps import apps as django_apps
    from django.db import connection
    migracion = import_module("inventario.migrations.0005_codigo_unico_sin_mayusculas")
    p = Producto.objects.create(sku="P1", nombre="Previo")
    with connection.cursor() as c:     # se simula un duplicado preexistente saltando la restriccion
        c.execute('DROP INDEX "codigoalternativo_valor_unico_sin_mayusculas"')
    CodigoAlternativo.objects.create(producto=p, tipo="fabricante", valor="Z1")
    CodigoAlternativo.objects.create(producto=p, tipo="barras", valor="z1")
    with pytest.raises(RuntimeError) as e:
        migracion.revisar_duplicados(django_apps, None)
    assert "Z1" in str(e.value) and "z1" in str(e.value)
    assert CodigoAlternativo.objects.count() == 2                # no borra nada


# ------------------------------------------------------------------ K2: version del analisis al confirmar
def test_k2_confirmar_con_analisis_de_version_vieja_se_rechaza_y_se_reanaliza(adm):
    from tests.test_importar_eleventa import token_de
    i = preparar(adm, xlsx([fila("566", "A0204")]))
    url = reverse("importar_vista_previa", args=[i.pk])
    token = token_de(adm.get(url))                       # la persona ve la vista previa...
    i.refresh_from_db()
    i.analisis = {**i.analisis, "version": 1}            # ...y el sistema se actualiza antes de que confirme
    i.save()
    r = adm.post(url, {"token": token, "accion": "confirmar"})
    assert r.status_code == 302 and r.url == url and Producto.objects.count() == 0
    i.refresh_from_db()
    assert i.estado == "vista_previa" and i.analisis["version"] == imp.ANALIZADOR_VERSION
    assert "El sistema se actualizó" in adm.get(url).content.decode()
    assert confirmar_http(adm, i).status_code == 302 and Producto.objects.count() == 1
