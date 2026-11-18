"""Base de pruebas en un archivo temporal (no en memoria).

Con la base en memoria de SQLite, dos hilos chocan con "database table is locked" en vez de
esperar su turno; con un archivo se comporta como en desarrollo (BEGIN IMMEDIATE, D-35),
y asi se puede probar la concurrencia de las escrituras de stock. Es una base temporal:
nunca toca db.sqlite3.
"""
import pytest


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings_parallel_suffix, tmp_path_factory):
    from django.conf import settings

    ruta = tmp_path_factory.mktemp("db_pruebas") / "pruebas.sqlite3"
    settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = str(ruta)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Una prueba transaccional vacia la base al terminar; sin serialized_rollback=True se
    llevaria los grupos y permisos de la migracion y rompe las pruebas siguientes."""
    marca = item.get_closest_marker("django_db")
    if marca and marca.kwargs.get("transaction") and not marca.kwargs.get("serialized_rollback"):
        pytest.fail(
            "Las pruebas con @pytest.mark.django_db(transaction=True) deben llevar también "
            "serialized_rollback=True (si no, se pierden los grupos y permisos de las migraciones).",
            pytrace=False,
        )
