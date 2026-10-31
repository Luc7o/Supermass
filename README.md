# SuperMass Market — sitio web + panel de administración

Flask (API + archivos estáticos) con SQLite. El cliente administra **ofertas** y **productos** desde `/admin`.

## Probar en tu computadora
```bash
pip install -r requirements.txt
python app.py
```
- Sitio: http://127.0.0.1:5000
- Panel: http://127.0.0.1:5000/admin (contraseña de prueba: `admin123`)

## Publicar (Render, Railway o similar)
1. Sube esta carpeta a un repositorio de GitHub.
2. Crea un servicio web con **Build**: `pip install -r requirements.txt` y **Start**: `gunicorn app:app`.
3. Variables de entorno (ver `.env.example`): `FLASK_ENV=production`, `SECRET_KEY`, `ADMIN_PASSWORD`, `DATABASE`.
4. **Agrega un disco persistente** y apunta `DATABASE` a él (ej. `/var/data/supermass.db`). Sin disco, los datos se pierden en cada despliegue.
5. Conecta el dominio propio. El panel queda en `tudominio.com/admin`.

> Vercel no sirve para esta versión: sus funciones no guardan archivos, y SQLite lo necesita. Si prefieres Vercel, cambia SQLite por Postgres (Neon o Supabase).

## Estructura
- `app.py`: API (`/api/ofertas`, `/api/productos`), login del panel y base de datos.
- `static/index.html`: sitio público (carga ofertas y productos desde la API).
- `static/admin.html`: panel de administración.

## Pendiente
- Editar sedes y redes desde el panel (hoy están en `static/index.html`).
- Subir fotos de productos.
- Copias de seguridad de la base de datos.
