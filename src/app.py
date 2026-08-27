from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g, send_from_directory, abort
import sqlite3
import os
import hashlib
import re
import ssl
import unicodedata
from datetime import date as _dt_date, datetime as _dt_datetime
from urllib.parse import quote as url_quote
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IS_PRODUCTION = os.environ.get('PUNTICO_ENV', '').lower() == 'production' or os.environ.get('RENDER') == 'true'
DB_NAME = os.environ.get('DATABASE_PATH', os.path.join(BASE_DIR, 'mitienda.db'))
IMG_FOLDER = os.environ.get('IMG_FOLDER', os.path.join(BASE_DIR, 'img'))
# Backend de datos: MySQL gestionado cuando Wasmer inyecta DB_*; SQLite para desarrollo local.
DB_BACKEND = 'mysql' if os.environ.get('DB_HOST') else 'sqlite'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
)

def ensure_runtime_dirs():
    os.makedirs(os.path.dirname(DB_NAME), exist_ok=True)
    os.makedirs(IMG_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generar_slug(db_conn, texto, exclude_id=None):
    """Slug de tienda a partir del nombre: 'La Flor' -> 'la-flor'."""
    base = unicodedata.normalize('NFKD', (texto or 'tienda')).encode('ascii', 'ignore').decode('ascii')
    base = re.sub(r'[^a-zA-Z0-9]+', '-', base).strip('-').lower() or 'tienda'
    slug = base
    n = 2
    while True:
        if exclude_id:
            row = db_conn.execute("SELECT id FROM usuarios WHERE slug = ? AND id != ?", (slug, exclude_id)).fetchone()
        else:
            row = db_conn.execute("SELECT id FROM usuarios WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return slug
        slug = f"{base}-{n}"
        n += 1

def hash_password(password):
    return generate_password_hash(password)

def check_password(password, hashed):
    if not hashed:
        return False
    # Compatibilidad con contraseÃ±as antiguas guardadas en SHA-256 simple.
    if re.fullmatch(r'[a-f0-9]{64}', hashed):
        return hashlib.sha256(password.encode()).hexdigest() == hashed
    return check_password_hash(hashed, password)

def _norm_row(row):
    """Convierte datetime/date a texto para igualar el comportamiento de sqlite3."""
    if isinstance(row, dict):
        return {
            k: (v.strftime('%Y-%m-%d %H:%M:%S') if isinstance(v, (_dt_datetime, _dt_date)) else v)
            for k, v in row.items()
        }
    return row


class _MyCursorProxy:
    """Cursor que normaliza filas al leerlas."""
    def __init__(self, cur):
        self._cur = cur

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def fetchone(self):
        r = self._cur.fetchone()
        return _norm_row(r) if r is not None else None

    def fetchall(self):
        return [_norm_row(r) for r in self._cur.fetchall()]


class _MySQLConn:
    """Adaptador minimo: expone la API de sqlite3 (placeholders ?) sobre pymysql."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        cur = self._conn.cursor()
        cur.execute(query.replace('?', '%s'), params)
        return _MyCursorProxy(cur)

    def commit(self):
        self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


def _mysql_connect():
    import pymysql
    ctx = ssl.create_default_context()
    ctx.check_hostname = False          # TLS activo; la CA de Wasmer es privada
    ctx.verify_mode = ssl.CERT_NONE
    return pymysql.connect(
        host=os.environ['DB_HOST'],
        port=int(os.environ.get('DB_PORT', '3306')),
        user=os.environ['DB_USERNAME'],
        password=os.environ['DB_PASSWORD'],
        database=os.environ['DB_NAME'],
        charset='utf8mb4',
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
        ssl=ctx,
    )


def get_db():
    if 'db' not in g:
        if DB_BACKEND == 'mysql':
            g.db = _MySQLConn(_mysql_connect())
        else:
            g.db = sqlite3.connect(DB_NAME)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

def get_config(key=None):
    db = get_db()
    if key:
        row = db.execute("SELECT valor FROM config WHERE clave = ?", (key,)).fetchone()
        return row['valor'] if row else ''
    rows = db.execute("SELECT * FROM config").fetchall()
    return {r['clave']: r['valor'] for r in rows}

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    ensure_runtime_dirs()
    is_mysql = DB_BACKEND == 'mysql'
    conn = _mysql_connect() if is_mysql else sqlite3.connect(DB_NAME)
    c = conn.cursor()

    def q(sql):
        return sql.replace('?', '%s') if is_mysql else sql

    if is_mysql:
        c.execute('''CREATE TABLE IF NOT EXISTS usuarios (
            id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(190) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            nombre VARCHAR(190) NOT NULL,
            telefono VARCHAR(255),
            direccion TEXT,
            rol VARCHAR(20) DEFAULT 'cliente',
            slug VARCHAR(190) UNIQUE,
            tienda_nombre VARCHAR(190),
            imagen_tienda TEXT,
            precioDelivery INT DEFAULT 0,
            entregaGratis INT DEFAULT 0,
            solicitud_vendedor INT DEFAULT 0,
            activo INT DEFAULT 1,
            pago_hasta DATE DEFAULT NULL,
            pago_activo INT DEFAULT 1,
            es_vip INT DEFAULT 0,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        c.execute('''CREATE TABLE IF NOT EXISTS productos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            vendedor_id INT NOT NULL,
            nombre VARCHAR(190) NOT NULL,
            descripcion TEXT,
            precio DOUBLE NOT NULL,
            stock INT DEFAULT 0,
            imagen TEXT,
            categoria VARCHAR(190),
            activo INT DEFAULT 1,
            FOREIGN KEY (vendedor_id) REFERENCES usuarios(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        c.execute('''CREATE TABLE IF NOT EXISTS pedidos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            vendedor_id INT NOT NULL,
            estado VARCHAR(30) DEFAULT 'pendiente',
            total DOUBLE NOT NULL,
            delivery INT DEFAULT 0,
            observaciones TEXT,
            fecha_pedido DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_id) REFERENCES usuarios(id),
            FOREIGN KEY (vendedor_id) REFERENCES usuarios(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        c.execute('''CREATE TABLE IF NOT EXISTS pedido_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            pedido_id INT NOT NULL,
            producto_id INT NOT NULL,
            cantidad INT NOT NULL,
            precio DOUBLE NOT NULL,
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id),
            FOREIGN KEY (producto_id) REFERENCES productos(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        c.execute('''CREATE TABLE IF NOT EXISTS categorias (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nombre VARCHAR(190) NOT NULL,
            subcategoria VARCHAR(190),
            UNIQUE(nombre, subcategoria)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        c.execute('''CREATE TABLE IF NOT EXISTS solicitudes_categoria (
            id INT AUTO_INCREMENT PRIMARY KEY,
            vendedor_id INT NOT NULL,
            nombre_categoria VARCHAR(190) NOT NULL,
            subcategoria VARCHAR(190),
            estado VARCHAR(20) DEFAULT 'pendiente',
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (vendedor_id) REFERENCES usuarios(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        c.execute('''CREATE TABLE IF NOT EXISTS config (
            clave VARCHAR(190) PRIMARY KEY,
            valor TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
    else:
        c.execute('''CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            nombre TEXT NOT NULL,
            telefono TEXT,
            direccion TEXT,
            rol TEXT DEFAULT 'cliente',
            slug TEXT UNIQUE,
            tienda_nombre TEXT,
            imagen_tienda TEXT,
            precioDelivery INTEGER DEFAULT 0,
            entregaGratis INTEGER DEFAULT 0,
            solicitud_vendedor INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            pago_hasta DATE DEFAULT NULL,
            pago_activo INTEGER DEFAULT 1,
            es_vip INTEGER DEFAULT 0,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendedor_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            precio REAL NOT NULL,
            stock INTEGER DEFAULT 0,
            imagen TEXT,
            categoria TEXT,
            activo INTEGER DEFAULT 1,
            FOREIGN KEY (vendedor_id) REFERENCES usuarios(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            vendedor_id INTEGER NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            total REAL NOT NULL,
            delivery INTEGER DEFAULT 0,
            observaciones TEXT,
            fecha_pedido DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_id) REFERENCES usuarios(id),
            FOREIGN KEY (vendedor_id) REFERENCES usuarios(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS pedido_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            producto_id INTEGER NOT NULL,
            cantidad INTEGER NOT NULL,
            precio REAL NOT NULL,
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id),
            FOREIGN KEY (producto_id) REFERENCES productos(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            subcategoria TEXT,
            UNIQUE(nombre, subcategoria)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS solicitudes_categoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendedor_id INTEGER NOT NULL,
            nombre_categoria TEXT NOT NULL,
            subcategoria TEXT,
            estado TEXT DEFAULT 'pendiente',
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (vendedor_id) REFERENCES usuarios(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS config (
            clave TEXT PRIMARY KEY,
            valor TEXT
        )''')

    defaults = {
        'nombre_sitio': 'MiTienda',
        'color_primario': '#ff9900',
        'color_secundario': '#667eea',
        'color_texto': '#333333',
        'color_fondo': '#f0f2f5',
        'banner_principal': '',
        'banner_secundario': '',
        'mostrar_banner': '1',
        'moneda': 'CUP',
        'simbolo_moneda': '$',
        'telefono_contacto': '',
        'email_contacto': '',
        'direccion_sitio': '',
    }
    insert_default = ("INSERT IGNORE INTO config (clave, valor) VALUES (%s, %s)" if is_mysql
                      else "INSERT OR IGNORE INTO config (clave, valor) VALUES (?, ?)")
    for k, v in defaults.items():
        c.execute(insert_default, (k, v))

    c.execute("SELECT COUNT(*) FROM usuarios WHERE rol = 'admin'")
    row = c.fetchone()
    admin_count = row[0] if not is_mysql else list(row.values())[0]
    if admin_count == 0:
        admin_email = os.environ.get('DEFAULT_ADMIN_EMAIL', 'admin@elpuntico.com')
        admin_password = os.environ.get('DEFAULT_ADMIN_PASSWORD')
        if not admin_password and not IS_PRODUCTION:
            admin_password = 'admin'
        if admin_password:
            c.execute(q(
                "INSERT INTO usuarios (email, password, nombre, telefono, direccion, rol) VALUES (?, ?, ?, ?, ?, ?)"
            ), (admin_email, hash_password(admin_password), 'Admin Principal', '0000', 'Sistema', 'admin'))
            print(f"Usuario admin creado: {admin_email}")
        elif IS_PRODUCTION:
            print("No se creo admin por defecto: define DEFAULT_ADMIN_PASSWORD en produccion.")

    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def rol_required(roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'usuario_id' not in session:
                return redirect(url_for('login'))
            if session.get('rol') not in roles:
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/')
def home():
    db = get_db()
    buscar = request.args.get('buscar', '').strip()
    
    # Portada = directorio de tiendas. Los productos viven dentro de cada tienda (/slug).
    sql = """SELECT u.*, 
             (SELECT COUNT(*) FROM productos p WHERE p.vendedor_id = u.id AND p.activo = 1 AND p.stock > 0) AS num_productos
             FROM usuarios u WHERE u.rol = 'vendedor' AND u.activo = 1"""
    params = []
    if buscar:
        sql += " AND (u.nombre LIKE ? OR u.direccion LIKE ?)"
        params.extend([f'%{buscar}%', f'%{buscar}%'])
    sql += " ORDER BY u.nombre"
    
    tiendas = db.execute(sql, params).fetchall()
    config = get_config()
    
    # Búsqueda de productos en todas las tiendas (estilo Google)
    productos_resultados = []
    if buscar:
        sql_prod = """
            SELECT p.*, u.slug, u.tienda_nombre, u.nombre AS vendedor_nombre,
                   u.imagen_tienda
            FROM productos p
            JOIN usuarios u ON p.vendedor_id = u.id
            WHERE p.activo = 1 AND p.stock > 0
              AND u.rol = 'vendedor' AND u.activo = 1
              AND (p.nombre LIKE ? OR p.descripcion LIKE ? OR p.categoria LIKE ?)
            ORDER BY 
                CASE 
                    WHEN p.nombre LIKE ? THEN 0
                    WHEN p.nombre LIKE ? THEN 1
                    ELSE 2
                END,
                p.nombre
            LIMIT 50
        """
        like_exact = f'%{buscar}%'
        like_start = f'{buscar}%'
        like_anywhere = f'%{buscar}%'
        productos_resultados = db.execute(sql_prod, [like_exact, like_exact, like_exact, like_start, like_anywhere]).fetchall()
    
    return render_template('home.html', tiendas=tiendas, buscar=buscar, config=config, productos_resultados=productos_resultados)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identificador = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        db = get_db()
        identificador_lower = identificador.lower()
        
        user = db.execute("""
            SELECT * FROM usuarios 
            WHERE (LOWER(email) = ? OR telefono = ?) 
            AND activo = 1
        """, (identificador_lower, identificador)).fetchone()
        
        if user and check_password(password, user['password']):
            if re.fullmatch(r'[a-f0-9]{64}', user['password']):
                db.execute("UPDATE usuarios SET password = ? WHERE id = ?", (hash_password(password), user['id']))
                db.commit()
            session['usuario_id'] = user['id']
            session['nombre'] = user['nombre']
            session['rol'] = user['rol']
            return redirect(url_for('home'))
        else:
            return render_template('login.html', error="Email, telefono o contrasena incorrectos", config=get_config())
    
    return render_template('login.html', config=get_config())

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        email = request.form.get('email', '').lower()
        password = request.form.get('password', '')
        nombre = request.form.get('nombre', '')
        telefono = request.form.get('telefono', '')
        direccion = request.form.get('direccion', '')
        
        if not email or not password or not nombre:
            return render_template('registro.html', error="Email, password y nombre son obligatorios", config=get_config())
        
        db = get_db()
        if db.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone():
            return render_template('registro.html', error="El email ya estÃ¡ registrado", config=get_config())
        
        db.execute("INSERT INTO usuarios (email, password, nombre, telefono, direccion, rol) VALUES (?, ?, ?, ?, ?, 'cliente')",
            (email, hash_password(password), nombre, telefono, direccion))
        db.commit()
        
        user = db.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
        session['usuario_id'] = user['id']
        session['nombre'] = user['nombre']
        session['rol'] = 'cliente'
        
        return redirect(url_for('home'))
    
    return render_template('registro.html', config=get_config())

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


@app.route('/cambiar_password', methods=['GET', 'POST'])
@login_required
def cambiar_password():
    error = None
    ok = None
    if request.method == 'POST':
        actual = request.form.get('actual', '')
        nueva = request.form.get('nueva', '')
        repetir = request.form.get('repetir', '')
        db = get_db()
        user = db.execute("SELECT * FROM usuarios WHERE id = ?", (session['usuario_id'],)).fetchone()
        if not user or not check_password(actual, user['password']):
            error = "La contrasena actual es incorrecta"
        elif len(nueva) < 4:
            error = "La nueva contrasena debe tener al menos 4 caracteres"
        elif nueva != repetir:
            error = "Las contrasenas nuevas no coinciden"
        else:
            db.execute("UPDATE usuarios SET password = ? WHERE id = ?", (hash_password(nueva), user['id']))
            db.commit()
            ok = "Contrasena actualizada correctamente"
    return render_template('cambiar_password.html', error=error, ok=ok, config=get_config())



@app.route('/agregar_carrito', methods=['POST'])
def agregar_carrito():
    producto_id = int(request.form.get('producto_id', 0) or 0)
    cantidad = max(1, int(request.form.get('cantidad', 1) or 1))
    
    if 'carrito' not in session:
        session['carrito'] = []
    
    existe = False
    for item in session['carrito']:
        if item['producto_id'] == producto_id:
            item['cantidad'] += cantidad
            existe = True
            break
    
    agregado = existe
    if not existe:
        db = get_db()
        producto = db.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if producto:
            session['carrito'].append({
                'producto_id': producto_id,
                'vendedor_id': producto['vendedor_id'],
                'nombre': producto['nombre'],
                'precio': producto['precio'],
                'cantidad': cantidad,
                'stock': producto['stock']
            })
            agregado = True
    
    session.modified = True
    
    # Desde la web (fetch): respuesta JSON sin recargar la pagina.
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({'ok': agregado, 'count': len(session['carrito'])})
    
    # Fallback clasico: volver a la pagina desde donde se agrego.
    return redirect(request.referrer or url_for('home'))

@app.route('/quitar_carrito/<int:index>')
def quitar_carrito(index):
    if 'carrito' in session and 0 <= index < len(session['carrito']):
        session['carrito'].pop(index)
        session.modified = True
    return redirect(url_for('carrito'))

@app.route('/carrito')
def carrito():
    cliente = None
    if session.get('usuario_id'):
        db = get_db()
        cliente = db.execute("SELECT nombre, telefono, direccion FROM usuarios WHERE id = ?", (session['usuario_id'],)).fetchone()
    return render_template('carrito.html', cliente=cliente, config=get_config())

@app.route('/checkout', methods=['POST'])
def checkout():
    if not session.get('carrito'):
        return redirect(url_for('carrito'))
    
    observaciones = request.form.get('observaciones', '')
    
    db = get_db()
    config = get_config()
    
    # Cliente sin registro: si esta logueado se usa su cuenta; si no,
    # todos los pedidos de invitados cuelgan de una cuenta compartida.
    cliente = None
    if session.get('usuario_id') and session.get('rol') == 'cliente':
        cliente = db.execute("SELECT * FROM usuarios WHERE id = ?", (session['usuario_id'],)).fetchone()
    if not cliente:
        cliente = db.execute("SELECT * FROM usuarios WHERE email = ? LIMIT 1", ('invitado@puntico.local',)).fetchone()
        if not cliente:
            db.execute("INSERT INTO usuarios (email, password, nombre, telefono, direccion, rol) VALUES (?, ?, ?, ?, ?, 'cliente')",
                ('invitado@puntico.local', hash_password(hashlib.md5(os.urandom(16)).hexdigest()), 'Cliente WhatsApp', '', ''))
            cliente = db.execute("SELECT * FROM usuarios WHERE email = ?", ('invitado@puntico.local',)).fetchone()
    
    from collections import defaultdict
    by_vendedor = defaultdict(list)
    for item in session['carrito']:
        by_vendedor[item['vendedor_id']].append(item)
    
    segmentos = []
    for vendedor_id, items in by_vendedor.items():
        total = sum(i['precio'] * i['cantidad'] for i in items)
        
        vendedor = db.execute("SELECT nombre, tienda_nombre, telefono, precioDelivery, entregaGratis FROM usuarios WHERE id = ?", (vendedor_id,)).fetchone()
        delivery = 0
        if vendedor and vendedor['precioDelivery'] > 0:
            if vendedor['entregaGratis'] > 0 and total >= vendedor['entregaGratis']:
                delivery = 0
            else:
                delivery = vendedor['precioDelivery']
        
        cursor = db.execute("INSERT INTO pedidos (cliente_id, vendedor_id, total, delivery, observaciones) VALUES (?, ?, ?, ?, ?)",
            (cliente['id'], vendedor_id, total, delivery, observaciones))
        pedido_id = cursor.lastrowid
        
        lineas = []
        for item in items:
            db.execute("INSERT INTO pedido_items (pedido_id, producto_id, cantidad, precio) VALUES (?, ?, ?, ?)",
                (pedido_id, item['producto_id'], item['cantidad'], item['precio']))
            db.execute("UPDATE productos SET stock = stock - ? WHERE id = ?", (item['cantidad'], item['producto_id']))
            lineas.append(f"- {item['cantidad']}x {item['nombre']} (${item['precio'] * item['cantidad']:.2f})")
        
        total_segmento = total + delivery
        nombre_tienda = (vendedor['tienda_nombre'] if vendedor and vendedor['tienda_nombre'] else (vendedor['nombre'] if vendedor else 'Vendedor'))
        msg = f"Hola {nombre_tienda}! Pedido #{pedido_id} en {config['nombre_sitio']}:\n"
        msg += "\n".join(lineas)
        msg += f"\n\nEnvío: ${delivery:.2f}\nTotal: ${total_segmento:.2f}"
        if observaciones:
            msg += f"\nObservaciones: {observaciones}"
        msg += "\n(Referencia del pedido: #" + str(pedido_id) + ")"
        
        wa_tel = ''.join(ch for ch in (vendedor['telefono'] or '') if ch.isdigit()) if vendedor else ''
        segmentos.append({
            'pedido_id': pedido_id,
            'vendedor': nombre_tienda,
            'total': total_segmento,
            'wa_link': f"https://wa.me/{wa_tel}?text={url_quote(msg)}" if len(wa_tel) >= 8 else None,
        })
    
    db.commit()
    session['carrito'] = []
    
    # Datos para la pantalla de confirmacion y retorno automatico a la tienda.
    tienda_slug = None
    tienda_nombre = None
    if len(segmentos) == 1:
        vinfo = db.execute("SELECT slug, nombre FROM usuarios WHERE id = ?", (list(by_vendedor.keys())[0],)).fetchone()
        if vinfo:
            tienda_slug = vinfo['slug']
            tienda_nombre = vinfo['nombre']
    session['ultimo_pedido'] = {'segmentos': segmentos, 'tienda_slug': tienda_slug, 'tienda_nombre': tienda_nombre}
    session.modified = True
    
    # Flujo AJAX desde el carrito: el mismo toque del usuario abre WhatsApp
    # (sin bloqueo de popups) y la pagina vuelve sola a la tienda.
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({
            'ok': True,
            'wa_url': segmentos[0]['wa_link'] if len(segmentos) == 1 else None,
            'multi': [s for s in segmentos] if len(segmentos) > 1 else [],
            'tienda_slug': tienda_slug,
            'tienda_nombre': tienda_nombre,
        })
    
    return redirect(url_for('pedido_confirmado'))

@app.route('/pedido-confirmado')
def pedido_confirmado():
    datos = session.pop('ultimo_pedido', None)
    session.modified = True
    if not datos:
        return redirect(url_for('home'))
    return render_template('pedido_confirmado.html',
        segmentos=datos.get('segmentos', []),
        tienda_slug=datos.get('tienda_slug'),
        tienda_nombre=datos.get('tienda_nombre'),
        config=get_config())

@app.route('/mis_pedidos')
@login_required
def mis_pedidos():
    db = get_db()
    pedidos = db.execute("""
        SELECT pe.*, u.nombre as vendedor_nombre 
        FROM pedidos pe 
        JOIN usuarios u ON pe.vendedor_id = u.id
        WHERE pe.cliente_id = ?
        ORDER BY pe.id DESC
    """, (session['usuario_id'],)).fetchall()
    
    return render_template('mis_pedidos.html', pedidos=pedidos, config=get_config())

@app.route('/panel')
@login_required
def panel():
    if session.get('rol') == 'admin':
        return redirect(url_for('admin_panel'))
    elif session.get('rol') == 'vendedor':
        return redirect(url_for('vendedor_panel'))
    return redirect(url_for('home'))

@app.route('/admin')
@rol_required(['admin'])
def admin_panel():
    db = get_db()
    
    usuarios = db.execute("SELECT * FROM usuarios WHERE rol != 'admin' ORDER BY id DESC").fetchall()
    productos = db.execute("SELECT p.*, u.nombre as vendedor_nombre FROM productos p JOIN usuarios u ON p.vendedor_id = u.id ORDER BY p.id DESC").fetchall()
    pedidos = db.execute("SELECT pe.*, u.nombre as vendedor_nombre, c.nombre as cliente_nombre FROM pedidos pe JOIN usuarios u ON pe.vendedor_id = u.id JOIN usuarios c ON pe.cliente_id = c.id ORDER BY pe.id DESC").fetchall()
    categorias = db.execute("""
    SELECT c.nombre as categoria, c.subcategoria, 
           (SELECT COUNT(*) FROM productos WHERE categoria = c.nombre AND (subcategoria = c.subcategoria OR c.subcategoria IS NULL)) as productos_count 
    FROM categorias c 
    ORDER BY c.nombre, c.subcategoria
    """).fetchall()
    
    config = get_config()
    
    stats = {
        'usuarios': db.execute("SELECT COUNT(*) as c FROM usuarios").fetchone()['c'],
        'vendedores': db.execute("SELECT COUNT(*) as c FROM usuarios WHERE rol = 'vendedor'").fetchone()['c'],
        'clientes': db.execute("SELECT COUNT(*) as c FROM usuarios WHERE rol = 'cliente'").fetchone()['c'],
        'productos': db.execute("SELECT COUNT(*) as c FROM productos").fetchone()['c'],
        'pedidos': db.execute("SELECT COUNT(*) as c FROM pedidos").fetchone()['c'],
        'pendientes': db.execute("SELECT COUNT(*) as c FROM pedidos WHERE estado = 'pendiente'").fetchone()['c'],
        'categorias': db.execute("SELECT COUNT(*) as c FROM categorias").fetchone()['c'],
    }
    
    categorias_json = [dict(c) for c in categorias]
    
    
    return render_template('admin.html', usuarios=usuarios, productos=productos, pedidos=pedidos, categorias=categorias_json, stats=stats, config=config)

@app.route('/admin/usuario/<int:user_id>/editar', methods=['POST'])
@rol_required(['admin'])
def admin_editar_usuario(user_id):
    db = get_db()
    nombre = request.form.get('nombre')
    email = request.form.get('email')
    telefono = request.form.get('telefono')
    direccion = request.form.get('direccion')
    rol = request.form.get('rol')
    activo = 1 if request.form.get('activo') else 0
    
    db.execute("UPDATE usuarios SET nombre = ?, email = ?, telefono = ?, direccion = ?, rol = ?, activo = ? WHERE id = ?",
        (nombre, email, telefono, direccion, rol, activo, user_id))
    password_nueva = request.form.get('password_nueva', '').strip()
    if password_nueva:
        db.execute("UPDATE usuarios SET password = ? WHERE id = ?", (hash_password(password_nueva), user_id))
    if rol == 'vendedor':
        user = db.execute("SELECT slug, tienda_nombre FROM usuarios WHERE id = ?", (user_id,)).fetchone()
        if user and not user['slug']:
            slug = generar_slug(db, user['tienda_nombre'] or nombre, exclude_id=user_id)
            db.execute("UPDATE usuarios SET slug = ? WHERE id = ?", (slug, user_id))
        if user and not user['tienda_nombre']:
            db.execute("UPDATE usuarios SET tienda_nombre = ? WHERE id = ?", (nombre, user_id))
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/usuario/crear', methods=['POST'])
@rol_required(['admin'])
def admin_crear_usuario():
    db = get_db()
    nombre = request.form.get('nombre')
    email = request.form.get('email').lower()
    password = request.form.get('password')
    telefono = request.form.get('telefono')
    direccion = request.form.get('direccion')
    rol = request.form.get('rol', 'cliente')
    
    if db.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone():
        return redirect(url_for('admin_panel'))
    
    db.execute("INSERT INTO usuarios (email, password, nombre, telefono, direccion, rol, tienda_nombre) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (email, hash_password(password), nombre, telefono, direccion, rol, nombre if rol == 'vendedor' else None))
    if rol == 'vendedor':
        slug = generar_slug(db, nombre)
        db.execute("UPDATE usuarios SET slug = ? WHERE email = ?", (slug, email))
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/usuario/<int:user_id>/estado')
@rol_required(['admin'])
def admin_cambiar_estado_usuario(user_id):
    db = get_db()
    user = db.execute("SELECT activo FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    if user:
        nuevo_estado = 0 if user['activo'] else 1
        db.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo_estado, user_id))
        db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/usuario/<int:user_id>/eliminar')
@rol_required(['admin'])
def admin_eliminar_usuario(user_id):
    db = get_db()
    productos = db.execute("SELECT id FROM productos WHERE vendedor_id = ?", (user_id,)).fetchall()
    for p in productos:
        db.execute("DELETE FROM pedido_items WHERE producto_id = ?", (p['id'],))
    db.execute("DELETE FROM productos WHERE vendedor_id = ?", (user_id,))
    db.execute("DELETE FROM pedido_items WHERE pedido_id IN (SELECT id FROM pedidos WHERE cliente_id = ? OR vendedor_id = ?)", (user_id, user_id))
    db.execute("DELETE FROM pedidos WHERE cliente_id = ? OR vendedor_id = ?", (user_id, user_id))
    db.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/producto/<int:prod_id>/toggle')
@rol_required(['admin'])
def admin_toggle_producto(prod_id):
    db = get_db()
    db.execute("UPDATE productos SET activo = CASE WHEN activo = 1 THEN 0 ELSE 1 END WHERE id = ?", (prod_id,))
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/producto/<int:prod_id>/eliminar')
@rol_required(['admin'])
def admin_eliminar_producto(prod_id):
    db = get_db()
    db.execute("DELETE FROM pedido_items WHERE producto_id = ?", (prod_id,))
    db.execute("DELETE FROM productos WHERE id = ?", (prod_id,))
    db.commit()
    return redirect(url_for('admin_panel'))

def _ajustar_stock_pedido(db, pedido_id, devolver):
    """Devuelve (True) o vuelve a descontar (False) el stock de los items de un pedido."""
    items = db.execute("SELECT producto_id, cantidad FROM pedido_items WHERE pedido_id = ?", (pedido_id,)).fetchall()
    for it in items:
        if devolver:
            db.execute("UPDATE productos SET stock = stock + ? WHERE id = ?", (it['cantidad'], it['producto_id']))
        else:
            db.execute("UPDATE productos SET stock = stock - ? WHERE id = ?", (it['cantidad'], it['producto_id']))

def _auto_cancelar_pedidos_pendientes():
    """Cancela automaticamente pedidos pendientes con mas de 24h y devuelve el stock."""
    try:
        if DB_BACKEND == 'mysql':
            conn = _mysql_connect()
            cur = conn.cursor()
            cur.execute("""
                SELECT p.id FROM pedidos p
                WHERE p.estado = 'pendiente'
                  AND TIMESTAMPDIFF(HOUR, p.fecha_pedido, NOW()) >= 24
            """)
            pedidos = cur.fetchall()
            for row in pedidos:
                pid = row[0] if isinstance(row, (list, tuple)) else row['id'] if isinstance(row, dict) else row.id
                cur.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = %s AND estado = 'pendiente'", (pid,))
                cur.execute("""
                    SELECT producto_id, cantidad FROM pedido_items WHERE pedido_id = %s
                """, (pid,))
                items = cur.fetchall()
                for it in items:
                    prod_id = it[0] if isinstance(it, (list, tuple)) else it['producto_id'] if isinstance(it, dict) else it.producto_id
                    cant = it[1] if isinstance(it, (list, tuple)) else it['cantidad'] if isinstance(it, dict) else it.cantidad
                    cur.execute("UPDATE productos SET stock = stock + %s WHERE id = %s", (cant, prod_id))
            conn.commit()
            cur.close()
            conn.close()
        else:
            import sqlite3 as _sqlite
            conn = _sqlite.connect(DB_NAME)
            conn.row_factory = _sqlite.Row
            pedidos = conn.execute("""
                SELECT id FROM pedidos
                WHERE estado = 'pendiente'
                  AND (julianday('now') - julianday(fecha_pedido)) * 24 >= 24
            """).fetchall()
            for p in pedidos:
                pid = p['id']
                conn.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ? AND estado = 'pendiente'", (pid,))
                items = conn.execute("SELECT producto_id, cantidad FROM pedido_items WHERE pedido_id = ?", (pid,)).fetchall()
                for it in items:
                    conn.execute("UPDATE productos SET stock = stock + ? WHERE id = ?", (it['cantidad'], it['producto_id']))
            conn.commit()
            conn.close()
        return len(pedidos) if pedidos else 0
    except Exception as e:
        print(f"[auto-cancel] Error: {e}")
        return 0

@app.route('/admin/pedido/<int:pedido_id>/<string:estado>')
@rol_required(['admin'])
def admin_cambiar_pedido(pedido_id, estado):
    db = get_db()
    pedido = db.execute("SELECT estado FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if pedido and pedido['estado'] != estado:
        if estado == 'cancelado':
            _ajustar_stock_pedido(db, pedido_id, True)
        elif pedido['estado'] == 'cancelado':
            _ajustar_stock_pedido(db, pedido_id, False)
    db.execute("UPDATE pedidos SET estado = ? WHERE id = ?", (estado, pedido_id))
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/pedido/<int:pedido_id>/eliminar')
@rol_required(['admin'])
def admin_eliminar_pedido(pedido_id):
    db = get_db()
    db.execute("DELETE FROM pedido_items WHERE pedido_id = ?", (pedido_id,))
    db.execute("DELETE FROM pedidos WHERE id = ?", (pedido_id,))
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/config', methods=['POST'])
@rol_required(['admin'])
def admin_config():
    db = get_db()
    is_mysql = DB_BACKEND == 'mysql'
    for key in request.form:
        val = request.form[key]
        if is_mysql:
            db.execute("REPLACE INTO config (clave, valor) VALUES (%s, %s)", (key, val))
        else:
            db.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES (?, ?)", (key, val))
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/categoria', methods=['POST'])
@rol_required(['admin'])
def admin_agregar_categoria():
    nombre = request.form.get('nombre')
    subcategoria = request.form.get('subcategoria') or None
    db = get_db()
    try:
        db.execute("INSERT INTO categorias (nombre, subcategoria) VALUES (?, ?)", (nombre, subcategoria))
        db.commit()
    except:
        pass
    return redirect(url_for('admin_panel'))

@app.route('/admin/categoria/<string:cat_id>/eliminar')
@rol_required(['admin'])
def admin_eliminar_categoria(cat_id):
    db = get_db()
    sub_id = request.args.get('sub', '')
    
    # Si tiene sub o es "_none_", eliminar solo esa sub
    if sub_id and sub_id != '':
        if sub_id == '_none_':
            db.execute("DELETE FROM categorias WHERE nombre = ? AND (subcategoria IS NULL OR subcategoria = '')", (cat_id,))
        else:
            db.execute("DELETE FROM categorias WHERE nombre = ? AND subcategoria = ?", (cat_id, sub_id))
    else:
        # Eliminar todo (categoria + subcategorias)
        db.execute("DELETE FROM categorias WHERE nombre = ?", (cat_id,))
    
    db.commit()
    return redirect(url_for('admin_panel'))

@app.route('/admin/pagos')
@rol_required(['admin'])
def admin_pagos():
    db = get_db()
    vendedores = db.execute("""
        SELECT u.*, 
            (u.pago_hasta IS NOT NULL) as tiene_pago,
            (SELECT COUNT(*) FROM productos WHERE vendedor_id = u.id AND activo = 1) as num_productos,
            (SELECT COUNT(*) FROM pedidos WHERE vendedor_id = u.id AND estado IN ('confirmado','entregado')) as num_pedidos
        FROM usuarios u 
        WHERE u.rol = 'vendedor' 
        ORDER BY u.es_vip DESC, u.pago_hasta ASC, u.nombre
    """).fetchall()
    from datetime import date
    hoy = date.today()
    today_int = int(hoy.strftime('%Y%m%d'))
    return render_template('admin_pagos.html', vendedores=vendedores, config=get_config(),
                           today_int=today_int, now_date=hoy)

def _parsed_fecha(texto):
    """Convierte pago_hasta (str con/sin hora, datetime o date) a date, o None."""
    from datetime import date, datetime
    if not texto:
        return None
    if isinstance(texto, datetime):
        return texto.date()
    if isinstance(texto, date):
        return texto
    s = str(texto).strip()
    try:
        return datetime.strptime(s[:10], '%Y-%m-%d').date()
    except Exception:
        return None

@app.route('/admin/pago/<int:user_id>/set_fecha', methods=['POST'])
@rol_required(['admin'])
def admin_set_fecha_pago(user_id):
    """Fija la fecha de vencimiento exacta elegida en el calendario (o la deja free si viene vacia)."""
    db = get_db()
    vendedor = db.execute("SELECT id FROM usuarios WHERE id = ? AND rol = 'vendedor'", (user_id,)).fetchone()
    if not vendedor:
        return redirect(url_for('admin_pagos'))
    fecha_str = request.form.get('pago_hasta', '').strip()
    if fecha_str:
        f = _parsed_fecha(fecha_str)
        if f is None:
            return redirect(url_for('admin_pagos'))
        db.execute("UPDATE usuarios SET pago_hasta = ?, pago_activo = 1 WHERE id = ?", (f.isoformat(), user_id))
    else:
        db.execute("UPDATE usuarios SET pago_hasta = NULL, pago_activo = 0 WHERE id = ?", (user_id,))
    db.commit()
    return redirect(url_for('admin_pagos'))

@app.route('/admin/pago/<user_id>/toggle_vip')
@rol_required(['admin'])
def admin_toggle_vip(user_id):
    db = get_db()
    v = db.execute("SELECT es_vip FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    if v:
        nuevo = 0 if v['es_vip'] else 1
        db.execute("UPDATE usuarios SET es_vip = ? WHERE id = ?", (nuevo, user_id))
        db.commit()
    return redirect(url_for('admin_pagos'))

@app.route('/vendedor')
@rol_required(['vendedor'])
def vendedor_panel():
    db = get_db()
    
    productos = db.execute("SELECT * FROM productos WHERE vendedor_id = ? ORDER BY id DESC", (session['usuario_id'],)).fetchall()
    pedidos = db.execute("""
        SELECT pe.*, u.nombre as cliente_nombre, u.direccion as cliente_direccion 
        FROM pedidos pe 
        JOIN usuarios u ON pe.cliente_id = u.id
        WHERE pe.vendedor_id = ?
        ORDER BY pe.id DESC
    """, (session['usuario_id'],)).fetchall()
    
    usuario = db.execute("SELECT * FROM usuarios WHERE id = ?", (session['usuario_id'],)).fetchone()
    
    categorias = db.execute("SELECT nombre as categoria, subcategoria FROM categorias ORDER BY nombre, subcategoria").fetchall()
    cats_dict = {}
    for c in categorias:
        key = c['categoria']
        if key not in cats_dict:
            cats_dict[key] = []
        if c['subcategoria']:
            cats_dict[key].append(c['subcategoria'])
    
    mi_url = request.url_root.rstrip('/') + '/' + (usuario['slug'] or '')
    from datetime import date
    hoy = date.today()
    vencimiento = {}
    if usuario['pago_hasta']:
        f = _parsed_fecha(usuario['pago_hasta'])
        if f:
            dias = (f - hoy).days
            vencimiento = {
                'fecha': f.isoformat(),
                'dias': dias,
                'vencida': dias < 0,
                'por_vencer': 0 <= dias <= 7,
                'estado': 'vencida' if dias < 0 else ('por_vencer' if dias <= 7 else 'activa'),
            }
    else:
        vencimiento = {'estado': 'free', 'fecha': None, 'dias': None, 'vencida': False, 'por_vencer': False}
    return render_template('vendedor.html', productos=productos, pedidos=pedidos, usuario=usuario, categorias=categorias, cats_dict=cats_dict, mi_url=mi_url, vencimiento=vencimiento)

@app.route('/vendedor/producto/agregar', methods=['POST'])
@rol_required(['vendedor'])
def vendedor_agregar_producto():
    db = get_db()
    cat_nombre = (request.form.get('categoria_nombre', '') or '').strip()
    cat_sub = (request.form.get('categoria_sub', '') or '').strip()
    
    if not cat_nombre:
        return redirect(url_for('vendedor_panel'))
    
    # Auto-crear la categoría si no existe
    is_mysql = DB_BACKEND == 'mysql'
    try:
        if is_mysql:
            db.execute("INSERT IGNORE INTO categorias (nombre, subcategoria) VALUES (%s, %s)", (cat_nombre, cat_sub or None))
        else:
            db.execute("INSERT OR IGNORE INTO categorias (nombre, subcategoria) VALUES (?, ?)", (cat_nombre, cat_sub or None))
    except Exception:
        pass
    
    categoria_display = f"{cat_nombre} - {cat_sub}" if cat_sub else cat_nombre
    
    db.execute("""INSERT INTO productos (vendedor_id, nombre, descripcion, precio, stock, categoria, imagen, fotos) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (session['usuario_id'], request.form.get('nombre', ''), request.form.get('descripcion', ''), 
         float(request.form.get('precio') or 0), int(request.form.get('stock') or 0), 
         cat_nombre, request.form.get('imagen', ''), request.form.get('fotos', '')))
    db.commit()
    return redirect(url_for('vendedor_panel'))

@app.route('/vendedor/producto/<int:prod_id>/editar', methods=['POST'])
@rol_required(['vendedor'])
def vendedor_editar_producto(prod_id):
    db = get_db()
    prod = db.execute("SELECT * FROM productos WHERE id = ? AND vendedor_id = ?", (prod_id, session['usuario_id'])).fetchone()
    if not prod:
        return redirect(url_for('vendedor_panel'))

    cat_nombre = (request.form.get('categoria_nombre', '') or '').strip()
    cat_sub = (request.form.get('categoria_sub', '') or '').strip()
    if cat_nombre:
        is_mysql = DB_BACKEND == 'mysql'
        try:
            if is_mysql:
                db.execute("INSERT IGNORE INTO categorias (nombre, subcategoria) VALUES (%s, %s)", (cat_nombre, cat_sub or None))
            else:
                db.execute("INSERT OR IGNORE INTO categorias (nombre, subcategoria) VALUES (?, ?)", (cat_nombre, cat_sub or None))
        except Exception:
            pass
    else:
        cat_nombre = prod['categoria']

    db.execute("""UPDATE productos SET nombre = ?, descripcion = ?, precio = ?, stock = ?,
            categoria = ?, imagen = ?, fotos = ? WHERE id = ? AND vendedor_id = ?""",
        (request.form.get('nombre', '') or prod['nombre'],
         request.form.get('descripcion', '') or prod['descripcion'],
         float(request.form.get('precio') if request.form.get('precio') else prod['precio']),
         int(request.form.get('stock') if request.form.get('stock') is not None and request.form.get('stock') != '' else prod['stock']),
         cat_nombre,
         request.form.get('imagen', '') or prod['imagen'],
         request.form.get('fotos', '') or prod['fotos'],
         prod_id, session['usuario_id']))
    db.commit()
    return redirect(url_for('vendedor_panel'))

@app.route('/vendedor/producto/<int:prod_id>/toggle')
@rol_required(['vendedor'])
def vendedor_toggle_producto(prod_id):
    db = get_db()
    db.execute("UPDATE productos SET activo = CASE WHEN activo = 1 THEN 0 ELSE 1 END WHERE id = ? AND vendedor_id = ?", 
        (prod_id, session['usuario_id']))
    db.commit()
    return redirect(url_for('vendedor_panel'))

@app.route('/vendedor/producto/<int:prod_id>/stock', methods=['POST'])
@rol_required(['vendedor'])
def vendedor_actualizar_stock(prod_id):
    stock = int(request.form.get('stock') or 0)
    db = get_db()
    db.execute("UPDATE productos SET stock = ? WHERE id = ? AND vendedor_id = ?", 
        (stock, prod_id, session['usuario_id']))
    db.commit()
    return redirect(url_for('vendedor_panel'))

@app.route('/vendedor/producto/<int:prod_id>/eliminar')
@rol_required(['vendedor'])
def vendedor_eliminar_producto(prod_id):
    db = get_db()
    db.execute("DELETE FROM pedido_items WHERE producto_id = ?", (prod_id,))
    db.execute("DELETE FROM productos WHERE id = ? AND vendedor_id = ?", (prod_id, session['usuario_id']))
    db.commit()
    return redirect(url_for('vendedor_panel'))

@app.route('/vendedor/config', methods=['POST'])
@rol_required(['vendedor'])
def vendedor_config():
    db = get_db()
    uid = session['usuario_id']
    accion = request.form.get('accion', '')
    
    if accion == 'tienda':
        # Solo toca datos de la tienda: nombre comercial y foto/logo.
        logo_path = None
        f = request.files.get('logo_file')
        if f and f.filename and allowed_file(f.filename):
            ext = f.filename.rsplit('.', 1)[1].lower()
            fname = f"t{uid}_{hashlib.md5(os.urandom(8)).hexdigest()[:8]}.{ext}"
            f.save(os.path.join(IMG_FOLDER, fname))
            logo_path = f"/img/{fname}"
        sets = ["tienda_nombre = ?"]
        params = [(request.form.get('tienda_nombre', '') or '').strip()]
        nueva_img = logo_path or request.form.get('imagen_tienda', '').strip()
        if nueva_img:
            sets.append("imagen_tienda = ?")
            params.append(nueva_img)
        params.append(uid)
        db.execute(f"UPDATE usuarios SET {', '.join(sets)} WHERE id = ?", tuple(params))
    elif accion == 'delivery':
        # Solo toca configuracion de delivery.
        db.execute("UPDATE usuarios SET precioDelivery = ?, entregaGratis = ? WHERE id = ?",
            (int(request.form.get('precioDelivery') or 0), int(request.form.get('entregaGratis') or 0), uid))
    else:
        # Solo toca el perfil personal (nombre, telefono, direccion).
        db.execute("UPDATE usuarios SET nombre = ?, telefono = ?, direccion = ? WHERE id = ?",
            (request.form.get('nombre', ''), request.form.get('telefono', ''), request.form.get('direccion', ''), uid))
    db.commit()
    return redirect(url_for('vendedor_panel'))

@app.route('/vendedor/pedido/<int:pedido_id>/<string:estado>')
@rol_required(['vendedor'])
def vendedor_cambiar_pedido(pedido_id, estado):
    db = get_db()
    pedido = db.execute("SELECT estado FROM pedidos WHERE id = ? AND vendedor_id = ?", (pedido_id, session['usuario_id'])).fetchone()
    if pedido and pedido['estado'] != estado:
        if estado == 'cancelado':
            _ajustar_stock_pedido(db, pedido_id, True)
        elif pedido['estado'] == 'cancelado':
            _ajustar_stock_pedido(db, pedido_id, False)
    db.execute("UPDATE pedidos SET estado = ? WHERE id = ? AND vendedor_id = ?", 
        (estado, pedido_id, session['usuario_id']))
    db.commit()
    return redirect(url_for('vendedor_panel'))

@app.route('/categorias')
def categorias():
    db = get_db()
    cats = db.execute("SELECT * FROM categorias").fetchall()
    return jsonify([dict(row) for row in cats])

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/img/<path:filename>')
def img_static(filename):
    return send_from_directory(IMG_FOLDER, filename)

@app.route('/admin/upload', methods=['POST'])
@rol_required(['admin'])
def admin_upload():
    if 'imagen' not in request.files:
        return redirect(url_for('admin_panel'))
    file = request.files['imagen']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(IMG_FOLDER, filename))
    return redirect(url_for('admin_panel'))

@app.route('/vendedor/upload', methods=['POST'])
@rol_required(['vendedor'])
def vendedor_upload():
    if 'imagen' not in request.files:
        return redirect(url_for('vendedor_panel'))
    file = request.files['imagen']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(IMG_FOLDER, filename))
    return redirect(url_for('vendedor_panel'))

@app.route('/uploads')
@login_required
def list_uploads():
    if not os.path.exists(IMG_FOLDER):
        return jsonify([])
    files = [f for f in os.listdir(IMG_FOLDER) if allowed_file(f)]
    return jsonify(files)

@app.route('/upload', methods=['POST'])
@login_required
def upload_img():
    if 'file' in request.files:
        file = request.files['file']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(IMG_FOLDER, filename))
            return jsonify({'success': True, 'path': f'/img/{filename}'})
    
    allow_remote_upload = os.environ.get('ALLOW_REMOTE_IMAGE_UPLOAD', '0' if IS_PRODUCTION else '1') == '1'
    if 'url' in request.form and allow_remote_upload:
        import urllib.request
        url = request.form.get('url', '')
        if url.startswith('http'):
            ext = url.rsplit('.', 1)[1].split('?')[0] if '.' in url else 'jpg'
            if ext.lower() not in ALLOWED_EXTENSIONS:
                ext = 'jpg'
            filename = f'{hashlib.md5(url.encode()).hexdigest()[:12]}.{ext}'
            filepath = os.path.join(IMG_FOLDER, filename)
            try:
                urllib.request.urlretrieve(url, filepath)
                return jsonify({'success': True, 'path': f'/img/{filename}'})
            except:
                return jsonify({'success': False, 'error': 'No se pudo descargar'})
    
    if 'url' in request.form and not allow_remote_upload:
        return jsonify({'success': False, 'error': 'Carga por URL deshabilitada en produccion'})
    
    return jsonify({'success': False})

def _migrar_slugs():
    """Anade columnas nuevas a instalaciones existentes: slug, tienda_nombre, imagen_tienda."""
    try:
        if DB_BACKEND == 'mysql':
            conn = _mysql_connect()
            cur = conn.cursor()
            for ddl in (
                "ALTER TABLE usuarios ADD COLUMN slug VARCHAR(190)",
                "ALTER TABLE usuarios ADD COLUMN tienda_nombre VARCHAR(190)",
                "ALTER TABLE usuarios ADD COLUMN imagen_tienda TEXT",
                "CREATE UNIQUE INDEX idx_usuarios_slug ON usuarios(slug)",
            ):
                try:
                    cur.execute(ddl)
                    conn.commit()
                except Exception:
                    pass
            ad = _MySQLConn(conn)
        else:
            conn = sqlite3.connect(DB_NAME)
            conn.row_factory = sqlite3.Row
            cols = [r[1] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
            if 'slug' not in cols:
                conn.execute("ALTER TABLE usuarios ADD COLUMN slug TEXT")
            if 'tienda_nombre' not in cols:
                conn.execute("ALTER TABLE usuarios ADD COLUMN tienda_nombre TEXT")
            if 'imagen_tienda' not in cols:
                conn.execute("ALTER TABLE usuarios ADD COLUMN imagen_tienda TEXT")
            conn.commit()
            ad = conn
        pendientes = ad.execute("SELECT id, nombre FROM usuarios WHERE rol = 'vendedor' AND (slug IS NULL OR slug = '')").fetchall()
        for r in pendientes:
            slug = generar_slug(ad, r['nombre'], exclude_id=r['id'])
            ad.execute("UPDATE usuarios SET slug = ? WHERE id = ?", (slug, r['id']))
        sin_nombre = ad.execute("SELECT id, nombre FROM usuarios WHERE rol = 'vendedor' AND (tienda_nombre IS NULL OR tienda_nombre = '')").fetchall()
        for r in sin_nombre:
            ad.execute("UPDATE usuarios SET tienda_nombre = ? WHERE id = ?", (r['nombre'], r['id']))
        if DB_BACKEND == 'mysql':
            conn.commit()
            conn.close()
        else:
            conn.commit()
            conn.close()
    except Exception as e:
        print('Migracion de slugs:', e)

_migrar_slugs()

def _migrar_fotos_producto():
    """Anade columna 'fotos' a la tabla productos para multiples imagenes."""
    try:
        if DB_BACKEND == 'mysql':
            conn = _mysql_connect()
            cur = conn.cursor()
            try:
                cur.execute("ALTER TABLE productos ADD COLUMN fotos TEXT")
                conn.commit()
            except Exception:
                pass
            conn.close()
        else:
            conn = sqlite3.connect(DB_NAME)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(productos)").fetchall()]
            if 'fotos' not in cols:
                conn.execute("ALTER TABLE productos ADD COLUMN fotos TEXT")
                conn.commit()
            conn.close()
    except Exception as e:
        print('Migracion fotos producto:', e)

_migrar_fotos_producto()

def _migrar_pago_vip():
    """Anade columnas pago_hasta, pago_activo, es_vip a usuarios."""
    try:
        if DB_BACKEND == 'mysql':
            conn = _mysql_connect()
            cur = conn.cursor()
            for ddl in (
                "ALTER TABLE usuarios ADD COLUMN pago_hasta DATE DEFAULT NULL",
                "ALTER TABLE usuarios ADD COLUMN pago_activo INT DEFAULT 1",
                "ALTER TABLE usuarios ADD COLUMN es_vip INT DEFAULT 0",
            ):
                try:
                    cur.execute(ddl)
                    conn.commit()
                except Exception:
                    pass
            conn.close()
        else:
            conn = sqlite3.connect(DB_NAME)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
            if 'pago_hasta' not in cols:
                conn.execute("ALTER TABLE usuarios ADD COLUMN pago_hasta DATE DEFAULT NULL")
            if 'pago_activo' not in cols:
                conn.execute("ALTER TABLE usuarios ADD COLUMN pago_activo INTEGER DEFAULT 1")
            if 'es_vip' not in cols:
                conn.execute("ALTER TABLE usuarios ADD COLUMN es_vip INTEGER DEFAULT 0")
            conn.commit()
            conn.close()
    except Exception as e:
        print('Migracion pago/vip:', e)

_migrar_pago_vip()

# Inicializa la BD (idempotente: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE).
init_db()
_auto_cancelar_pedidos_pendientes()
_ultima_revision_pedidos = __import__('time').time()

@app.before_request
def _check_pedidos_expirados():
    """Cada 30 minutos cancela pedidos pendientes con mas de 24h."""
    global _ultima_revision_pedidos
    import time
    ahora = time.time()
    if ahora - _ultima_revision_pedidos > 1800:
        _ultima_revision_pedidos = ahora
        _auto_cancelar_pedidos_pendientes()

@app.route('/<slug>')
def tienda_publica(slug):
    """Tienda publica de cada vendedor: elpuntico.wasmer.app/la-flor"""
    if slug in ('favicon.ico', 'robots.txt'):
        abort(404)
    db = get_db()
    vendedor = db.execute("SELECT * FROM usuarios WHERE slug = ? AND rol = 'vendedor' AND activo = 1", (slug,)).fetchone()
    if not vendedor:
        abort(404)
    productos = db.execute("SELECT * FROM productos WHERE vendedor_id = ? AND activo = 1 AND stock > 0 ORDER BY id DESC", (vendedor['id'],)).fetchall()
    return render_template('tienda_publica.html', vendedor=vendedor, productos=productos, config=get_config())

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 80))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    print("=" * 40)
    print("  MiTienda - Servidor Local")
    print("=" * 40)
    print("\nUsuario admin por defecto:")
    print("  Email: admin@elpuntico.com")
    print("  Password: admin")
    print("\nDesde OTROS dispositivos en tu red:")
    print(f"  http://[TU-IP-REAL]:{port}")
    print("\nDesde ESTE dispositivo:")
    print(f"  http://localhost:{port}")
    print("=" * 40)
    app.run(debug=debug, host='0.0.0.0', port=port)
