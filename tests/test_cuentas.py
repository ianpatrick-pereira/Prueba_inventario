"""Login, perfiles y permisos (T-F1-05; decisiones D-38 y D-39, RF-18)."""
import importlib
import re
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

from inventario import servicios
from inventario.models import Movimiento, Producto

pytestmark = pytest.mark.django_db
D = Decimal
CLAVE = "Zq7-mPx2vL-prueba"

MODELOS = ["categoria", "proveedor", "producto", "codigoalternativo", "productoproveedor",
           "documento", "movimiento"]
PERSONALIZADOS = {"registrar_entrada", "registrar_salida", "registrar_ajuste", "revertir_movimiento",
                  "permitir_stock_negativo", "importar_eleventa", "archivar_producto"}
# Escrita a mano, independiente de la migracion (D-38).
OPERADOR = (
    {f"view_{m}" for m in MODELOS}
    | {f"{a}_{m}" for m in ["categoria", "proveedor", "producto", "codigoalternativo", "productoproveedor"]
       for a in ("add", "change")}
    | {"add_documento", "registrar_entrada", "registrar_salida"}
)
# 8 modelos x 4 + 7 personalizados = 39 (archivar_producto, D-64).
ADMINISTRADOR = ({f"{a}_{m}" for m in MODELOS + ["importacioneleventa"] for a in ("add", "change", "delete", "view")}
                 | PERSONALIZADOS)


def codenames(grupo):
    return set(Group.objects.get(name=grupo).permissions.values_list("codename", flat=True))


def usuario(nombre, grupo=None, **kw):
    u = get_user_model().objects.create_user(nombre, password=CLAVE, **kw)
    if grupo:
        u.groups.add(Group.objects.get(name=grupo))
    return u


@pytest.fixture
def producto():
    return Producto.objects.create(sku="C-1", nombre="x")


# 1. grupos y permisos
def test_los_grupos_existen_con_exactamente_los_permisos_de_d38():
    assert codenames("Operador") == OPERADOR
    assert codenames("Administrador") == ADMINISTRADOR
    for g in Group.objects.filter(name__in=["Operador", "Administrador"]):
        assert set(g.permissions.values_list("content_type__app_label", flat=True)) == {"inventario"}


def test_tamanos_de_la_matriz_d38_y_d40():
    assert len(OPERADOR) == 20 and len(codenames("Operador")) == 20
    assert len(ADMINISTRADOR) == 39 and len(codenames("Administrador")) == 39  # 33 + 5 (importacion) + archivar_producto
    assert "importar_eleventa" in codenames("Administrador") and "importar_eleventa" not in codenames("Operador")
    assert "permitir_stock_negativo" in codenames("Administrador")
    assert "permitir_stock_negativo" not in codenames("Operador")
    assert "archivar_producto" in codenames("Administrador") and "archivar_producto" not in codenames("Operador")


def test_el_operador_no_puede_ajustar_revertir_ni_borrar():
    c = codenames("Operador")
    assert not (c & {"registrar_ajuste", "revertir_movimiento", "add_movimiento", "change_movimiento",
                     "permitir_stock_negativo"})
    assert not {x for x in c if x.startswith("delete_")}
    assert not {"change_documento"} & c


def test_la_migracion_de_grupos_es_idempotente():
    migracion = importlib.import_module("cuentas.migrations.0001_grupos_y_permisos")
    antes = (codenames("Operador"), codenames("Administrador"), Group.objects.count(),
             Permission.objects.count())
    for _ in range(2):
        migracion.crear_grupos(django_apps, None)
    despues = (codenames("Operador"), codenames("Administrador"), Group.objects.count(),
               Permission.objects.count())
    assert antes == despues
    assert Group.objects.filter(name="Operador").count() == 1


def test_la_migracion_no_crea_usuarios():
    assert get_user_model().objects.count() == 0


# 2. login
def test_login_correcto_redirige_al_inicio(client):
    usuario("ana", "Operador")
    r = client.post("/cuentas/login/", {"username": "ana", "password": CLAVE})
    assert r.status_code == 302 and r.url == "/"
    assert client.get("/").status_code == 200


def test_login_incorrecto_da_el_mismo_error_generico(client):
    usuario("ana", "Operador")
    mensajes = []
    for datos in ({"username": "ana", "password": "mala-clave-1"},
                  {"username": "no-existe", "password": "mala-clave-1"}):
        r = client.post("/cuentas/login/", datos)
        assert r.status_code == 200
        mensajes.append(r.context["form"].non_field_errors())
        assert "Usuario o contraseña incorrectos." in r.content.decode()
        assert "_auth_user_id" not in client.session
    assert mensajes[0] == mensajes[1]


def test_usuario_inactivo_no_entra_y_mismo_mensaje(client):
    usuario("baja", "Operador", is_active=False)
    r = client.post("/cuentas/login/", {"username": "baja", "password": CLAVE})
    assert r.status_code == 200
    assert "Usuario o contraseña incorrectos." in r.content.decode()
    assert "_auth_user_id" not in client.session


# 3. rutas protegidas y next
def test_inicio_sin_sesion_redirige_al_login_con_next(client):
    r = client.get("/")
    assert r.status_code == 302 and r.url == "/cuentas/login/?next=/"


def test_salud_sigue_publica(client):
    assert client.get("/salud/").status_code == 200


def test_solo_salud_y_login_son_publicas():
    from django.urls import resolve, reverse

    assert "django.contrib.auth.middleware.LoginRequiredMiddleware" in settings.MIDDLEWARE
    for nombre, publica in {"salud": True, "login": True, "inicio": False, "logout": False}.items():
        vista = resolve(reverse(nombre)).func
        # login_required=False lo pone @login_not_required; sin marca = protegida por defecto.
        assert (getattr(vista, "login_required", True) is False) == publica, nombre


@pytest.mark.parametrize("destino", ["https://evil.example/robo", "//evil.example/robo"])
def test_next_externo_no_se_sigue(client, destino):
    usuario("ana", "Operador")
    r = client.post("/cuentas/login/", {"username": "ana", "password": CLAVE, "next": destino})
    assert r.status_code == 302 and r.url == "/"


def test_next_interno_se_sigue(client):
    usuario("ana", "Operador")
    r = client.post("/cuentas/login/", {"username": "ana", "password": CLAVE, "next": "/salud/"})
    assert r.url == "/salud/"


# 4. logout
def test_logout_por_get_no_cierra_la_sesion(client):
    usuario("ana", "Operador")
    client.login(username="ana", password=CLAVE)
    assert client.get("/cuentas/logout/").status_code == 405
    assert client.get("/").status_code == 200


def test_logout_por_post_cierra_la_sesion(client):
    usuario("ana", "Operador")
    client.login(username="ana", password=CLAVE)
    r = client.post("/cuentas/logout/")
    assert r.status_code == 302
    assert client.get("/").status_code == 302


# 5. CSRF
def test_login_sin_token_csrf_es_rechazado():
    usuario("ana", "Operador")
    c = Client(enforce_csrf_checks=True)
    r = c.post("/cuentas/login/", {"username": "ana", "password": CLAVE})
    assert r.status_code == 403


def test_login_con_token_csrf_funciona():
    usuario("ana", "Operador")
    c = Client(enforce_csrf_checks=True)
    c.get("/cuentas/login/")
    token = c.cookies["csrftoken"].value
    r = c.post("/cuentas/login/", {"username": "ana", "password": CLAVE, "csrfmiddlewaretoken": token})
    assert r.status_code == 302


def test_plantilla_de_login_incluye_csrf_y_no_carga_js_externo(client):
    html = client.get("/cuentas/login/").content.decode()
    assert "csrfmiddlewaretoken" in html
    assert all('src="/static/js/sistema.js?v=' in x for x in re.findall(r"<script[^>]*>", html)) and "http://" not in html and "https://" not in html


def test_inicio_muestra_usuario_y_perfil(client):
    usuario("ana", "Operador")
    usuario("jefa", "Administrador")
    client.login(username="ana", password=CLAVE)
    html = client.get("/").content.decode()
    assert "ana" in html and "Operador" in html
    client.login(username="jefa", password=CLAVE)
    assert "Administrador" in client.get("/").content.decode()
    usuario("sin", None)
    client.login(username="sin", password=CLAVE)
    assert "Sin perfil" in client.get("/").content.decode()


# 6. operador
def test_operador_registra_entradas_y_salidas(producto):
    op = usuario("op", "Operador")
    servicios.registrar_entrada(producto, 10, op)
    servicios.registrar_salida(producto, 3, op)
    assert servicios.stock_de(producto) == D("7.00")


def test_operador_no_puede_ajustar_contar_ni_revertir(producto):
    op = usuario("op", "Operador")
    adm = usuario("adm", "Administrador")
    e = servicios.registrar_entrada(producto, 10, adm)
    n = Movimiento.objects.count()
    with pytest.raises(PermissionDenied):
        servicios.registrar_ajuste(producto, 1, op, "motivo")
    with pytest.raises(PermissionDenied):
        servicios.ajustar_a_conteo(producto, 3, op, "motivo")
    with pytest.raises(PermissionDenied):
        servicios.revertir(e, op, "motivo")
    assert Movimiento.objects.count() == n
    assert servicios.stock_de(producto) == D("10.00")


# 7. administrador, sin grupo, inactivo, anonimo
def test_administrador_puede_todas_las_operaciones(producto):
    adm = usuario("adm", "Administrador")
    e = servicios.registrar_entrada(producto, 10, adm)
    servicios.registrar_salida(producto, 2, adm)
    servicios.registrar_ajuste(producto, 1, adm, "sobrante")
    servicios.ajustar_a_conteo(producto, 5, adm, "conteo")
    servicios.revertir(e, adm, "error", permitir_negativo=True)
    assert Movimiento.objects.count() == 5


def operaciones(producto, e):
    return {
        "entrada": lambda u: servicios.registrar_entrada(producto, 1, u),
        "salida": lambda u: servicios.registrar_salida(producto, 1, u),
        "ajuste": lambda u: servicios.registrar_ajuste(producto, 1, u, "m"),
        "conteo": lambda u: servicios.ajustar_a_conteo(producto, 99, u, "m"),
        "revertir": lambda u: servicios.revertir(e, u, "m"),
    }


@pytest.mark.parametrize("tipo", ["sin_grupo", "inactivo_con_permisos", "anonimo", "none"])
def test_usuarios_sin_derecho_son_rechazados_en_todo(producto, tipo):
    adm = usuario("adm", "Administrador")
    servicios.registrar_entrada(producto, 50, adm)
    e = servicios.registrar_entrada(producto, 1, adm)
    u = {
        "sin_grupo": lambda: usuario("sg"),
        "inactivo_con_permisos": lambda: usuario("ina", "Administrador", is_active=False),
        "anonimo": AnonymousUser,
        "none": lambda: None,
    }[tipo]()
    n = Movimiento.objects.count()
    for nombre, op in operaciones(producto, e).items():
        with pytest.raises(PermissionDenied):
            op(u)
    assert Movimiento.objects.count() == n


def test_usuario_desactivado_despues_de_obtener_permisos_es_rechazado(producto):
    adm = usuario("adm", "Administrador")
    servicios.registrar_entrada(producto, 5, adm)
    adm.is_active = False
    adm.save()
    with pytest.raises(PermissionDenied):
        servicios.registrar_entrada(producto, 5, adm)


def test_el_permiso_se_comprueba_antes_de_validar(producto):
    op = usuario("op", "Operador")
    with pytest.raises(PermissionDenied):  # no ValidationError por el motivo vacio
        servicios.registrar_ajuste(producto, 0, op, "")


# 8. sesion y contrasenas
def test_ajustes_de_sesion():
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_AGE == 8 * 60 * 60
    assert settings.SESSION_EXPIRE_AT_BROWSER_CLOSE is True
    assert settings.SESSION_COOKIE_SECURE is (not settings.DEBUG)
    assert settings.CSRF_COOKIE_SECURE is (not settings.DEBUG)
    assert settings.LOGIN_URL == "login" and settings.LOGOUT_REDIRECT_URL == "login"


def test_validadores_de_contrasena_y_largo_minimo_10():
    nombres = [v["NAME"].rsplit(".", 1)[1] for v in settings.AUTH_PASSWORD_VALIDATORS]
    assert nombres == ["UserAttributeSimilarityValidator", "MinimumLengthValidator",
                       "CommonPasswordValidator", "NumericPasswordValidator"]
    with pytest.raises(ValidationError):
        validate_password("Abcdefg9x")  # 9 caracteres
    with pytest.raises(ValidationError):
        validate_password("1234567890")  # numerica
    validate_password("Zq7-mPx2vL")


# comando para crear el primer administrador
def test_crear_administrador(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda *_: CLAVE)
    monkeypatch.setattr("cuentas.management.commands.crear_administrador.getpass", lambda *_: CLAVE)
    call_command("crear_administrador", "--usuario", "jefe")
    u = get_user_model().objects.get(username="jefe")
    assert u.is_staff and not u.is_superuser and u.check_password(CLAVE)
    assert list(u.groups.values_list("name", flat=True)) == ["Administrador"]
    with pytest.raises(CommandError):
        call_command("crear_administrador", "--usuario", "jefe")


def test_crear_administrador_rechaza_clave_corta(monkeypatch):
    monkeypatch.setattr("cuentas.management.commands.crear_administrador.getpass", lambda *_: "Abc9xyz")
    with pytest.raises(CommandError):
        call_command("crear_administrador", "--usuario", "jefe")
    assert get_user_model().objects.count() == 0


# H1 (D-40): permitir_negativo exige permiso propio
def _intentos_con_negativo(producto, u, e):
    return {
        "salida": lambda: servicios.registrar_salida(producto, 1, u, permitir_negativo=True),
        "ajuste": lambda: servicios.registrar_ajuste(producto, -1, u, "m", permitir_negativo=True),
        "revertir": lambda: servicios.revertir(e, u, "m", permitir_negativo=True),
    }


def test_operador_con_permitir_negativo_rechazado_aunque_haya_stock(producto):
    adm = usuario("adm", "Administrador")
    servicios.registrar_entrada(producto, 100, adm)
    e = servicios.registrar_entrada(producto, 1, adm)
    op = usuario("op", "Operador")
    op.user_permissions.add(Permission.objects.get(codename="registrar_ajuste"),
                            Permission.objects.get(codename="revertir_movimiento"))
    n = Movimiento.objects.count()
    for nombre, intento in _intentos_con_negativo(producto, op, e).items():
        with pytest.raises(PermissionDenied, match="permitir_stock_negativo"):
            intento()
    assert Movimiento.objects.count() == n


def test_administrador_puede_usar_permitir_negativo(producto):
    adm = usuario("adm", "Administrador")
    e = servicios.registrar_entrada(producto, 2, adm)
    servicios.registrar_salida(producto, 5, adm, permitir_negativo=True)
    servicios.registrar_ajuste(producto, -3, adm, "m", permitir_negativo=True)
    servicios.revertir(e, adm, "m", permitir_negativo=True)
    assert servicios.stock_de(producto) == D("-8.00")


def test_usuario_tecnico_con_el_permiso_puede_usar_permitir_negativo(producto):
    tecnico = usuario("importador")
    tecnico.user_permissions.add(Permission.objects.get(codename="registrar_salida"),
                                 Permission.objects.get(codename="permitir_stock_negativo"))
    servicios.registrar_salida(producto, 4, tecnico, permitir_negativo=True)
    assert servicios.stock_de(producto) == D("-4.00")
    solo_salida = usuario("solo")
    solo_salida.user_permissions.add(Permission.objects.get(codename="registrar_salida"))
    with pytest.raises(PermissionDenied):
        servicios.registrar_salida(producto, 1, solo_salida, permitir_negativo=True)


def test_el_permiso_negativo_no_reemplaza_el_de_la_operacion(producto):
    solo_neg = usuario("neg")
    solo_neg.user_permissions.add(Permission.objects.get(codename="permitir_stock_negativo"))
    with pytest.raises(PermissionDenied):
        servicios.registrar_salida(producto, 1, solo_neg, permitir_negativo=True)


# H2: la reversa de la migracion no borra nada
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_migrar_cuentas_a_cero_y_volver_conserva_grupos_y_asignaciones():
    u = usuario("ana", "Operador")
    antes = (codenames("Operador"), codenames("Administrador"), set(u.groups.values_list("name", flat=True)))
    call_command("migrate", "cuentas", "zero", verbosity=0)
    assert Group.objects.filter(name__in=["Operador", "Administrador"]).count() == 2  # no se borro nada
    assert set(u.groups.values_list("name", flat=True)) == {"Operador"}
    call_command("migrate", "cuentas", verbosity=0)
    despues = (codenames("Operador"), codenames("Administrador"), set(u.groups.values_list("name", flat=True)))
    assert antes == despues
    assert Group.objects.filter(name="Operador").count() == 1


def test_la_reversa_de_la_migracion_es_noop():
    migracion = importlib.import_module("cuentas.migrations.0001_grupos_y_permisos")
    assert migracion.Migration.operations[0].reverse_code.__name__ == "noop"
