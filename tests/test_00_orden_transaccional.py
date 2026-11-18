"""Prueba transaccional (vacia la base al terminar) con serialized_rollback=True.

Nota: pytest-django ejecuta las pruebas transaccionales DESPUES de las normales, asi que no se
puede forzar que corran antes. Aun asi, serialized_rollback=True restaura los grupos y permisos
de la migracion despues de cada prueba transaccional, de modo que el orden no importa.
Sin esa opcion, una prueba posterior que necesite los grupos se rompe (se comprobo quitandola).
"""
import pytest
from django.contrib.auth.models import Group


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_transaccional_temprana_ve_los_grupos_y_deja_la_base_vacia():
    assert set(Group.objects.values_list("name", flat=True)) == {"Administrador", "Operador"}
    Group.objects.create(name="Temporal")  # se descarta al terminar la prueba


@pytest.mark.django_db
def test_los_grupos_siguen_existiendo_despues_de_una_prueba_transaccional():
    assert set(Group.objects.values_list("name", flat=True)) == {"Administrador", "Operador"}
