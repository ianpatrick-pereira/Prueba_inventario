"""Tokens de un solo uso para los formularios que registran movimientos (D-45).

Cada formulario lleva un token guardado en la sesion. Al enviarlo se consume: si el mismo
formulario se envia otra vez (doble clic, recargar la pagina), el segundo envio no encuentra
el token y no registra nada. El consumo relee la sesion DENTRO de una transaccion (en SQLite,
BEGIN IMMEDIATE serializa las escrituras), asi dos envios casi simultaneos no consumen el
mismo token.
"""
import secrets

from django.db import transaction

CLAVE = "tokens_formulario"
MAXIMO = 30  # tokens pendientes por sesion; los mas viejos se descartan


def emitir(request, accion):
    token = secrets.token_urlsafe(16)
    tokens = dict(request.session.get(CLAVE, {}))
    tokens[token] = accion
    while len(tokens) > MAXIMO:
        tokens.pop(next(iter(tokens)))
    request.session[CLAVE] = tokens
    return token


def consumir(request, token, accion):
    """True si el token existe, es de esta sesion y de esta accion; lo invalida."""
    clave_sesion = request.session.session_key
    if not token or not clave_sesion:
        return False
    with transaction.atomic():
        fresca = request.session.__class__(clave_sesion)
        tokens = dict(fresca.get(CLAVE, {}))
        valido = tokens.get(token) == accion
        if valido:
            del tokens[token]
            fresca[CLAVE] = tokens
            fresca.save()
    # Evita que el middleware de sesiones vuelva a guardar el token ya consumido.
    request.session[CLAVE] = tokens
    return valido
