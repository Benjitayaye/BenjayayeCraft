"""Una base por usuario para que lector y web compartan precios entre carpetas."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def carpeta_datos():
    personalizada=os.environ.get('BENJAYAYE_DATOS')
    if personalizada:return Path(personalizada).expanduser().resolve()
    if os.name=='nt':
        base=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')
    else:
        base=Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local'/'share')
    return base/'BenjayayeCraft'


def importar_base(origen,destino):
    """Importa sin borrar el original ni reemplazar precios manuales existentes."""
    origen=Path(origen).resolve();destino=Path(destino).resolve()
    if not origen.is_file() or origen==destino:return
    destino.parent.mkdir(parents=True,exist_ok=True)
    # El hash incluye el WAL, si existe, porque una base abierta puede tener cambios allí.
    import hashlib
    huella=hashlib.sha256()
    for p in (origen,Path(str(origen)+'-wal')):
        if p.is_file():huella.update(p.read_bytes())
    firma=huella.hexdigest()
    with closing(sqlite3.connect(destino,timeout=15,uri=True)) as con:
        con.execute('ATTACH DATABASE ? AS anterior',(origen.as_uri()+'?mode=ro',))
        with con:
            con.execute('CREATE TABLE IF NOT EXISTS bases_importadas(ruta TEXT PRIMARY KEY,firma TEXT NOT NULL)')
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT firma FROM bases_importadas WHERE ruta=?',(str(origen),)).fetchone()==(firma,):return
            tablas={r[0] for r in con.execute("SELECT name FROM anterior.sqlite_master WHERE type='table'")}
            for tabla in ('precios','lotes','precios_lector','historial_lector','referencias_mercado','historial_mercado','puntos_historial_mercado'):
                if tabla not in tablas:continue
                esquema=con.execute('SELECT sql FROM anterior.sqlite_master WHERE type=\'table\' AND name=?',(tabla,)).fetchone()[0]
                if not con.execute('SELECT 1 FROM main.sqlite_master WHERE type=\'table\' AND name=?',(tabla,)).fetchone():
                    con.execute(esquema)
                if tabla in ('precios','lotes'):
                    con.execute(f'INSERT OR IGNORE INTO main.{tabla} SELECT * FROM anterior.{tabla}')
                elif tabla=='precios_lector':
                    con.execute('''INSERT INTO main.precios_lector SELECT * FROM anterior.precios_lector WHERE 1
                        ON CONFLICT(item_id,ciudad,calidad) DO UPDATE SET
                        venta=excluded.venta,compra=excluded.compra,fecha=excluded.fecha,metodo=excluded.metodo
                        WHERE excluded.fecha > precios_lector.fecha''')
                elif tabla in ('referencias_mercado','historial_mercado','puntos_historial_mercado'):
                    clave='item_id,ciudad,calidad'+(',periodo' if tabla!='referencias_mercado' else '')
                    campos=['fecha','datos'] if tabla!='historial_mercado' else ['fecha','precio_medio','aproximado','punto_precio','punto_vendido','imagen','firma']
                    cambios=','.join(f'{c}=excluded.{c}' for c in campos)
                    con.execute(f'''INSERT INTO main.{tabla} SELECT * FROM anterior.{tabla} WHERE 1
                        ON CONFLICT({clave}) DO UPDATE SET {cambios}
                        WHERE excluded.fecha > {tabla}.fecha''')
                else:
                    columnas='item_id,ciudad,calidad,venta,compra,cantidad_venta,cantidad_compra,fecha,metodo'
                    iguales=' AND '.join(f'n.{c} IS a.{c}' for c in columnas.split(','))
                    con.execute(f'''INSERT INTO main.historial_lector ({columnas})
                        SELECT {','.join('a.'+c for c in columnas.split(','))} FROM anterior.historial_lector a
                        WHERE NOT EXISTS(SELECT 1 FROM main.historial_lector n WHERE {iguales})''')
            con.execute('INSERT OR REPLACE INTO bases_importadas VALUES (?,?)',(str(origen),firma))


def obtener_db(raiz):
    destino=carpeta_datos()/'precios.sqlite3'
    destino.parent.mkdir(parents=True,exist_ok=True)
    importar_base(Path(raiz)/'datos'/'precios.sqlite3',destino)
    return destino
