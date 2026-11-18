"""Configuración solo para pruebas: aislada de cualquier .env local."""
import os

os.environ["INVENTARIO_ENV_FILE"] = os.devnull
os.environ.setdefault("SECRET_KEY", "clave-solo-para-pruebas-no-secreta")

from .settings import *  # noqa: E402,F401,F403
