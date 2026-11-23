"""T-F1-23: filas con problemas se crean marcadas para revision (D-63) y archivar productos (D-64).
Todos los archivos y datos son ficticios y se generan aqui."""
from decimal import Decimal
from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.urls import reverse

from inventario import importar_eleventa as imp
from inventario import servicios
from inventario.models import ImportacionEleventa, Movimiento, Producto
from tests.test_importar_eleventa import (  # noqa: F401  (fixtures y ayudas compartidas)
    adm, admin_user, confirmar_http, crear_usuario, media_tmp, preparar, subir, token_de, xlsx,
)
from tests.test_xls_texto_eleventa import ENCABEZADO_REAL

pytestmark = pytest.mark.django_db
D = Decimal


def fila(codigo, desc="Algo", costo=100, venta=200, inv=5):
    return [codigo, desc, costo, venta, 150, inv, 1, "Dep"]


def texto(*filas):
    return (ENCABEZADO_REAL + "\n" + "\n".join(filas) + "\n").encode("cp1252")


def cargar(adm, contenido, nombre="inventario.xlsx", actualizar=False):
    i = preparar(adm, contenido, nombre)
    assert confirmar_http(adm, i, actualizar).status_code == 302
    return i


def operador_client():
    c = Client()
    c.force_login(crear_usuario("oper", "Operador"))
    return c


# ================================================================== D-63: importador
def test_cada_tipo_de_fila_con_error_se_crea_marcada_sin_stock_ni_precios(adm):
    contenido = texto(
        "0077\tMANGUERA\t1/2 REFORZADA\t1.234,50\t2.500,00\t2.000,00\t10\t2\tFiltros",     # columnas corridas
        "T1\tTexto en precio\tabc\t2.000,00\t1.500,00\t3\t1\tAceites",                        # texto en un numero
        "DUP\tUno\t1.000,00\t2.000,00\t1.500,00\t3\t1\tAceites",
        "DUP\tDos\t1.000,00\t2.000,00\t1.500,00\t3\t1\tAceites",                              # repetido
        "OK1\tNormal\t1.000,00\t2.000,00\t1.500,00\t3\t1\tAceites",
    )
    cargar(adm, contenido, "inventario.xls")
    marcados = {p.sku: p for p in Producto.objects.filter(requiere_revision=True)}
    assert set(marcados) == {"0077", "T1", "DUP"}
    for p in marcados.values():
        assert p.costo == p.precio_venta == p.precio_mayoreo == 0 and p.categoria is None
        assert servicios.stock_de(p) == 0 and not Movimiento.objects.filter(producto=p).exists()
        assert p.motivo_revision.startswith("Fila ") and p.activo
    assert marcados["0077"].nombre == "0077"                       # descripcion no confiable por corrimiento: el codigo
    assert "La fila tiene 9 columnas" in marcados["0077"].motivo_revision
    assert marcados["T1"].nombre == "Texto en precio" and "texto no numérico" in marcados["T1"].motivo_revision
    assert marcados["DUP"].nombre == "Uno" and "duplicado" in marcados["DUP"].motivo_revision
    ok = Producto.objects.get(sku="OK1")                              # las filas validas se importan como hoy
    assert not ok.requiere_revision and ok.costo == 1000 and servicios.stock_de(ok) == 3
    assert Movimiento.objects.count() == 1


def test_codigo_vacio_no_se_crea_y_codigo_existente_no_se_toca(adm):
    previo = Producto.objects.create(sku="EX1", nombre="Original", costo=D("5"))
    i = preparar(adm, xlsx([["", "Sin codigo", 1, 1, 1, 1, 0, ""], ["EX1", "Pisar", 1, 1, 1, "abc", 0, ""],
                            fila("N1")]))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    por = {f.numero: f for f in r.context["pagina"]}
    vacia = next(f for f in r.context["pagina"] if f.codigo == "")
    existente = next(f for f in r.context["pagina"] if f.codigo == "EX1")
    assert vacia.estado == "error" and existente.estado == "error" and "ya existe" in existente.nota
    assert r.context["resumen"]["con_error"] == 2 and r.context["resumen"]["para_revision"] == 0 and por
    confirmar_http(adm, i)
    previo.refresh_from_db()
    assert previo.nombre == "Original" and previo.costo == 5 and not previo.requiere_revision
    assert sorted(Producto.objects.values_list("sku", flat=True)) == ["EX1", "N1"]
    resumen = ImportacionEleventa.objects.get().resumen
    assert resumen["creados_para_revision"] == 0 and len(resumen["errores"]) == 2


def test_codigo_repetido_en_el_archivo_crea_uno_y_lista_las_demas(adm):
    i = preparar(adm, xlsx([fila("R1", "Primera"), fila("R1", "Segunda"), fila("R1", "Tercera")]))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    estados = [(f.numero, f.estado) for f in r.context["pagina"]]
    assert [e for _, e in estados].count("revision") == 1 and [e for _, e in estados].count("error") == 2
    assert all("una sola vez" in f.nota for f in r.context["pagina"] if f.estado == "error")
    confirmar_http(adm, i)
    assert Producto.objects.filter(sku="R1").count() == 1 and Producto.objects.get(sku="R1").nombre == "Primera"


def test_repetir_la_carga_del_mismo_archivo_solo_crea_los_que_faltan_sin_duplicar_stock(adm):
    contenido = xlsx([fila("A1", inv=10), fila("MAL", "Texto", inv="abc"), fila("A2", inv=4)])
    primera = cargar(adm, contenido)
    assert Producto.objects.count() == 3 and Movimiento.objects.count() == 2
    r = subir(adm, contenido)
    assert r.status_code == 302                                           # el hash ya no es unico
    segunda = ImportacionEleventa.objects.order_by("-pk").first()
    assert segunda.pk != primera.pk and segunda.hash_sha256 == primera.hash_sha256
    vp = adm.get(reverse("importar_vista_previa", args=[segunda.pk]))
    assert "ya se cargó el" in vp.content.decode()
    confirmar_http(adm, segunda)
    assert Producto.objects.count() == 3 and Movimiento.objects.count() == 2
    assert sum(m.cantidad for m in Movimiento.objects.all()) == D("14")


def test_repetir_la_carga_crea_solo_el_producto_que_falta(adm):
    contenido = xlsx([fila("A1", inv=10), fila("A2", inv=4)])
    cargar(adm, contenido)
    # simula un producto que no llego a crearse (p. ej. el archivo cambio): se agrega una fila nueva al reimportar
    mas = xlsx([fila("A1", inv=10), fila("A2", inv=4), fila("A3", inv=2)])
    cargar(adm, mas)
    assert sorted(Producto.objects.values_list("sku", flat=True)) == ["A1", "A2", "A3"]
    assert sum(m.cantidad for m in Movimiento.objects.all()) == D("16")        # A1 y A2 no se duplicaron


def test_vista_previa_y_resumen_muestran_las_filas_para_revision(adm):
    i = preparar(adm, xlsx([fila("MAL", "Texto", inv="abc"), fila("OK")]))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    html = r.content.decode()
    assert "Se creará para revisión" in html and "Se crearán para revisión" in html
    assert r.context["resumen"]["para_revision"] == 1 and r.context["resumen"]["nuevas"] == 1
    confirmar_http(adm, i)
    html = adm.get(reverse("importar_resumen", args=[i.pk])).content.decode()
    assert "Creados para revisión" in html and "Productos creados para revisión" in html and "MAL" in html
    assert ImportacionEleventa.objects.get().resumen["creados_para_revision"] == 1


def test_la_fila_para_revision_no_recibe_codigo_alternativo(adm):
    cargar(adm, xlsx([fila("MAL", "ABC123", inv="abc")]))
    assert Producto.objects.get(sku="MAL").codigoalternativo_set.count() == 0


# ================================================================== D-63: pantallas
@pytest.fixture
def con_revision(adm):
    cargar(adm, xlsx([fila("MAL", "Texto raro", inv="abc"), fila("BIEN", "Normal")]))
    return Producto.objects.get(sku="MAL")


def test_lista_muestra_insignia_chip_con_conteo_y_filtro(adm, con_revision):
    html = adm.get(reverse("producto_lista")).content.decode()
    assert "⚠ Requiere modificación urgente" in html and "⚠ Requiere revisión (1)" in html
    r = adm.get(reverse("producto_lista"), {"revision": "1"})
    assert [p.sku for p in r.context["productos"]] == ["MAL"]
    assert "Productos que requieren revisión" in r.content.decode()
    # combinado con busqueda
    assert [p.sku for p in adm.get(reverse("producto_lista"), {"revision": "1", "q": "BIEN"}).context["productos"]] == []


def test_ficha_con_aviso_motivo_y_marcar_como_revisado(adm, con_revision):
    url = reverse("producto_detalle", args=[con_revision.pk])
    html = adm.get(url).content.decode()
    assert "Requiere modificación urgente" in html and "Marcar como revisado" in html
    assert "texto no numérico" in html and reverse("corregir", args=[con_revision.pk]) in html
    r = adm.post(reverse("producto_revisado", args=[con_revision.pk]))
    assert r.status_code == 302
    con_revision.refresh_from_db()
    assert not con_revision.requiere_revision and con_revision.motivo_revision == ""
    assert "Marcar como revisado" not in adm.get(url).content.decode()
    assert "⚠ Requiere revisión (0)" in adm.get(reverse("producto_lista")).content.decode()


def test_marcar_revisado_exige_permiso_y_post(adm, con_revision):
    url = reverse("producto_revisado", args=[con_revision.pk])
    assert adm.get(url).status_code == 405
    anonimo = Client()
    assert anonimo.post(url).status_code == 302                       # al login
    con_revision.refresh_from_db()
    assert con_revision.requiere_revision
    sin_permiso = Client()
    sin_permiso.force_login(crear_usuario("solover"))
    assert sin_permiso.post(url).status_code == 403


def test_operador_puede_marcar_revisado_porque_edita_productos(con_revision):
    assert operador_client().post(reverse("producto_revisado", args=[con_revision.pk])).status_code == 302
    con_revision.refresh_from_db()
    assert not con_revision.requiere_revision


def test_panel_muestra_tarjeta_con_conteo_y_enlace(adm, con_revision):
    r = adm.get("/")
    assert r.context["requieren_revision"] == 1
    html = r.content.decode()
    assert "Productos que requieren revisión" in html and "?revision=1" in html


def test_panel_cuenta_solo_los_activos(adm, con_revision):
    Producto.objects.filter(pk=con_revision.pk).update(activo=False)
    assert adm.get("/").context["requieren_revision"] == 0


# ================================================================== D-64: archivar
@pytest.fixture
def producto_con_kardex(admin_user):
    p = Producto.objects.create(sku="K1", nombre="Con historial", stock_min=D("6"), stock_max=D("20"))
    servicios.registrar_entrada(p, D("3"), admin_user)
    return p


def archivar(client, p):
    url = reverse("producto_archivar", args=[p.pk])
    return client.post(url, {"token": token_de(client.get(url))})


def test_confirmacion_de_archivar_get_no_cambia_nada_y_post_archiva(adm, producto_con_kardex):
    p = producto_con_kardex
    r = adm.get(reverse("producto_archivar", args=[p.pk]))
    assert r.status_code == 200 and "no se borra nada" in r.content.decode()
    p.refresh_from_db()
    assert p.activo
    r = archivar(adm, p)
    assert r.status_code == 302
    p.refresh_from_db()
    assert not p.activo and Movimiento.objects.filter(producto=p).count() == 1


def test_archivar_usa_token_de_un_solo_uso(adm, producto_con_kardex):
    p = producto_con_kardex
    url = reverse("producto_archivar", args=[p.pk])
    token = token_de(adm.get(url))
    adm.post(url, {"token": token})
    Producto.objects.filter(pk=p.pk).update(activo=True)
    adm.post(url, {"token": token})                                   # token ya consumido
    p.refresh_from_db()
    assert p.activo
    assert adm.post(url, {}).status_code == 302 and Producto.objects.get(pk=p.pk).activo


def test_archivado_desaparece_de_lista_busqueda_panel_e_impresion_y_conserva_kardex(adm, producto_con_kardex):
    p = producto_con_kardex
    assert [x.sku for x in adm.get(reverse("producto_lista"), {"q": "historial"}).context["productos"]] == ["K1"]
    assert adm.get("/").context["bajo_minimo"] == 1
    archivar(adm, p)
    assert not list(adm.get(reverse("producto_lista")).context["productos"])
    assert not list(adm.get(reverse("producto_lista"), {"q": "K1"}).context["productos"])
    assert not list(adm.get(reverse("producto_lista"), {"bajo_minimo": "1"}).context["productos"])   # la hoja impresa
    panel = adm.get("/").context
    assert panel["bajo_minimo"] == 0 and panel["valor_inventario"] == 0
    kardex = adm.get(reverse("kardex", args=[p.pk]))
    assert kardex.status_code == 200 and len(kardex.context["filas"]) == 1
    assert adm.get(reverse("producto_detalle", args=[p.pk])).status_code == 200


def test_archivados_los_ve_solo_el_administrador_con_su_chip(adm, producto_con_kardex):
    p = producto_con_kardex
    archivar(adm, p)
    r = adm.get(reverse("producto_lista"), {"archivados": "1"})
    assert [x.sku for x in r.context["productos"]] == ["K1"] and "Archivado" in r.content.decode()
    assert any(c["texto"] == "Archivados" for c in adm.get(reverse("producto_lista")).context["chips_estado"])
    oper = operador_client()
    r = oper.get(reverse("producto_lista"), {"archivados": "1"})            # el operador no ve archivados ni el chip
    assert not list(r.context["productos"]) and all(c["texto"] != "Archivados" for c in r.context["chips_estado"])


def test_categoria_cuenta_solo_activos(adm, producto_con_kardex):
    from inventario.models import Categoria
    cat = Categoria.objects.create(nombre="C")
    Producto.objects.filter(pk=producto_con_kardex.pk).update(categoria=cat)
    archivar(adm, producto_con_kardex)
    r = adm.get(reverse("categoria_lista"))
    assert [c.n_productos for c in r.context["categorias"]] == [0]


def test_ficha_de_archivado_con_aviso_y_reactivar(adm, producto_con_kardex):
    p = producto_con_kardex
    archivar(adm, p)
    html = adm.get(reverse("producto_detalle", args=[p.pk])).content.decode()
    assert "Producto archivado" in html and "Reactivar producto" in html
    assert adm.post(reverse("producto_reactivar", args=[p.pk])).status_code == 302
    p.refresh_from_db()
    assert p.activo
    assert [x.sku for x in adm.get(reverse("producto_lista")).context["productos"]] == ["K1"]
    assert "Producto archivado" not in adm.get(reverse("producto_detalle", args=[p.pk])).content.decode()


def test_zona_de_peligro_en_editar_solo_para_quien_archiva(adm, producto_con_kardex):
    p = producto_con_kardex
    html = adm.get(reverse("producto_editar", args=[p.pk])).content.decode()
    assert "Zona de peligro" in html and "Archivar producto" in html and "Borrar" not in html
    assert "Zona de peligro" not in operador_client().get(reverse("producto_editar", args=[p.pk])).content.decode()
    assert "Zona de peligro" not in adm.get(reverse("producto_nuevo")).content.decode()


def test_el_operador_no_archiva_ni_reactiva(producto_con_kardex):
    oper = operador_client()
    p = producto_con_kardex
    assert oper.get(reverse("producto_archivar", args=[p.pk])).status_code == 403
    assert oper.post(reverse("producto_archivar", args=[p.pk]), {"token": "x"}).status_code == 403
    assert oper.post(reverse("producto_reactivar", args=[p.pk])).status_code == 403
    assert Producto.objects.get(pk=p.pk).activo


def test_no_existe_ninguna_pantalla_para_borrar_productos(adm, producto_con_kardex):
    from django.urls import NoReverseMatch
    for nombre in ("producto_borrar", "producto_eliminar", "producto_delete"):
        with pytest.raises(NoReverseMatch):
            reverse(nombre, args=[producto_con_kardex.pk])
    assert "/borrar/" not in adm.get(reverse("producto_editar", args=[producto_con_kardex.pk])).content.decode()


@pytest.mark.parametrize("operacion", ["entrada", "salida", "ajuste", "conteo", "reversa"])
def test_un_producto_archivado_rechaza_movimientos_en_el_servicio(admin_user, producto_con_kardex, operacion):
    p = producto_con_kardex
    mov = Movimiento.objects.get(producto=p)
    Producto.objects.filter(pk=p.pk).update(activo=False)
    llamadas = {
        "entrada": lambda: servicios.registrar_entrada(p, D("1"), admin_user),
        "salida": lambda: servicios.registrar_salida(p, D("1"), admin_user),
        "ajuste": lambda: servicios.registrar_ajuste(p, D("1"), admin_user, "x"),
        "conteo": lambda: servicios.ajustar_a_conteo(p, D("9"), admin_user, "x"),
        "reversa": lambda: servicios.revertir(mov, admin_user, "x"),
    }
    with pytest.raises(ValidationError) as e:
        llamadas[operacion]()
    assert "archivado" in e.value.messages[0]
    assert Movimiento.objects.filter(producto=p).count() == 1
    Producto.objects.filter(pk=p.pk).update(activo=True)
    llamadas["entrada"]()                                              # reactivado: vuelve a admitir movimientos
    assert Movimiento.objects.filter(producto=p).count() == 2


def test_la_pantalla_de_entrada_muestra_el_mensaje_claro_si_esta_archivado(adm, producto_con_kardex):
    p = producto_con_kardex
    url = reverse("entrada", args=[p.pk])
    token = token_de(adm.get(url))
    Producto.objects.filter(pk=p.pk).update(activo=False)
    r = adm.post(url, {"token": token, "cantidad": "2"})
    assert r.status_code == 200 and "está archivado" in r.content.decode()
    assert Movimiento.objects.filter(producto=p).count() == 1


def test_kardex_de_archivado_no_ofrece_movimientos(adm, producto_con_kardex):
    p = producto_con_kardex
    archivar(adm, p)
    html = adm.get(reverse("kardex", args=[p.pk])).content.decode()
    assert "Producto archivado" in html and "Registrar entrada" not in html and "Corregir stock" not in html


def test_archivar_nunca_borra_movimientos_ni_documentos(adm, producto_con_kardex):
    from inventario.models import Documento
    antes = (Movimiento.objects.count(), Documento.objects.count())
    archivar(adm, producto_con_kardex)
    assert (Movimiento.objects.count(), Documento.objects.count()) == antes
    assert Producto.objects.filter(pk=producto_con_kardex.pk).exists()


# ================================================================== migraciones y permisos
def test_los_productos_existentes_toman_los_valores_por_defecto():
    p = Producto.objects.create(sku="D1", nombre="x")
    assert p.requiere_revision is False and p.motivo_revision == "" and p.activo is True
    with connection.cursor() as c:                      # una fila insertada sin los campos nuevos (como antes de 0006)
        c.execute("INSERT INTO inventario_producto (sku, nombre, unidad, costo, precio_venta, precio_mayoreo, "
                  "stock_min, stock_max) VALUES ('D2', 'y', 'u', 0, 0, 0, 0, 0)")
    q = Producto.objects.get(sku="D2")
    assert q.requiere_revision is False and q.motivo_revision == "" and q.activo is True


def test_permiso_archivar_solo_para_el_administrador_y_la_migracion_es_idempotente():
    antes = (set(Group.objects.get(name="Administrador").permissions.values_list("codename", flat=True)),
             set(Group.objects.get(name="Operador").permissions.values_list("codename", flat=True)))
    migracion = import_module("cuentas.migrations.0003_permiso_archivar_producto")
    for _ in range(2):
        migracion.asignar(django_apps, None)
    despues = (set(Group.objects.get(name="Administrador").permissions.values_list("codename", flat=True)),
               set(Group.objects.get(name="Operador").permissions.values_list("codename", flat=True)))
    assert antes == despues
    assert "archivar_producto" in despues[0] and "archivar_producto" not in despues[1]
    assert Group.objects.filter(name="Administrador").count() == 1


def test_el_hash_de_la_importacion_ya_no_es_unico_pero_tiene_indice():
    campo = ImportacionEleventa._meta.get_field("hash_sha256")
    assert not campo.unique and campo.db_index


# ================================================================== T1: error en el CODIGO -> no se crea
@pytest.mark.parametrize("codigo", ['"0077"', "=1+2", "-123", "+5", "@x", "A" * 51])
def test_t1_fila_con_el_error_en_el_codigo_no_se_crea_para_revision(adm, codigo):
    contenido = texto(f"{codigo}\tDescripcion\t1.000\t2.000\t1.500\t3\t1\tAceites", "OK1\tNormal\t1\t2\t3\t4\t0\tD")
    i = preparar(adm, contenido, "inventario.xls")
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    mala = next(f for f in r.context["pagina"] if f.codigo == codigo)
    assert mala.estado == "error" and "El código no es válido; corrígelo en Eleventa." in mala.nota
    assert r.context["resumen"]["con_error"] == 1 and r.context["resumen"]["para_revision"] == 0
    confirmar_http(adm, i)
    assert list(Producto.objects.values_list("sku", flat=True)) == ["OK1"]


def test_t1_valor_logico_de_xlsx_y_codigo_vacio_no_se_crean(adm):
    i = preparar(adm, xlsx([[True, "Booleano", 1, 1, 1, 1, 0, ""], ["", "Vacio", 1, 1, 1, 1, 0, ""], fila("OK1")]))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    assert r.context["resumen"]["con_error"] == 2 and r.context["resumen"]["para_revision"] == 0
    confirmar_http(adm, i)
    assert list(Producto.objects.values_list("sku", flat=True)) == ["OK1"]


def test_t1_codigo_valido_con_error_en_otra_columna_si_se_crea_para_revision(adm):
    i = preparar(adm, xlsx([fila("OK9", "Texto", inv="abc")]))
    confirmar_http(adm, i)
    assert Producto.objects.get(sku="OK9").requiere_revision


# ================================================================== T2: el admin no borra productos
def test_t2_ni_el_superusuario_borra_productos_desde_el_admin(client, django_user_model):
    from django.contrib import admin as dj_admin
    from django.test import RequestFactory
    su = django_user_model.objects.create_superuser("root", password="Zq7-mPx2vL-prueba")
    p = Producto.objects.create(sku="AD1", nombre="x")
    client.force_login(su)
    modelo = dj_admin.site._registry[Producto]
    peticion = RequestFactory().get("/")
    peticion.user = su
    assert modelo.has_delete_permission(peticion) is False and modelo.has_delete_permission(peticion, p) is False
    assert client.get(reverse("admin:inventario_producto_delete", args=[p.pk])).status_code == 403
    assert client.post(reverse("admin:inventario_producto_delete", args=[p.pk]), {"post": "yes"}).status_code == 403
    r = client.post(reverse("admin:inventario_producto_changelist"),
                    {"action": "delete_selected", "_selected_action": [p.pk], "post": "yes"})
    assert Producto.objects.filter(pk=p.pk).exists()
    assert "delete_selected" not in [a[0] for a in modelo.get_actions(peticion).values()] and r.status_code in (200, 302)
    assert "delete_selected" not in modelo.get_actions(peticion)


def test_t2_el_admin_filtra_por_activo_y_revision(client, django_user_model):
    su = django_user_model.objects.create_superuser("root", password="Zq7-mPx2vL-prueba")
    client.force_login(su)
    Producto.objects.create(sku="AR1", nombre="x", requiere_revision=True)
    Producto.objects.create(sku="AR2", nombre="y", activo=False)
    url = reverse("admin:inventario_producto_changelist")
    assert [p.sku for p in client.get(url, {"requiere_revision__exact": "1"}).context["cl"].result_list] == ["AR1"]
    assert [p.sku for p in client.get(url, {"activo__exact": "0"}).context["cl"].result_list] == ["AR2"]


# ================================================================== T3: el lote rechaza archivados
def test_t3_inventario_inicial_en_lote_rechaza_productos_archivados(admin_user):
    from inventario.models import Documento
    p = Producto.objects.create(sku="L1", nombre="x", activo=False)
    q = Producto.objects.create(sku="L2", nombre="y")
    doc = Documento.objects.create(tipo=Documento.INVENTARIO_INICIAL, nombre="t")
    with pytest.raises(ValidationError) as e:
        servicios.registrar_inventario_inicial_en_lote([(q, D("1")), (p, D("2"))], admin_user, doc)
    assert "archivado" in e.value.messages[0] and not Movimiento.objects.exists()


# ================================================================== T4: D-65
def marcado(sku="REV1", con_movimiento=False, usuario=None, **kw):
    p = Producto.objects.create(sku=sku, nombre=sku, requiere_revision=True, motivo_revision="Fila 3: texto", **kw)
    if con_movimiento:
        servicios.registrar_ajuste(p, D("4"), usuario, "Conteo a mano")
    return p


@pytest.mark.parametrize("actualizar", [False, True])
def test_t4a_marcado_sin_movimientos_se_completa_con_fila_valida(adm, admin_user, actualizar):
    p = marcado("REV1")
    i = preparar(adm, xlsx([["REV1", "ABC123", 1500, 2990, 2500, 7, 2, "Filtros"], fila("N1")]))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    f = next(x for x in r.context["pagina"] if x.codigo == "REV1")
    assert f.estado == "completar" and r.context["resumen"]["por_completar"] == 1
    html = r.content.decode()
    assert "Se completará (estaba en revisión)" in html and "Se completarán (estaban en revisión)" in html
    confirmar_http(adm, i, actualizar)
    p.refresh_from_db()
    assert not p.requiere_revision and p.motivo_revision.startswith("Completado por la carga del ")
    assert p.nombre == "ABC123" and p.costo == 1500 and p.precio_venta == 2990 and p.precio_mayoreo == 2500
    assert p.stock_min == 2 and p.categoria.nombre == "Filtros"
    assert servicios.stock_de(p) == 7
    m = Movimiento.objects.get(producto=p)
    assert m.motivo == imp.MOTIVO_INICIAL and m.documento.tipo == "inventario_inicial"
    assert [(a.valor, a.tipo) for a in p.codigoalternativo_set.all()] == [("ABC123", "fabricante")]
    assert ImportacionEleventa.objects.get().resumen["completados"] == 1


@pytest.mark.parametrize("actualizar", [False, True])
def test_t4a_marcado_con_movimientos_se_trata_como_existente_y_conserva_la_marca(adm, admin_user, actualizar):
    p = marcado("REV2", con_movimiento=True, usuario=admin_user)
    i = preparar(adm, xlsx([["REV2", "Nombre nuevo", 1500, 2990, 2500, 99, 2, "Filtros"]]))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    f = r.context["pagina"][0]
    assert f.estado == "existente" and "ya tiene movimientos" in f.nota and r.context["resumen"]["por_completar"] == 0
    confirmar_http(adm, i, actualizar)
    p.refresh_from_db()
    assert p.requiere_revision and p.motivo_revision == "Fila 3: texto"          # conserva la marca y el motivo
    assert servicios.stock_de(p) == 4 and Movimiento.objects.filter(producto=p).count() == 1
    assert (p.nombre == "Nombre nuevo") == actualizar                              # solo si se pidio actualizar


def test_t4a_la_carga_posterior_completa_al_que_se_creo_para_revision(adm):
    cargar(adm, xlsx([fila("MAL", "Texto", inv="abc")]), "primero.xlsx")
    assert Producto.objects.get(sku="MAL").requiere_revision
    cargar(adm, xlsx([["MAL", "Corregido", 100, 200, 150, 6, 1, "Dep"]]), "segundo.xlsx")
    p = Producto.objects.get(sku="MAL")
    assert not p.requiere_revision and p.nombre == "Corregido" and servicios.stock_de(p) == 6


@pytest.mark.parametrize("actualizar", [False, True])
def test_t4b_un_archivado_nunca_se_actualiza_ni_completa(adm, admin_user, actualizar):
    a = Producto.objects.create(sku="ARC1", nombre="Original", activo=False, costo=D("5"))
    b = Producto.objects.create(sku="ARC2", nombre="Original2", activo=False, requiere_revision=True,
                                motivo_revision="x")
    i = preparar(adm, xlsx([["ARC1", "ABC123", 1500, 2990, 2500, 7, 2, "Filtros"],
                            ["ARC2", "ABC124", 1500, 2990, 2500, 7, 2, "Filtros"]]))
    r = adm.get(reverse("importar_vista_previa", args=[i.pk]))
    assert {f.estado for f in r.context["pagina"]} == {"archivado"} and r.context["resumen"]["archivados"] == 2
    assert "Archivado: no se modifica" in r.content.decode()
    confirmar_http(adm, i, actualizar)
    for p in (a, b):
        p.refresh_from_db()
        assert not p.activo and p.nombre.startswith("Original") and servicios.stock_de(p) == 0
        assert p.codigoalternativo_set.count() == 0
    b.refresh_from_db()
    assert b.requiere_revision and b.motivo_revision == "x" and not Movimiento.objects.exists()
