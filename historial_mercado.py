"""Persistencia del gráfico leído del juego y de las filas de referencia.

Ningún promedio histórico ni fila secundaria modifica precios_lector.
Este módulo también funciona sin Pillow en la calculadora.
"""
import base64
import hashlib
import json
import sqlite3
import struct
import math
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from almacenamiento import conectar_base

PERIODOS=('24h','7d','4w')


def abrir(db):
    Path(db).parent.mkdir(parents=True,exist_ok=True)
    con=conectar_base(db)
    crear_tablas(con)
    return con


def crear_tablas(con):
    con.execute('''CREATE TABLE IF NOT EXISTS referencias_mercado (
        item_id TEXT,ciudad TEXT,calidad INTEGER,fecha TEXT,datos TEXT,
        PRIMARY KEY(item_id,ciudad,calidad))''')
    con.execute('''CREATE TABLE IF NOT EXISTS historial_mercado (
        item_id TEXT,ciudad TEXT,calidad INTEGER,periodo TEXT,fecha TEXT,
        precio_medio INTEGER,aproximado INTEGER,punto_precio INTEGER,punto_vendido INTEGER,
        imagen BLOB,firma TEXT,PRIMARY KEY(item_id,ciudad,calidad,periodo))''')
    con.execute('''CREATE TABLE IF NOT EXISTS puntos_historial_mercado (
        item_id TEXT,ciudad TEXT,calidad INTEGER,periodo TEXT,fecha TEXT,datos TEXT,
        PRIMARY KEY(item_id,ciudad,calidad,periodo))''')


def validar_identidad(catalogo,id,ciudad,calidad):
    from precios_guardados import CIUDADES_LECTOR
    if id not in catalogo or ciudad not in CIUDADES_LECTOR or type(calidad) is not int or not 1<=calidad<=5:
        raise ValueError('Revisa objeto, ciudad y calidad del historial')


def entero(valor,cero=False):
    if valor is None:return None
    if type(valor) is not int or not (0 if cero else 1)<=valor<=10**12:raise ValueError('Cantidad histórica inválida')
    return valor


def validar_puntos(puntos):
    if not isinstance(puntos,list) or len(puntos)>48:raise ValueError('Demasiados puntos del historial')
    salida=[];vistos=set()
    for p in puntos:
        if not isinstance(p,dict):raise ValueError('Punto histórico inválido')
        indice=p.get('posicion')
        if type(indice) is not int or not 1<=indice<=48 or indice in vistos:raise ValueError('Posición histórica inválida')
        vistos.add(indice)
        for eje in ('x','y'):
            v=p.get(eje)
            if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1:raise ValueError('Coordenada histórica inválida')
        precio=entero(p.get('precio'),True);vendido=entero(p.get('vendido'),True)
        if precio is None:raise ValueError('Falta el precio leído del punto')
        salida.append({'posicion':indice,'x':p['x'],'y':p['y'],'precio':precio,'vendido':vendido})
    return salida


def guardar_referencias(db,catalogo,datos,conexion=None):
    if datos.get('prueba'):return
    tablas=datos.get('referencias') or {};historia=datos.get('historial_mercado')
    if not tablas and not historia:return
    id,ciudad,calidad=(datos.get(k) for k in ('item_id','ciudad','calidad'))
    validar_identidad(catalogo,id,ciudad,calidad)
    fecha=datetime.fromisoformat(datos['fecha'])
    if fecha.tzinfo is None:raise ValueError('Falta fecha de la captura')
    fecha=fecha.astimezone(timezone.utc).isoformat();limpias={}
    for lado,filas in tablas.items():
        if lado not in ('venta','compra') or not isinstance(filas,list) or not 1<=len(filas)<=20:raise ValueError('Lista de referencia inválida')
        limpias[lado]=[{'fila':i+1,'precio':entero(f.get('precio')),'cantidad':entero(f.get('cantidad')),
                       'texto_precio':str(f.get('texto_precio',''))[:80],'texto_cantidad':str(f.get('texto_cantidad',''))[:40],
                       'uso':f.get('uso') if f.get('uso') in ('precio','referencia','revisar') else ('precio' if i==0 else 'referencia')} for i,f in enumerate(filas)]
    registro=None;puntos=[]
    if historia:
        periodo=historia.get('periodo')
        if periodo not in PERIODOS:raise ValueError('Periodo histórico inválido')
        texto=historia.get('imagen_png','')
        if not isinstance(texto,str) or len(texto)>3*1024*1024:raise ValueError('Captura del historial demasiado grande')
        imagen=base64.b64decode(texto,validate=True)
        if not imagen.startswith(b'\x89PNG\r\n\x1a\n') or len(imagen)<24:raise ValueError('La referencia debe ser una captura PNG')
        w,h=struct.unpack('>II',imagen[16:24])
        if not 1<=w<=1400 or not 1<=h<=900:raise ValueError('Tamaño de historial inválido')
        valores=[entero(historia.get(k),True) for k in ('precio_medio','punto_precio','punto_vendido')]
        puntos=validar_puntos(historia.get('puntos',[]))
        registro=(id,ciudad,calidad,periodo,fecha,valores[0],int(bool(historia.get('aproximado'))),valores[1],valores[2],imagen,hashlib.sha256(imagen).hexdigest())
    def escribir(con):
        if limpias:
            con.execute('''INSERT INTO referencias_mercado VALUES(?,?,?,?,?)
                ON CONFLICT(item_id,ciudad,calidad) DO UPDATE SET fecha=excluded.fecha,datos=excluded.datos
                WHERE excluded.fecha>=referencias_mercado.fecha''',(id,ciudad,calidad,fecha,json.dumps(limpias,ensure_ascii=False)))
        if registro:
            con.execute('''INSERT INTO historial_mercado VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(item_id,ciudad,calidad,periodo) DO UPDATE SET fecha=excluded.fecha,
                precio_medio=excluded.precio_medio,aproximado=excluded.aproximado,punto_precio=excluded.punto_precio,
                punto_vendido=excluded.punto_vendido,imagen=excluded.imagen,firma=excluded.firma
                WHERE excluded.fecha>=historial_mercado.fecha''',registro)
            con.execute('''INSERT INTO puntos_historial_mercado VALUES(?,?,?,?,?,?)
                ON CONFLICT(item_id,ciudad,calidad,periodo) DO UPDATE SET fecha=excluded.fecha,datos=excluded.datos
                WHERE excluded.fecha>=puntos_historial_mercado.fecha''',
                (id,ciudad,calidad,periodo,fecha,json.dumps(puntos,ensure_ascii=False)))
    if conexion is not None:
        crear_tablas(conexion);escribir(conexion)
    else:
        with closing(abrir(db)) as con,con:escribir(con)


def consultar_historial(db,catalogo,id,ciudad,calidad,periodo):
    validar_identidad(catalogo,id,ciudad,calidad)
    if periodo not in PERIODOS:raise ValueError('Periodo histórico inválido')
    with closing(abrir(db)) as con:
        con.row_factory=sqlite3.Row
        fila=con.execute('SELECT fecha,precio_medio,aproximado,punto_precio,punto_vendido,firma FROM historial_mercado WHERE item_id=? AND ciudad=? AND calidad=? AND periodo=?',(id,ciudad,calidad,periodo)).fetchone()
        referencia=con.execute('SELECT fecha,datos FROM referencias_mercado WHERE item_id=? AND ciudad=? AND calidad=?',(id,ciudad,calidad)).fetchone()
        disponibles=[f[0] for f in con.execute('SELECT periodo FROM historial_mercado WHERE item_id=? AND ciudad=? AND calidad=?',(id,ciudad,calidad))]
        puntos=con.execute('SELECT fecha,datos FROM puntos_historial_mercado WHERE item_id=? AND ciudad=? AND calidad=? AND periodo=?',(id,ciudad,calidad,periodo)).fetchone()
    historia=dict(fila) if fila else None
    if historia:historia['puntos']=json.loads(puntos['datos']) if puntos and puntos['fecha']==historia['fecha'] else []
    return {'item_id':id,'ciudad':ciudad,'calidad':calidad,'periodo':periodo,
            'historial':historia,'periodos_disponibles':disponibles,
            'referencias':json.loads(referencia['datos']) if referencia else {},'referencia_fecha':referencia['fecha'] if referencia else None}


def imagen_historial(db,catalogo,id,ciudad,calidad,periodo):
    validar_identidad(catalogo,id,ciudad,calidad)
    if periodo not in PERIODOS:raise ValueError('Periodo histórico inválido')
    with closing(abrir(db)) as con:
        fila=con.execute('SELECT imagen FROM historial_mercado WHERE item_id=? AND ciudad=? AND calidad=? AND periodo=?',(id,ciudad,calidad,periodo)).fetchone()
        return fila[0] if fila else None
