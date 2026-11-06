# ISC ATS backend

## Docker local

El Compose inicia la API y el worker. PostgreSQL, Redis y MinIO se configuran
en `.env`. Los contenedores locales usan la red externa `dokploy-network`.

Para usar MinIO de Dokploy develop desde tu equipo, configura
`MINIO_ENDPOINT` con el host y puerto de su API S3 accesible desde tu equipo
(sin `http://`, `https://` ni rutas), `MINIO_SECURE=true` si usa HTTPS o
`false` si usa HTTP, y el bucket y las credenciales de develop.
La consola web no es el endpoint S3. Un nombre interno de Dokploy solo funciona
desde contenedores en el mismo servidor y red.

Despues de cambiar `.env`, aplica los valores a ambos servicios:

```sh
docker compose up -d --no-build --force-recreate backend worker
```

Como alternativa, para el MinIO local opcional configura `MINIO_ENDPOINT=minio:9000`,
`MINIO_SECURE=false`, `MINIO_BUCKET=candidates-cvs` y las credenciales
`MINIO_ACCESS_KEY` y `MINIO_SECRET_KEY` en `.env`.

```sh
docker compose --profile local-storage up -d --build minio
docker compose up -d --build backend worker
docker compose ps
```

MinIO se compila desde la version oficial `RELEASE.2025-10-15T17-29-55Z`;
la primera compilacion descarga Go y sus dependencias y puede tardar varios minutos.
La API crea el bucket al arrancar.

Consola de archivos: http://localhost:9001. Usa los valores de
`MINIO_ACCESS_KEY` y `MINIO_SECRET_KEY` para ingresar. La API S3 se accede
desde los contenedores como `minio:9000`.

Los archivos permanecen en el volumen `minio-data` al reiniciar o recrear
contenedores. `docker compose down -v` elimina ese volumen y sus archivos.
El frontend debe compartir `dokploy-network` y usar
`BACKEND_INTERNAL_URL=http://isc-ats-backend:8000/api/v1`.
