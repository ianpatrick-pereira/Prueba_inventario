"""Configuración solo para pruebas: aislada de cualquier .env local."""
import os

os.environ["INVENTARIO_ENV_FILE"] = os.devnull
# Las pruebas no dependen de la terminal: aunque DEBUG=1 este exportado, aqui DEBUG es siempre False
# (de DEBUG dependen valores calculados al importar settings, como SESSION_COOKIE_SECURE).
os.environ["DEBUG"] = "False"
os.environ.setdefault("SECRET_KEY", "clave-solo-para-pruebas-no-secreta")

from .settings import *  # noqa: E402,F401,F403
