"""Crea el codigo alternativo (tipo «fabricante») de los productos YA cargados cuya descripcion es en realidad
su codigo real (D-62). Misma regla del importador de Eleventa.

Uso (solo administrador, por terminal):
  python manage.py codigos_desde_descripcion              # simula (por defecto): no escribe nada
  python manage.py codigos_desde_descripcion --aplicar    # crea los codigos alternativos

Idempotente: si el producto ya tiene ese alternativo no hace nada, asi que una segunda corrida no duplica.
Nunca cambia un sku ni borra datos."""
from django.core.management.base import BaseCommand
from django.db import transaction

from inventario.models import CodigoAlternativo, Producto
from inventario.validadores import codigo_desde_descripcion


class Command(BaseCommand):
    help = "Crea el código alternativo desde la descripción de los productos cargados (D-62). Simula por defecto."

    def add_arguments(self, parser):
        grupo = parser.add_mutually_exclusive_group()
        grupo.add_argument("--simular", action="store_true", help="No escribe nada (es lo que se hace por defecto).")
        grupo.add_argument("--aplicar", action="store_true", help="Crea los códigos alternativos.")

    def handle(self, *args, aplicar=False, **opciones):
        productos = list(Producto.objects.order_by("pk").values_list("pk", "sku", "nombre"))
        skus = {sku.casefold(): sku for _, sku, _ in productos}                  # K1: sin distinguir mayusculas
        alternativos = {valor.casefold(): sku for valor, sku in CodigoAlternativo.objects.values_list("valor", "producto__sku")}
        por_crear, conflictos, ya_existen = [], [], 0
        for pk, sku, nombre in productos:
            valor = codigo_desde_descripcion(sku, nombre)
            if not valor:
                continue
            k = valor.casefold()
            if k in skus:
                conflictos.append((sku, valor, skus[k]))
            elif k in alternativos:
                if alternativos[k].casefold() == sku.casefold():
                    ya_existen += 1
                else:
                    conflictos.append((sku, valor, alternativos[k]))
            else:
                por_crear.append((pk, sku, valor))
                alternativos[k] = sku             # el siguiente producto con el mismo valor sera conflicto
        if aplicar and por_crear:
            with transaction.atomic():
                CodigoAlternativo.objects.bulk_create(
                    [CodigoAlternativo(producto_id=pk, tipo=CodigoAlternativo.FABRICANTE, valor=v) for pk, _, v in por_crear]
                )
        modo = "APLICADO" if aplicar else "SIMULACIÓN (no se escribió nada; usa --aplicar para crear)"
        self.stdout.write(f"{modo}")
        self.stdout.write(f"Códigos alternativos {'creados' if aplicar else 'que se crearían'}: {len(por_crear)}")
        for _, sku, valor in por_crear:
            self.stdout.write(f"  {sku} -> {valor}")
        self.stdout.write(f"Ya existían (sin cambios): {ya_existen}")
        self.stdout.write(f"Conflictos (no se crean): {len(conflictos)}")
        for sku, valor, dueno in conflictos:
            self.stdout.write(f"  {sku}: el código de la descripción {valor} ya pertenece a {dueno}")
