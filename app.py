import os, sqlite3, time, hmac
from functools import wraps
from flask import Flask, jsonify, request, session, g, abort

PROD = os.environ.get("FLASK_ENV") == "production"
SECRET = os.environ.get("SECRET_KEY")
PASSWORD = os.environ.get("ADMIN_PASSWORD")
if PROD and not (SECRET and PASSWORD):
    raise RuntimeError("En producción define SECRET_KEY y ADMIN_PASSWORD")
SECRET, PASSWORD = SECRET or "dev-secret", PASSWORD or "admin123"
DB = os.environ.get("DATABASE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "supermass.db"))

ICONOS = ["saco", "leche", "carne", "hoja", "pan", "vaso", "brillo", "biberon"]
CATEGORIAS = ["Frutas y verduras", "Carnes y aves", "Panadería", "Bebidas", "Limpieza", "Bebés", "Abarrotes"]
CAMPOS = {
    "ofertas": {"titulo": str, "precio": float, "precio_antes": float, "etiqueta": str, "icono": str, "activa": int},
    "productos": {"nombre": str, "categoria": str, "precio": float, "activo": int},
}
REQ = {"ofertas": ["titulo", "precio"], "productos": ["nombre", "categoria", "precio"]}
ACT = {"ofertas": "activa", "productos": "activo"}

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=PROD, PERMANENT_SESSION_LIFETIME=8 * 3600)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def cerrar(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS ofertas(id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, precio REAL NOT NULL,
        precio_antes REAL, etiqueta TEXT DEFAULT '', icono TEXT DEFAULT 'saco', activa INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS productos(id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, categoria TEXT NOT NULL,
        precio REAL NOT NULL, activo INTEGER DEFAULT 1);""")
    if not c.execute("SELECT 1 FROM ofertas").fetchone():
        c.executemany("INSERT INTO ofertas(titulo,precio,precio_antes,etiqueta,icono) VALUES(?,?,?,?,?)", [
            ("Pack limpieza hogar", 18.5, 23.0, "Nuevo", "brillo"), ("Pollo fresco por kg", 9.9, 11.6, "-15%", "carne"),
            ("Leche evaporada", 3.8, 7.6, "2x1", "leche"), ("Arroz extra 5 kg", 21.9, 27.4, "-20%", "saco")])
        c.executemany("INSERT INTO productos(nombre,categoria,precio) VALUES(?,?,?)", [
            ("Papa blanca 1 kg", "Frutas y verduras", 3.2), ("Plátano de seda 1 kg", "Frutas y verduras", 4.5),
            ("Pechuga de pollo 1 kg", "Carnes y aves", 14.9), ("Pan francés x10", "Panadería", 5.0),
            ("Gaseosa 3 L", "Bebidas", 9.5), ("Agua sin gas 625 ml", "Bebidas", 1.5),
            ("Detergente 900 g", "Limpieza", 12.9), ("Pañales talla M x30", "Bebés", 39.9),
            ("Aceite vegetal 1 L", "Abarrotes", 10.9), ("Fideos spaghetti 500 g", "Abarrotes", 3.9)])
        c.commit()
    c.close()


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
def err(e):
    return jsonify(error=getattr(e, "description", "Error")), e.code


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
    return jsonify([dict(r) for r in db().execute(q + " ORDER BY id DESC")])


@app.post("/api/<t>")
@admin
def crear(t):
    chk(t)
    d = cuerpo(t)
    cur = db().execute(f"INSERT INTO {t}({','.join(d)}) VALUES({','.join('?' * len(d))})", list(d.values()))
    db().commit()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/<t>/<int:i>")
@admin
def editar(t, i):
    chk(t)
    d = cuerpo(t, parcial=True)
    if d:
        db().execute(f"UPDATE {t} SET {','.join(k + '=?' for k in d)} WHERE id=?", list(d.values()) + [i])
        db().commit()
    return jsonify(ok=True)


@app.delete("/api/<t>/<int:i>")
@admin
def borrar(t, i):
    chk(t)
    db().execute(f"DELETE FROM {t} WHERE id=?", (i,))
    db().commit()
    return jsonify(ok=True)


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
