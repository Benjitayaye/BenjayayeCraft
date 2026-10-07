"""Recursos locales con revalidación y compresión; los precios no se cachean."""
import gzip
import hashlib
from functools import lru_cache
from pathlib import Path


def acepta_gzip(cabecera):
    for parte in cabecera.lower().split(','):
        valores=parte.strip().split(';')
        if valores[0]!='gzip':continue
        try:
            q=next((float(v.strip()[2:]) for v in valores[1:] if v.strip().startswith('q=')),1)
            return 0<q<=1
        except ValueError:return False
    return False


@lru_cache(maxsize=64)
def contenido_archivo(ruta,modificado,tamano,comprimir):
    datos=Path(ruta).read_bytes()
    if comprimir:datos=gzip.compress(datos,compresslevel=5,mtime=0)
    return datos,'"'+hashlib.sha256(datos).hexdigest()+'"'


def enviar_archivo(handler,ruta,tipo):
    ruta=Path(ruta);stat=ruta.stat()
    comprimir=(tipo.startswith('text/') or 'json' in tipo) and stat.st_size>1024 and acepta_gzip(handler.headers.get('Accept-Encoding',''))
    datos,etag=contenido_archivo(str(ruta),stat.st_mtime_ns,stat.st_size,comprimir)
    recibidos=[e.strip().removeprefix('W/') for e in handler.headers.get('If-None-Match','').split(',')]
    sin_cambio=etag in recibidos or '*' in recibidos
    handler.send_response(304 if sin_cambio else 200)
    handler.send_header('Content-Type',tipo)
    handler.send_header('ETag',etag)
    handler.send_header('Cache-Control','no-cache')
    handler.send_header('Vary','Accept-Encoding')
    handler.send_header('X-Content-Type-Options','nosniff')
    if comprimir:handler.send_header('Content-Encoding','gzip')
    if not sin_cambio:handler.send_header('Content-Length',str(len(datos)))
    handler.end_headers()
    if not sin_cambio:
        try:handler.wfile.write(datos)
        except (BrokenPipeError,ConnectionResetError):handler.close_connection=True
