"""Cotizaciones de Américas, separadas de los precios personales y del OCR."""
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone

from almacenamiento import conectar_base

FUENTE_API = 'Albion Online Data Project'
REGION_API = 'americas'
RUTA_PUBLICOS = '/api/mercado/publicos'
RUTA_ENVIO_API = RUTA_PUBLICOS + '/sincronizar'
CAMPOS_API = ('sell_price_min', 'sell_price_max', 'buy_price_min', 'buy_price_max')
CIUDADES_API = ('Martlock', 'Bridgewatch', 'Lymhurst', 'Fort Sterling', 'Thetford',
               'Caerleon', 'Brecilien', 'Black Market')
MAX_ENVIO_API = 600
MAX_BYTES_API = 256 * 1024
EDAD_API_WEB = timedelta(hours=24)


def token_valido(token):
    return isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token) is not None


def _fecha_utc(fecha, ahora):
    if not isinstance(fecha, str) or not 10 <= len(fecha) <= 40:
        return None
    try:
        f = datetime.fromisoformat(fecha.replace('Z', '+00:00'))
        f = (f if f.tzinfo else f.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None
    if f.year >= 2000 and f <= ahora + timedelta(minutes=5):
        return f.isoformat(timespec='microseconds')
    return None


def _cotizacion(item, ciudad, calidad, campo, precio, fecha, ahora):
    if type(precio) is not int or not 0 < precio <= 10**12:
        return None
    f = _fecha_utc(fecha, ahora)
    return (item, ciudad, calidad, campo, precio, f) if f else None


def cotizaciones_desde_api(filas, ciudades, ids, calidades, ahora=None):
    """Guarda los cuatro extremos y sus fechas, aunque el análisis use solo dos."""
    ahora = ahora or datetime.now(timezone.utc)
    ids, calidades = set(ids), set(calidades)
    resultado = []
    for fila in filas:
        if not isinstance(fila, dict):
            continue
        item, ciudad, calidad = fila.get('item_id'), fila.get('city'), fila.get('quality')
        if not isinstance(item, str) or item not in ids or ciudad not in ciudades or ciudad not in CIUDADES_API:
            continue
        if type(calidad) is not int or calidad not in calidades:
            continue
        for campo in CAMPOS_API:
            registro = _cotizacion(item, ciudad, calidad, campo, fila.get(campo), fila.get(campo+'_date'), ahora)
            if registro:
                resultado.append(registro)
    return resultado


def _existe(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='precios_api'").fetchone()


def _tablas(con):
    con.execute('''CREATE TABLE IF NOT EXISTS precios_api (
        item_id TEXT NOT NULL, ciudad TEXT NOT NULL, calidad INTEGER NOT NULL,
        campo TEXT NOT NULL, precio INTEGER NOT NULL, fecha TEXT NOT NULL,
        pendiente INTEGER NOT NULL,
        PRIMARY KEY (item_id, ciudad, calidad, campo)) WITHOUT ROWID''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_precios_api_pendientes ON precios_api(fecha) WHERE pendiente=1')
    con.execute('CREATE TABLE IF NOT EXISTS precios_api_estado (clave TEXT PRIMARY KEY, dato TEXT NOT NULL)')


def _estado(con, clave):
    f = con.execute('SELECT dato FROM precios_api_estado WHERE clave=?', (clave,)).fetchone()
    return f[0] if f else None


def _guardar_estado(con, clave, dato):
    con.execute('INSERT OR REPLACE INTO precios_api_estado VALUES (?,?)', (clave, dato))


def guardar_cotizaciones(db, filas, *, encolar=True):
    if not filas:
        return 0
    with closing(conectar_base(db)) as con, con:
        con.execute('BEGIN IMMEDIATE')
        _tablas(con)
        con.executemany('''INSERT INTO precios_api VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(item_id,ciudad,calidad,campo) DO UPDATE SET
            precio=excluded.precio, fecha=excluded.fecha, pendiente=excluded.pendiente
            WHERE excluded.fecha > precios_api.fecha''', ((*r, int(encolar)) for r in filas))
        cambios = con.total_changes
        if cambios:
            _guardar_estado(con, 'actualizado', datetime.now(timezone.utc).isoformat())
        return cambios


def recibir_cotizaciones(db, catalogo, datos, ahora=None):
    if not isinstance(datos, dict) or datos.get('region') != REGION_API or type(datos.get('version')) is not int or datos['version'] != 1:
        raise ValueError('El envío debe corresponder a la API de Américas (versión 1).')
    filas = datos.get('precios')
    if not isinstance(filas, list) or not 1 <= len(filas) <= MAX_ENVIO_API:
        raise ValueError('Tamaño de lote inválido.')
    ahora = ahora or datetime.now(timezone.utc)
    normalizadas = []
    for fila in filas:
        if not isinstance(fila, list) or len(fila) != 6:
            raise ValueError('Formato de cotización inválido.')
        item, ciudad, calidad, campo, precio, fecha = fila
        if (not isinstance(item, str) or item not in catalogo or ciudad not in CIUDADES_API
                or type(calidad) is not int or not 1 <= calidad <= 5 or campo not in CAMPOS_API):
            raise ValueError('Objeto, ciudad, calidad o campo de cotización inválido.')
        registro = _cotizacion(item, ciudad, calidad, campo, precio, fecha, ahora)
        if not registro:
            raise ValueError('La cotización necesita un precio positivo y la fecha de la API.')
        normalizadas.append(registro)
    cambios = guardar_cotizaciones(db, normalizadas, encolar=False)
    return {'region': REGION_API, 'version': 1, 'recibidos': len(filas), 'actualizados': cambios}


def pendientes_para(db, destino, *, reenviar=False):
    """La cola sobrevive al cierre. Una URL nueva recibe también los precios previos."""
    with closing(conectar_base(db)) as con, con:
        con.execute('BEGIN IMMEDIATE')
        _tablas(con)
        if reenviar or _estado(con, 'destino') != destino:
            con.execute('UPDATE precios_api SET pendiente=1 WHERE pendiente=0')
            _guardar_estado(con, 'destino', destino)
        return con.execute('''SELECT item_id,ciudad,calidad,campo,precio,fecha FROM precios_api
            WHERE pendiente=1 ORDER BY fecha DESC LIMIT ?''', (MAX_ENVIO_API,)).fetchall()


def confirmar_envio(db, destino, filas):
    with closing(conectar_base(db)) as con, con:
        con.execute('BEGIN IMMEDIATE')
        if _estado(con, 'destino') == destino:
            con.executemany('''UPDATE precios_api SET pendiente=0
                WHERE item_id=? AND ciudad=? AND calidad=? AND campo=? AND precio=? AND fecha=? AND pendiente=1''',
                filas)


def agregar_publicos(db, lecturas, ahora=None):
    """Prepara el precio público exacto como alternativa, sin tocar los precios del lector."""
    corte = ((ahora or datetime.now(timezone.utc)) - EDAD_API_WEB).isoformat(timespec='microseconds')
    with closing(conectar_base(db)) as con:
        if not _existe(con):
            return lecturas
        for p in lecturas['precios']:
            campo = 'buy_price_max' if p['tipo'] == 'compra' else 'sell_price_min'
            f = con.execute('''SELECT precio,fecha FROM precios_api
                WHERE item_id=? AND ciudad=? AND calidad=? AND campo=?''',
                (p['id'], p['ciudad'], p['calidad'], campo)).fetchone()
            if f:
                p['publico'] = {k: p[k] for k in ('id', 'ciudad', 'calidad', 'tipo')}
                p['publico'].update(precio=f[0], fecha=f[1], fuente=FUENTE_API, vigente=f[1] >= corte)
    return lecturas


def consultar_cotizaciones(db, catalogo, item_id):
    if item_id not in catalogo:
        raise ValueError('El ID del objeto no existe en el catálogo.')
    filas = {}
    with closing(conectar_base(db)) as con:
        actualizado = _estado(con, 'actualizado') if _existe(con) else None
        if actualizado:
            for ciudad, calidad, campo, precio, fecha in con.execute('''
                SELECT ciudad,calidad,campo,precio,fecha FROM precios_api
                WHERE item_id=? ORDER BY ciudad,calidad,campo''', (item_id,)):
                entrada = filas.setdefault((ciudad, calidad), {'ciudad': ciudad, 'calidad': calidad})
                entrada[campo] = {'precio': precio, 'fecha': fecha}
    return {'item_id': item_id, 'fuente': FUENTE_API, 'region': REGION_API, 'actualizado': actualizado,
            'cotizaciones': list(filas.values())}
