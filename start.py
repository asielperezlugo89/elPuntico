from app import app, init_db
import webbrowser
import os

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 80))
    os.environ.setdefault('FLASK_DEBUG', '1')
    print("=" * 45)
    print("  MiTienda - Marketplace Local")
    print("=" * 45)
    print()
    print("Usuario Admin por defecto:")
    print("  Email: admin@elpuntico.com")
    print("  Password: admin")
    print()
    print("Abre en tu navegador:")
    print(f"  http://localhost:{port}")
    print("=" * 45)
    print()
    print("Presiona Ctrl+C para detener")
    print()
    
    try:
        webbrowser.open(f'http://localhost:{port}')
    except:
        pass
    
    app.run(debug=True, host='0.0.0.0', port=port)
