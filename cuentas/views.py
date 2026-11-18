from django.shortcuts import render

PERFILES = ("Administrador", "Operador")


def perfil_de(usuario):
    """Nombre del perfil para mostrar: grupo Administrador/Operador del usuario."""
    if usuario.is_superuser:
        return "Superusuario"
    grupos = set(usuario.groups.values_list("name", flat=True))
    for nombre in PERFILES:
        if nombre in grupos:
            return nombre
    return "Sin perfil"


def inicio(request):
    # Protegida por LoginRequiredMiddleware (todo lo nuevo lo está por defecto).
    return render(request, "inicio.html", {"perfil": perfil_de(request.user)})
