# V66 · Precios de la API en la calculadora

Cada vez que pulses **Analizar** con **Lector + datos públicos** o **Solo API pública**, la V66 guarda las cotizaciones válidas que recibió de Albion Online Data Project para Américas. Incluye los precios mínimo y máximo de compra y de venta, sus fechas, el objeto exacto, la ciudad y la calidad. Se guardan aunque el Analizador no encuentre una reventa rentable. Se conserva la observación más nueva por cada campo; un precio ausente de la API no reemplaza un dato existente. Elige **Solo API pública** para consultar directamente ambos mercados de la ruta aunque tengas lecturas locales.

## En tu computadora

1. Extrae el ZIP de PC. Abre `INICIAR_LECTOR_WINDOWS.bat` y `INICIAR_WINDOWS.bat` con el mismo usuario de Windows.
2. En **Analizador**, selecciona la ruta, busca el objeto y pulsa **Analizar**.
3. En la calculadora, elige **Mercado → Precios → Ambos**. Selecciona la misma variante, ciudad y calidad. Se usa primero el precio del lector; si falta, se aplica el precio público de las últimas 24 horas o el manual guardado.
4. En **Ver cotizaciones sincronizadas de la API**, consulta el ID de cualquier objeto. Se muestran los cuatro precios recibidos y sus fechas, incluidos los antiguos. Una raya indica que no hay dato.

No necesitas configurar un enlace ni un token para usar las dos aplicaciones en el mismo equipo. Para añadir más objetos o ciudades, haz otra consulta en el Analizador con esos filtros.

## En Render

Usa el paquete **Solo Web V66** para actualizar el servidor; no subas la edición de escritorio. Si tu repositorio tiene personalizaciones (por ejemplo, sesiones o separación por usuario), conserva esas funciones e integra los cambios de precios de la V66 sobre tu versión. Mantén la carpeta de datos que ya usa tu servicio.

1. En el [Web Service de Render](https://render.com/docs/web-services), actualiza el código de la calculadora. Para iniciar la edición Solo Web incluida, usa **Start Command**: `python app.py --web-alojada`. No requiere dependencias adicionales. Puedes usar `/salud` como **Health Check Path**.
2. En **Environment**, crea **BENJAYAYE_API_SYNC_TOKEN** y **BENJAYAYE_ACCESO_WEB** con dos claves diferentes. Para generar una clave ejecuta `python -c "import secrets; print(secrets.token_urlsafe(32))"` en tu terminal; repite para la segunda. La primera autoriza los envíos del lector. La segunda abre la calculadora con `https://TU-WEB.onrender.com/?acceso=CLAVE_DE_ACCESO_WEB`. El servidor privado incluido comparte los datos de la calculadora entre las personas que tengan esa clave.
3. Si ya tienes un [disco persistente](https://render.com/docs/disks), conserva la ruta **BENJAYAYE_DATOS** que apunta al disco. Para un disco nuevo montado en `/var/data` puedes usar `BENJAYAYE_DATOS=/var/data/benjayaye`. Sin disco, el almacenamiento local de Render se pierde al reiniciar o desplegar de nuevo.
4. Despliega la web. En el lector V66 abre **Analizador → Sincronizar con web…**, escribe solo la URL base HTTPS (por ejemplo, `https://TU-WEB.onrender.com`) y pega el valor de **BENJAYAYE_API_SYNC_TOKEN**. Pulsa **Guardar**. Las cotizaciones guardadas y las nuevas se envían por lotes automáticamente mientras esté abierto el lector.
5. Abre la calculadora alojada y selecciona **Ambos**. Busca el objeto o abre **Ver cotizaciones sincronizadas de la API**. Si hubo un reinicio sin disco o cambiaste la base del servidor, usa **Guardar y reenviar todo** desde el lector.

Si la web no responde, los precios quedan en cola y se reintentan al volver a abrir el lector o al recuperarse la conexión. Solo se envían cotizaciones públicas; las lecturas OCR, los precios manuales, las capturas y los lotes siguen en sus propias bases.
