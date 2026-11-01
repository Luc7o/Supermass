# SuperMass Market — sitio web + panel de administración

Flask (API + archivos estáticos) con Postgres en Neon. El cliente administra **ofertas** y **productos** desde `/admin`.

## 1. Crear la base de datos en Neon
1. Crea un proyecto en https://neon.tech.
2. En el panel, botón **Connect**, copia la cadena de conexión (`postgresql://...`).
3. No necesitas crear tablas: la app las crea sola al arrancar y agrega datos de ejemplo.

## 2. Probar en tu computadora
```bash
pip install -r requirements.txt
```
Crea un archivo `.env` junto a `app.py` (ya está en `.gitignore`, no se sube a Git):
```
DATABASE_URL=postgresql://usuario:clave@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require
```
Luego:
```bash
python app.py
```
- Sitio: http://127.0.0.1:5000
- Panel: http://127.0.0.1:5000/admin (contraseña de prueba: `admin123`)

## 3. Publicar
**Opción A: Render o Railway (recomendada)**
- Build: `pip install -r requirements.txt` — Start: `gunicorn app:app`
- Variables: `FLASK_ENV=production`, `SECRET_KEY`, `ADMIN_PASSWORD`, `DATABASE_URL`.
- Ya no necesitas disco persistente: los datos viven en Neon.

**Opción B: Vercel**
- Con Neon ya es posible. Sube el repositorio, define las mismas variables y usa la cadena de conexión **pooled** de Neon.
- Revisa que Vercel detecte `app.py` como aplicación Flask. No lo probé en Vercel.
- Limitación: el límite de intentos de login se guarda en memoria y no se comparte entre instancias.

Con ambas opciones, conecta tu dominio y usa siempre `SECRET_KEY` y `ADMIN_PASSWORD` propios.

## Qué puede administrar el cliente desde `/admin`
- **Ofertas** y **Productos** (con foto): crear, editar, ocultar y eliminar.
- **Sedes**: nombre, dirección y horario. El mapa de Google se arma con la dirección.
- **Redes y contacto**: Facebook, Instagram, TikTok y WhatsApp. Los campos vacíos se ocultan en el sitio.
- Las fotos se reducen en el navegador y se guardan en la base de datos (Neon).

## Estructura
- `app.py`: API, login del panel y conexión a Postgres. Las tablas se crean solas.
- `static/index.html`: sitio público (todo el contenido sale de la API).
- `static/admin.html`: panel de administración.

## Pendiente
- Cuentas de administrador por usuario (hoy hay una sola contraseña).
- Copias de seguridad de la base de datos (Neon ofrece restauración según el plan).
