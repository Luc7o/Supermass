import os, time, hmac, hashlib, re, random
from datetime import timedelta
from urllib.parse import urlparse
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import psycopg
from psycopg.rows import dict_row
from functools import wraps
from flask import Flask, jsonify, request, session, g, abort, Response

PROD = os.environ.get("FLASK_ENV") == "production"
SECRET = os.environ.get("SECRET_KEY")
PASSWORD = os.environ.get("ADMIN_PASSWORD")
if PROD and not (SECRET and PASSWORD):
    raise RuntimeError("En producción define SECRET_KEY y ADMIN_PASSWORD")
SECRET, PASSWORD = SECRET or "dev-secret", PASSWORD or "admin123"
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Define DATABASE_URL con la cadena de conexión de Neon")

ICONOS = ["saco", "leche", "carne", "hoja", "pan", "vaso", "brillo", "biberon"]
CATEGORIAS = ["Frutas y verduras", "Carnes y aves", "Panadería", "Bebidas", "Limpieza", "Bebés", "Abarrotes"]
CAMPOS = {
    "ofertas": {"titulo": str, "precio": float, "precio_antes": float, "etiqueta": str, "icono": str, "activa": int},
    "productos": {"nombre": str, "categoria": str, "precio": float, "activo": int},
    "sedes": {"nombre": str, "direccion": str, "horario": str, "activa": int},
}
REQ = {"ofertas": ["titulo", "precio"], "productos": ["nombre", "categoria", "precio"], "sedes": ["nombre", "direccion"]}
ACT = {"ofertas": "activa", "productos": "activo", "sedes": "activa"}
AJUSTES = ["facebook", "instagram", "tiktok", "whatsapp"]
# Si defines ADMIN_HOST (ej. admin.tudominio.com), el panel solo se sirve en ese dominio
ADMIN_HOST = os.environ.get("ADMIN_HOST", "").strip().lower()

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=PROD, PERMANENT_SESSION_LIFETIME=8 * 3600,
                  MAX_CONTENT_LENGTH=2 * 1024 * 1024)


def db():
    if "db" not in g:
        g.db = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    return g.db


@app.teardown_appcontext
def cerrar(_):
    d = g.pop("db", None)
    if d:
        d.close()


DDL = """
CREATE TABLE IF NOT EXISTS ofertas(
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, titulo TEXT NOT NULL, precio DOUBLE PRECISION NOT NULL,
    precio_antes DOUBLE PRECISION, etiqueta TEXT DEFAULT '', icono TEXT DEFAULT 'saco', activa SMALLINT DEFAULT 1);
CREATE TABLE IF NOT EXISTS productos(
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, nombre TEXT NOT NULL, categoria TEXT NOT NULL,
    precio DOUBLE PRECISION NOT NULL, activo SMALLINT DEFAULT 1);
ALTER TABLE productos ADD COLUMN IF NOT EXISTS imagen_id INTEGER;
CREATE TABLE IF NOT EXISTS imagenes(
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, tipo TEXT NOT NULL, datos BYTEA NOT NULL);
CREATE TABLE IF NOT EXISTS sedes(
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, nombre TEXT NOT NULL, direccion TEXT NOT NULL,
    horario TEXT DEFAULT '', activa SMALLINT DEFAULT 1);
CREATE TABLE IF NOT EXISTS ajustes(clave TEXT PRIMARY KEY, valor TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS eventos(
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    tipo TEXT NOT NULL, pagina TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '', dispositivo TEXT NOT NULL DEFAULT '',
    visitante TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS eventos_ts ON eventos(ts);"""


def init_db():
    with psycopg.connect(DATABASE_URL) as c:
        c.execute(DDL)
        if not c.execute("SELECT 1 FROM ofertas LIMIT 1").fetchone():
            with c.cursor() as cur:
                cur.executemany("INSERT INTO ofertas(titulo,precio,precio_antes,etiqueta,icono) VALUES(%s,%s,%s,%s,%s)", [
                    ("Pack limpieza hogar", 18.5, 23.0, "Nuevo", "brillo"), ("Pollo fresco por kg", 9.9, 11.6, "-15%", "carne"),
                    ("Leche evaporada", 3.8, 7.6, "2x1", "leche"), ("Arroz extra 5 kg", 21.9, 27.4, "-20%", "saco")])
                cur.executemany("INSERT INTO productos(nombre,categoria,precio) VALUES(%s,%s,%s)", [
                    ("Papa blanca 1 kg", "Frutas y verduras", 3.2), ("Plátano de seda 1 kg", "Frutas y verduras", 4.5),
                    ("Pechuga de pollo 1 kg", "Carnes y aves", 14.9), ("Pan francés x10", "Panadería", 5.0),
                    ("Gaseosa 3 L", "Bebidas", 9.5), ("Agua sin gas 625 ml", "Bebidas", 1.5),
                    ("Detergente 900 g", "Limpieza", 12.9), ("Pañales talla M x30", "Bebés", 39.9),
                    ("Aceite vegetal 1 L", "Abarrotes", 10.9), ("Fideos spaghetti 500 g", "Abarrotes", 3.9)])
        if not c.execute("SELECT 1 FROM sedes LIMIT 1").fetchone():
            c.cursor().executemany("INSERT INTO sedes(nombre,direccion) VALUES(%s,%s)", [
                ("SuperMass Prolongación Trujillo", "Prolongación Trujillo 950, Huancayo"),
                ("SuperMass Ricardo Menéndez", "Jr. Ricardo Menéndez 1099, Huancayo"),
                ("SuperMass Huáscar", "Jr. Huáscar 702, Huancayo")])


init_db()


def admin(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin"):
            return jsonify(error="No autorizado"), 401
        return f(*a, **k)
    return w


def limpiar(t, data, parcial=False):
    out = {}
    for campo, tipo in CAMPOS[t].items():
        if campo not in data:
            if campo in REQ[t] and not parcial:
                raise ValueError(campo)
            continue
        v = data[campo]
        if tipo is str:
            v = str(v).strip()[:120]
            if campo in REQ[t] and not v:
                raise ValueError(campo)
            if campo == "icono" and v not in ICONOS:
                v = "saco"
            if campo == "categoria" and v not in CATEGORIAS:
                raise ValueError(campo)
        elif tipo is int:
            v = 1 if v in (1, True, "1") else 0
        elif v in ("", None):
            if campo in REQ[t]:
                raise ValueError(campo)
            v = None
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(campo)
            if v < 0:
                raise ValueError(campo)
        out[campo] = v
    return out


def cuerpo(t, parcial=False):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Se esperaba JSON")
    try:
        return limpiar(t, data, parcial)
    except ValueError as e:
        abort(400, f"Revisa el campo: {e}")


def chk(t):
    if t not in CAMPOS:
        abort(404)


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
def err(e):
    return jsonify(error=getattr(e, "description", "Error")), e.code


@app.before_request
def separar_panel():
    if not ADMIN_HOST:
        return None
    es_admin = request.host.split(":")[0].lower() == ADMIN_HOST
    if request.path in ("/admin", "/admin.html") and not es_admin:
        abort(404)
    if es_admin and request.path in ("/", "/index.html", "/admin"):
        return app.send_static_file("admin.html")
    return None


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.get("/admin")
def panel():
    return app.send_static_file("admin.html")


@app.get("/api/config")
def config():
    return jsonify(iconos=ICONOS, categorias=CATEGORIAS)


@app.get("/api/<t>")
def listar(t):
    chk(t)
    q = f"SELECT * FROM {t}"
    if not (request.args.get("todos") and session.get("admin")):
        q += f" WHERE {ACT[t]}=1"
    return jsonify(db().execute(q + (" ORDER BY id" if t == "sedes" else " ORDER BY id DESC")).fetchall())


@app.post("/api/<t>")
@admin
def crear(t):
    chk(t)
    d = cuerpo(t)
    nuevo = db().execute(f"INSERT INTO {t}({','.join(d)}) VALUES({','.join(['%s'] * len(d))}) RETURNING id",
                         list(d.values())).fetchone()
    db().commit()
    return jsonify(id=nuevo["id"]), 201


@app.put("/api/<t>/<int:i>")
@admin
def editar(t, i):
    chk(t)
    d = cuerpo(t, parcial=True)
    if d:
        db().execute(f"UPDATE {t} SET {','.join(k + '=%s' for k in d)} WHERE id=%s", list(d.values()) + [i])
        db().commit()
    return jsonify(ok=True)


@app.delete("/api/<t>/<int:i>")
@admin
def borrar(t, i):
    chk(t)
    if t == "productos":
        f = db().execute("SELECT imagen_id FROM productos WHERE id=%s", (i,)).fetchone()
        if f and f["imagen_id"]:
            db().execute("DELETE FROM imagenes WHERE id=%s", (f["imagen_id"],))
    db().execute(f"DELETE FROM {t} WHERE id=%s", (i,))
    db().commit()
    return jsonify(ok=True)


def tipo_imagen(b):
    if b.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b.startswith(b"RIFF") and b[8:12] == b"WEBP":
        return "image/webp"


@app.post("/api/productos/<int:i>/imagen")
@admin
def subir_imagen(i):
    f = request.files.get("imagen")
    datos = f.read() if f else b""
    tipo = tipo_imagen(datos)
    if not tipo or len(datos) > 1_500_000:
        abort(400, "Sube una imagen JPG, PNG o WebP de hasta 1,5 MB")
    d = db()
    fila = d.execute("SELECT imagen_id FROM productos WHERE id=%s", (i,)).fetchone()
    if not fila:
        abort(404, "Producto no encontrado")
    nuevo = d.execute("INSERT INTO imagenes(tipo,datos) VALUES(%s,%s) RETURNING id", (tipo, datos)).fetchone()["id"]
    d.execute("UPDATE productos SET imagen_id=%s WHERE id=%s", (nuevo, i))
    if fila["imagen_id"]:
        d.execute("DELETE FROM imagenes WHERE id=%s", (fila["imagen_id"],))
    d.commit()
    return jsonify(imagen_id=nuevo)


@app.delete("/api/productos/<int:i>/imagen")
@admin
def quitar_imagen(i):
    d = db()
    fila = d.execute("SELECT imagen_id FROM productos WHERE id=%s", (i,)).fetchone()
    if fila and fila["imagen_id"]:
        d.execute("UPDATE productos SET imagen_id=NULL WHERE id=%s", (i,))
        d.execute("DELETE FROM imagenes WHERE id=%s", (fila["imagen_id"],))
        d.commit()
    return jsonify(ok=True)


@app.get("/api/img/<int:i>")
def ver_imagen(i):
    r = db().execute("SELECT tipo,datos FROM imagenes WHERE id=%s", (i,)).fetchone()
    if not r:
        abort(404)
    return Response(bytes(r["datos"]), mimetype=r["tipo"],
                    headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})


def leer_ajustes():
    d = {k: "" for k in AJUSTES}
    for r in db().execute("SELECT clave,valor FROM ajustes").fetchall():
        if r["clave"] in d:
            d[r["clave"]] = r["valor"]
    return d


@app.get("/api/ajustes")
def ver_ajustes():
    return jsonify(leer_ajustes())


@app.put("/api/ajustes")
@admin
def guardar_ajustes():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Se esperaba JSON")
    vals = {}
    for k in AJUSTES:
        if k not in data:
            continue
        v = str(data[k]).strip()[:200]
        if k == "whatsapp":
            v = "".join(ch for ch in v if ch.isdigit())
            if v and not 8 <= len(v) <= 15:
                abort(400, "WhatsApp: escribe solo números con código de país, ej. 51987654321")
        elif v and not v.startswith("https://"):
            abort(400, f"{k.capitalize()}: el enlace debe empezar con https://")
        vals[k] = v
    for k, v in vals.items():
        db().execute("INSERT INTO ajustes(clave,valor) VALUES(%s,%s) ON CONFLICT(clave) DO UPDATE SET valor=EXCLUDED.valor", (k, v))
    db().commit()
    return jsonify(ok=True)


# ---------- Estadísticas de visitas (sin cookies ni datos personales) ----------
PAGINAS = ["inicio", "sedes", "ofertas", "productos", "contacto"]
BOTS = re.compile(r"bot|crawl|spider|slurp|preview|headless|lighthouse|pingdom|monitor|curl|wget|python-requests|facebookexternalhit", re.I)
RATE = {}


def visitante_id():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    ua = request.headers.get("User-Agent", "")
    # Hash que rota cada mes: no se guarda la IP ni se puede reconstruir
    return hashlib.sha256(f"{SECRET}|{time.strftime('%Y-%m')}|{ip}|{ua}".encode()).hexdigest()[:16]


def dispositivo(ua):
    if re.search(r"ipad|tablet", ua, re.I):
        return "Tablet"
    if re.search(r"mobi|android|iphone", ua, re.I):
        return "Móvil"
    return "Computadora"


def excede_limite(v):
    ahora = time.time()
    lst = [t for t in RATE.get(v, []) if ahora - t < 3600]
    if len(lst) >= 120:
        RATE[v] = lst
        return True
    lst.append(ahora)
    RATE[v] = lst
    if len(RATE) > 5000:
        RATE.clear()
    return False


@app.post("/api/track")
def track():
    ua = request.headers.get("User-Agent", "")
    d = request.get_json(silent=True)
    if session.get("admin") or not ua or BOTS.search(ua) or not isinstance(d, dict):
        return "", 204
    tipo, pagina = d.get("tipo"), d.get("pagina")
    if tipo not in ("vista", "clic") or pagina not in PAGINAS:
        return "", 204
    extra = re.sub(r"[^\w .:áéíóúüñÁÉÍÓÚÜÑ-]", "", str(d.get("extra", ""))[:80]).strip()
    ref = ""
    if tipo == "vista" and d.get("entrada"):
        host = (urlparse(str(d.get("ref", ""))[:300]).hostname or "").removeprefix("www.")
        propio = request.host.split(":")[0].lower().removeprefix("www.")
        ref = "directo" if (not host or host == propio or host == ADMIN_HOST) else host[:60]
    vid = visitante_id()
    if excede_limite(vid):
        return "", 204
    db().execute("INSERT INTO eventos(tipo,pagina,ref,dispositivo,visitante,extra) VALUES(%s,%s,%s,%s,%s,%s)",
                 (tipo, pagina, ref, dispositivo(ua), vid, extra))
    if random.random() < 0.01:
        db().execute("DELETE FROM eventos WHERE ts < now() - interval '400 days'")
    db().commit()
    return "", 204


def local(fecha):
    return db().execute("SELECT (%s::date)::timestamp AT TIME ZONE 'America/Lima' AS t", (fecha,)).fetchone()["t"]


@app.get("/api/stats")
@admin
def stats():
    try:
        dias = int(request.args.get("dias", 30))
    except ValueError:
        dias = 30
    dias = dias if dias in (7, 30, 90) else 30
    d = db()
    q = lambda sql, *p: d.execute(sql, p).fetchall()
    hoy = q("SELECT (now() AT TIME ZONE 'America/Lima')::date AS h")[0]["h"]
    inicio = hoy - timedelta(days=dias - 1)
    ini, ini_prev, ini_hoy = local(inicio), local(inicio - timedelta(days=dias)), local(hoy)
    RES = ("SELECT count(*) FILTER (WHERE tipo='vista') AS vistas, "
           "count(DISTINCT visitante) FILTER (WHERE tipo='vista') AS visitantes, "
           "count(*) FILTER (WHERE tipo='clic') AS clics FROM eventos WHERE ts >= %s")
    serie = q("""SELECT g.d::date AS dia, coalesce(e.v,0) AS vistas, coalesce(e.u,0) AS visitantes
        FROM generate_series(%s::date, %s::date, interval '1 day') AS g(d)
        LEFT JOIN (SELECT (ts AT TIME ZONE 'America/Lima')::date AS dia, count(*) AS v, count(DISTINCT visitante) AS u
                   FROM eventos WHERE tipo='vista' AND ts >= %s GROUP BY 1) e ON e.dia = g.d::date ORDER BY 1""",
              inicio, hoy, ini)
    lista = lambda sql, *p: [{"label": r["label"], "n": r["n"]} for r in q(sql, *p)]
    horas = {r["h"]: r["n"] for r in q("""SELECT extract(hour FROM ts AT TIME ZONE 'America/Lima')::int AS h, count(*) AS n
        FROM eventos WHERE tipo='vista' AND ts >= %s GROUP BY 1""", ini)}
    of = q("SELECT count(*) AS total, count(*) FILTER (WHERE activa=1) AS activas FROM ofertas")[0]
    pr = q("SELECT count(*) AS total, count(*) FILTER (WHERE activo=1) AS activos, "
           "count(*) FILTER (WHERE activo=1 AND imagen_id IS NULL) AS sin_foto FROM productos")[0]
    se = q("SELECT count(*) AS total, count(*) FILTER (WHERE activa=1) AS activas, "
           "count(*) FILTER (WHERE activa=1 AND coalesce(horario,'')='') AS sin_horario FROM sedes")[0]
    aj = leer_ajustes()
    redes = sum(1 for k in ("facebook", "instagram", "tiktok") if aj[k])
    al = []
    if not of["activas"]:
        al.append({"nivel": "warn", "texto": "No hay ofertas activas: la portada se ve vacía.", "tab": "ofertas"})
    if not pr["activos"]:
        al.append({"nivel": "warn", "texto": "No hay productos activos.", "tab": "productos"})
    elif pr["sin_foto"]:
        al.append({"nivel": "info", "texto": f"{pr['sin_foto']} producto(s) sin foto.", "tab": "productos"})
    if not se["activas"]:
        al.append({"nivel": "warn", "texto": "No hay sedes activas.", "tab": "sedes"})
    elif se["sin_horario"]:
        al.append({"nivel": "info", "texto": f"{se['sin_horario']} sede(s) sin horario.", "tab": "sedes"})
    if not aj["whatsapp"]:
        al.append({"nivel": "warn", "texto": "WhatsApp sin configurar: los botones de contacto están ocultos.", "tab": "ajustes"})
    if redes < 3:
        al.append({"nivel": "info", "texto": f"Faltan redes sociales ({redes} de 3).", "tab": "ajustes"})
    return jsonify(
        dias=dias, desde=str(inicio), hasta=str(hoy),
        actual=q(RES, ini)[0], previo=q(RES + " AND ts < %s", ini_prev, ini)[0], hoy=q(RES, ini_hoy)[0],
        serie=[{"dia": str(r["dia"]), "vistas": r["vistas"], "visitantes": r["visitantes"]} for r in serie],
        paginas=lista("SELECT pagina AS label, count(*) AS n FROM eventos WHERE tipo='vista' AND ts >= %s GROUP BY 1 ORDER BY 2 DESC", ini),
        dispositivos=lista("SELECT dispositivo AS label, count(DISTINCT visitante) AS n FROM eventos WHERE tipo='vista' AND ts >= %s GROUP BY 1 ORDER BY 2 DESC", ini),
        referencias=lista("SELECT ref AS label, count(DISTINCT visitante) AS n FROM eventos WHERE tipo='vista' AND ref<>'' AND ts >= %s GROUP BY 1 ORDER BY 2 DESC LIMIT 6", ini),
        clics=lista("SELECT extra AS label, count(*) AS n FROM eventos WHERE tipo='clic' AND ts >= %s GROUP BY 1 ORDER BY 2 DESC LIMIT 12", ini),
        horas=[horas.get(i, 0) for i in range(24)],
        contenido={"ofertas": of, "productos": pr, "sedes": se, "redes": redes},
        alertas=al)


FALLOS = {}


@app.post("/api/login")
def login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    n, hasta = FALLOS.get(ip, (0, 0))
    if n >= 5 and time.time() < hasta:
        return jsonify(error="Demasiados intentos. Espera unos minutos."), 429
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if hmac.compare_digest(str(pw).encode(), PASSWORD.encode()):
        FALLOS.pop(ip, None)
        session.permanent = True
        session["admin"] = True
        return jsonify(ok=True)
    FALLOS[ip] = (n + 1, time.time() + 300)
    return jsonify(error="Contraseña incorrecta"), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(admin=bool(session.get("admin")))


if __name__ == "__main__":
    app.run(debug=True)
