"""Calculadora local de fabricación para Albion Online (Python 3.10+).

Ejecuta ``python app.py`` y abre la dirección que se imprime. No instala
dependencias: Python sirve la interfaz y realiza el cálculo de economía.
"""

from __future__ import annotations

import json
import hmac
import ipaddress
import re
import secrets
import sqlite3
import socket
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.cookies import CookieError,SimpleCookie
from contextlib import closing
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from mercado import consultar_precios
from lector_datos import consultar_lecturas
from historial_mercado import consultar_historial,imagen_historial
from almacenamiento import obtener_db
from archivos_web import enviar_archivo


ROOT = Path(__file__).resolve().parent
CATALOGO = json.loads((ROOT / "datos" / "catalogo_es.json").read_text(encoding="utf-8"))
POR_ID = {item["id"]: item for item in CATALOGO}
DATOS_COMIDA = json.loads((ROOT / "datos" / "recetas_comida.json").read_text(encoding="utf-8"))
RECETAS_COMIDA = DATOS_COMIDA["recetas"]
SIN_DEVOLUCION = {m["id"] for r in RECETAS_COMIDA.values() for m in r["materiales"] if not m["retorna"]}
DB_PRECIOS = obtener_db(ROOT)
ID_SEGURO = re.compile(r"^[A-Z0-9_@]+$")
SITIO_IMAGENES = "https://render.albiononline.com/v1/item/"
SITIO_RECETAS = "https://gameinfo.albiononline.com/api/gameinfo/items/"
RECETAS_EN_MEMORIA: dict[str, dict] = {}


def _abrir_precios() -> sqlite3.Connection:
    """Una conexión por solicitud permite guardar desde varios navegadores."""
    DB_PRECIOS.parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(DB_PRECIOS, timeout=10)
    try:
        conexion.execute("CREATE TABLE IF NOT EXISTS precios (id TEXT PRIMARY KEY, precio TEXT NOT NULL)")
        conexion.execute("""CREATE TABLE IF NOT EXISTS lotes (
            id TEXT PRIMARY KEY, datos TEXT NOT NULL,
            creado TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )""")
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


def guardar_lote(datos: dict) -> dict:
    """Guarda una copia de la receta; reintentar una solicitud no la duplica."""
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
    with closing(_abrir_precios()) as conexion:
        filas = conexion.execute("SELECT id, datos, creado FROM lotes ORDER BY rowid").fetchall()
    lotes, objetos, acumulados = [], {}, {}
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
        lotes.append({"id": lote_id, "item_id": item_id, "nombre": POR_ID[item_id]["es"],
                      "cantidad": cantidad, "crafteos": crafteos, "unidades_por_crafteo": produccion,
                      "retorno": float(retorno), "retorno_guardado": "retorno" in lote,
                      "creado": creado, "materiales": detalle})
    materiales = []
    for resumen in acumulados.values():
        resumen["a_comprar"] = redondear_compra(resumen["cantidad_neta"])
        resumen["total"] = float(resumen["total"])
        resumen["cantidad_neta"] = float(resumen["cantidad_neta"])
        resumen["destinos"] = [dict(d, materiales=float(d["materiales"]),
                                    cantidad_neta=float(d["cantidad_neta"]), a_comprar=redondear_compra(d["cantidad_neta"]))
                               for d in resumen["destinos"].values()]
        materiales.append(resumen)
    return {"lotes": lotes, "objetos": list(objetos.values()), "materiales": materiales,
            "total_items": sum(o["cantidad"] for o in objetos.values())}


def borrar_lote(lote_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", lote_id):
        raise ValueError("Identificador de lote inválido")
    with closing(_abrir_precios()) as conexion:
        with conexion:
            conexion.execute("DELETE FROM lotes WHERE id = ?", (lote_id,))
    return listar_plan()


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
        "otros_costos": float(otros_costos),
        "inversion": float(inversion),
        "costo_por_objeto": float(inversion / cantidad_producida),
        "ingreso_bruto": float(ingreso_bruto),
        "impuesto_venta": float(impuesto_venta),
        "ingreso_neto": float(ingreso_neto),
        "ganancia": float(ganancia),
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


def obtener_receta(item_id: str, consultar_oficial: bool = False) -> dict:
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

    plantilla = _receta_ejemplo(item_id) or _plantilla_con_artefacto(item_id)
    if plantilla and plantilla["completa"] and not consultar_oficial:
        return plantilla

    base, _, nivel_texto = item_id.partition("@")
    nivel = int(nivel_texto) if nivel_texto.isdigit() else 0
    url = SITIO_RECETAS + urllib.parse.quote(base, safe="_") + "/data"
    try:
        solicitud = urllib.request.Request(url, headers={"User-Agent": "AlbionCraftLocal/1.0"})
        with urllib.request.urlopen(solicitud, timeout=5) as respuesta:
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
        if cookie and hmac.compare_digest(cookie.value,token):return True

        ruta=urllib.parse.urlsplit(self.path)
        clave=urllib.parse.parse_qs(ruta.query).get("acceso",[""])[0]
        if ruta.path=="/" and clave and hmac.compare_digest(clave,token):
            self.send_response(303)
            self.send_header("Set-Cookie",f"benjayaye_acceso={token}; HttpOnly; SameSite=Strict; Path=/")
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

    def _enviar(self, codigo: int, contenido: bytes, tipo: str) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(contenido)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:self.wfile.write(contenido)
        except (BrokenPipeError,ConnectionResetError):self.close_connection=True

    def _json(self, codigo: int, objeto: object) -> None:
        self._enviar(codigo, json.dumps(objeto, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _eventos_precios(self):
        """Detecta commits de otros procesos sin trasladar imágenes por HTTP."""
        import time
        try:
            with closing(sqlite3.connect(DB_PRECIOS,timeout=2)) as con:
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
        if not self._acceso_compartido_autorizado():return
        ruta = urllib.parse.urlparse(self.path)
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
                self._json(200, consultar_lecturas(DB_PRECIOS, POR_ID,
                    p.get('ids', [''])[0].split(','), p.get('ciudad', [''])[0],
                    p.get('objeto', [''])[0], p.get('ciudad_objeto', [''])[0],
                    int(p.get('calidad', ['1'])[0]), p.get('tipo', ['venta'])[0]))
            except (ValueError, TypeError) as exc:
                self._json(400, {'error': str(exc)})
            except (sqlite3.Error, OSError):
                self._json(500, {'error': 'No se pudieron abrir las lecturas guardadas'})
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

    def do_POST(self) -> None:
        if not self._acceso_compartido_autorizado():return
        if self.path not in ("/api/calcular", "/api/precios", "/api/plan"):
            self.send_error(404)
            return
        try:
            tamano = int(self.headers.get("Content-Length", "0"))
            if not 0 < tamano <= 65536:
                raise ValueError("Solicitud demasiado grande o vacía")
            datos = json.loads(self.rfile.read(tamano))
            if self.path == "/api/plan":
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

    def do_DELETE(self) -> None:
        if not self._acceso_compartido_autorizado():return
        ruta = urllib.parse.urlparse(self.path)
        if ruta.path not in ("/api/precios", "/api/plan"):
            self.send_error(404)
            return
        try:
            parametros = urllib.parse.parse_qs(ruta.query)
            if ruta.path == "/api/plan":
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
    parser.add_argument("--puerto", type=int, default=8501, help="Puerto local (0 para uno libre)")
    parser.add_argument("--sin-navegador", action="store_true", help="No abrir el navegador automáticamente")
    parser.add_argument("--compartir-red-local",action="store_true",
                        help="Permite abrir la página desde otros dispositivos de tu misma red Wi-Fi/LAN")
    args = parser.parse_args()
    host="0.0.0.0" if args.compartir_red_local else "127.0.0.1"
    try:
        servidor = ThreadingHTTPServer((host, args.puerto), Manejador)
    except OSError:
        servidor = ThreadingHTTPServer((host, 0), Manejador)
    token=secrets.token_urlsafe(24) if args.compartir_red_local else None
    servidor.token_compartir=token
    clave="?acceso="+urllib.parse.quote(token,safe="") if token else ""
    direccion = f"http://127.0.0.1:{servidor.server_port}/{clave}"
    if args.compartir_red_local:
        ips=direcciones_privadas_ipv4()
        enlaces="\n".join(f"  http://{ip}:{servidor.server_port}/?acceso={urllib.parse.quote(token,safe='')}" for ip in ips)
        print("\nCalculadora de Albion compartida en tu red local.",flush=True)
        print(f"Enlace en esta computadora: {direccion}",flush=True)
        print("Enlace para otra persona (misma red Wi-Fi/LAN):",flush=True)
        print(enlaces or "  No se detectó una IP privada. Comprueba que la computadora esté conectada a la red.",flush=True)
        print("El enlace permite editar los datos compartidos. Mantén abierta esta ventana; el enlace deja de funcionar al cerrar el programa.\n",flush=True)
    else:
        print(f"\nCalculadora de Albion lista: {direccion}\nCierra con Ctrl+C.\n", flush=True)
    if not args.sin_navegador:
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
