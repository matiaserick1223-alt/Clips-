# KaiClip Studio Móvil – versión web (PWA)

Software para convertir **videos que tengas derecho a usar** en clips verticales desde Safari (iPhone) o Chrome (Android). Esta versión se ejecuta en un **servidor**, no procesa videos enteramente en el teléfono. Tiene pantalla móvil, subir archivo, detección de escenas basada en actividad del audio, cortes 9:16, vista previa, descarga de MP4/ZIP, sugerencias de textos, y opción de compartir mediante la hoja de compartir del sistema.

**Importante:** esta entrega es el código completo, **no una web ya publicada ni una app instalada desde App Store**. Necesita despliegue para obtener una URL que abra el celular.

## Publicarlo para usarlo desde el celular (Render)

1. Descomprime este ZIP, crea un repositorio en GitHub y sube el contenido de `KaiClipStudio_Movil` a la **raíz** del repositorio (incluyendo Dockerfile, render.yaml, main.py, engine.py, static/).
2. En [dashboard.render.com](https://dashboard.render.com), crea un **Blueprint** a partir de ese repositorio. Render detectará `render.yaml` y creará un Web Service (plan `starter`, que es **de pago**; revisa el precio vigente antes de confirmar).
3. Cuando el despliegue termine, Render te entregará una URL `https://...onrender.com`. Ábrela en Safari/Chrome desde el celular. En Render → servicio → Environment, puedes consultar/cambiar `APP_PIN`, el PIN para subir y ver trabajos. **No compartas tu PIN ni enlaces de clips con desconocidos.**
4. En iPhone: Safari → Compartir → **Añadir a pantalla de inicio**. En Android: Chrome → menú ⋮ → **Añadir a pantalla principal** o **Instalar aplicación**.
5. Sube un video desde Archivos/Fotos, marca la casilla de derechos, configura cantidad y duración, toca «Generar», espera el resultado con la página abierta, descarga el MP4 o usa «Compartir» para abrir el menú del celular. La disponibilidad de TikTok dentro del menú «Compartir» depende del dispositivo, del navegador y de la app TikTok.

**No incluye publicación desatendida/autónoma a TikTok.** TikTok requiere registro de aplicación, OAuth autorizado con `video.publish`, y auditoría para publicar visibilidad pública desde la API: https://developers.tiktok.com/docs/en/content-posting-api-get-started

## Ejecutarlo localmente en una computadora

Necesitas Python 3.11+, FFmpeg (con libx264), pip e internet para instalar dependencias.

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Abre `http://localhost:8000` en esa computadora. Para acceder desde un celular en la misma WiFi, busca la IP LAN de la computadora y visita `http://IP_DE_TU_PC:8000` (permite conexiones entrantes en el firewall). La instalación PWA y compartir archivos tienen más soporte desde **HTTPS**, disponible al desplegar en Render.

Para habilitar subtítulos con IA además de instalar las dependencias básicas:

```bash
pip install -r requirements-captions.txt
```

En Render, selecciona el Dockerfile alternativo **Dockerfile.subtitles** desde la configuración del servicio (o cambia el Dockerfile principal por su contenido). Esto aumenta los requisitos de memoria/CPU, el tamaño de imagen, y descarga el modelo Whisper la primera vez que se usa. Un servicio pequeño puede ser insuficiente para esto.

## Protección de datos y límites

- **Al subir un video, este se envía al servidor de despliegue** para procesarse; no se mantiene exclusivamente en el teléfono.
- Videos y clips quedan en el espacio temporal del servidor y se eliminan durante tareas posteriores, una vez vencido `CLIP_TTL_HOURS` (3 h por defecto). Si el host reinicia, podrían desaparecer antes. No se prometen almacenamiento permanente ni backups.
- Cada archivo: máximo 250 MB por defecto (`MAX_UPLOAD_MB`). Máximo 8 clips de 10 a 90 segundos. Solo un trabajo se procesa simultáneamente; otro puede quedar en cola. Para grandes transmisiones hacen falta más recursos.
- Los resultados dependen de variaciones en el **volumen del audio**, no de entendimiento semántico de lo que dice Kai o de reconocimiento facial; tampoco hay garantía de visitas, monetización o viralidad.
- `APP_PIN` es opcional en desarrollo; **pon un PIN secreto en un despliegue público**, o cualquiera con el enlace podría consumir CPU y subir videos. Cada carpeta tiene identificador aleatorio para acceder a sus archivos; no hay cuentas ni sistema de usuarios.
- Subtítulos con IA opcionales y no activados en el Dockerfile estándar, que busca funcionar con menos recursos. Si el host usa la imagen ampliada, aparece el interruptor de subtítulos automáticamente. No se finge que existen cuando no lo están.
- **Derechos:** solo usa videos propios, con autorización, o de otra forma legalmente reutilizables. Editar/transcribir contenido de otro creador no da por sí solo autorización para re-subirlo.
- No descarga directamente desde Twitch ni YouTube. Solo importa archivos que el usuario selecciona.

## Archivos

- `main.py`: API FastAPI con trabajos y entrega de archivos.
- `engine.py`: análisis de audio, render FFmpeg, Whisper opcional.
- `static/`: aplicación web móvil (HTML/CSS/JS), manifiesto e icono.
- `Dockerfile`, `render.yaml`: despliegue.
- `Dockerfile.subtitles`, `requirements-captions.txt`: servidor con transcripción opcional.
- `tests/`: pruebas automatizadas.

## Variables

| Variable | Ejemplo | Función |
| --- | --- | --- |
| `APP_PIN` | `cambia-esto` | Obligatorio recomendado para el servidor público. Render genera uno. |
| `MAX_UPLOAD_MB` | `250` | Tamaño máximo por archivo. |
| `CLIP_TTL_HOURS` | `3` | Tiempo que se conservan trabajos terminados. |
| `CLIP_DATA_DIR` | `/tmp/kaiclip_mobile` | Carpeta temporal para archivos. |
| `PORT` | `8000` | Puerto de la web. |

No afiliado oficialmente a Kai Cenat, Twitch ni TikTok.
