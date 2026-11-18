from django.contrib.auth.forms import AuthenticationForm

MENSAJE_LOGIN = "Usuario o contraseña incorrectos."


class LoginForm(AuthenticationForm):
    """Mensaje genérico: no revela si el usuario existe, está inactivo o falló la clave."""

    error_messages = {
        "invalid_login": MENSAJE_LOGIN,
        "inactive": MENSAJE_LOGIN,
    }
