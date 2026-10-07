"""Lecturas confirmadas, separadas de los precios manuales y de la API."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

CIUDADES_LECTOR = ('Martlock', 'Bridgewatch', 'Lymhurst', 'Fort Sterling', 'Thetford', 'Caerleon', 'Brecilien', 'Black Market')


def abrir(db):
    Path(db).parent.mkdir(parents=True,exist_ok=True)
    con = sqlite3.connect(db, timeout=10)
    con.execute('''CREATE TABLE IF NOT EXISTS precios_lector (
        item_id TEXT NOT NULL, ciudad TEXT NOT NULL, calidad INTEGER NOT NULL,
        venta INTEGER, compra INTEGER, fecha TEXT NOT NULL, metodo TEXT NOT NULL,
        PRIMARY KEY(item_id, ciudad, calidad))''')
    con.execute('''CREATE TABLE IF NOT EXISTS historial_lector (
        id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL,
        ciudad TEXT NOT NULL, calidad INTEGER NOT NULL, venta INTEGER, compra INTEGER,
        cantidad_venta INTEGER, cantidad_compra INTEGER, fecha TEXT NOT NULL, metodo TEXT NOT NULL)''')
    con.execute('CREATE TABLE IF NOT EXISTS estado_lector (id INTEGER PRIMARY KEY CHECK(id=1),datos TEXT NOT NULL)')
    return con


def _estado(con,datos,estado):
    campos=('item_id','base_id','ciudad','calidad','venta','compra','fecha')
    salida={k:datos.get(k) for k in campos}
    salida.update(estado=estado,recibida=datetime.now(timezone.utc).isoformat())
    anterior=con.execute('SELECT datos FROM estado_lector WHERE id=1').fetchone()
    if anterior:
        anterior=json.loads(anterior[0])
        # Una captura idéntica no necesita despertar todas las páginas cuatro
        # veces por segundo. El latido conserva visible la actividad del lector.
        mismos=all(anterior.get(k)==salida.get(k) for k in campos if k!='fecha') and anterior.get('estado')==estado
        try:edad=(datetime.now(timezone.utc)-datetime.fromisoformat(anterior['recibida'])).total_seconds()
        except (KeyError,ValueError,TypeError):edad=10
        if mismos and 0<=edad<2:return
    con.execute('INSERT OR REPLACE INTO estado_lector VALUES(1,?)',(json.dumps(salida,ensure_ascii=False),))


def registrar_estado_lector(db,datos):
    """La web ve una lectura pendiente sin usarla como precio confirmado."""
    if datos.get('prueba'):return
    with closing(abrir(db)) as con, con:
        fila=con.execute('SELECT venta,compra FROM precios_lector WHERE item_id=? AND ciudad=? AND calidad=?',
                         (datos.get('item_id'),datos.get('ciudad'),datos.get('calidad'))).fetchone()
        estado='guardado' if fila and fila==(datos.get('venta'),datos.get('compra')) else 'pendiente'
        _estado(con,datos,estado)


def guardar_lectura(db, catalogo, datos):
    id, ciudad, calidad = datos.get('item_id'), datos.get('ciudad'), datos.get('calidad')
    if id not in catalogo or ciudad not in CIUDADES_LECTOR or type(calidad) is not int or not 1 <= calidad <= 5:
        raise ValueError('Revisa objeto, mercado y calidad antes de guardar')
    precios = []
    for campo in ('venta', 'compra'):
        valor = datos.get(campo)
        if valor is not None and (type(valor) is not int or not 0 < valor <= 10**12):
            raise ValueError('Los precios deben ser enteros positivos o estar vacíos')
        precios.append(valor)
    if all(p is None for p in precios):
        raise ValueError('Debe haber al menos un precio para guardar')
    cantidades=[]
    for campo in ('cantidad_venta','cantidad_compra'):
        valor=datos.get(campo)
        if valor is not None and (type(valor) is not int or not 0 < valor <= 10**12):
            raise ValueError('Las cantidades deben ser enteros positivos o estar vacías')
        cantidades.append(valor)
    fecha = datos.get('fecha')
    try:
        instante = datetime.fromisoformat(fecha)
        if instante.tzinfo is None or instante.year < 2000:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('La lectura necesita su fecha de captura') from None
    metodo = 'automatico' if datos.get('metodo') == 'automatico' else 'confirmado'
    fecha_utc=instante.astimezone(timezone.utc).isoformat()
    with closing(abrir(db)) as con, con:
        previo=con.execute('SELECT item_id,ciudad,calidad,venta,compra,cantidad_venta,cantidad_compra,fecha,metodo FROM historial_lector ORDER BY id DESC LIMIT 1').fetchone()
        registro=(id,ciudad,calidad,*precios,*cantidades,fecha_utc,metodo)
        if registro!=previo:
            con.execute('INSERT INTO historial_lector (item_id,ciudad,calidad,venta,compra,cantidad_venta,cantidad_compra,fecha,metodo) VALUES (?,?,?,?,?,?,?,?,?)',registro)
        con.execute('''INSERT INTO precios_lector VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id,ciudad,calidad) DO UPDATE SET
            venta=excluded.venta, compra=excluded.compra, fecha=excluded.fecha, metodo=excluded.metodo
            WHERE excluded.fecha >= precios_lector.fecha''',
            (id, ciudad, calidad, *precios, fecha_utc, metodo))
        _estado(con,dict(datos,fecha=fecha_utc),'guardado')
    from historial_mercado import guardar_referencias
    guardar_referencias(db,catalogo,datos)
    return {'guardado': True, 'item_id': id}


def listar_historial(db):
    with closing(abrir(db)) as con:
        con.row_factory=sqlite3.Row
        return [dict(f) for f in con.execute('SELECT * FROM historial_lector ORDER BY id DESC LIMIT 200')]


def consultar_lecturas(db, catalogo, ids, ciudad, objeto, ciudad_objeto, calidad=1, tipo='venta'):
    if ciudad not in CIUDADES_LECTOR or ciudad_objeto not in CIUDADES_LECTOR or tipo not in ('venta', 'compra'):
        raise ValueError('Mercado o tipo de orden inválido')
    if not 1 <= calidad <= 5 or not 1 <= len(ids) <= 21 or any(id not in catalogo for id in ids) or objeto not in ids:
        raise ValueError('Objeto o calidad inválidos')
    salida = []
    with closing(abrir(db)) as con:
        for id in dict.fromkeys(ids):
            es_objeto = id == objeto
            mercado, nivel, lado = (ciudad_objeto, calidad, tipo) if es_objeto else (ciudad, 1, 'venta')
            fila = con.execute('SELECT venta, compra, fecha, metodo FROM precios_lector WHERE item_id=? AND ciudad=? AND calidad=?',
                               (id, mercado, nivel)).fetchone()
            precio = fila[0 if lado == 'venta' else 1] if fila else None
            disponibles=[]
            if precio is None:
                disponibles=[{'ciudad':c,'calidad':q,'venta':v,'compra':b,'fecha':f} for c,q,v,b,f in con.execute(
                    'SELECT ciudad,calidad,venta,compra,fecha FROM precios_lector WHERE item_id=? ORDER BY fecha DESC LIMIT 8',(id,))]
            salida.append({'id': id, 'precio': precio, 'fecha': fila[2] if fila and precio is not None else None,
                           'ciudad': mercado, 'calidad': nivel, 'tipo': lado, 'fuente': 'Lector de pantalla',
                           'disponibles':disponibles})
        fila_estado=con.execute('SELECT datos FROM estado_lector WHERE id=1').fetchone()
        estado=json.loads(fila_estado[0]) if fila_estado else None
        ultima=con.execute('SELECT item_id,ciudad,calidad,fecha FROM historial_lector ORDER BY id DESC LIMIT 1').fetchone()
        ultima=dict(zip(('item_id','ciudad','calidad','fecha'),ultima)) if ultima else None
    return {'servidor': 'Américas', 'ciudad': ciudad, 'consultado': datetime.now(timezone.utc).isoformat(),
            'precios': salida, 'fuente': 'Lector de pantalla','estado_lector':estado,'ultima_guardada':ultima}
