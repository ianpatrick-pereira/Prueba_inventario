"""Estaticos propios con version (T-F1-18): /static/css/sistema.css?v=<hash corto del contenido>.

El navegador guarda en cache los archivos por su URL; sin version, tras actualizar el sistema podia
mostrar el HTML nuevo con el CSS viejo. Aqui la URL cambia sola cuando cambia el contenido del archivo.

Por que no ManifestStaticFilesStorage ahora: necesita `collectstatic` (y un archivo manifest) para
funcionar, algo que solo tiene sentido en produccion (fase 4); en desarrollo con runserver los archivos
se sirven directo desde cada app. Esta etiqueta no necesita ningun paso extra ni dependencias.
"""
import hashlib
import os
from functools import lru_cache

from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django import template

register = template.Library()


@lru_cache(maxsize=256)
def _hash_de(ruta, mtime_ns, tamano):
    """Hash del contenido; se recalcula solo si cambian la fecha de modificacion o el tamano."""
    with open(ruta, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:10]


def version_de(ruta_absoluta):
    """Hash corto (10 hex) del contenido del archivo; '' si no existe."""
    try:
        st = os.stat(ruta_absoluta)
    except OSError:
        return ""
    return _hash_de(str(ruta_absoluta), st.st_mtime_ns, st.st_size)


def url_versionada(ruta):
    """URL estatica de `ruta` con ?v=<hash>. Si el archivo no se encuentra, la URL normal."""
    url = static(ruta)
    encontrado = finders.find(ruta)
    version = version_de(encontrado) if isinstance(encontrado, (str, os.PathLike)) else ""
    return f"{url}?v={version}" if version else url


@register.simple_tag
def static_v(ruta):
    return url_versionada(ruta)
