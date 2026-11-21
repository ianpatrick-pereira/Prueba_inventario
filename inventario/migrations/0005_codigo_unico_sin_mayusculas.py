"""K1 (auditoria T-F1-22): un codigo alternativo es unico SIN distinguir mayusculas.

Antes de crear la restriccion se buscan duplicados que ya existan («a0204» y «A0204»). Si los hay, la
migracion se detiene con un mensaje que los lista: NO borra ni cambia nada; hay que resolverlos a mano."""
from django.db import migrations, models
from django.db.models.functions import Lower


def revisar_duplicados(apps, schema_editor):
    CodigoAlternativo = apps.get_model("inventario", "CodigoAlternativo")
    grupos = {}
    for pk, valor, sku in CodigoAlternativo.objects.order_by("pk").values_list("pk", "valor", "producto__sku"):
        grupos.setdefault(valor.casefold(), []).append(f"«{valor}» (producto {sku}, id {pk})")
    repetidos = [" / ".join(v) for v in grupos.values() if len(v) > 1]
    if repetidos:
        raise RuntimeError(
            "No se puede crear la restricción de códigos alternativos únicos sin distinguir mayúsculas: "
            "hay códigos repetidos. Corrígelos (quita o cambia uno de cada par) y vuelve a migrar. "
            "Repetidos: " + "; ".join(repetidos)
        )


class Migration(migrations.Migration):
    dependencies = [("inventario", "0004_carga_eleventa")]
    operations = [
        migrations.RunPython(revisar_duplicados, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="codigoalternativo",
            constraint=models.UniqueConstraint(
                Lower("valor"), name="codigoalternativo_valor_unico_sin_mayusculas",
                violation_error_message="Ese código alternativo ya existe (sin distinguir mayúsculas).",
            ),
        ),
    ]
