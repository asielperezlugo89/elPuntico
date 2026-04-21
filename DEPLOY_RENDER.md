# Publicar El Puntico en Render

Esta app ya quedo preparada para desplegarse como servicio Flask en internet.

## Opcion mas rapida

Usa `render.yaml` con un `Blueprint` en Render.

## 1. Sube el proyecto a GitHub

- Crea un repositorio nuevo.
- Sube todo el contenido del proyecto.
- No subas una base de datos real con datos privados.
- Si quieres empezar limpio, borra `mitienda.db` antes de subir y deja que Render la cree sola.

## 2. Crea el deploy en Render

- Entra en Render.
- Pulsa `New > Blueprint`.
- Conecta tu repositorio.
- Render detectara el archivo `render.yaml`.

La configuracion ya incluida es esta:

- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`
- Health Check Path: `/health`
- Disco persistente: `/var/data`
- Puerto: `10000`

## 3. Variables de entorno

El `render.yaml` ya define casi todo.

Solo tendras que completar al crear el Blueprint:

- `DEFAULT_ADMIN_PASSWORD`

Las demas quedan listas:

- `SECRET_KEY`: se genera automaticamente
- `PUNTICO_ENV=production`
- `DATABASE_PATH=/var/data/mitienda.db`
- `IMG_FOLDER=/var/data/img`
- `DEFAULT_ADMIN_EMAIL=admin@elpuntico.com`
- `ALLOW_REMOTE_IMAGE_UPLOAD=0`

## 4. Sobre el plan

El archivo `render.yaml` usa `plan: starter`.

Esto no es capricho: segun la documentacion oficial de Render, los discos persistentes se adjuntan a servicios pagos, y tu app necesita persistencia para:

- la base de datos SQLite
- las imagenes subidas

Si lo cambias a `free`, puede servir solo como prueba, pero:

- el servicio entra en reposo por inactividad
- no es una buena opcion para produccion
- la persistencia no es la adecuada para este caso

## 5. Primer acceso

- Cuando Render termine, abre la URL publica.
- Inicia sesion con:
  - Email: `admin@elpuntico.com`
  - Password: la que pusiste en `DEFAULT_ADMIN_PASSWORD`

## 6. Recomendacion siguiente

Para una primera salida a internet, Render + Flask + SQLite puede servir.

Si el proyecto empieza a crecer, el siguiente paso correcto es:

- pasar de SQLite a Postgres
- mover imagenes a almacenamiento externo
- agregar proteccion CSRF en formularios sensibles
