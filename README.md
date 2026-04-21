# El Puntico

Marketplace web hecho con Flask.

## Ejecutar en local

```powershell
pip install -r requirements.txt
python start.py
```

## Publicar en internet con Render

Este proyecto ya incluye:

- `render.yaml`
- `requirements.txt`
- `DEPLOY_RENDER.md`
- `.env.example`

### Opcion recomendada

1. Crea un repositorio nuevo en GitHub.
2. Sube este proyecto.
3. En Render, elige `New > Blueprint`.
4. Conecta el repositorio.
5. Render leera `render.yaml` y te pedira el valor de `DEFAULT_ADMIN_PASSWORD`.
6. Cuando termine el deploy, abre la URL publica `onrender.com`.

### Nota importante

El archivo `render.yaml` usa `plan: starter` porque Render solo permite discos persistentes en servicios pagos. Eso es importante aqui porque la app guarda:

- base de datos SQLite
- imagenes subidas

Si quieres una version solo de prueba, puedes cambiar temporalmente a `plan: free`, pero perderas persistencia y el servicio puede dormirse por inactividad.

## Archivos clave

- `app.py`: aplicacion Flask
- `start.py`: launcher local
- `render.yaml`: configuracion de infraestructura en Render
- `DEPLOY_RENDER.md`: guia de despliegue
