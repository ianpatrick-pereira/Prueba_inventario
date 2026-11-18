"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.urls import include, path

from cuentas.views import inicio


@login_not_required
def salud(request):
    """Ruta mínima para comprobar que el servidor responde."""
    return HttpResponse("OK", content_type="text/plain; charset=utf-8")


urlpatterns = [
    path("", inicio, name="inicio"),
    path("salud/", salud, name="salud"),
    path("cuentas/", include("cuentas.urls")),
    path("admin/", admin.site.urls),
]
