"""Crea los grupos «Administrador» y «Operador» con sus permisos (D-38).

Idempotente: get_or_create del grupo y permissions.set(...) con el conjunto exacto,
así que correrla de nuevo deja el mismo resultado. No crea usuarios ni contraseñas.
Si se agregan modelos o permisos nuevos a `inventario`, hace falta una migración de
datos nueva (o actualizar esta lógica) para que los grupos los incluyan.
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

APP = "inventario"
MODELOS = ["categoria", "proveedor", "producto", "codigoalternativo", "productoproveedor",
           "documento", "movimiento"]
EDITABLES = ["categoria", "proveedor", "producto", "codigoalternativo", "productoproveedor"]


def codenames_operador():
    c = {f"view_{m}" for m in MODELOS}
    for m in EDITABLES:
        c |= {f"add_{m}", f"change_{m}"}
    c.add("add_documento")
    c |= {"registrar_entrada", "registrar_salida"}
    return c


def crear_grupos(apps, schema_editor):
    # Las migraciones corren antes que la señal post_migrate: se crean los permisos aquí.
    for app_config in apps.get_app_configs():
        if app_config.label in (APP, "auth"):
            app_config.models_module = getattr(app_config, "models_module", None) or True
            create_permissions(app_config, apps=apps, verbosity=0)
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    todos = Permission.objects.filter(content_type__app_label=APP)
    admin, _ = Group.objects.get_or_create(name="Administrador")
    admin.permissions.set(todos)
    operador, _ = Group.objects.get_or_create(name="Operador")
    operador.permissions.set(todos.filter(codename__in=codenames_operador()))


def borrar_grupos(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=["Administrador", "Operador"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("inventario", "0003_permisos_movimiento"),
    ]
    operations = [migrations.RunPython(crear_grupos, borrar_grupos)]
