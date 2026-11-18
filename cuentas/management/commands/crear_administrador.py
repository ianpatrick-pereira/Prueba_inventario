"""Crea el primer administrador sin guardar contraseñas en el código ni en el historial.

Uso: python manage.py crear_administrador --usuario NOMBRE [--superusuario]
La contraseña se pide por teclado (dos veces) y se valida con las reglas del proyecto.
Deja la cuenta con is_staff=True (acceso a /admin/) y en el grupo «Administrador».
Con --superusuario además puede gestionar usuarios y grupos en /admin/.
"""
from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Crea un usuario administrador (grupo «Administrador», acceso a /admin/)."

    def add_arguments(self, parser):
        parser.add_argument("--usuario", required=True)
        parser.add_argument("--superusuario", action="store_true",
                            help="También lo hace superusuario (gestionar usuarios y grupos).")

    def handle(self, *args, usuario, superusuario, **opciones):
        User = get_user_model()
        if User.objects.filter(**{User.USERNAME_FIELD: usuario}).exists():
            raise CommandError(f"El usuario «{usuario}» ya existe.")
        try:
            grupo = Group.objects.get(name="Administrador")
        except Group.DoesNotExist:
            raise CommandError("Falta el grupo «Administrador»: ejecuta primero `manage.py migrate`.")
        clave = getpass("Contraseña: ")
        if clave != getpass("Repite la contraseña: "):
            raise CommandError("Las contraseñas no coinciden.")
        try:
            validate_password(clave, User(**{User.USERNAME_FIELD: usuario}))
        except ValidationError as e:
            raise CommandError("; ".join(e.messages))
        u = User.objects.create_user(usuario, password=clave, is_staff=True, is_superuser=superusuario)
        u.groups.add(grupo)
        self.stdout.write(self.style.SUCCESS(f"Administrador «{usuario}» creado."))
