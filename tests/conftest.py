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
