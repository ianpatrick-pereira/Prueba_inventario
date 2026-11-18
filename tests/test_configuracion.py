import os
import subprocess
import sys
from pathlib import Path

from django.core.management import call_command

BASE_DIR = Path(__file__).resolve().parent.parent


def _ejecutar_settings(env_extra):
    """Importa config.settings en un proceso limpio, sin .env ni variables heredadas."""
    env = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "DEBUG", "ALLOWED_HOSTS")}
    env["INVENTARIO_ENV_FILE"] = os.devnull
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", "from django.conf import settings; print(settings.DEBUG)"],
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )


def test_salud_responde_200(client):
    respuesta = client.get("/salud/")
    assert respuesta.status_code == 200
    assert respuesta.content.decode() == "OK"


def test_debug_sin_definir_es_false():
    resultado = _ejecutar_settings({"SECRET_KEY": "x" * 50})
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == "False"


def test_sin_secret_key_y_sin_debug_falla_con_mensaje_claro():
    resultado = _ejecutar_settings({})
    assert resultado.returncode != 0
    assert "SECRET_KEY" in resultado.stderr


def test_manage_check_sin_errores():
    call_command("check")


def test_idioma_y_zona_horaria():
    from django.conf import settings

    assert settings.LANGUAGE_CODE == "es-cl"
    assert settings.TIME_ZONE == "America/Santiago"
    assert settings.USE_TZ is True
