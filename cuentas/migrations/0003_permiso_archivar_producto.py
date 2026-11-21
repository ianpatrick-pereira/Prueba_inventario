"""Asigna al grupo «Administrador» el permiso nuevo `archivar_producto` (D-64) volviendo a aplicar la lógica
idempotente de 0001. El grupo «Operador» no cambia: archivar y reactivar es solo del Administrador."""
from importlib import import_module

from django.db import migrations


def asignar(apps, schema_editor):
    import_module("cuentas.migrations.0001_grupos_y_permisos").crear_grupos(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("cuentas", "0002_permiso_importar_eleventa"),
        ("inventario", "0006_revision_y_archivo"),
    ]
    operations = [migrations.RunPython(asignar, migrations.RunPython.noop)]
