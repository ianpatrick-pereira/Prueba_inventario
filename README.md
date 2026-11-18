# Sistema de inventario

Sistema web de inventario y compras: lee facturas de proveedores con IA, aprende los códigos de cada proveedor, calcula el stock desde un kardex de movimientos y se sincroniza con el punto de venta existente (Eleventa).

Pensado para cualquier negocio que maneje volumen de inventario; el primer caso de uso es un lubricentro.

## Estado

Fase 1 en curso: existe el proyecto Django base (configuración segura y ruta de salud). Todavía no hay funciones de inventario.

## Documentación

- `docs/PROYECTO.md`: resumen del proyecto, decisiones clave y enlaces a la documentación completa.
- `docs/bosquejo.html`: prototipo navegable (datos de ejemplo, sin servidor).
- `CLAUDE.md`: política de trabajo para la IA que programa el sistema.

## Tecnología prevista

Python + Django, SQLite en desarrollo y PostgreSQL en producción.

## Cómo ejecutarlo

Requiere Python 3.13. Pasos en Windows 11 (PowerShell), desde la carpeta del proyecto:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver
```

Luego abre http://127.0.0.1:8000/salud/ en el navegador: debe mostrar `OK`.

Si PowerShell bloquea la activación del entorno virtual, ejecuta una vez
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` y repite la activación.

Antes de usarlo, edita `.env` y cambia `SECRET_KEY` por una clave propia (las instrucciones están dentro de `.env.example`). El archivo `.env` nunca se sube a Git.

Equivalentes en Linux/macOS:

- Crear el entorno: `python3 -m venv .venv`
- Activarlo: `source .venv/bin/activate`
- Instalar: `pip install -r requirements-dev.txt`
- Copiar la configuración: `cp .env.example .env`
- Migrar y arrancar: `python manage.py migrate && python manage.py runserver`

### Pruebas

Con el entorno activado: `pytest`. Las pruebas no usan tu `.env`.
