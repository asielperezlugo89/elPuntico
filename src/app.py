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

    # Sin categorias por defecto - el admin las agrega

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
    buscar = request.args.get('buscar', '')
    
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
    
    return render_template('home.html', tiendas=tiendas, buscar=buscar, config=config)

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
    for key in request.form:
        val = request.form[key]
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
    
    mi_url = request.url_root.rstrip('/') + '/' + (usuario['slug'] or '')
    return render_template('vendedor.html', productos=productos, pedidos=pedidos, usuario=usuario, categorias=categorias, mi_url=mi_url)

@app.route('/vendedor/producto/agregar', methods=['POST'])
@rol_required(['vendedor'])
def vendedor_agregar_producto():
    db = get_db()
    cat_input = request.form.get('categoria', '')
    
    categoria = cat_input
    if ' - ' in cat_input:
        parts = cat_input.split(' - ')
        categoria = parts[0].strip()
    
    db.execute("""INSERT INTO productos (vendedor_id, nombre, descripcion, precio, stock, categoria, imagen) 
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session['usuario_id'], request.form.get('nombre', ''), request.form.get('descripcion', ''), 
         float(request.form.get('precio') or 0), int(request.form.get('stock') or 0), 
         categoria, request.form.get('imagen', '')))
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

# Inicializa la BD tambien cuando el servidor lo importa (p.ej. `flask --app app run`).
# Es idempotente: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE.
init_db()

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

def _seed_categorias():
    """Si la tabla categorias esta vacia, la puebla con categorias y subcategorias por defecto."""
    CATEGORIAS = {
        "Alimentos y Bebidas": [
            "Carnes y Aves", "Frutas y Verduras", "Lacteos y Huevos", "Panaderia y Reposteria",
            "Bebidas alcoholicas", "Bebidas No alcoholicas", "Snacks y Dulces", "Enlatados y Conservas",
            "Cereales y Granos", "Condimentos y Especias", "Congelados", "Productos Organicos",
            "Aceites y Vinagres", "Pastas y Arroces", "Cafe y Te",
        ],
        "Electronica": [
            "Celulares y Accesorios", "Computadoras y Laptops", "Tablets", "Audio y Video",
            "Camaras y Fotografia", "Videojuegos", "Smart Home", "Wearables y Relojes Inteligentes",
            "Cargadores y Baterias", "Cables y Adaptadores", "Almacenamiento",
            "Monitores y Pantallas", "Impresoras", "Redes y Wi-Fi", "Componentes de PC",
        ],
        "Hogar y Decoracion": [
            "Muebles", "Decoracion de Interiores", "Iluminacion", "Cocina", "Bano",
            "Jardin y Exteriores", "Herramientas", "Organizacion y Almacenamiento",
            "Ropa de Cama", "Cortinas y Toldos", "Cuadros y Arte Decorativo",
            "Relojes de Pared", "Velas y Aromatizadores", "Alfombras", "Cojines y Mantas",
        ],
        "Moda y Accesorios": [
            "Ropa de Hombre", "Ropa de Mujer", "Ropa de Ninos", "Ropa Deportiva",
            "Calzado", "Bolsos y Maletas", "Joyeria y Relojes", "Gafas de Sol",
            "Cinturones y Accesorios", "Sombreros y Gorras", "Bufandas y Guantes",
            "Trajes y Vestidos Formales", "Ropa Interior", "Pijamas y Ropa de Dormir",
            "Accesorios para el Cabello",
        ],
        "Belleza y Cuidado Personal": [
            "Cuidado de la Piel", "Cuidado del Cabello", "Maquillaje", "Perfumes y Fragancias",
            "Higiene Personal", "Afeitado y Cuidado Facial", "Unas y Manicure",
            "Cuidado Dental", "Desodorantes", "Protector Solar", "Maletines de Belleza",
            "Cuidado de Pies", "Aceites Esenciales",
        ],
        "Deportes y Recreacion": [
            "Fitness y Gimnasio", "Futbol", "Beisbol", "Baloncesto", "Ciclismo",
            "Camping y Senderismo", "Pesca", "Natacion", "Yoga y Pilates",
            "Correr y Atletismo", "Artes Marciales", "Tenis", "Voleibol",
            "Skateboarding", "Ajedrez y Juegos de Mesa Deportivos",
        ],
        "Salud y Bienestar": [
            "Vitaminas y Suplementos", "Equipos Medicos", "Primeros Auxilios",
            "Cuidado del Bebe", "Medicinas Naturales", "Termometros y Monitores",
            "Mascarillas y Proteccion", "Sillas de Ruedas y Movilidad",
            "Terapia Fisica", "Rehabilitacion", "Salud Sexual", "Cuidado de la Salud Mental",
        ],
        "Mascotas": [
            "Alimento para Perros", "Alimento para Gatos", "Alimento para Aves",
            "Alimento para Peces", "Juguetes para Mascotas", "Camas y Transportadoras",
            "Correas y Collares", "Higiene y Aseo de Mascotas", "Salud Veterinaria",
            "Accesorios para Perros", "Accesorios para Gatos", "Acuarios y Terrarios",
        ],
        "Bebes y Ninos": [
            "Ropa de Bebe", "Juguetes Educativos", "Juguetes Infantiles",
            "Alimentacion del Bebe", "Panales y Toallitas", "Cuna y Cama",
            "Sillas de Bebe", "Carriolas y cochecitos", "Seguridad del Hogar para Bebes",
            "Chupetes y Mordedores", "Bano del Bebe", "Articulos de Maternidad",
        ],
        "Libros y Papeleria": [
            "Libros de Ficcion", "Libros de No Ficcion", "Libros Infantiles",
            "Libros Academicos", "Libros de Autoayuda", "Cuadernos y Libretas",
            "Utiles Escolares", "Arte y Manualidades", "Material de Oficina",
            "Impresion y Papel", "Organizacion de Escritorio", "Comics y Novela Grafica",
        ],
        "Vehiculos": [
            "Repuestos y Autopartes", "Accesorios de Auto", "Llantas y Rines",
            "Aceites y Lubricantes", "Herramientas Automotrices", "Audio y Multimedia para Auto",
            "Limpieza de Auto", "Seguridad Vial", "Bicicletas y Accesorios",
            "Motos y Accesorios", "Carros Electricos Infantiles", "GPS y Navegadores",
        ],
        "Electrodomesticos": [
            "Cocina (Licuadoras, Ollas, Sartenes)", "Refrigeracion",
            "Lavado (Lavadoras, Secadoras)", "Climatizacion (Aires, Ventiladores)",
            "Limpieza (Aspiradoras, Planchas)", "Pequenos Electrodomesticos",
            "Hornos y Microondas", "Purificadores de Agua",
            "Purificadores de Aire", "Ventilacion",
        ],
        "Fereteria y Construccion": [
            "Pinturas y Acabados", "Herramientas Manuales", "Herramientas Electricas",
            "Tornilleria y Fijaciones", "Plomeria", "Electricidad",
            "Seguridad e Iluminacion", "Materiales de Construccion",
            "Puertas y Ventanas", "Pisos y Revestimientos",
        ],
        "Jardineria": [
            "Plantas y Semillas", "Macetas y Jardineras", "Herramientas de Jardin",
            "Fertilizantes y Abonos", "Sistemas de Riego", "Decoracion de Jardin",
            "Iluminacion Exterior", "Cercas y Portones", "Compost y Vermicompost",
            "Control de Plagas",
        ],
        "Musica": [
            "Instrumentos de Cuerda", "Instrumentos de Viento", "Instrumentos de Percusion",
            "Teclados y Pianos", "Guitarras", "Accesorios Musicales",
            "Equipos de Sonido", "Microfonos y Audio", "DJ y Produccion Musical",
            "Partituras y Libros de Musica",
        ],
        "Fotografia y Video": [
            "Camaras DSLR y Mirrorless", "Camaras de Accion (GoPro)", "Lentes y Objetivos",
            "Tripodes y Monopodes", "Iluminacion de Estudio", "Drones",
            "Estabilizadores y Gimbals", "Filtros y Accesorios", "Tarjetas de Memoria",
            "Bolsos y Fundas para Camaras", "Grabadoras de Audio", "Teleprompters",
        ],
        "Arte y Manualidades": [
            "Pintura (Oleo, Acuarela)", "Lienzos y Tableros", "Pinceles y Espatulas",
            "Bocetarios y Cuadernos de Dibujo", "Lapices y Marcadores",
            "Escultura y Modelado", "Bordido y Costura Creativa", "Resina y Epoxi",
            "Scrapbooking", "Stickers y Cintas Decorativas", "Hilo, Tela y Macrame",
        ],
        "Abarrotes y Limpieza": [
            "Productos de Limpieza del Hogar", "Detergentes y Suavizantes",
            "Papel Higienico y Servilletas", "Bolsas de Basura y Residuos",
            "Utensilios de Cocina", "Vajilla y Cristaleria", "Cuberteria",
            "Bodega y Alimentos Basicos", "Envases y Recipientes", "Jabones y Sanitizantes",
        ],
        "Tecnologia y Software": [
            "Software de Oficina", "Antivirus y Seguridad", "Apps y Suscripciones",
            "Cursos Online de Tecnologia", "Servicios de Hosting",
            "Diseno Grafico y Multimedia", "Programacion y Desarrollo", "Inteligencia Artificial",
        ],
        "Servicios": [
            "Reparacion de Celulares", "Reparacion de Computadoras",
            "Fumigacion y Control de Plagas", "Limpieza de Hogar",
            "Plomeria y Electricidad", "Clases Particulares",
            "Diseno Web y Marketing Digital", "Fotografia de Eventos",
            "Transporte y Mudanzas", "Catering y Banquetes",
            "Peluqueria a Domicilio", "Mecanica y Mantenimiento",
        ],
        "Bebidas y Licores": [
            "Ron", "Whisky", "Vodka", "Tequila", "Cerveza Artesanal",
            "Vinos", "Champagne y Espumantes", "Snaps y Licores",
            "Bebidas Energeticas", "Agua y Bebidas Naturales",
            "Jugos y Nectares", "Refrescos y Gaseosas",
        ],
        "Regalos y Souvenirs": [
            "Regalos para Ella", "Regalos para El", "Regalos para Bebes",
            "Regalos para Parejas", "Souvenirs Tipicos", "Cajas de Regalo",
            "Flores y Arreglos Florales", "Tarjetas y Globos",
            "Regalos Personalizados", "Cupones y Gift Cards",
            "Articulos Religiosos", "Veladoras y velas aromaticas",
        ],
        "Seguros y Finanzas": [
            "Seguros de Vida", "Seguros de Auto", "Seguros de Salud",
            "Prestamos Personales", "Inversiones", "Asesoria Financiera",
            "Tarjetas de Credito", "Criptomonedas",
        ],
        "Materiales Educativos": [
            "Cursos Presenciales", "Cursos Online", "Material Didactico",
            "Juguetes Montessori", "Kits Educativos de Ciencia",
            "Idiomas y Certificaciones", "Preparacion de Examenes",
            "Material para Profesores",
        ],
        "Agro y Campo": [
            "Semillas Profesionales", "Fertilizantes Industriales",
            "Maquinaria Agricola", "Riego Industrial", "Ganaderia",
            "Avicultura", "Apicultura", "Invernaderos",
            "Alimentos para Animales de Granja", "Herramientas de Campo",
        ],
    }
    try:
        db = get_db()
        row = db.execute("SELECT COUNT(*) as n FROM categorias").fetchone()
        if row and row['n'] > 0:
            return
        for nombre_cat, subs in CATEGORIAS.items():
            for sub in subs:
                try:
                    db.execute("INSERT INTO categorias (nombre, subcategoria) VALUES (?, ?)", (nombre_cat, sub))
                except Exception:
                    pass
        db.commit()
        print(f"Seed categorias: {sum(len(v) for v in CATEGORIAS.values())} subcategorias en {len(CATEGORIAS)} categorias")
    except Exception as e:
        print("Seed categorias error:", e)

_seed_categorias()

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
