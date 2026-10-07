# Benjayaye Craft — sitio web

Este paquete contiene únicamente el backend y los archivos de la calculadora web. No incluye el lector visual de pantalla, el autoclicker de Windows, scripts `.bat`, pruebas ni la carpeta local de miles de iconos. Los iconos que falten se solicitan al servicio de imágenes de Albion.

## Archivos

- `app.py` y módulos de apoyo: servidor web, cálculos, consultas de precios e historial guardado.
- `web/`: interfaz, estilos e imágenes de fondo.
- `datos/catalogo_es.json` y `datos/recetas_comida.json`: catálogo y recetas.
- `.gitignore`: evita añadir bases de datos y archivos locales.

`lector_datos.py` y `historial_mercado.py` son módulos del backend que la página importa para mostrar los datos guardados; no activan ni controlan el lector de pantalla del juego.

## Prueba local

Con Python instalado, instala los requisitos y ejecuta `python app.py --sin-navegador`. La dirección local usa el puerto 8501.

## AWS

Este paquete está organizado para subir la parte web a GitHub. Antes de exponerlo públicamente en AWS todavía hay que configurar el puerto y la dirección de escucha del servidor, la persistencia de la base de datos y la autenticación de usuarios. No publiques una instancia accesible desde Internet hasta completar esa configuración.
