"""servicios.registrar_inventario_inicial_en_lote (T-F1-08, D-51)."""
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test.utils import CaptureQueriesContext
from django.db import connection

from inventario import servicios
from inventario.models import Documento, Movimiento, Producto

pytestmark = pytest.mark.django_db
D = Decimal


def usuario(nombre, grupo=None):
    u = get_user_model().objects.create_user(nombre, password="Zq7-mPx2vL-prueba")
    if grupo:
        u.groups.add(Group.objects.get(name=grupo))
    return u


@pytest.fixture
def doc():
    return Documento.objects.create(tipo="inventario_inicial", nombre="inicial")


@pytest.fixture
def productos():
    return [Producto.objects.create(sku=f"L{i}", nombre=f"p{i}") for i in range(3)]


def test_crea_un_ajuste_por_producto_con_documento_motivo_y_usuario(doc, productos):
    adm = usuario("adm", "Administrador")
    movs = servicios.registrar_inventario_inicial_en_lote(
        [(p, c) for p, c in zip(productos, ["1", D("2.5"), 3])], adm, doc)
    assert len(movs) == 3 and Movimiento.objects.count() == 3
    assert {m.tipo for m in Movimiento.objects.all()} == {"ajuste"}
    assert {m.motivo for m in Movimiento.objects.all()} == {servicios.MOTIVO_INVENTARIO_INICIAL}
    assert {m.documento_id for m in Movimiento.objects.all()} == {doc.pk}
    assert {m.usuario for m in Movimiento.objects.all()} == {adm}
    assert [servicios.stock_de(p) for p in productos] == [D("1.00"), D("2.50"), D("3.00")]


def test_exige_el_permiso_de_ajuste_una_vez_y_no_crea_nada(doc, productos):
    for u in (usuario("op", "Operador"), usuario("sin")):
        with pytest.raises(PermissionDenied):
            servicios.registrar_inventario_inicial_en_lote([(productos[0], 1)], u, doc)
    assert Movimiento.objects.count() == 0
    solo = usuario("solo")
    solo.user_permissions.add(Permission.objects.get(codename="registrar_ajuste"))
    servicios.registrar_inventario_inicial_en_lote([(productos[0], 1)], solo, doc)
    assert Movimiento.objects.count() == 1


def test_inactivo_o_anonimo_rechazado(doc, productos):
    from django.contrib.auth.models import AnonymousUser

    u = usuario("adm", "Administrador")
    u.is_active = False
    u.save()
    for quien in (u, AnonymousUser(), None):
        with pytest.raises(PermissionDenied):
            servicios.registrar_inventario_inicial_en_lote([(productos[0], 1)], quien, doc)


def test_rechaza_productos_con_movimientos_previos_y_no_crea_ninguno(doc, productos):
    adm = usuario("adm", "Administrador")
    servicios.registrar_entrada(productos[1], 5, adm)
    with pytest.raises(ValidationError, match="ya tiene movimientos"):
        servicios.registrar_inventario_inicial_en_lote([(p, 1) for p in productos], adm, doc)
    assert Movimiento.objects.count() == 1


@pytest.mark.parametrize("cantidad", [0, -1, "0.001", 1.5, True, "abc", D("99999999999")])
def test_valida_cada_movimiento_y_es_todo_o_nada(doc, productos, cantidad):
    adm = usuario("adm", "Administrador")
    with pytest.raises(ValidationError):
        servicios.registrar_inventario_inicial_en_lote([(productos[0], 1), (productos[1], cantidad)], adm, doc)
    assert Movimiento.objects.count() == 0


def test_producto_repetido_y_lista_vacia(doc, productos):
    adm = usuario("adm", "Administrador")
    with pytest.raises(ValidationError):
        servicios.registrar_inventario_inicial_en_lote([(productos[0], 1), (productos[0], 2)], adm, doc)
    assert servicios.registrar_inventario_inicial_en_lote([], adm, doc) == []


def test_numero_de_consultas_no_crece_con_las_filas(doc):
    adm = usuario("adm", "Administrador")
    lote = lambda n: [(Producto.objects.create(sku=f"Q{n}-{i}", nombre="x"), 1) for i in range(n)]
    a, b = lote(5), lote(300)
    with CaptureQueriesContext(connection) as q1:
        servicios.registrar_inventario_inicial_en_lote(a, adm, doc)
    with CaptureQueriesContext(connection) as q2:
        servicios.registrar_inventario_inicial_en_lote(b, adm, doc)
    assert len(q2) <= len(q1) + 2


def test_exige_documento_de_inventario_inicial_o_ninguno(productos):
    adm = usuario("adm", "Administrador")
    for tipo in ("venta", "factura", "ajuste", "conteo"):
        with pytest.raises(ValidationError) as e:
            servicios.registrar_inventario_inicial_en_lote(
                [(productos[0], 1)], adm, Documento.objects.create(tipo=tipo, nombre=tipo, numero="1" if tipo != "factura" else "9",
                                                                    proveedor=None if tipo != "factura" else __import__("inventario.models", fromlist=["Proveedor"]).Proveedor.objects.create(nombre="P")))
        assert "documento" in e.value.error_dict
    assert Movimiento.objects.count() == 0
    servicios.registrar_inventario_inicial_en_lote([(productos[0], 1)], adm, None)      # None se acepta
    assert Movimiento.objects.count() == 1


def test_bloquea_los_productos_antes_de_verificar_movimientos_previos(doc, productos, monkeypatch):
    from django.db.models import QuerySet

    adm = usuario("adm", "Administrador")
    llamadas = []
    real = QuerySet.select_for_update

    def espia(self, *a, **k):
        llamadas.append(self.model.__name__)
        return real(self, *a, **k)

    monkeypatch.setattr(QuerySet, "select_for_update", espia)
    with CaptureQueriesContext(connection) as q:
        servicios.registrar_inventario_inicial_en_lote([(p, 1) for p in productos], adm, doc)
    assert llamadas == ["Producto"]
    sqls = [x["sql"] for x in q.captured_queries]
    bloqueo = next(i for i, s in enumerate(sqls) if 'FROM "inventario_producto"' in s)
    previos = next(i for i, s in enumerate(sqls) if 'FROM "inventario_movimiento"' in s and "LIMIT 1" in s)
    assert bloqueo < previos


def test_docstring_aclara_que_es_solo_para_inventario_inicial():
    assert "SOLO para la carga de INVENTARIO INICIAL" in servicios.registrar_inventario_inicial_en_lote.__doc__
