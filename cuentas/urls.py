from django.contrib.auth import views as auth_views
from django.urls import path

from .forms import LoginForm

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(authentication_form=LoginForm, redirect_authenticated_user=True),
        name="login",
    ),
    # Solo POST: un GET responde 405 y no cierra la sesión.
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
