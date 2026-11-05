import os, time, hmac, hashlib, re, random, smtplib, ssl
from email.message import EmailMessage
from html import escape
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
# Dirección del sitio público (para el enlace "Ver sitio" del panel). Si no la defines y el panel está en
# admin.tudominio.com, se usa tudominio.com
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
# Correo (reporte semanal): servidor SMTP, por ejemplo smtp.gmail.com con una contraseña de aplicación
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip() or SMTP_USER
CRON_SECRET = os.environ.get("CRON_SECRET", "")

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


def url_sitio():
    if PUBLIC_URL:
        return PUBLIC_URL
    if ADMIN_HOST.startswith("admin."):
        return "https://" + ADMIN_HOST[len("admin."):]
    return "/"


@app.get("/api/config")
def config():
    return jsonify(iconos=ICONOS, categorias=CATEGORIAS, sitio=url_sitio())


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


def calcular(dias):
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
    return dict(
        dias=dias, desde=str(inicio), hasta=str(hoy),
        actual=q(RES, ini)[0], previo=q(RES + " AND ts < %s", ini_prev, ini)[0], hoy=q(RES, ini_hoy)[0],
        serie=[{"dia": str(r["dia"]), "vistas": r["vistas"], "visitantes": r["visitantes"]} for r in serie],
        paginas=lista("SELECT pagina AS label, count(*) AS n FROM eventos WHERE tipo='vista' AND ts >= %s GROUP BY 1 ORDER BY 2 DESC", ini),
        dispositivos=lista("SELECT dispositivo AS label, count(DISTINCT visitante) AS n FROM eventos WHERE tipo='vista' AND ts >= %s GROUP BY 1 ORDER BY 2 DESC", ini),
        referencias=lista("SELECT ref AS label, count(DISTINCT visitante) AS n FROM eventos WHERE tipo='vista' AND ref<>'' AND ts >= %s GROUP BY 1 ORDER BY 2 DESC LIMIT 6", ini),
        clics=lista("SELECT extra AS label, count(*) AS n FROM eventos WHERE tipo='clic' AND ts >= %s GROUP BY 1 ORDER BY 2 DESC LIMIT 12", ini),
        horas=[horas.get(i, 0) for i in range(24)],
        contenido={"ofertas": of, "productos": pr, "sedes": se, "redes": redes},
        recientes=q("SELECT to_char(ts AT TIME ZONE 'America/Lima','DD/MM HH24:MI') AS fecha, tipo, pagina, extra, dispositivo "
                    "FROM eventos ORDER BY id DESC LIMIT 8"),
        alertas=al)


@app.get("/api/stats")
@admin
def stats():
    try:
        dias = int(request.args.get("dias", 30))
    except ValueError:
        dias = 30
    return jsonify(calcular(dias if dias in (7, 30, 90) else 30))


# ---------- Reporte semanal por correo ----------
ETQ = {"whatsapp": "WhatsApp", "whatsapp_local": "WhatsApp (ofrecer local)", "facebook": "Facebook",
       "instagram": "Instagram", "tiktok": "TikTok"}
PAG = {"inicio": "Inicio", "sedes": "Nuestras sedes", "ofertas": "Ofertas", "productos": "Productos", "contacto": "Contacto"}
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def etiqueta(x):
    if x in ETQ:
        return ETQ[x]
    if x.startswith("como_llegar:"):
        return "Cómo llegar · " + x[12:]
    if x.startswith("ver_sede:"):
        return "Ver sede · " + x[9:]
    return x or "(sin nombre)"


def correos_validos(texto):
    lista = [e.strip() for e in re.split(r"[,;\s]+", str(texto)) if e.strip()]
    if not lista or len(lista) > 5 or not all(EMAIL_RE.match(e) for e in lista):
        raise ValueError("correos")
    return lista


def get_cfg(k):
    r = db().execute("SELECT valor FROM ajustes WHERE clave=%s", (k,)).fetchone()
    return r["valor"] if r else ""


def set_cfg(k, v):
    db().execute("INSERT INTO ajustes(clave,valor) VALUES(%s,%s) ON CONFLICT(clave) DO UPDATE SET valor=EXCLUDED.valor", (k, v))


def smtp_ok():
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def armar_reporte(s, enlace):
    a, p = s["actual"], s["previo"]
    fecha = lambda x: x[8:10] + "/" + x[5:7]
    rango = f"{fecha(s['desde'])} al {fecha(s['hasta'])}"

    def cambio(x, y):
        if not y:
            return "sin datos de la semana anterior"
        v = round((x - y) / y * 100)
        return "igual que la semana anterior" if v == 0 else f"{'▲' if v > 0 else '▼'} {abs(v)}% vs semana anterior"

    kp = [(n, a[k], cambio(a[k], p[k])) for n, k in (("Visitas", "vistas"), ("Visitantes", "visitantes"), ("Acciones", "clics"))]
    mejor = max(range(24), key=lambda i: s["horas"][i])
    hora = f"{mejor}:00 a {mejor}:59" if s["horas"][mejor] else "sin datos"
    secciones = [
        ("Páginas más vistas", [(PAG.get(i["label"], i["label"]), i["n"]) for i in s["paginas"]]),
        ("De dónde llegan", [("Directo" if i["label"] == "directo" else i["label"], i["n"]) for i in s["referencias"]]),
        ("Dispositivos", [(i["label"], i["n"]) for i in s["dispositivos"]]),
        ("Acciones más frecuentes", [(etiqueta(i["label"]), i["n"]) for i in s["clics"]]),
    ]
    mx = max([d["vistas"] for d in s["serie"]] + [1])
    dias = [(fecha(d["dia"]), d["vistas"], "█" * round(d["vistas"] / mx * 18)) for d in s["serie"]]
    asunto = f"Resumen semanal SuperMass Market ({rango})"

    t = [asunto, ""] + [f"{n}: {v} ({c})" for n, v, c in kp] + [f"Hora con más visitas: {hora}", ""]
    for tit, its in secciones:
        t += [tit + ":"] + ([f"  - {n}: {v}" for n, v in its[:5]] or ["  (sin datos)"]) + [""]
    if s["alertas"]:
        t += ["Por completar:"] + [f"  - {x['texto']}" for x in s["alertas"]] + [""]
    t.append("Abrir el panel: " + enlace)

    E = escape
    celdas = "".join(
        f'<td style="padding:14px 8px;background:#FFF9EA;border-radius:12px;text-align:center"><div style="font-size:13px;color:#665A5C">{n}</div>'
        f'<div style="font-size:30px;font-weight:800;color:#780115">{v}</div><div style="font-size:12px;color:#665A5C">{E(c)}</div></td>' for n, v, c in kp)
    bloques = "".join(
        f'<h3 style="margin:22px 0 8px;font-size:16px;color:#780115">{E(tit)}</h3>'
        + ("".join(f'<div style="padding:5px 0;border-bottom:1px solid #ECE4DD;font-size:14px"><span style="float:right;font-weight:700">{v}</span>{E(str(n))}</div>' for n, v in its[:5])
           or '<div style="font-size:14px;color:#665A5C">Sin datos esta semana.</div>') for tit, its in secciones)
    filas = "".join(f'<tr><td style="padding:2px 10px 2px 0;color:#665A5C">{d}</td><td style="color:#D6122B">{b}</td><td style="padding-left:8px;font-weight:700">{v}</td></tr>' for d, v, b in dias)
    pend = ("".join(f'<li style="margin:4px 0">{E(x["texto"])}</li>' for x in s["alertas"]) and
            '<h3 style="margin:22px 0 8px;font-size:16px;color:#780115">Por completar</h3><ul style="padding-left:18px;font-size:14px">'
            + "".join(f'<li style="margin:4px 0">{E(x["texto"])}</li>' for x in s["alertas"]) + "</ul>") if s["alertas"] else ""
    html = (f'<div style="background:#FAF7F2;padding:20px;font-family:Arial,sans-serif;color:#1F1416"><div style="max-width:600px;margin:auto;background:#fff;border-radius:16px;overflow:hidden">'
            f'<div style="background:#FFC21A;padding:22px 26px"><div style="font-size:22px;font-weight:800;color:#780115">SuperMass Market</div>'
            f'<div style="color:#780115">Resumen semanal · {rango}</div></div><div style="padding:24px 26px">'
            f'<table width="100%" cellspacing="8" style="border-collapse:separate"><tr>{celdas}</tr></table>'
            f'<p style="font-size:14px;margin:14px 0 0">Hora con más visitas: <b>{hora}</b> (hora de Lima)</p>'
            f'<h3 style="margin:22px 0 8px;font-size:16px;color:#780115">Visitas por día</h3>'
            f'<table style="font-family:Consolas,monospace;font-size:13px">{filas}</table>{bloques}{pend}'
            f'<p style="margin:26px 0 0"><a href="{E(enlace)}" style="background:#D6122B;color:#fff;text-decoration:none;padding:12px 22px;border-radius:99px;font-weight:700">Abrir el panel</a></p>'
            f'</div></div></div>')
    return asunto, "\n".join(t), html


def enviar(destinos, asunto, texto, html):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = asunto, f"SuperMass Market <{MAIL_FROM}>", ", ".join(destinos)
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20) as srv:
            srv.login(SMTP_USER, SMTP_PASSWORD)
            srv.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as srv:
            srv.starttls(context=ctx)
            srv.login(SMTP_USER, SMTP_PASSWORD)
            srv.send_message(msg)


def enviar_reporte(destinos):
    enlace = ("https://" + ADMIN_HOST) if ADMIN_HOST else request.host_url.rstrip("/") + "/admin"
    enviar(destinos, *armar_reporte(calcular(7), enlace))


@app.get("/api/reporte")
@admin
def ver_reporte():
    return jsonify(emails=get_cfg("reporte_emails"), activo=get_cfg("reporte_activo") == "1",
                   smtp=smtp_ok(), ultimo=get_cfg("reporte_ultimo"))


@app.put("/api/reporte")
@admin
def guardar_reporte():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Se esperaba JSON")
    activo = data.get("activo") in (True, 1, "1")
    texto = str(data.get("emails", "")).strip()
    try:
        emails = ", ".join(correos_validos(texto)) if texto or activo else ""
    except ValueError:
        abort(400, "Escribe uno o más correos válidos (máximo 5), separados por coma")
    set_cfg("reporte_emails", emails)
    set_cfg("reporte_activo", "1" if activo else "0")
    db().commit()
    return jsonify(ok=True)


@app.post("/api/reporte/prueba")
@admin
def reporte_prueba():
    data = request.get_json(silent=True) or {}
    try:
        destinos = correos_validos(data.get("emails", ""))
    except ValueError:
        abort(400, "Escribe uno o más correos válidos (máximo 5), separados por coma")
    if not smtp_ok():
        abort(400, "Falta configurar el servidor de correo (SMTP_HOST, SMTP_USER y SMTP_PASSWORD) en el hosting")
    try:
        enviar_reporte(destinos)
    except smtplib.SMTPAuthenticationError:
        abort(400, "El servidor de correo rechazó el usuario o la contraseña (SMTP)")
    except Exception as e:
        abort(400, f"No se pudo enviar el correo ({type(e).__name__})")
    return jsonify(ok=True)


@app.get("/api/cron/reporte")
def cron_reporte():
    auth = request.headers.get("Authorization", "")
    if not CRON_SECRET or not hmac.compare_digest(auth.encode(), f"Bearer {CRON_SECRET}".encode()):
        return jsonify(error="No autorizado"), 401
    if get_cfg("reporte_activo") != "1":
        return jsonify(enviado=False, motivo="desactivado")
    try:
        destinos = correos_validos(get_cfg("reporte_emails"))
    except ValueError:
        return jsonify(enviado=False, motivo="sin correos")
    if not smtp_ok():
        return jsonify(error="SMTP sin configurar"), 500
    try:
        enviar_reporte(destinos)
    except Exception as e:
        return jsonify(error=type(e).__name__), 500
    set_cfg("reporte_ultimo", db().execute("SELECT to_char(now() AT TIME ZONE 'America/Lima','YYYY-MM-DD HH24:MI') AS t").fetchone()["t"])
    db().commit()
    return jsonify(enviado=True)


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
