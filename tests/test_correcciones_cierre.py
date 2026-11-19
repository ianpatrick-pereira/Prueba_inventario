"""Correcciones de la auditoria de cierre de la fase 1 (T-F1-11)."""
import datetime as dt
from pathlib import Path
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from inventario import servicios
from inventario.models import Categoria, CodigoAlternativo, Movimiento, Producto, ProductoProveedor, Proveedor

pytestmark = pytest.mark.django_db
D = Decimal
CLAVE = "Zq7-mPx2vL-prueba"


@pytest.fixture(params=["superusuario", "administrador_staff"])
def admin_web(request):
    """Cliente del panel /admin/: superusuario y un Administrador (grupo) con is_staff."""
    U = get_user_model()
    if request.param == "superusuario":
        u = U.objects.create_superuser("root", password=CLAVE)
    else:
        u = U.objects.create_user("jefe", password=CLAVE, is_staff=True)
        u.groups.add(Group.objects.get(name="Administrador"))
    c = Client()
    c.force_login(u)
    return c


def datos_producto(**kw):
    d = {"nombre": "Producto", "categoria": "", "unidad": "unidad", "costo": "0", "precio_venta": "0",
         "precio_mayoreo": "0", "stock_min": "1", "stock_max": "5"}
    d.update(kw)
    return d


def cambiar(c, modelo, pk):
    return reverse(f"admin:inventario_{modelo}_change", args=[pk])


def agregar(modelo):
    return reverse(f"admin:inventario_{modelo}_add")


# ------------------------------------------------------------------ 1. admin (H1)
def test_admin_producto_cambiar_el_sku_por_post_no_tiene_efecto(admin_web):
    p = Producto.objects.create(sku="C934J", nombre="Original")
    url = cambiar(admin_web, "producto", p.pk)
    assert 'name="sku"' not in admin_web.get(url).content.decode()      # solo lectura
    r = admin_web.post(url, datos_producto(sku="HACK", nombre="Editado"))
    assert r.status_code == 302
    p.refresh_from_db()
    assert p.sku == "C934J" and p.nombre == "Editado"


def test_admin_producto_crear_si_permite_fijar_el_sku_normalizado(admin_web):
    r = admin_web.post(agregar("producto"), datos_producto(sku=" 0077 "))
    assert r.status_code == 302 and Producto.objects.get().sku == "0077"
    r = admin_web.post(agregar("producto"), datos_producto(sku="0077"))
    assert r.status_code == 200 and "Ya existe un producto con el código" in r.content.decode()


@pytest.mark.parametrize("campo", ["costo", "precio_venta", "precio_mayoreo", "stock_min", "stock_max"])
def test_admin_producto_rechaza_negativos(admin_web, campo):
    r = admin_web.post(agregar("producto"), datos_producto(sku="N1", **{campo: "-1"}))
    assert r.status_code == 200 and "No puede ser negativo." in r.content.decode()
    assert not Producto.objects.exists()


def test_admin_producto_rechaza_maximo_menor_que_minimo_y_valida_en_edicion(admin_web):
    r = admin_web.post(agregar("producto"), datos_producto(sku="M1", stock_min="9", stock_max="1"))
    assert r.status_code == 200 and "El stock máximo no puede ser menor que el mínimo." in r.content.decode()
    p = Producto.objects.create(sku="M2", nombre="x")
    r = admin_web.post(cambiar(admin_web, "producto", p.pk), datos_producto(stock_min="9", stock_max="1"))
    assert r.status_code == 200 and "El stock máximo" in r.content.decode()
    r = admin_web.post(cambiar(admin_web, "producto", p.pk), datos_producto(costo="-5"))
    assert r.status_code == 200 and Producto.objects.get().costo == 0


def test_admin_codigo_alternativo_valor_fijo_al_editar_y_duplicados(admin_web):
    p = Producto.objects.create(sku="P1", nombre="uno")
    q = Producto.objects.create(sku="P2", nombre="dos")
    r = admin_web.post(agregar("codigoalternativo"), {"producto": p.pk, "tipo": "barras", "valor": " 0123 "})
    assert r.status_code == 302
    c = CodigoAlternativo.objects.get()
    assert c.valor == "0123"
    r = admin_web.post(agregar("codigoalternativo"), {"producto": q.pk, "tipo": "barras", "valor": "0123"})
    assert r.status_code == 200 and "ya pertenece al producto" in r.content.decode()
    url = cambiar(admin_web, "codigoalternativo", c.pk)
    assert 'name="valor"' not in admin_web.get(url).content.decode()
    admin_web.post(url, {"producto": p.pk, "tipo": "fabricante", "valor": "CAMBIADO"})
    c.refresh_from_db()
    assert c.valor == "0123" and c.tipo == "fabricante"


def test_admin_producto_proveedor_codigo_fijo_al_editar_y_duplicados(admin_web):
    p = Producto.objects.create(sku="P1", nombre="uno")
    a, b = Proveedor.objects.create(nombre="A"), Proveedor.objects.create(nombre="B")
    r = admin_web.post(agregar("productoproveedor"), {"producto": p.pk, "proveedor": a.pk, "codigo_proveedor": " X1 "})
    assert r.status_code == 302
    pp = ProductoProveedor.objects.get()
    assert pp.codigo_proveedor == "X1"
    otro = Producto.objects.create(sku="P2", nombre="dos")
    r = admin_web.post(agregar("productoproveedor"), {"producto": otro.pk, "proveedor": a.pk, "codigo_proveedor": "X1"})
    assert r.status_code == 200 and "ya usa el código" in r.content.decode()
    admin_web.post(agregar("productoproveedor"), {"producto": otro.pk, "proveedor": b.pk, "codigo_proveedor": "X1"})
    assert ProductoProveedor.objects.count() == 2                       # mismo codigo en otro proveedor: valido
    url = cambiar(admin_web, "productoproveedor", pp.pk)
    html = admin_web.get(url).content.decode()
    assert 'name="proveedor"' not in html and 'name="codigo_proveedor"' not in html
    admin_web.post(url, {"producto": otro.pk, "proveedor": b.pk, "codigo_proveedor": "ZZ"})
    pp.refresh_from_db()
    assert (pp.proveedor, pp.codigo_proveedor) == (a, "X1")


def test_admin_categoria_y_proveedor_detectan_duplicados_por_mayusculas_y_espacios(admin_web):
    Categoria.objects.create(nombre="Aceites")
    r = admin_web.post(agregar("categoria"), {"nombre": "  ACEITES "})
    assert r.status_code == 200 and "Ya existe un categoría" in r.content.decode()
    Proveedor.objects.create(nombre="Filtros   Sur")
    r = admin_web.post(agregar("proveedor"), {"nombre": " filtros sur", "rut": "", "contacto": ""})
    assert r.status_code == 200 and "Ya existe un proveedor" in r.content.decode()
    assert Categoria.objects.count() == 1 and Proveedor.objects.count() == 1


def test_admin_proveedor_normaliza_y_valida_el_rut(admin_web):
    r = admin_web.post(agregar("proveedor"), {"nombre": "P1", "rut": "12.345.678-5", "contacto": ""})
    assert r.status_code == 302 and Proveedor.objects.get().rut == "12345678-5"
    r = admin_web.post(agregar("proveedor"), {"nombre": "P2", "rut": "12.345.678-4", "contacto": ""})
    assert r.status_code == 200 and "dígito verificador" in r.content.decode()
    r = admin_web.post(agregar("proveedor"), {"nombre": "P3", "rut": "10.000.013-k", "contacto": ""})
    assert Proveedor.objects.get(nombre="P3").rut == "10000013-K"
    r = admin_web.post(agregar("proveedor"), {"nombre": "P4", "rut": "12345678-5", "contacto": ""})
    assert r.status_code == 200 and "Ya existe un proveedor con ese RUT" in r.content.decode()


# ------------------------------------------------------------------ 2. kardex: fechas fuera de rango (H2)
@pytest.fixture
def adm(client):
    u = get_user_model().objects.create_user("adm", password=CLAVE)
    u.groups.add(Group.objects.get(name="Administrador"))
    client.force_login(u)
    return client


@pytest.mark.parametrize("campo,valor", [
    ("desde", "9999-12-31"), ("hasta", "9999-12-31"), ("desde", "0001-01-01"), ("hasta", "0001-01-01"),
    ("desde", "1999-12-31"), ("hasta", "2101-01-01"), ("desde", "99999-01-01"), ("hasta", "2100-13-40"),
])
def test_kardex_fecha_fuera_de_rango_avisa_y_no_filtra_sin_error(adm, campo, valor):
    p = Producto.objects.create(sku="K1", nombre="k")
    u = get_user_model().objects.get(username="adm")
    servicios.registrar_entrada(p, 5, u)
    r = adm.get(reverse("kardex", args=[p.pk]), {campo: valor})
    assert r.status_code == 200
    assert "Fecha no válida" in r.content.decode() and "no se aplicó el filtro" in r.content.decode()
    assert len(r.context["filas"]) == 1 and r.context["saldo_inicial"] is None   # sin filtrar


def test_kardex_fechas_en_los_bordes_del_rango_se_aceptan(adm):
    p = Producto.objects.create(sku="K1", nombre="k")
    r = adm.get(reverse("kardex", args=[p.pk]), {"desde": "2000-01-01", "hasta": "2100-12-31"})
    assert r.status_code == 200 and "Fecha no válida" not in r.content.decode()


# ------------------------------------------------------------------ 3. kardex abre en la ultima pagina
def _llenar(p, u, n):
    base = timezone.now() - dt.timedelta(days=400)
    Movimiento.objects.bulk_create([
        Movimiento(producto=p, usuario=u, tipo="entrada", cantidad=D("1"), fecha=base + dt.timedelta(minutes=i))
        for i in range(n)
    ])


def test_kardex_abre_por_defecto_en_la_ultima_pagina_con_saldos_continuos(adm):
    p = Producto.objects.create(sku="K1", nombre="k")
    u = get_user_model().objects.get(username="adm")
    _llenar(p, u, 250)
    url = reverse("kardex", args=[p.pk])
    r = adm.get(url)
    assert r.context["pagina"].number == 3 and len(r.context["filas"]) == 50
    assert r.context["filas"][0]["saldo"] == D("201") and r.context["filas"][-1]["saldo"] == D("250")
    assert r.context["filas"][-1]["saldo"] == servicios.stock_de(p)          # lo mas reciente, arriba de todo
    t = r.content.decode()
    assert "Primera" in t and "Anterior" in t and "Siguiente" not in t
    assert "?page=1" in t


@pytest.mark.parametrize("pagina,primer_saldo,filas", [(1, D("1"), 100), (2, D("101"), 100), (3, D("201"), 50)])
def test_kardex_los_enlaces_de_pagina_siguen_funcionando(adm, pagina, primer_saldo, filas):
    p = Producto.objects.create(sku="K1", nombre="k")
    _llenar(p, get_user_model().objects.get(username="adm"), 250)
    r = adm.get(reverse("kardex", args=[p.pk]), {"page": pagina})
    assert r.context["pagina"].number == pagina and len(r.context["filas"]) == filas
    assert r.context["filas"][0]["saldo"] == primer_saldo


def test_kardex_con_filtro_tambien_abre_en_la_ultima_pagina(adm):
    p = Producto.objects.create(sku="K1", nombre="k")
    _llenar(p, get_user_model().objects.get(username="adm"), 130)
    desde = (timezone.now() - dt.timedelta(days=401)).date().isoformat()
    r = adm.get(reverse("kardex", args=[p.pk]), {"desde": desde})
    assert r.context["pagina"].number == 2 and r.context["filas"][-1]["saldo"] == D("130")


def test_kardex_sin_movimientos_no_falla(adm):
    p = Producto.objects.create(sku="K1", nombre="k")
    r = adm.get(reverse("kardex", args=[p.pk]))
    assert r.status_code == 200 and r.context["filas"] == []


# ------------------------------------------------------------------ 4. login: ancho del campo de contraseña
def test_el_css_cubre_el_campo_de_contrasena_igual_que_el_de_usuario():
    css = (Path(__file__).resolve().parent.parent / "cuentas" / "static" / "css" / "sistema.css").read_text(encoding="utf-8")
    regla = next(r for r in css.split("}") if "input[type=text]" in r)
    assert "input[type=password]" in regla and "width: 100%" in regla


# ------------------------------------------------------------------ 6. README
def test_readme_tiene_la_seccion_operacion_segura():
    t = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    assert "## Operación segura" in t
    for frase in ("No revertir migraciones con datos reales", "respaldo", "valores negativos",
                  "solo por migración", "Nunca marcar `is_staff` a un operador",
                  "solo de forma interactiva en una terminal", "recarga la página"):
        assert frase in t, frase
