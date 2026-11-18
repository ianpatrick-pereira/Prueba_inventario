"""Prueba transaccional (vacia la base al terminar) con serialized_rollback=True.

pytest-django ejecuta las pruebas transaccionales DESPUES de las normales, asi que no se puede
forzar que corran antes. serialized_rollback=True restaura los grupos y permisos de las
migraciones despues de cada prueba transaccional; el hook de tests/conftest.py exige esa opcion
(ver tests/test_hook_transaccional.py). Esta prueba solo comprueba que dentro de una prueba
transaccional los grupos existen (se restauran) y que lo creado en ella se descarta.
"""
import pytest
from django.contrib.auth.models import Group


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_transaccional_ve_los_grupos_restaurados():
    assert set(Group.objects.values_list("name", flat=True)) == {"Administrador", "Operador"}
    Group.objects.create(name="Temporal")  # se descarta al terminar la prueba
