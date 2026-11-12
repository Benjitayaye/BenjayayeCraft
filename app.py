"""Calculadora local de fabricación para Albion Online (Python 3.10+).

Ejecuta ``python app.py`` y abre la dirección que se imprime. No instala
dependencias: Python sirve la interfaz y realiza el cálculo de economía.
"""

from __future__ import annotations

import json
import csv
import gzip
import io
import unicodedata
import hmac
import ipaddress
import os
import re
import secrets
import sqlite3
import socket
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.cookies import CookieError,SimpleCookie
from contextlib import closing
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hashlib import sha256
from functools import lru_cache
from pathlib import Path
from mercado import consultar_precios
from precios_guardados import consultar_lecturas
from historial_mercado import consultar_historial,imagen_historial
from almacenamiento import obtener_db,conectar_base
from archivos_web import enviar_archivo,acepta_gzip
from precios_api import (MAX_BYTES_API, RUTA_ENVIO_API, RUTA_PUBLICOS, agregar_publicos,
                         consultar_cotizaciones, recibir_cotizaciones, token_valido)


ROOT = Path(__file__).resolve().parent
CATALOGO = json.loads((ROOT / "datos" / "catalogo_es.json").read_text(encoding="utf-8"))
POR_ID = {item["id"]: item for item in CATALOGO}
VALORES_OBJETOS = json.loads((ROOT / 'datos' / 'valores_objetos.json').read_text(encoding='utf-8'))['valores']
DATOS_COMIDA = json.loads((ROOT / "datos" / "recetas_comida.json").read_text(encoding="utf-8"))
RECETAS_COMIDA = DATOS_COMIDA["recetas"]
SIN_DEVOLUCION = {m["id"] for r in RECETAS_COMIDA.values() for m in r["materiales"] if not m["retorna"]}
DB_PRECIOS = obtener_db(ROOT)
ID_SEGURO = re.compile(r"^[A-Z0-9_@]+$")
SITIO_IMAGENES = "https://render.albiononline.com/v1/item/"
SITIO_RECETAS = "https://gameinfo.albiononline.com/api/gameinfo/items/"
RECETAS_EN_MEMORIA: dict[str, dict] = {}
DATOS_EQUIPO = json.loads((ROOT / 'datos' / 'recetas_equipo.json').read_text(encoding='utf-8'))
RECETAS_EQUIPO = DATOS_EQUIPO['recetas']


def _abrir_precios() -> sqlite3.Connection:
    """Una conexión por solicitud permite guardar desde varios navegadores."""
    DB_PRECIOS.parent.mkdir(parents=True, exist_ok=True)
    conexion = conectar_base(DB_PRECIOS)
    try:
        conexion.execute("CREATE TABLE IF NOT EXISTS precios (id TEXT PRIMARY KEY, precio TEXT NOT NULL)")
        conexion.execute("""CREATE TABLE IF NOT EXISTS lotes (
            id TEXT PRIMARY KEY, datos TEXT NOT NULL,
            creado TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )""")
        conexion.execute('CREATE TABLE IF NOT EXISTS compras_plan (id TEXT PRIMARY KEY, cantidad INTEGER NOT NULL CHECK(cantidad>0))')
    except Exception:
        conexion.close()
        raise
    return conexion


def listar_precios() -> dict[str, float]:
    with closing(_abrir_precios()) as conexion:
        return {id: float(Decimal(precio)) for id, precio in conexion.execute("SELECT id, precio FROM precios")}


def guardar_precio(item_id: str, precio: object) -> dict:
    if item_id not in POR_ID or not ID_SEGURO.fullmatch(item_id):
        raise ValueError("El material no figura en el catálogo")
    valor = numero(precio, "Precio")
    if valor > Decimal("1000000000000000"):
        raise ValueError("Precio demasiado alto")
    with closing(_abrir_precios()) as conexion:
        with conexion:
            conexion.execute(
                "INSERT INTO precios (id, precio) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET precio=excluded.precio",
                (item_id, str(valor)),
            )
    return {"id": item_id, "precio": float(valor)}


def borrar_precio(item_id: str) -> dict:
    if item_id not in POR_ID or not ID_SEGURO.fullmatch(item_id):
        raise ValueError("El material no figura en el catálogo")
    with closing(_abrir_precios()) as conexion:
        with conexion:
            conexion.execute("DELETE FROM precios WHERE id = ?", (item_id,))
    return {"id": item_id, "eliminado": True}


def numero(valor: object, nombre: str, minimo: Decimal = Decimal("0")) -> Decimal:
    """Acepta cantidades y plata; rechaza NaN, infinito y negativos."""
    try:
        resultado = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{nombre}: introduce un número válido") from exc
    if not resultado.is_finite() or resultado < minimo:
        raise ValueError(f"{nombre}: el valor debe ser mayor o igual a {minimo}")
    return resultado


def admite_devolucion(material: dict) -> bool:
    identificador = material["id"]
    return (material.get("retorna", True) is True
            and "_ARTEFACT_" not in identificador and identificador not in SIN_DEVOLUCION)


def cantidad_tras_devolucion(bruta: Decimal, retorno: Decimal, retorna: bool) -> Decimal:
    return bruta * (100 - retorno) / 100 if retorna else bruta


def redondear_compra(cantidad: Decimal) -> int:
    """Redondeo solicitado: 9,2 → 9; 9,5 → 10; 9,76 → 10."""
    return int(cantidad.to_integral_value(rounding=ROUND_HALF_UP))


def normalizar_lote(datos: dict) -> tuple[str, dict]:
    """La misma validación sirve para crear, editar y recuperar un lote."""
    if not isinstance(datos, dict):
        raise ValueError("Introduce los datos del lote")
    lote_id = str(datos.get("solicitud_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", lote_id):
        raise ValueError("Identificador de guardado inválido")
    item_id = datos.get("item_id")
    if not isinstance(item_id, str) or item_id not in POR_ID:
        raise ValueError("Selecciona un objeto del catálogo")
    if datos.get("receta_completa") is not True:
        raise ValueError("Completa y confirma la receta antes de guardar el lote")
    cantidad = numero(datos.get("cantidad"), "Cantidad", Decimal("1"))
    if cantidad != cantidad.to_integral_value() or cantidad > 1_000_000:
        raise ValueError("Cantidad: usa un entero entre 1 y 1.000.000")
    produccion = unidades_por_crafteo(datos)
    retorno = numero(datos.get("retorno", 0), "Devolución")
    if retorno > 100:
        raise ValueError("Devolución: usa un porcentaje entre 0 y 100 %")
    materiales = datos.get("materiales")
    if not isinstance(materiales, list) or not 1 <= len(materiales) <= 20:
        raise ValueError("El lote debe incluir entre 1 y 20 materiales")
    cantidades: dict[tuple[str, bool], Decimal] = {}
    for material in materiales:
        if not isinstance(material, dict):
            raise ValueError("Material del lote inválido")
        identificador = material.get("id")
        if not isinstance(identificador, str) or identificador not in POR_ID:
            raise ValueError("El material no figura en el catálogo")
        unidades = numero(material.get("cantidad"), "Unidades por objeto", Decimal("0.000001"))
        if unidades > 1_000_000 or unidades != unidades.quantize(Decimal("0.000001")):
            raise ValueError("Usa hasta 1.000.000 de unidades y como máximo 6 decimales")
        clave = (identificador, admite_devolucion(material))
        cantidades[clave] = cantidades.get(clave, Decimal(0)) + unidades
    copia = {
        "item_id": item_id, "cantidad": int(cantidad * produccion),
        "crafteos": int(cantidad), "unidades_por_crafteo": int(produccion),
        "retorno": str(retorno),
        "materiales": [{"id": id, "cantidad": str(valor), "retorna": retorna}
                       for (id, retorna), valor in sorted(cantidades.items())],
    }
    if datos.get('economia') is not None:
        copia['economia'] = normalizar_economia_lote(datos['economia'], copia['materiales'])
    return lote_id,copia


def guardar_lote(datos: dict) -> dict:
    """Guarda una copia de la receta; reintentar una solicitud no la duplica."""
    lote_id,copia=normalizar_lote(datos)
    contenido = json.dumps(copia, ensure_ascii=False, sort_keys=True)
    with closing(_abrir_precios()) as conexion:
        with conexion:
            conexion.execute("INSERT OR IGNORE INTO lotes (id, datos) VALUES (?, ?)", (lote_id, contenido))
            anterior = conexion.execute("SELECT datos FROM lotes WHERE id = ?", (lote_id,)).fetchone()[0]
            if anterior != contenido:
                raise ValueError("Este guardado ya existe con otra receta; vuelve a intentarlo")
    return listar_plan()


def listar_plan() -> dict:
    """Suma el consumo neto exacto y redondea la compra de cada material."""
    with closing(_abrir_precios()) as conexion,conexion:
        conexion.execute('BEGIN')
        filas = tuple(conexion.execute("SELECT id, datos, creado FROM lotes ORDER BY rowid"))
        compras = tuple(conexion.execute('SELECT id,cantidad FROM compras_plan ORDER BY id'))
    plan=deepcopy(_base_plan(filas));cantidades=dict(compras)
    for material in plan['materiales']:
        material.update(estado_compra(material['a_comprar'],cantidades.get(material['id'])))
    return plan


def _base_plan(filas):
    # Las compras son una capa breve; cambiar una casilla no recalcula lotes.
    return _plan_en_memoria(filas,(),tuple(VALORES_OBJETOS.items()),id(POR_ID),id(resumir_economia_lote))


def estado_compra(necesarios,cantidad):
    comprada=cantidad or 0
    return dict(cantidad_marcada=cantidad,cantidad_comprada=comprada,
                faltan=max(0,necesarios-comprada),sobran=max(0,comprada-necesarios),
                comprado=necesarios>0 and comprada>=necesarios)


@lru_cache(maxsize=1)
def _plan_en_memoria(filas,compras_guardadas,_valores,_catalogo,_calculo):
    compras=dict(compras_guardadas)
    lotes, objetos, acumulados = [], {}, {}
    finanzas = {'lotes_completos': 0, 'lotes_pendientes': 0}
    sumas = {k: Decimal(0) for k in ('ganancia', 'impuesto_estacion', 'inversion', 'ingreso_neto')}
    # Cada importe tiene cobertura propia: una estación pendiente no oculta
    # las ventas conocidas ni convierte todos los gastos en cero.
    claves = ('materiales_netos', 'impuesto_estacion', 'otros_costos', 'inversion',
              'ingreso_bruto', 'impuesto_venta', 'ingreso_neto', 'gasto_total', 'ganancia')
    disponibles = {k: Decimal(0) for k in claves}
    cobertura = dict.fromkeys(claves, 0)
    for lote_id, contenido, creado in filas:
        lote = json.loads(contenido)
        item_id, cantidad = lote["item_id"], lote["cantidad"]
        crafteos = lote.get("crafteos", cantidad)
        produccion = lote.get("unidades_por_crafteo", 1)
        # Los lotes antiguos no guardaban el porcentaje. Se conservan con 0 %.
        retorno = Decimal(lote.get("retorno", "0"))
        objeto = objetos.setdefault(item_id, {"id": item_id, "nombre": POR_ID[item_id]["es"], "cantidad": 0})
        objeto["cantidad"] += cantidad
        por_material = {}
        for material in lote["materiales"]:
            id = material["id"]
            por_crafteo = Decimal(material["cantidad"])
            total = por_crafteo * crafteos
            neta = cantidad_tras_devolucion(total, retorno, admite_devolucion(material))
            fila = por_material.setdefault(id, {"por_crafteo": Decimal(0), "total": Decimal(0), "cantidad_neta": Decimal(0)})
            fila["por_crafteo"] += por_crafteo
            fila["total"] += total
            fila["cantidad_neta"] += neta
        detalle = []
        for id, fila in por_material.items():
            por_crafteo, total, neta = fila["por_crafteo"], fila["total"], fila["cantidad_neta"]
            detalle.append({"id": id, "nombre": POR_ID[id]["es"], "por_objeto": float(por_crafteo / produccion),
                            "por_crafteo": float(por_crafteo), "total": float(total),
                            "cantidad_neta": float(neta), "a_comprar": redondear_compra(neta)})
            resumen = acumulados.setdefault(id, {"id": id, "nombre": POR_ID[id]["es"], "total": Decimal(0),
                                                  "cantidad_neta": Decimal(0), "destinos": {}})
            resumen["total"] += total
            resumen["cantidad_neta"] += neta
            destino = resumen["destinos"].setdefault(item_id, {
                "item_id": item_id, "nombre": POR_ID[item_id]["es"], "cantidad_items": 0, "materiales": Decimal(0),
                "cantidad_neta": Decimal(0),
            })
            destino["cantidad_items"] += cantidad
            destino["materiales"] += total
            destino["cantidad_neta"] += neta
        economico = resumir_economia_lote(lote)
        finanzas['lotes_completos' if economico['completa'] else 'lotes_pendientes'] += 1
        if economico['completa']:
            for clave in sumas:sumas[clave] += Decimal(str(economico[clave]))
        for clave in claves:
            if economico.get(clave) is not None:
                disponibles[clave] += Decimal(str(economico[clave]))
                cobertura[clave] += 1
        lotes.append({"id": lote_id, "item_id": item_id, "nombre": POR_ID[item_id]["es"],
                      'revision':revision_lote(lote),
                      "cantidad": cantidad, "crafteos": crafteos, "unidades_por_crafteo": produccion,
                      "retorno": float(retorno), "retorno_guardado": "retorno" in lote,
                      "creado": creado, "materiales": detalle, 'economia': lote.get('economia'),
                      'resumen_economico': economico})
    materiales = []
    for resumen in acumulados.values():
        resumen["a_comprar"] = redondear_compra(resumen["cantidad_neta"])
        resumen['cantidad_marcada'] = compras.get(resumen['id'])
        resumen['cantidad_comprada'] = compras.get(resumen['id'],0)
        resumen['faltan'] = max(0,resumen['a_comprar']-resumen['cantidad_comprada'])
        resumen['sobran'] = max(0,resumen['cantidad_comprada']-resumen['a_comprar'])
        resumen['comprado'] = resumen['a_comprar']>0 and resumen['faltan']==0
        resumen["total"] = float(resumen["total"])
        resumen["cantidad_neta"] = float(resumen["cantidad_neta"])
        resumen["destinos"] = [dict(d, materiales=float(d["materiales"]),
                                    cantidad_neta=float(d["cantidad_neta"]), a_comprar=redondear_compra(d["cantidad_neta"]))
                               for d in resumen["destinos"].values()]
        materiales.append(resumen)
    finanzas.update({k: float(v) for k,v in sumas.items()})
    finanzas['ganancia_total'] = float(sumas['ganancia']) if not finanzas['lotes_pendientes'] else None
    finanzas['totales'] = {k: float(v) if cobertura[k] == len(lotes) else None for k,v in disponibles.items()}
    finanzas['disponibles'] = {k: float(v) for k,v in disponibles.items()}
    finanzas['cobertura'] = cobertura
    return {"lotes": lotes, "objetos": list(objetos.values()), "materiales": materiales, 'finanzas': finanzas,
            "total_items": sum(o["cantidad"] for o in objetos.values())}


def normalizar_economia_lote(economia: dict, materiales: list) -> dict:
    if not isinstance(economia, dict):raise ValueError('Precios y costes del lote inválidos')
    salida = {}
    for clave, nombre in [('precio_venta','Precio de venta'),('tasa_estacion','Tasa de estación'),
                           ('valor_item','Valor del objeto'),('comision','Comisión'),('otros_costos','Otros costes')]:
        valor = numero(economia.get(clave,0), nombre)
        if valor > (100 if clave=='comision' else 10**15):raise ValueError(nombre+': valor demasiado alto')
        salida[clave] = str(valor)
    calidad = economia.get('calidad',1)
    if type(calidad) is not int or not 1<=calidad<=5:raise ValueError('Selecciona una calidad entre 1 y 5')
    salida['calidad'] = calidad
    precios = economia.get('precios_materiales',{})
    ids = {m['id'] for m in materiales}
    if not isinstance(precios,dict) or set(precios)-ids:raise ValueError('Los precios deben corresponder a los materiales de este lote')
    salida['precios_materiales'] = {}
    for identificador in ids:
        precio=numero(precios.get(identificador,0),'Precio del material')
        if precio>10**15:raise ValueError('Precio del material demasiado alto')
        salida['precios_materiales'][identificador]=str(precio)
    for clave in ('modo','ciudad_materiales','ciudad_objeto','tipo'):
        texto = str(economia.get(clave,''))
        if len(texto)>80:raise ValueError('Contexto de precios demasiado largo')
        salida[clave]=texto
    return salida


def resumir_economia_lote(lote: dict) -> dict:
    economia = lote.get('economia')
    vacio = {'completa':False, 'faltantes':['Precios y costes no guardados'], 'precio_venta':None,
             'impuesto_estacion':None,'estacion_por_objeto':None,'inversion':None,'ingreso_neto':None,'ganancia':None,
             'materiales_netos':None,'otros_costos':None,'ingreso_bruto':None,'impuesto_venta':None,
             'gasto_total':None,'valor_item':valor_objeto_incluido(lote['item_id']), 'valor_item_automatico':False}
    if economia is None:return vacio
    materiales=[dict(m,precio=economia['precios_materiales'].get(m['id'],0)) for m in lote['materiales']]
    datos=dict(economia,materiales=materiales,cantidad=lote.get('crafteos',lote['cantidad']),
               unidades_por_crafteo=lote.get('unidades_por_crafteo',1),retorno=lote.get('retorno',0),
               receta_completa=True,item_id=lote['item_id'])
    calculo=calcular_economia(datos)
    faltantes=[]
    if numero(economia['precio_venta'],'Precio')<=0:faltantes.append('Precio de venta')
    for m in materiales:
        if numero(m['precio'],'Precio')<=0:faltantes.append('Precio de '+POR_ID[m['id']]['es'])
    if not calculo['estacion_calculable']:faltantes.append('Valor del objeto para calcular la estación')
    resultado=dict(vacio,completa=not faltantes,faltantes=faltantes,precio_venta=float(economia['precio_venta']),
                   otros_costos=calculo['otros_costos'],valor_item=calculo['valor_item'],
                   valor_item_automatico=calculo['valor_item_automatico'])
    if calculo['estacion_calculable']:
        resultado.update(impuesto_estacion=calculo['impuesto_estacion'],estacion_por_objeto=calculo['estacion_por_objeto'])
    materiales_completos=all(numero(m['precio'],'Precio')>0 for m in materiales)
    if materiales_completos:
        resultado['materiales_netos']=calculo['materiales_netos']
        if calculo['estacion_calculable']:resultado['inversion']=calculo['inversion']
    if numero(economia['precio_venta'],'Precio')>0:
        resultado.update({k:calculo[k] for k in ('ingreso_bruto','impuesto_venta','ingreso_neto')})
    elif numero(economia['comision'],'Comisión')==0:
        resultado['impuesto_venta']=0
    if resultado['inversion'] is not None and resultado['impuesto_venta'] is not None:
        resultado['gasto_total']=calculo['gasto_total']
    if not faltantes:
        resultado.update(ganancia=calculo['ganancia'],gasto_total=calculo['gasto_total'])
    return resultado


def valor_objeto_incluido(item_id: str | None) -> int | None:
    """Valor base de la receta incluida, nunca precio de mercado ni poder del ítem."""
    return VALORES_OBJETOS.get(item_id) if isinstance(item_id,str) else None


def completar_economia_plan(contexto: dict) -> dict:
    """Rellena huecos desde las fuentes elegidas; conserva precios ya guardados."""
    if not isinstance(contexto,dict):raise ValueError('Contexto de precios inválido')
    # Validar incluso con la lista vacía y antes de modificar ningún lote.
    base=normalizar_economia_lote(contexto,[])
    # Estos campos pertenecen a cada objeto: nunca propagar el ítem abierto
    # en la calculadora como precio o valor de todos los lotes antiguos.
    base.update(precio_venta='0',valor_item='0',otros_costos='0')
    from precios_guardados import CIUDADES_LECTOR
    if base['modo'] not in ('manual','lector','ambos'):raise ValueError('Fuente de precios inválida')
    if any(base[c] not in CIUDADES_LECTOR for c in ('ciudad_materiales','ciudad_objeto')) or base['tipo'] not in ('venta','compra'):
        raise ValueError('Ciudad o tipo de precio inválidos')
    manuales=listar_precios()
    cache_lecturas={}
    with closing(_abrir_precios()) as conexion:
        filas=conexion.execute('SELECT id,datos FROM lotes ORDER BY rowid').fetchall()
        cambios=[]
        for lote_id,contenido in filas:
            lote=json.loads(contenido)
            anterior=lote.get('economia')
            e=deepcopy(anterior) if anterior is not None else dict(base,precios_materiales={})
            for clave in ('modo','ciudad_materiales','ciudad_objeto','tipo'):
                if not e.get(clave):e[clave]=base[clave]
            ids=list(dict.fromkeys([lote['item_id'],*(m['id'] for m in lote['materiales'])]))
            lecturas={}
            faltan_precios=numero(e.get('precio_venta',0),'Precio')<=0 or any(
                numero(e['precios_materiales'].get(m['id'],0),'Precio')<=0 for m in lote['materiales'])
            if e['modo']!='manual' and faltan_precios:
                usar_api=e['modo']=='ambos'
                clave=(tuple(ids),e['ciudad_materiales'],lote['item_id'],e['ciudad_objeto'],e['calidad'],e['tipo'],usar_api)
                if clave not in cache_lecturas:
                    leidos=consultar_lecturas(DB_PRECIOS,POR_ID,ids,e['ciudad_materiales'],lote['item_id'],
                                             e['ciudad_objeto'],e['calidad'],e['tipo'])
                    if usar_api:agregar_publicos(DB_PRECIOS,leidos)
                    cache_lecturas[clave]={p['id']:p['precio'] or (
                        p['publico']['precio'] if p.get('publico',{}).get('vigente') else None)
                        for p in leidos['precios']}
                lecturas=cache_lecturas[clave]
            def precio(id):
                if e['modo']!='manual' and (lecturas.get(id) or 0)>0:return lecturas[id]
                return manuales.get(id,0) if e['modo']!='lector' else 0
            if numero(e.get('precio_venta',0),'Precio')<=0:e['precio_venta']=str(precio(lote['item_id']))
            for m in lote['materiales']:
                if numero(e['precios_materiales'].get(m['id'],0),'Precio')<=0:
                    e['precios_materiales'][m['id']]=str(precio(m['id']))
            if numero(e.get('valor_item',0),'Valor')<=0 and valor_objeto_incluido(lote['item_id']):
                e['valor_item']=str(valor_objeto_incluido(lote['item_id']))
            e=normalizar_economia_lote(e,lote['materiales'])
            if e!=anterior:
                lote['economia']=e;cambios.append((json.dumps(lote,ensure_ascii=False,sort_keys=True),lote_id,contenido))
        # Un lector o un segundo navegador puede editar durante la consulta.
        # No sobrescribir esa edición con la copia que acabamos de completar.
        with conexion:conexion.executemany('UPDATE lotes SET datos=? WHERE id=? AND datos=?',cambios)
    return listar_plan()


def revision_lote(lote: dict) -> str:
    return sha256(json.dumps(lote,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def actualizar_economia_lote(lote_id: str, economia: dict, cantidad=None, retorno=None, revision=None) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',str(lote_id)):raise ValueError('Identificador de lote inválido')
    with closing(_abrir_precios()) as conexion:
        with conexion:
            conexion.execute('BEGIN IMMEDIATE')
            fila=conexion.execute('SELECT datos FROM lotes WHERE id=?',(lote_id,)).fetchone()
            if not fila:raise ValueError('El lote ya no existe. Actualiza la lista')
            lote=json.loads(fila[0])
            if revision is not None and (not isinstance(revision,str) or not hmac.compare_digest(revision,revision_lote(lote))):
                raise ValueError('El lote cambió en otra pestaña. Cancela y pulsa Actualizar lista antes de editarlo')
            payload={'solicitud_id':lote_id,'item_id':lote['item_id'],
                     'cantidad':cantidad if cantidad is not None else lote.get('crafteos',lote['cantidad']),
                     'retorno':retorno if retorno is not None else lote.get('retorno',0),
                     'unidades_por_crafteo':lote.get('unidades_por_crafteo',1),
                     'materiales':lote['materiales'],'receta_completa':True,'economia':economia}
            _,nuevo=normalizar_lote(payload)
            # Mantener la indicación histórica si solo se editaron precios.
            if retorno is None and 'retorno' not in lote:nuevo.pop('retorno',None)
            lote=nuevo
            conexion.execute('UPDATE lotes SET datos=? WHERE id=?',(json.dumps(lote,ensure_ascii=False,sort_keys=True),lote_id))
    return listar_plan()


def borrar_lote(lote_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", lote_id):
        raise ValueError("Identificador de lote inválido")
    with closing(_abrir_precios()) as conexion:
        with conexion:
            conexion.execute('BEGIN IMMEDIATE')
            fila=conexion.execute('SELECT id,datos,creado FROM lotes WHERE id=?',(lote_id,)).fetchone()
            conexion.execute("DELETE FROM lotes WHERE id = ?", (lote_id,))
    plan=listar_plan()
    if fila:plan['eliminado']={'id':fila[0],'datos':json.loads(fila[1]),'creado':fila[2]}
    return plan


def borrar_todos_lotes() -> dict:
    """Vacía los lotes en una transacción y conserva los precios y las compras."""
    with closing(_abrir_precios()) as conexion, conexion:
        borrados = conexion.execute("DELETE FROM lotes").rowcount
    return dict(listar_plan(), lotes_borrados=borrados)


FORMATO_RESPALDO='benjayaye-craft-calculadora'


def validar_cantidad_compra(cantidad: object, permitir_cero: bool = False) -> int:
    minimo=0 if permitir_cero else 1
    if type(cantidad) is not int or not minimo<=cantidad<=2**53-1:
        raise ValueError('La cantidad de compra debe ser un entero '+('mayor o igual a cero' if permitir_cero else 'positivo')+' válido')
    return cantidad


def guardar_compra_material(item_id: str, cantidad: object, comprado: object, resumido: bool = False) -> dict:
    """Compatibilidad con las casillas: completar la compra o volver a cero."""
    if type(comprado) is not bool:
        raise ValueError('Material o marca de compra inválidos')
    cantidad=validar_cantidad_compra(cantidad)
    return guardar_cantidad_comprada(item_id,cantidad,cantidad if comprado else 0,resumido)


def guardar_cantidad_comprada(item_id: str, cantidad: object, cantidad_comprada: object, resumido: bool = False) -> dict:
    """Conserva compras parciales y sobrantes, sin alterar el coste del plan."""
    if not isinstance(item_id,str) or item_id not in POR_ID:
        raise ValueError('Material de compra inválido')
    cantidad=validar_cantidad_compra(cantidad,permitir_cero=True)
    cantidad_comprada=validar_cantidad_compra(cantidad_comprada,permitir_cero=True)
    with closing(_abrir_precios()) as con,con:
        con.execute('BEGIN IMMEDIATE')
        filas=tuple(con.execute('SELECT id,datos,creado FROM lotes ORDER BY rowid'))
        material=next((m for m in _base_plan(filas)['materiales'] if m['id']==item_id),None)
        if material is None:raise ValueError('Este material no está en la lista de compra actual')
        if cantidad!=material['a_comprar']:
            raise ValueError('La cantidad a comprar cambió. Actualiza la lista y revisa la compra')
        if cantidad_comprada:
            con.execute('INSERT INTO compras_plan(id,cantidad) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET cantidad=excluded.cantidad',
                        (item_id,cantidad_comprada))
        else:con.execute('DELETE FROM compras_plan WHERE id=?',(item_id,))
    if resumido:
        return {'guardado':True,'material':dict(id=item_id,**estado_compra(cantidad,cantidad_comprada or None))}
    return listar_plan()


def desmarcar_compras() -> dict:
    with closing(_abrir_precios()) as con,con:con.execute('DELETE FROM compras_plan')
    return listar_plan()


def exportar_compras_csv(filtro='todos',busqueda='') -> bytes:
    if filtro not in ('todos','pendientes','completados'):raise ValueError('Filtro de compras inválido')
    if not isinstance(busqueda,str) or len(busqueda)>200:raise ValueError('Búsqueda de material demasiado larga')
    def normalizar(texto):return ''.join(c for c in unicodedata.normalize('NFD',texto.casefold()) if not unicodedata.combining(c))
    palabras=normalizar(busqueda).split()
    archivo=io.StringIO(newline='');escritor=csv.writer(archivo,delimiter=';')
    escritor.writerow(['Material','ID','Tier','Encantamiento','Necesarios','Comprados','Pendientes','Sobrantes','Destino'])
    def texto_seguro(texto):
        texto=str(texto)
        return "'"+texto if texto.lstrip().startswith(('=','+','-','@')) else texto
    for m in listar_plan()['materiales']:
        completo=m['comprado'] or m['a_comprar']==0
        if filtro=='pendientes' and completo or filtro=='completados' and not completo:continue
        tier=re.match(r'^T([1-8])_',m['id']);encanto=re.search(r'@([1-4])$',m['id'])
        etiqueta=f'T{tier[1]}.{encanto[1] if encanto else 0}' if tier else ''
        texto=normalizar(f'{m["nombre"]} {m["id"]} {etiqueta}')
        if not all(p in texto for p in palabras):continue
        destino=' | '.join(f'{d["cantidad_items"]} × {d["nombre"]} ({d["item_id"]})' for d in m['destinos'])
        escritor.writerow([texto_seguro(m['nombre']),m['id'],int(tier[1]) if tier else '',int(encanto[1]) if encanto else 0,
                            m['a_comprar'],m['cantidad_comprada'],m['faltan'],m['sobran'],texto_seguro(destino)])
    return archivo.getvalue().encode('utf-8-sig')


def exportar_respaldo() -> dict:
    """Copia consistente de lotes y precios manuales, sin datos del lector."""
    with closing(_abrir_precios()) as con,con:
        con.execute('BEGIN')
        precios=dict(con.execute('SELECT id,precio FROM precios'))
        lotes=[{'id':id,'datos':json.loads(datos),'creado':creado}
               for id,datos,creado in con.execute('SELECT id,datos,creado FROM lotes ORDER BY rowid')]
        compras=dict(con.execute('SELECT id,cantidad FROM compras_plan'))
    copia={'formato':FORMATO_RESPALDO,'version':1,'creado':datetime.now(timezone.utc).isoformat(),
           'precios':precios,'lotes':lotes}
    if compras:copia['compras']=compras
    return copia


def importar_respaldo(datos: dict) -> dict:
    """Añade lo ausente; valida la copia entera antes de escribir una sola fila."""
    if not isinstance(datos,dict) or datos.get('formato')!=FORMATO_RESPALDO or type(datos.get('version')) is not int or datos['version']!=1:
        raise ValueError('Selecciona un respaldo de la calculadora Benjayaye Craft (versión 1)')
    precios,lotes=datos.get('precios'),datos.get('lotes')
    if not isinstance(precios,dict) or len(precios)>len(POR_ID) or not isinstance(lotes,list) or len(lotes)>20000:
        raise ValueError('El respaldo contiene demasiados datos o tiene un formato inválido')
    compras=datos.get('compras',{})
    if not isinstance(compras,dict) or len(compras)>len(POR_ID):raise ValueError('Marcas de compra inválidas en el respaldo')
    compras_limpias=[]
    for id,cantidad in compras.items():
        if id not in POR_ID:raise ValueError('Material desconocido en las compras del respaldo')
        compras_limpias.append((id,validar_cantidad_compra(cantidad)))
    limpios=[]
    for id,p in precios.items():
        if id not in POR_ID:raise ValueError('El respaldo tiene un precio con ID desconocido')
        valor=numero(p,'Precio del respaldo')
        if valor>10**15:raise ValueError('Precio demasiado alto en el respaldo')
        limpios.append((id,str(valor)))
    filas=[];ids=set()
    for fila in lotes:
        if not isinstance(fila,dict) or not isinstance(fila.get('datos'),dict):raise ValueError('Lote del respaldo inválido')
        id=fila.get('id');original=fila['datos']
        if not isinstance(id,str) or id in ids:raise ValueError('El respaldo repite un lote o su ID no es válido')
        ids.add(id)
        payload={'solicitud_id':id,'item_id':original.get('item_id'),
                 'cantidad':original.get('crafteos',original.get('cantidad')),
                 'unidades_por_crafteo':original.get('unidades_por_crafteo',1),
                 'retorno':original.get('retorno',0),'materiales':original.get('materiales'),
                 'economia':original.get('economia'),'receta_completa':True}
        _,lote=normalizar_lote(payload)
        if numero(original.get('cantidad'),'Cantidad guardada')!=lote['cantidad']:
            raise ValueError('La producción guardada no coincide con la cantidad de crafteos')
        if 'retorno' not in original:lote.pop('retorno',None)
        creado=fila.get('creado')
        try:
            if not isinstance(creado,str) or len(creado)>40:raise ValueError()
            instante=datetime.fromisoformat(creado.replace('Z','+00:00'))
            if instante.tzinfo is None or instante.year<2000:raise ValueError()
        except (TypeError,ValueError):raise ValueError('Fecha de lote inválida en el respaldo') from None
        filas.append((id,json.dumps(lote,ensure_ascii=False,sort_keys=True),creado))
    con_precios=con_lotes=0
    with closing(_abrir_precios()) as con,con:
        for fila in limpios:con_precios+=con.execute('INSERT OR IGNORE INTO precios (id,precio) VALUES (?,?)',fila).rowcount
        for fila in filas:con_lotes+=con.execute('INSERT OR IGNORE INTO lotes (id,datos,creado) VALUES (?,?,?)',fila).rowcount
        con.executemany('INSERT OR IGNORE INTO compras_plan (id,cantidad) VALUES (?,?)',compras_limpias)
    plan=listar_plan();plan['importados']={'precios':con_precios,'lotes':con_lotes}
    return plan


def unidades_por_crafteo(datos: dict) -> Decimal:
    produccion = numero(datos.get("unidades_por_crafteo", 1), "Unidades por crafteo", Decimal(1))
    if produccion != produccion.to_integral_value() or produccion > 10000:
        raise ValueError("Unidades por crafteo: usa un entero entre 1 y 10.000")
    return produccion


def calcular_economia(datos: dict) -> dict:
    """Cálculo en Python. Los precios y los materiales vienen de los controles."""
    if not isinstance(datos, dict):
        raise ValueError("La solicitud de cálculo debe contener los datos del formulario")
    cantidad = numero(datos.get("cantidad", 1), "Cantidad", Decimal("1"))
    if cantidad != cantidad.to_integral_value():
        raise ValueError("Cantidad: introduce un número entero")
    produccion = unidades_por_crafteo(datos)
    cantidad_producida = cantidad * produccion
    precio_venta = numero(datos.get("precio_venta", 0), "Precio de venta")
    retorno = numero(datos.get("retorno", 0), "Devolución")
    tasa_estacion = numero(datos.get("tasa_estacion", 0), "Tasa de estación")
    valor_item = numero(datos.get("valor_item", 0), "Valor del ítem")
    valor_automatico = not valor_item and valor_objeto_incluido(datos.get('item_id')) is not None
    if valor_automatico:valor_item=Decimal(valor_objeto_incluido(datos.get('item_id')))
    comision = numero(datos.get("comision", 0), "Comisión de venta")
    otros_costos = numero(datos.get("otros_costos", 0), "Otros costos")
    if retorno > 100 or comision > 100:
        raise ValueError("Devolución y comisión deben estar entre 0 y 100 %")

    materiales = datos.get("materiales", [])
    if not isinstance(materiales, list) or len(materiales) > 20:
        raise ValueError("Puedes cargar hasta 20 materiales")
    detalle = []
    costo_bruto = ahorro_total = costo_neto = Decimal("0")
    for indice, material in enumerate(materiales, start=1):
        if not isinstance(material, dict):
            raise ValueError(f"Material {indice}: datos inválidos")
        identificador = str(material.get("id", "")).strip()
        if not identificador:
            continue
        if identificador not in POR_ID:
            raise ValueError(f"Material {indice}: ID no encontrado en el catálogo")
        por_objeto = numero(material.get("cantidad", 0), f"Cantidad del material {indice}", Decimal("0.000001"))
        precio = numero(material.get("precio", 0), f"Precio del material {indice}")
        lote = por_objeto * cantidad
        bruto = lote * precio
        # Los artefactos se consumen por completo al fabricar; la devolución
        # solo se aplica a los recursos que admiten retorno.
        retorna = admite_devolucion(dict(material, id=identificador))
        cantidad_neta = cantidad_tras_devolucion(lote, retorno, retorna)
        neto = cantidad_neta * precio
        ahorro = bruto - neto
        costo_bruto += bruto
        ahorro_total += ahorro
        costo_neto += neto
        detalle.append({
            "id": identificador,
            "nombre": POR_ID[identificador]["es"],
            "por_objeto": float(por_objeto / produccion),
            "por_crafteo": float(por_objeto),
            "por_lote": float(lote),
            "cantidad_neta": float(cantidad_neta),
            "a_comprar": redondear_compra(cantidad_neta),
            "bruto": float(bruto),
            "ahorro": float(ahorro),
            "neto": float(neto),
            "retorna": retorna,
        })

    # Tasa expresada como plata por 100 puntos de nutrición de la estación.
    impuesto_estacion = cantidad_producida * valor_item * Decimal("0.1125") * tasa_estacion / 100
    inversion = costo_neto + impuesto_estacion + otros_costos
    ingreso_bruto = cantidad_producida * precio_venta
    impuesto_venta = ingreso_bruto * comision / 100
    ingreso_neto = ingreso_bruto - impuesto_venta
    ganancia = ingreso_neto - inversion
    precio_equilibrio=(inversion/cantidad_producida/(1-comision/100)).quantize(Decimal('0.01'),rounding=ROUND_CEILING) if comision<100 else None

    return {
        "estado": ("listo" if datos.get("receta_completa", True) is True else "receta_incompleta")
        if detalle else "sin_materiales",
        "detalle": detalle,
        "cantidad": int(cantidad),
        "cantidad_producida": int(cantidad_producida),
        "unidades_por_crafteo": int(produccion),
        "materiales_brutos": float(costo_bruto),
        "ahorro_retorno": float(ahorro_total),
        "materiales_netos": float(costo_neto),
        "impuesto_estacion": float(impuesto_estacion),
        'estacion_por_objeto': float(impuesto_estacion/cantidad_producida),
        'estacion_calculable': not tasa_estacion or valor_item>0,
        'valor_item':float(valor_item),
        'valor_item_automatico':bool(valor_automatico),
        "otros_costos": float(otros_costos),
        "inversion": float(inversion),
        "costo_por_objeto": float(inversion / cantidad_producida),
        "ingreso_bruto": float(ingreso_bruto),
        "impuesto_venta": float(impuesto_venta),
        "ingreso_neto": float(ingreso_neto),
        "ganancia": float(ganancia),
        'gasto_total':float(inversion+impuesto_venta),
        "ganancia_por_objeto": float(ganancia / cantidad_producida),
        "precio_equilibrio": float(precio_equilibrio) if precio_equilibrio is not None else None,
        "margen_neto": float(ganancia/ingreso_bruto*100) if ingreso_bruto>0 else None,
    }


def _id_recurso(nombre: str) -> str:
    """La API usa _LEVELn, mientras el catálogo añade @n al ID comercial."""
    if nombre in POR_ID:
        return nombre
    nivel = re.search(r"_LEVEL([1-4])$", nombre)
    if nivel and f"{nombre}@{nivel.group(1)}" in POR_ID:
        return f"{nombre}@{nivel.group(1)}"
    return nombre


def _receta_ejemplo(item_id: str) -> dict | None:
    """Plantilla de la túnica de clérigo, identificada como ejemplo editable."""
    match = re.fullmatch(r"T([4-8])_ARMOR_CLOTH_SET2(?:@([1-4]))?", item_id)
    if not match:
        return None
    tier, encantamiento = int(match.group(1)), int(match.group(2) or 0)
    recurso = f"T{tier}_CLOTH"
    if encantamiento:
        recurso += f"_LEVEL{encantamiento}@{encantamiento}"
    return {
        "materiales": [{"id": recurso, "cantidad": 16, "retorna": True}],
        "origen": "Plantilla local editable de túnica de clérigo",
        "aviso": "Confirma la receta y el valor del ítem en el juego antes de fabricar.",
        "completa": True,
    }


def _plantilla_con_artefacto(item_id: str) -> dict | None:
    """Añade el artefacto de tier correcto aun si la API no responde.

    Para armaduras conocemos además la cantidad de recurso refinado. En armas
    y otros objetos se expone el artefacto, pero se deja clara la receta parcial.
    """
    coincidencia = re.fullmatch(r"T([4-8])_(.+?)(?:@([1-4]))?", item_id)
    if not coincidencia:
        return None
    tier, familia = int(coincidencia.group(1)), coincidencia.group(2)
    encantamiento = int(coincidencia.group(3) or 0)
    artefacto = f"T{tier}_ARTEFACT_{familia}"
    if artefacto not in POR_ID:
        return None
    materiales = []
    armadura = re.fullmatch(r"(ARMOR|HEAD|SHOES)_(CLOTH|LEATHER|PLATE)_.+", familia)
    if armadura:
        pieza, tipo = armadura.groups()
        recurso = f"T{tier}_{'METALBAR' if tipo == 'PLATE' else tipo}"
        if encantamiento:
            recurso += f"_LEVEL{encantamiento}@{encantamiento}"
        if recurso in POR_ID:
            materiales.append({"id": recurso, "cantidad": 16 if pieza == "ARMOR" else 8, "retorna": True})
    materiales.append({"id": artefacto, "cantidad": 1, "retorna": False})
    completa = len(materiales) == 2
    return {
        "materiales": materiales,
        "origen": "Plantilla local editable de objeto con artefacto",
        "aviso": (
            "El artefacto no recibe devolución. Confirma los materiales y cantidades en el juego."
            if completa else
            "Receta parcial: añade los demás materiales. El artefacto no recibe devolución."
        ),
        "completa": completa,
    }


def _plantilla_armadura_basica(item_id: str) -> dict | None:
    m=re.fullmatch(r'T([4-8])_(ARMOR|HEAD|SHOES)_(CLOTH|LEATHER|PLATE)_SET[1-3](?:@([1-4]))?',item_id)
    if not m:return None
    tier,pieza,tipo,encanto=m.groups()
    recurso=f'T{tier}_{"METALBAR" if tipo=="PLATE" else tipo}'
    if encanto:recurso+=f'_LEVEL{encanto}@{encanto}'
    if recurso not in POR_ID:return None
    return {'materiales':[{'id':recurso,'cantidad':16 if pieza=='ARMOR' else 8,'retorna':True}],
            'completa':True,'origen':'Receta base incluida de armadura, capucha o calzado',
            'aviso':'Puedes consultar la receta oficial o ajustar sus cantidades antes de fabricar.'}


def obtener_receta(item_id: str, consultar_oficial: bool = False) -> dict:
    receta=_obtener_receta(item_id,consultar_oficial)
    return dict(receta,valor_item=valor_objeto_incluido(item_id))


def _receta_equipo_incluida(item_id: str) -> dict:
    receta=deepcopy(RECETAS_EQUIPO[item_id])
    return dict(receta,completa=True,origen='Receta de equipo incluida · datos del juego',
                aviso=f"Copia del {DATOS_EQUIPO['consulta_utc']}. "+
                ('Incluye alternativas de artefacto o token: se muestra la primera receta. Puedes ajustar los materiales.'
                 if receta.get('alternativas',1)>1 else 'Puedes consultar la receta oficial o ajustar sus cantidades.'))


def _obtener_receta(item_id: str, consultar_oficial: bool = False) -> dict:
    if item_id not in POR_ID or not ID_SEGURO.fullmatch(item_id):
        raise ValueError("El objeto no figura en el catálogo")
    # La API de comida puede omitir la receta .0 y el rendimiento. La copia
    # local conserva los ingredientes y amountcrafted de cada variante.
    if item_id in RECETAS_COMIDA:
        return dict(deepcopy(RECETAS_COMIDA[item_id]), completa=True, tipo="comida",
                    origen="Receta de comida incluida · datos del juego (ao-bin-dumps)",
                    aviso=f"Ingredientes por crafteo. Datos consultados el {DATOS_COMIDA['consulta_utc']}; puedes editar las cantidades.")
    if item_id in RECETAS_EN_MEMORIA and not consultar_oficial:
        return RECETAS_EN_MEMORIA[item_id]
    if item_id in RECETAS_EQUIPO and not consultar_oficial:
        return _receta_equipo_incluida(item_id)

    plantilla = _receta_ejemplo(item_id) or _plantilla_con_artefacto(item_id) or _plantilla_armadura_basica(item_id)
    if plantilla and plantilla["completa"] and not consultar_oficial:
        return plantilla

    base, _, nivel_texto = item_id.partition("@")
    nivel = int(nivel_texto) if nivel_texto.isdigit() else 0
    url = SITIO_RECETAS + urllib.parse.quote(base, safe="_") + "/data"
    try:
        solicitud = urllib.request.Request(url, headers={"User-Agent": "AlbionCraftLocal/1.0"})
        with urllib.request.urlopen(solicitud, timeout=5 if consultar_oficial else 1.5) as respuesta:
            registro = json.loads(respuesta.read(2_000_000))
        requisitos = registro.get("craftingRequirements") if nivel == 0 else None
        if nivel:
            opciones = registro.get("enchantments") or {}
            for opcion in opciones.get("enchantments", []):
                if int(opcion.get("enchantmentLevel", -1)) == nivel:
                    requisitos = opcion.get("craftingRequirements")
                    break
        recursos = (requisitos or {}).get("craftResourceList") or []
        materiales = []
        for recurso in recursos:
            identificador = _id_recurso(str(recurso.get("uniqueName", "")))
            if identificador not in POR_ID:
                raise ValueError(f"Recurso ausente del catálogo: {identificador}")
            materiales.append({
                "id": identificador,
                "cantidad": float(recurso["count"]),
                "retorna": "ARTEFACT" not in identificador and "TOKEN" not in identificador,
            })
        # Algunas respuestas del servicio omiten el artefacto. Cuando el
        # catálogo contiene el artefacto correspondiente al objeto, lo
        # incluimos una sola vez en la receta que se muestra al usuario.
        plantilla_artefacto = _plantilla_con_artefacto(item_id)
        if plantilla_artefacto and not any("_ARTEFACT_" in m["id"] or "_TOKEN_" in m["id"] for m in materiales):
            materiales.append(plantilla_artefacto["materiales"][-1])
        if materiales:
            completa = not plantilla_artefacto or any("_ARTEFACT_" not in m["id"] for m in materiales)
            if not completa and item_id in RECETAS_EQUIPO:return _receta_equipo_incluida(item_id)
            receta = {
                "materiales": materiales,
                "origen": "Receta consultada en la API de Albion Online",
                "aviso": "Revisa la receta en el juego; los precios se introducen manualmente." if completa
                         else "Receta parcial: añade los demás materiales antes de calcular la ganancia.",
                "completa": completa,
            }
            RECETAS_EN_MEMORIA[item_id] = receta
            return receta
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        pass

    if item_id in RECETAS_EQUIPO:return _receta_equipo_incluida(item_id)
    return plantilla or {
        "materiales": [],
        "origen": "Receta no disponible",
        "aviso": "Añade los materiales manualmente. No se calcula una ganancia sin materiales.",
        "completa": False,
    }


class Manejador(BaseHTTPRequestHandler):
    server_version = "AlbionCraftLocal/1.0"

    def _acceso_compartido_autorizado(self) -> bool:
        """Protege todos los recursos cuando el servidor se comparte en la LAN."""
        token=getattr(self.server,"token_compartir",None)
        if not token:return True
        cookies=SimpleCookie()
        try:cookies.load(self.headers.get("Cookie",""))
        except (CookieError,ValueError,TypeError):pass
        cookie=cookies.get("benjayaye_acceso")
        if cookie and hmac.compare_digest(cookie.value.encode('utf-8'),token.encode('utf-8')):return True

        ruta=urllib.parse.urlsplit(self.path)
        clave=urllib.parse.parse_qs(ruta.query).get("acceso",[""])[0]
        if ruta.path=="/" and clave and hmac.compare_digest(clave.encode('utf-8'),token.encode('utf-8')):
            self.send_response(303)
            seguridad='; Secure' if getattr(self.server,'web_alojada',False) else ''
            self.send_header("Set-Cookie",f"benjayaye_acceso={token}; HttpOnly; SameSite=Strict; Path=/{seguridad}")
            self.send_header("Location","/")
            self.send_header("Cache-Control","no-store")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Content-Length","0")
            self.end_headers()
            return False
        self.send_error(403,"Acceso privado. Abre el enlace que te compartieron.")
        return False

    def log_message(self, formato: str, *args) -> None:
        """No escribe parámetros de invitación en la consola del servidor."""
        if len(args)>1 and isinstance(args[1],str):
            partes=args[1].split()
            if len(partes)>1:
                partes[1]=urllib.parse.urlsplit(partes[1]).path or "/"
                args=(args[0]," ".join(partes),*args[2:])
        super().log_message(formato,*args)

    def _enviar(self, codigo: int, contenido: bytes, tipo: str, nombre: str | None = None, comprimido: bool = False) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(contenido)))
        if nombre:self.send_header('Content-Disposition',f'attachment; filename="{nombre}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if 'application/json' in tipo:self.send_header('Vary','Accept-Encoding')
        if comprimido:self.send_header('Content-Encoding','gzip')
        self.end_headers()
        try:self.wfile.write(contenido)
        except (BrokenPipeError,ConnectionResetError):self.close_connection=True

    def _json(self, codigo: int, objeto: object) -> None:
        contenido=json.dumps(objeto,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        comprimir=len(contenido)>=2048 and acepta_gzip(self.headers.get('Accept-Encoding',''))
        if comprimir:contenido=gzip.compress(contenido,compresslevel=3,mtime=0)
        self._enviar(codigo,contenido,'application/json; charset=utf-8',comprimido=comprimir)

    def _eventos_precios(self):
        """Detecta commits de otros procesos sin trasladar imágenes por HTTP."""
        import time
        try:
            with closing(conectar_base(DB_PRECIOS,timeout=2)) as con:
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                self.wfile.write(b'retry: 1000\n\n');self.wfile.flush()
                anterior=None;inicio=time.monotonic();latido=inicio
                # Renovar la conexión limita los hilos de pestañas que ya cerraron.
                while time.monotonic()-inicio<25:
                    revision=con.execute('PRAGMA data_version').fetchone()[0]
                    if revision!=anterior:
                        self.wfile.write(f'event: precios\ndata: {revision}\n\n'.encode())
                        self.wfile.flush();anterior=revision
                    elif time.monotonic()-latido>5:
                        self.wfile.write(b': activo\n\n');self.wfile.flush();latido=time.monotonic()
                    time.sleep(.2)
        except (OSError,sqlite3.Error):pass
        finally:self.close_connection=True

    def do_GET(self) -> None:
        if self.path == '/salud':
            self._json(200, {'ok': True});return
        if not self._acceso_compartido_autorizado():return
        ruta = urllib.parse.urlparse(self.path)
        if ruta.path=='/api/plan/compras.csv':
            try:
                parametros=urllib.parse.parse_qs(ruta.query);filtro=parametros.get('estado',['todos'])[0]
                self._enviar(200,exportar_compras_csv(filtro,parametros.get('buscar',[''])[0]),'text/csv; charset=utf-8','benjayaye-compras.csv')
            except ValueError as error:self._json(400,{'error':str(error)})
            except (sqlite3.Error,OSError):self._json(500,{'error':'No se pudo exportar la lista de compras'})
            return
        if ruta.path in ('/api/lector/historial','/api/lector/historial/imagen'):
            p=urllib.parse.parse_qs(ruta.query)
            try:
                args=(DB_PRECIOS,POR_ID,p.get('item_id',[''])[0],p.get('ciudad',[''])[0],int(p.get('calidad',['1'])[0]),p.get('periodo',['24h'])[0])
                if ruta.path.endswith('/imagen'):
                    contenido=imagen_historial(*args)
                    if contenido:self._enviar(200,contenido,'image/png')
                    else:self._json(404,{'error':'Todavía no hay una captura de este historial'})
                else:self._json(200,consultar_historial(*args))
            except (ValueError,TypeError) as e:self._json(400,{'error':str(e)})
            except (sqlite3.Error,OSError):self._json(500,{'error':'No se pudo abrir el historial de mercado'})
            return
        if ruta.path == '/api/lector/eventos':
            self._eventos_precios();return
        if ruta.path == "/api/lector/precios":
            p = urllib.parse.parse_qs(ruta.query)
            try:
                lecturas = consultar_lecturas(DB_PRECIOS, POR_ID,
                    p.get('ids', [''])[0].split(','), p.get('ciudad', [''])[0],
                    p.get('objeto', [''])[0], p.get('ciudad_objeto', [''])[0],
                    int(p.get('calidad', ['1'])[0]), p.get('tipo', ['venta'])[0])
                if p.get('publicos') == ['1']:
                    agregar_publicos(DB_PRECIOS, lecturas)
                self._json(200, lecturas)
            except (ValueError, TypeError) as exc:
                self._json(400, {'error': str(exc)})
            except (sqlite3.Error, OSError):
                self._json(500, {'error': 'No se pudieron abrir las lecturas guardadas'})
            return
        if ruta.path == RUTA_PUBLICOS:
            p = urllib.parse.parse_qs(ruta.query)
            try:
                self._json(200, consultar_cotizaciones(DB_PRECIOS, POR_ID, p.get('item_id',[''])[0]))
            except ValueError as exc:
                self._json(400, {'error': str(exc)})
            except (sqlite3.Error, OSError):
                self._json(503, {'error': 'No se pudieron abrir los precios públicos guardados.'})
            return
        if ruta.path == "/api/mercado":
            parametros = urllib.parse.parse_qs(ruta.query)
            try:
                self._json(200, consultar_precios(parametros.get("ciudad", [""])[0],
                    parametros.get("ids", [""])[0].split(","), POR_ID))
            except (urllib.error.URLError, TimeoutError, OSError):
                self._json(502, {"error": "No se pudo conectar con los precios de Américas. Reintenta en un minuto."})
            except (ValueError, TypeError):
                self._json(400, {"error": "No se pudieron consultar esos precios. Revisa la ciudad y los objetos."})
            return
        if ruta.path == "/api/catalogo":
            enviar_archivo(self,ROOT / "datos" / "catalogo_es.json","application/json; charset=utf-8")
            return
        if ruta.path == '/api/recetas':
            enviar_archivo(self,ROOT / 'datos' / 'recetas_equipo.json','application/json; charset=utf-8')
            return
        if ruta.path == '/api/respaldo':
            try:
                copia=json.dumps(exportar_respaldo(),ensure_ascii=False,indent=2).encode('utf-8')
                self._enviar(200,copia,'application/json; charset=utf-8',
                             'benjayaye-respaldo-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
            except (sqlite3.Error,OSError):self._json(500,{'error':'No se pudo crear el respaldo'})
            return
        if ruta.path == "/api/precios":
            try:
                self._json(200, {"precios": listar_precios()})
            except (sqlite3.Error, OSError):
                self._json(500, {"error": "No se pudo abrir la base de precios guardados"})
            return
        if ruta.path == "/api/plan":
            try:
                self._json(200, listar_plan())
            except (sqlite3.Error, OSError):
                self._json(500, {"error": "No se pudieron abrir los lotes guardados"})
            return
        if ruta.path == "/api/receta":
            parametros = urllib.parse.parse_qs(ruta.query)
            try:
                receta = obtener_receta(parametros.get("item_id", [""])[0], parametros.get("oficial", ["0"])[0] == "1")
                self._json(200, receta)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            return
        if ruta.path.startswith("/iconos/"):
            nombre = urllib.parse.unquote(ruta.path.removeprefix("/iconos/").removesuffix(".png"))
            if nombre not in POR_ID or not ID_SEGURO.fullmatch(nombre):
                self.send_error(404)
                return
            calidad = urllib.parse.parse_qs(ruta.query).get('quality',['1'])[0]
            if calidad not in ('1','2','3','4','5'):
                self.send_error(400, 'Calidad fuera de rango')
                return
            archivo = ROOT / "iconos" / f"{nombre}.png"
            if archivo.is_file() and calidad == '1':
                enviar_archivo(self,archivo,"image/png")
            else:
                self.send_response(302)
                self.send_header("Location", SITIO_IMAGENES + urllib.parse.quote(nombre, safe="@_") + ".png?size=160&quality=" + calidad)
                self.end_headers()
            return
        static = {
            "/web/historial.js": ("web/historial.js", "text/javascript; charset=utf-8"),
            "/web/mercado.js": ("web/mercado.js", "text/javascript; charset=utf-8"),
            "/": ("web/index.html", "text/html; charset=utf-8"),
            "/web/estilos.css": ("web/estilos.css", "text/css; charset=utf-8"),
            "/web/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
            "/web/plan.js": ("web/plan.js", "text/javascript; charset=utf-8"),
            "/web/fondo-n12.jpg": ("web/fondo-n12.jpg", "image/jpeg"),
        }
        if ruta.path not in static:
            self.send_error(404)
            return
        archivo, tipo = static[ruta.path]
        enviar_archivo(self,ROOT / archivo,tipo)

    def _recibir_api_publica(self):
        token = os.environ.get('BENJAYAYE_API_SYNC_TOKEN', '')
        if not token_valido(token):
            self._json(503, {'error': 'La sincronización remota de la API no está configurada.'});return
        recibido = self.headers.get('Authorization', '')
        if not hmac.compare_digest(recibido.encode('utf-8'), ('Bearer '+token).encode('utf-8')):
            self._json(401, {'error': 'Token de sincronización incorrecto.'});return
        try:
            tamano = int(self.headers.get('Content-Length', '0'))
            if not 0 < tamano <= MAX_BYTES_API or self.headers.get_content_type() != 'application/json':
                raise ValueError('El envío debe ser un lote JSON de tamaño válido.')
            self.connection.settimeout(10)
            datos = json.loads(self.rfile.read(tamano))
            self._json(200, recibir_cotizaciones(DB_PRECIOS, POR_ID, datos))
        except (ValueError, TypeError, UnicodeError) as exc:
            self._json(400, {'error': 'Envío JSON inválido.' if isinstance(exc,(json.JSONDecodeError,UnicodeError)) else str(exc)})
        except (sqlite3.Error, OSError):
            self._json(503, {'error': 'No se pudieron guardar las cotizaciones. Reintenta más tarde.'})

    def do_POST(self) -> None:
        if self.path == RUTA_ENVIO_API:
            self._recibir_api_publica();return
        if not self._acceso_compartido_autorizado():return
        if self.path not in ("/api/calcular", "/api/precios", "/api/plan", '/api/respaldo/importar'):
            self.send_error(404)
            return
        try:
            tamano = int(self.headers.get("Content-Length", "0"))
            if not 0 < tamano <= (16*1024*1024 if self.path=='/api/respaldo/importar' else 65536):
                raise ValueError("Solicitud demasiado grande o vacía")
            datos = json.loads(self.rfile.read(tamano))
            if self.path == '/api/respaldo/importar':
                self._json(200,importar_respaldo(datos))
            elif self.path == "/api/plan":
                self._json(200, guardar_lote(datos))
            elif self.path == "/api/precios":
                if not isinstance(datos, dict):
                    raise ValueError("Introduce un material y su precio")
                self._json(200, guardar_precio(str(datos.get("id", "")), datos.get("precio")))
            else:
                self._json(200, calcular_economia(datos))
        except (ValueError, json.JSONDecodeError, TypeError) as exc:
            self._json(400, {"error": str(exc)})
        except (sqlite3.Error, OSError):
            self._json(500, {"error": "No se pudieron guardar los datos. Comprueba que la carpeta permita escritura."})

    def do_PATCH(self) -> None:
        if not self._acceso_compartido_autorizado():return
        if self.path not in ('/api/plan','/api/plan/completar','/api/plan/compras'):self.send_error(404);return
        try:
            tamano=int(self.headers.get('Content-Length','0'))
            if not 0<tamano<=65536:raise ValueError('Solicitud demasiado grande o vacía')
            datos=json.loads(self.rfile.read(tamano))
            if not isinstance(datos,dict):raise ValueError('Datos del lote inválidos')
            if self.path=='/api/plan/compras':
                respuesta=datos.get('respuesta','plan')
                if respuesta not in ('plan','material'):raise ValueError('Respuesta de compra inválida')
                if 'cantidad_comprada' in datos:
                    self._json(200,guardar_cantidad_comprada(datos.get('item_id'),datos.get('cantidad'),datos['cantidad_comprada'],respuesta=='material'))
                else:self._json(200,guardar_compra_material(datos.get('item_id'),datos.get('cantidad'),datos.get('comprado'),respuesta=='material'))
            elif self.path=='/api/plan/completar':
                self._json(200,completar_economia_plan(datos))
            else:self._json(200,actualizar_economia_lote(datos.get('lote_id',''),datos.get('economia'),
                                                       datos.get('cantidad'),datos.get('retorno'),datos.get('revision')))
        except (ValueError,TypeError,json.JSONDecodeError) as exc:self._json(400,{'error':str(exc)})
        except (sqlite3.Error,OSError):self._json(500,{'error':'No se pudieron guardar los precios del lote'})

    def do_DELETE(self) -> None:
        if not self._acceso_compartido_autorizado():return
        ruta = urllib.parse.urlparse(self.path)
        if ruta.path not in ("/api/precios", "/api/plan", "/api/plan/lotes", '/api/plan/compras'):
            self.send_error(404)
            return
        try:
            parametros = urllib.parse.parse_qs(ruta.query)
            if ruta.path == '/api/plan/compras':
                self._json(200,desmarcar_compras())
            elif ruta.path == "/api/plan/lotes":
                if ruta.query:
                    raise ValueError("El borrado de todos los lotes no admite filtros")
                self._json(200, borrar_todos_lotes())
            elif ruta.path == "/api/plan":
                self._json(200, borrar_lote(parametros.get("lote_id", [""])[0]))
            else:
                self._json(200, borrar_precio(parametros.get("item_id", [""])[0]))
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except (sqlite3.Error, OSError):
            self._json(500, {"error": "No se pudo eliminar el registro guardado"})


def direcciones_privadas_ipv4() -> list[str]:
    """Devuelve las IP privadas disponibles para abrir la página en la LAN."""
    encontradas=[]
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1",9))
            direccion=sock.getsockname()[0]
            if direccion not in encontradas:encontradas.append(direccion)
    except OSError:pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET,socket.SOCK_STREAM):
            direccion=info[4][0]
            if direccion not in encontradas:encontradas.append(direccion)
    except OSError:pass
    resultado=[]
    for direccion in encontradas:
        try:ip=ipaddress.ip_address(direccion)
        except ValueError:continue
        if ip.version==4 and ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_reserved):
            resultado.append(str(ip))
    return resultado


def iniciar() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Calculadora local de Albion Online")
    parser.add_argument("--puerto", type=int, help="Puerto de escucha (8501 por defecto en local)")
    parser.add_argument("--sin-navegador", action="store_true", help="No abrir el navegador automáticamente")
    modo=parser.add_mutually_exclusive_group()
    modo.add_argument("--compartir-red-local",action="store_true",
                        help="Permite abrir la página desde otros dispositivos de tu misma red Wi-Fi/LAN")
    modo.add_argument("--web-alojada", action="store_true", help="Usa PORT y acceso privado en un servicio HTTPS")
    args = parser.parse_args()
    host="0.0.0.0" if args.compartir_red_local or args.web_alojada else "127.0.0.1"
    if args.web_alojada:
        token=os.environ.get('BENJAYAYE_ACCESO_WEB','')
        if not token_valido(token) or token == os.environ.get('BENJAYAYE_API_SYNC_TOKEN'):
            parser.error('Configura BENJAYAYE_ACCESO_WEB con una clave privada distinta del token de sincronización.')
        try:puerto=int(os.environ.get('PORT','10000')) if args.puerto is None else args.puerto
        except ValueError:parser.error('PORT debe contener un número de puerto.')
    else:
        token=secrets.token_urlsafe(24) if args.compartir_red_local else None
        puerto=8501 if args.puerto is None else args.puerto
    try:
        servidor = ThreadingHTTPServer((host, puerto), Manejador)
    except OSError:
        if args.web_alojada:raise
        servidor = ThreadingHTTPServer((host, 0), Manejador)
    servidor.token_compartir=token
    servidor.web_alojada=args.web_alojada
    clave="?acceso="+urllib.parse.quote(token,safe="") if token else ""
    direccion = f"http://127.0.0.1:{servidor.server_port}/{clave}"
    if args.web_alojada:
        print(f'Calculadora alojada en el puerto {servidor.server_port}.',flush=True)
    elif args.compartir_red_local:
        ips=direcciones_privadas_ipv4()
        enlaces="\n".join(f"  http://{ip}:{servidor.server_port}/?acceso={urllib.parse.quote(token,safe='')}" for ip in ips)
        print("\nCalculadora de Albion compartida en tu red local.",flush=True)
        print(f"Enlace en esta computadora: {direccion}",flush=True)
        print("Enlace para otra persona (misma red Wi-Fi/LAN):",flush=True)
        print(enlaces or "  No se detectó una IP privada. Comprueba que la computadora esté conectada a la red.",flush=True)
        print("El enlace permite editar los datos compartidos. Mantén abierta esta ventana; el enlace deja de funcionar al cerrar el programa.\n",flush=True)
    else:
        print(f"\nCalculadora de Albion lista: {direccion}\nCierra con Ctrl+C.\n", flush=True)
    if not args.sin_navegador and not args.web_alojada:
        import threading

        threading.Timer(0.6, lambda: webbrowser.open(direccion)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nCalculadora cerrada.")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    iniciar()
