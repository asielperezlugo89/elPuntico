from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g, send_from_directory
import sqlite3
import os
import hashlib
import re
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IS_PRODUCTION = os.environ.get('PUNTICO_ENV', '').lower() == 'production' or os.environ.get('RENDER') == 'true'
DB_NAME = os.environ.get('DATABASE_PATH', os.path.join(BASE_DIR, 'mitienda.db'))
IMG_FOLDER = os.environ.get('IMG_FOLDER', os.path.join(BASE_DIR, 'img'))
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

def hash_password(password):
    return generate_password_hash(password)

def check_password(password, hashed):
    if not hashed:
        return False
    # Compatibilidad con contraseñas antiguas guardadas en SHA-256 simple.
    if re.fullmatch(r'[a-f0-9]{64}', hashed):
        return hashlib.sha256(password.encode()).hexdigest() == hashed
    return check_password_hash(hashed, password)

def get_db():
    if 'db' not in g:
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
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        nombre TEXT NOT NULL,
        telefono TEXT,
        direccion TEXT,
        rol TEXT DEFAULT 'cliente',
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
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO config (clave, valor) VALUES (?, ?)", (k, v))
    
    c.execute("SELECT COUNT(*) FROM usuarios WHERE rol = 'admin'")
    if c.fetchone()[0] == 0:
        admin_email = os.environ.get('DEFAULT_ADMIN_EMAIL', 'admin@elpuntico.com')
        admin_password = os.environ.get('DEFAULT_ADMIN_PASSWORD')
        if not admin_password and not IS_PRODUCTION:
            admin_password = 'admin'
        if admin_password:
            c.execute(
                "INSERT INTO usuarios (email, password, nombre, telefono, direccion, rol) VALUES (?, ?, ?, ?, ?, ?)",
                (admin_email, hash_password(admin_password), 'Admin Principal', '0000', 'Sistema', 'admin')
            )
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
    categoria = request.args.get('categoria', '')
    
    sql = "SELECT p.*, u.nombre as vendedor_nombre, u.direccion as vendedor_direccion, u.precioDelivery, u.entregaGratis FROM productos p JOIN usuarios u ON p.vendedor_id = u.id WHERE p.activo = 1 AND p.stock > 0"
    params = []
    
    if buscar:
        sql += " AND (p.nombre LIKE ? OR p.descripcion LIKE ?)"
        params.extend([f'%{buscar}%', f'%{buscar}%'])
    if categoria:
        sql += " AND p.categoria = ?"
        params.append(categoria)
    
    categorias = db.execute("SELECT * FROM categorias ORDER BY nombre, subcategoria").fetchall()
    sql += " ORDER BY p.id DESC"
    
    productos = db.execute(sql, params).fetchall()
    config = get_config()
    
    return render_template('home.html', productos=productos, categorias=categorias, buscar=buscar, categoria=categoria, config=config)

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
            return render_template('registro.html', error="El email ya está registrado", config=get_config())
        
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



@app.route('/agregar_carrito', methods=['POST'])
@login_required
def agregar_carrito():
    if session.get('rol') != 'cliente':
        return redirect(url_for('login'))
    
    producto_id = int(request.form.get('producto_id', 0) or 0)
    cantidad = int(request.form.get('cantidad', 1) or 1)
    
    if 'carrito' not in session:
        session['carrito'] = []
    
    existe = False
    for item in session['carrito']:
        if item['producto_id'] == producto_id:
            item['cantidad'] += cantidad
            existe = True
            break
    
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
    
    session.modified = True
    return redirect(url_for('carrito'))

@app.route('/quitar_carrito/<int:index>')
@login_required
def quitar_carrito(index):
    if 'carrito' in session and 0 <= index < len(session['carrito']):
        session['carrito'].pop(index)
        session.modified = True
    return redirect(url_for('carrito'))

@app.route('/carrito')
@login_required
def carrito():
    if session.get('rol') != 'cliente':
        return redirect(url_for('home'))
    
    return render_template('carrito.html', config=get_config())

@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    if session.get('rol') != 'cliente' or 'carrito' not in session:
        return redirect(url_for('home'))
    
    if not session['carrito']:
        return redirect(url_for('carrito'))
    
    db = get_db()
    observaciones = request.form.get('observaciones', '')
    
    from collections import defaultdict
    by_vendedor = defaultdict(list)
    for item in session['carrito']:
        by_vendedor[item['vendedor_id']].append(item)
    
    for vendedor_id, items in by_vendedor.items():
        total = sum(i['precio'] * i['cantidad'] for i in items)
        
        vendedor = db.execute("SELECT precioDelivery, entregaGratis FROM usuarios WHERE id = ?", (vendedor_id,)).fetchone()
        delivery = 0
        if vendedor and vendedor['precioDelivery'] > 0:
            if vendedor['entregaGratis'] > 0 and total >= vendedor['entregaGratis']:
                delivery = 0
            else:
                delivery = vendedor['precioDelivery']
        
        cursor = db.execute("INSERT INTO pedidos (cliente_id, vendedor_id, total, delivery, observaciones) VALUES (?, ?, ?, ?, ?)",
            (session['usuario_id'], vendedor_id, total, delivery, observaciones))
        pedido_id = cursor.lastrowid
        
        for item in items:
            db.execute("INSERT INTO pedido_items (pedido_id, producto_id, cantidad, precio) VALUES (?, ?, ?, ?)",
                (pedido_id, item['producto_id'], item['cantidad'], item['precio']))
            db.execute("UPDATE productos SET stock = stock - ? WHERE id = ?", (item['cantidad'], item['producto_id']))
    
    db.commit()
    session['carrito'] = []
    session.modified = True
    
    return render_template('checkout_ok.html', config=get_config())

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
    
    db.execute("INSERT INTO usuarios (email, password, nombre, telefono, direccion, rol) VALUES (?, ?, ?, ?, ?, ?)",
        (email, hash_password(password), nombre, telefono, direccion, rol))
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

@app.route('/admin/pedido/<int:pedido_id>/<string:estado>')
@rol_required(['admin'])
def admin_cambiar_pedido(pedido_id, estado):
    db = get_db()
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
def admin_eliminar_categoria(cat_id, sub_id=''):
    db = get_db()
    
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
    
    return render_template('vendedor.html', productos=productos, pedidos=pedidos, usuario=usuario, categorias=categorias)

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
    db.execute("""UPDATE usuarios SET precioDelivery = ?, entregaGratis = ?, nombre = ?, telefono = ?, direccion = ? 
        WHERE id = ?""",
        (int(request.form.get('precioDelivery') or 0), int(request.form.get('entregaGratis') or 0), 
         request.form.get('nombre', ''), request.form.get('telefono', ''), request.form.get('direccion', ''),
         session['usuario_id']))
    db.commit()
    return redirect(url_for('vendedor_panel'))

@app.route('/vendedor/pedido/<int:pedido_id>/<string:estado>')
@rol_required(['vendedor'])
def vendedor_cambiar_pedido(pedido_id, estado):
    db = get_db()
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
    return send_from_directory('img', filename)

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

if __name__ == '__main__':
    init_db()
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
