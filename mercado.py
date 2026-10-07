"""Precios públicos de Américas: lectura, validación y caché breve por ciudad."""
import gzip
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from threading import Lock

CIUDADES = ('Martlock', 'Bridgewatch', 'Lymhurst', 'Fort Sterling', 'Thetford')
BASE = 'https://west.albion-online-data.com/api/v2/stats/prices/'
_CACHE = {}
_LOCK = Lock()


def consultar_precios(ciudad, ids, catalogo):
    if ciudad not in CIUDADES:
        raise ValueError('Elige una de las cinco ciudades disponibles')
    ids = sorted(set(ids))
    if not 1 <= len(ids) <= 21 or any(id not in catalogo for id in ids):
        raise ValueError('Selecciona hasta 21 objetos válidos del catálogo')
    clave = (ciudad, tuple(ids))
    with _LOCK:
        previo = _CACHE.get(clave)
        if previo and time.monotonic() - previo[0] < 60:
            return previo[1]
    parametros = urllib.parse.urlencode({'locations': ciudad, 'qualities': 1})
    url = BASE + urllib.parse.quote(','.join(ids), safe=',_@') + '.json?' + parametros
    solicitud = urllib.request.Request(url, headers={
        'Accept-Encoding': 'gzip', 'User-Agent': 'BenjayayeCraft/1.0'})
    with urllib.request.urlopen(solicitud, timeout=15) as respuesta:
        contenido = respuesta.read(2_000_001)
        if len(contenido) > 2_000_000:
            raise ValueError('Respuesta de precios demasiado grande')
        if respuesta.headers.get('Content-Encoding') == 'gzip':
            contenido = gzip.decompress(contenido)
        filas = json.loads(contenido)
    if not isinstance(filas, list):
        raise ValueError('El servicio de precios devolvió una respuesta inválida')
    precios = {id: {'id': id, 'precio': None, 'fecha': None} for id in ids}
    for fila in filas:
        if not isinstance(fila, dict):
            continue
        id = fila.get('item_id')
        if id not in precios or fila.get('city') != ciudad or fila.get('quality') != 1:
            continue
        precio, fecha = fila.get('sell_price_min'), fila.get('sell_price_min_date')
        if isinstance(precio, bool) or not isinstance(precio, (int, float)) or not 0 < precio <= 1e15:
            continue
        try:
            instante = datetime.fromisoformat(fecha.replace('Z', '+00:00'))
            if instante.tzinfo is None:
                instante = instante.replace(tzinfo=timezone.utc)
            if instante.year < 2000:
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        precios[id] = {'id': id, 'precio': precio, 'fecha': instante.astimezone(timezone.utc).isoformat()}
    resultado = {'servidor': 'Américas', 'ciudad': ciudad, 'calidad': 1,
                 'consultado': datetime.now(timezone.utc).isoformat(), 'precios': list(precios.values())}
    with _LOCK:
        ahora = time.monotonic()
        for key in list(_CACHE):
            if ahora - _CACHE[key][0] >= 60:
                del _CACHE[key]
        _CACHE[clave] = (ahora, resultado)
    return resultado
