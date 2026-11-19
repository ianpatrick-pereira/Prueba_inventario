"""Asigna al grupo «Administrador» los permisos nuevos de `inventario` (importar_eleventa y los
del modelo ImportacionEleventa) volviendo a aplicar la lógica idempotente de 0001.

El grupo «Operador» no cambia: la importación es solo del Administrador (D-48).
"""
from importlib import import_module

from django.db import migrations


def asignar(apps, schema_editor):
    import_module("cuentas.migrations.0001_grupos_y_permisos").crear_grupos(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("cuentas", "0001_grupos_y_permisos"),
        ("inventario", "0004_carga_eleventa"),
    ]
    operations = [migrations.RunPython(asignar, migrations.RunPython.noop)]
