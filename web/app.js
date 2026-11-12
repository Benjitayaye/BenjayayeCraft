"use strict";

const $ = (id) => document.getElementById(id);
const moneda = new Intl.NumberFormat("es-ES", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const unidades = new Intl.NumberFormat("es-ES", {maximumFractionDigits: 2});
const respaldo = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#8be9f8"/><stop offset="1" stop-color="#8b6dff"/></linearGradient></defs><rect x="8" y="8" width="80" height="80" rx="17" fill="#23445d"/><path d="M48 13 71 45 48 79 25 45Z" fill="url(#g)" stroke="#c6edff" stroke-width="2"/></svg>');
const estado = {
  catalogo: [], porId: new Map(), familias: new Map(), familia: null,
  busquedaPorId: new Map(), revisionPrecios: 0, revisionOrdenCache: -1,
  mapaOrdenCache: null, idsPreciosOrdenados: [],
  id: null, materiales: [], precios: new Map(), precioSeleccionado: null,
  guardadosPendientes: new Map(), completa: false,
  planCargado: false, planOcupado: false, guardadoIntento: null,
  tokenReceta: 0, tokenCalculo: 0,
  unidadesPorCrafteo: 1,
  limitePrecios: 60, controladorReceta: null, controladorCalculo: null,
  recetas: new Map(),
  recetasIncluidas: new Map(),
  familiaPorId: new Map(), recientes: [],
};
const esArtefacto = (material) => Boolean(material && material.id.includes("_ARTEFACT_"));
const sinDevolucion = (material) => esArtefacto(material) || material?.id === "QUESTITEM_TOKEN_AVALON";
const esComidaActual = () => Boolean(estado.id?.includes("_MEAL_"));
const nombreProductoActual = () => esComidaActual() ? estado.porId.get(estado.id)?.es : estado.familia?.nombre;

function sinAcentos(texto) {
  return String(texto || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}
function idImagen(img, id, calidad = 1) {
  const base = id ? `/iconos/${encodeURIComponent(id)}.png` : respaldo;
  img.onerror = () => {
    if (id && calidad > 1) {
      img.title = 'Icono base: no se pudo cargar el marco de calidad. La calidad seleccionada se mantiene.';
      img.onerror = () => { img.onerror = null; img.src = respaldo; };
      img.src = base;
    } else { img.onerror = null; img.src = respaldo; }
  };
  img.title = id && calidad > 1 ? nombresCalidad[calidad] : '';
  const url = id && calidad > 1 ? `${base}?quality=${calidad}` : base;
  if (img.getAttribute('src') !== url) img.src = url;
  img.decoding = 'async';
}
function actualizarImagenesObjeto() {
  const calidad = Number($('calidad-lector').value) || 1;
  for (const campo of ['imagen-objeto','imagen-precio']) {
    idImagen($(campo), estado.id, calidad);
    $(campo).alt = `${estado.porId.get(estado.id)?.es || 'Objeto'} · ${nombresCalidad[calidad]}`;
  }
  $('calidad-imagen').textContent = nombresCalidad[calidad];
}
function leerNumero(id) { return Number($(id).value) || 0; }
function dinero(valor) { return moneda.format(Number(valor) || 0); }
function entero(valor) { return unidades.format(Number(valor) || 0); }

function construirFamilias() {
  for (const item of estado.catalogo) {
    estado.porId.set(item.id, item);
    estado.busquedaPorId.set(item.id, sinAcentos(`${item.es} ${item.id}`));
    const coincidencia = /^T([1-8])_(.+?)(?:_LEVEL([1-4]))?(?:@([1-4]))?$/.exec(item.id);
    if (!coincidencia) continue;
    const tier = Number(coincidencia[1]);
    const encantamiento = Number(coincidencia[4] || coincidencia[3] || 0);
    const clave = coincidencia[2];
    let familia = estado.familias.get(clave);
    if (!familia) {
      familia = {clave, variantes: new Map(), nombres: new Set(), ejemplar: item.es, prioridad: 0};
      estado.familias.set(clave, familia);
    }
    familia.variantes.set(`${tier}:${encantamiento}`, item.id);
    estado.familiaPorId.set(item.id,familia);
    familia.nombres.add(item.es);
    familia.nombres.add(item.en);
    const prioridad = encantamiento === 0 ? (tier === 4 ? 100 : tier >= 5 ? 20 - tier : 10) : 0;
    if (prioridad > familia.prioridad) { familia.prioridad = prioridad; familia.ejemplar = item.es; }
  }
  for (const familia of estado.familias.values()) {
    familia.nombre = familia.ejemplar.replace(/\s+(?:del|de la)\s+(?:iniciado|adepto|experto|maestro|gran maestro|anciano)$/i, "");
    familia.busqueda = sinAcentos([...familia.nombres, familia.clave].join(" "));
  }
}

function construirOpcionesProducto() {
  const opciones = [...estado.familias.values()].filter(f => !f.clave.startsWith("MEAL_")).map(familia => ({
    nombre: familia.nombre, pista: familia.clave, busqueda: familia.busqueda, valor: familia, tipo: "objeto",
  }));
  // Las comidas cambian de nombre con el tier: buscar ternera debe elegir
  // guiso de ternera T8, no el nombre de otra comida de su misma familia.
  for (const item of estado.catalogo) {
    const partes = /^T([1-8])_(MEAL_.+)$/.exec(item.id);
    if (!partes || item.id.includes("@")) continue;
    opciones.push({nombre: item.es, pista: `Comida · T${partes[1]} · ${item.id}`,
      busqueda: sinAcentos(`${item.es} ${item.en} ${item.id}`),
      valor: estado.familias.get(partes[2]), tier: Number(partes[1]), tipo: "comida"});
  }
  return opciones.sort((a,b) => a.nombre.localeCompare(b.nombre, "es"));
}

function actualizarProduccion() {
  const comida = esComidaActual();
  $("etiqueta-cantidad").textContent = comida ? "Cantidad de crafteos" : "Cantidad a craftear";
  $("resumen-produccion").hidden = !comida;
  const cantidad = leerNumero("cantidad");
  $("resumen-produccion").textContent = !estado.materiales.length ? "Cargando rendimiento de la receta…"
    : `${entero(estado.unidadesPorCrafteo)} comidas por crafteo · ${entero(cantidad)} crafteos = ${entero(cantidad * estado.unidadesPorCrafteo)} comidas en total`;
}

function crearBuscador(entrada, desplegable, opciones, elegir) {
  let visibles = [], activo = -1;
  entrada.setAttribute('role', 'combobox'); entrada.setAttribute('aria-haspopup', 'listbox');
  function ocultar() { desplegable.hidden = true; activo = -1; entrada.removeAttribute('aria-activedescendant'); entrada.setAttribute("aria-expanded", "false"); }
  function seleccionar(opcion) { entrada.value = opcion.nombre; elegir(opcion); ocultar(); }
  function resaltar() {
    [...desplegable.children].forEach((n,i) => n.setAttribute('aria-selected', String(i === activo)));
    if (activo >= 0) {
      const n = desplegable.children[activo]; entrada.setAttribute('aria-activedescendant', n.id);
      n.scrollIntoView({block:'nearest'});
    }
  }
  function mostrar() {
    const query = sinAcentos(entrada.value.trim());
    if (!query) { ocultar(); return; }
    const terminos = query.split(/\s+/); visibles = []; activo = -1;
    entrada.removeAttribute('aria-activedescendant');
    for (const opcion of opciones()) {
      if (terminos.every(t => opcion.busqueda.includes(t))) visibles.push(opcion);
      if (visibles.length === 12) break;
    }
    desplegable.replaceChildren();
    for (const opcion of visibles) {
      const boton = document.createElement("button");
      boton.type = "button"; boton.tabIndex = -1; boton.id = `${entrada.id}-opcion-${desplegable.children.length}`;
      boton.setAttribute("role", "option");
      boton.setAttribute('aria-selected','false');
      boton.textContent = opcion.nombre;
      const detalle = document.createElement("small");
      detalle.textContent = opcion.pista;
      boton.appendChild(detalle);
      boton.addEventListener("mousedown", (e) => e.preventDefault());
      boton.addEventListener("click", () => {
        seleccionar(opcion);
      });
      desplegable.appendChild(boton);
    }
    desplegable.hidden = visibles.length === 0;
    entrada.setAttribute("aria-expanded", String(visibles.length > 0));
  }
  entrada.addEventListener("input", mostrar);
  entrada.addEventListener("focus", mostrar);
  entrada.addEventListener("blur", () => setTimeout(() => {
    ocultar();
    if (entrada.id === "buscar-objeto" && estado.familia && entrada.value !== nombreProductoActual()) {
      const exacto = opciones().find(o => sinAcentos(o.nombre) === sinAcentos(entrada.value));
      if (exacto) elegir(exacto);
      else entrada.value = nombreProductoActual();
    }
  }, 160));
  entrada.addEventListener("keydown", (e) => {
    if (e.key === "Escape") ocultar();
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); if (desplegable.hidden) mostrar();
      if (visibles.length) { activo = (activo + (e.key === 'ArrowDown' ? 1 : activo < 0 ? 0 : -1) + visibles.length) % visibles.length; resaltar(); }
    }
    if (e.key === "Enter" && visibles.length && !desplegable.hidden) {
      e.preventDefault(); seleccionar(visibles[Math.max(0, activo)]);
    }
  });
}

function opcionesTier(familia, preferido = 8) {
  const tiers = [...new Set([...familia.variantes.keys()].map(k => Number(k.split(":")[0])))].sort((a,b) => a-b);
  $("tier").replaceChildren(...tiers.map(tier => new Option(`T${tier}`, String(tier))));
  $("tier").value = String(tiers.includes(preferido) ? preferido : tiers[0]);
  opcionesEncantamiento(0);
}
function opcionesEncantamiento(preferido = 0) {
  const tier = Number($("tier").value);
  const familia = estado.familia;
  const opciones = [...new Set([...familia.variantes.keys()]
    .filter(k => Number(k.split(":")[0]) === tier)
    .map(k => Number(k.split(":")[1])))].sort((a,b) => a-b);
  $("encantamiento").replaceChildren(...opciones.map(v => new Option(`.${v}`, String(v))));
  $("encantamiento").value = String(opciones.includes(preferido) ? preferido : opciones[0]);
}
function seleccionarFamilia(familia, tier = 8, encantamiento = 0) {
  estado.familia = familia;
  $("buscar-objeto").value = familia.nombre;
  opcionesTier(familia, tier);
  opcionesEncantamiento(encantamiento);
  seleccionarVariante();
}
function seleccionarVariante() {
  const tier = Number($("tier").value);
  const encantamiento = Number($("encantamiento").value);
  const id = estado.familia?.variantes.get(`${tier}:${encantamiento}`);
  if (!id) return;
  if (estado.id && estado.id !== id) {
    $("precio-venta").value = "0";
    $("valor-item").value = "0";
  }
  estado.id = id;
  const item = estado.porId.get(id);
  if (esComidaActual()) $("buscar-objeto").value = item.es;
  $("nombre-elegido").textContent = item.es;
  $("id-elegido").textContent = `${id}  ·  T${tier}.${encantamiento}`;
  actualizarImagenesObjeto();
  registrarReciente();
  consultarReceta(false);
}

async function precargarRecetas() {
  const controlador=new AbortController();
  const reloj=setTimeout(()=>controlador.abort(),4000);
  try {
    const respuesta=await fetch('/api/recetas',{signal:controlador.signal});
    if(!respuesta.ok)throw new Error('Recetas no disponibles');
    const datos=await respuesta.json();
    for(const [id,r] of Object.entries(datos.recetas || {})) {
      if(!Array.isArray(r.materiales)||!r.materiales.length)continue;
      estado.recetasIncluidas.set(id,{...r,completa:true,valor_item:datos.valores?.[id],
        origen:'Receta de equipo incluida · datos del juego',
        aviso:`Copia del ${datos.consulta_utc}. `+(r.alternativas>1 ? 'Se muestra la primera receta de artefacto o token. Puedes cambiar sus materiales.' : 'Puedes ajustar los materiales o consultar la receta oficial.')});
    }
    $('estado-recetas-locales').textContent=`${entero(estado.recetasIncluidas.size)} recetas de equipo incluidas · selección rápida`;
  } catch(_) {$('estado-recetas-locales').textContent='Recetas por objeto disponibles · puedes seguir trabajando';}
  finally {clearTimeout(reloj);}
}

function pintarRecientes() {
  const zona=$('objetos-recientes');zona.replaceChildren();
  for(const r of estado.recientes) {
    const item=estado.porId.get(r.id);if(!item)continue;
    const boton=document.createElement('button');boton.type='button';boton.className='objeto-reciente';
    boton.textContent=item.es;boton.title=r.id+' · '+nombresCalidad[r.calidad];
    boton.dataset.itemId=r.id;boton.setAttribute('aria-label','Volver a '+item.es+' · '+r.id+' · '+nombresCalidad[r.calidad]);
    boton.addEventListener('click',()=>{
      const f=estado.familiaPorId.get(r.id);if(!f)return;
      $('calidad-lector').value=String(r.calidad);guardarOpcionesMercado();
      const match=/^T([1-8])_.*?(?:@([1-4]))?$/.exec(r.id);
      const encanto=Number(match?.[2] || /_LEVEL([1-4])/.exec(r.id)?.[1] || 0);
      seleccionarFamilia(f,Number(match?.[1] || 8),encanto);
    });
    zona.append(boton);
  }
}
function registrarReciente() {
  if(!estado.id)return;
  const calidad=Number($('calidad-lector').value) || 1;
  estado.recientes=[{id:estado.id,calidad},...estado.recientes.filter(r=>r.id!==estado.id||r.calidad!==calidad)].slice(0,6);
  try{localStorage.setItem('benjayaye-recientes-v50',JSON.stringify(estado.recientes));}catch(_){}
  pintarRecientes();
}

async function consultarReceta(oficial) {
  if (!estado.id) return;
  const id = estado.id;
  const guardada = !oficial && (estado.recetas.get(id) || estado.recetasIncluidas.get(id));
  const token = ++estado.tokenReceta;
  estado.controladorReceta?.abort();
  const controlador = new AbortController(); estado.controladorReceta = controlador;
  invalidarPreciosMercado();
  const boton = $("consultar-receta");
  boton.disabled = true;
  boton.textContent = "Consultando receta…";
  $("origen-receta").textContent = "Buscando los materiales de " + estado.porId.get(id).es + "…";
  estado.materiales = [];
  estado.unidadesPorCrafteo = 1;
  estado.completa = false;
  $("confirmar-receta").checked = false;
  $("confirmacion-receta").hidden = true;
  if (!guardada) { pintarMateriales(); limpiarResultados(); }
  try {
    let receta = guardada;
    if (!receta) {
      const url = `/api/receta?item_id=${encodeURIComponent(id)}${oficial ? "&oficial=1" : ""}`;
      const respuesta = await fetch(url, {signal: controlador.signal});
      receta = await respuesta.json();
      if (!respuesta.ok) throw new Error(receta.error || "No se pudo obtener la receta");
    }
    if (token !== estado.tokenReceta) return;
    if (receta.completa !== false && receta.materiales.length) {
      estado.recetas.delete(id); estado.recetas.set(id, JSON.parse(JSON.stringify(receta)));
      if (estado.recetas.size > 160) estado.recetas.delete(estado.recetas.keys().next().value);
    }
    estado.materiales = receta.materiales.map(m => ({
      id: m.id, cantidad: Number(m.cantidad), precio: precioMaterial(m.id),
      retorna: !sinDevolucion(m) && Boolean(m.retorna),
    }));
    estado.unidadesPorCrafteo = receta.unidades_por_crafteo || 1;
    if (!leerNumero('valor-item') && receta.valor_item > 0) $('valor-item').value = String(receta.valor_item);
    estado.completa = receta.completa !== false;
    $("confirmacion-receta").hidden = estado.completa;
    $("origen-receta").textContent = `${receta.origen}. ${receta.aviso}`;
  } catch (error) {
    if (token === estado.tokenReceta) {
      $("confirmacion-receta").hidden = false;
      $("origen-receta").textContent = `${error.message}. Puedes añadir los materiales manualmente.`;
    }
  } finally {
    if (token === estado.tokenReceta) { boton.disabled = false; boton.textContent = esComidaActual() ? "Restablecer receta de comida" : "Consultar receta oficial"; actualizarBotonLote(); aplicarPreciosMercado(true, 0); actualizarMercado(); }
  }
}

function pintarMateriales() {
  const primero = estado.materiales.find(m => !esArtefacto(m));
  const artefacto = estado.materiales.find(esArtefacto);
  const adicionales = estado.materiales.filter(m => !esArtefacto(m) && m !== primero);
  $("nombre-material").textContent = primero ? (estado.porId.get(primero.id)?.es || primero.id) : "Añade un material abajo";
  $("unidades-material").value = primero ? String(primero.cantidad) : "0";
  $("precio-material").value = primero ? String(primero.precio) : "0";
  $("unidades-material").disabled = !primero;
  $("precio-material").disabled = !primero;
  $("precio-material").readOnly = precioEsSoloLectura(primero?.id);
  $("precio-artefacto").readOnly = precioEsSoloLectura(artefacto?.id);
  idImagen($("imagen-material"), primero?.id);
  pintarResumenMateriales(estado.materiales);
  const listaAdicionales = $("otros-materiales");
  listaAdicionales.replaceChildren();
  listaAdicionales.hidden = adicionales.length === 0;
  for (const material of adicionales) {
    listaAdicionales.append(crearMaterialAdicional(material));
  }
  $("bloque-artefacto").hidden = !artefacto;
  $("bloque-artefacto").dataset.id = artefacto?.id || "";
  if (artefacto) {
    $("nombre-artefacto").textContent = estado.porId.get(artefacto.id)?.es || artefacto.id;
    $("unidades-artefacto").value = String(artefacto.cantidad);
    $("precio-artefacto").value = String(artefacto.precio);
    idImagen($("imagen-artefacto"), artefacto.id);
  }

  const cuerpo = $("filas-materiales");
  cuerpo.replaceChildren();
  if (!estado.materiales.length) {
    const fila = document.createElement("tr");
    const celda = document.createElement("td");
    celda.colSpan = 7; celda.className = "tabla-vacia";
    celda.textContent = "No hay una receta disponible. Busca un material por nombre y añádelo.";
    fila.append(celda); cuerpo.append(fila); return;
  }
  estado.materiales.forEach((material, indice) => {
    const item = estado.porId.get(material.id);
    const fila = document.createElement("tr");
    fila.dataset.indice = String(indice);
    const celdaNombre = document.createElement("td");
    const div = document.createElement("div"); div.className = "material-celda";
    const icono = document.createElement("img"); icono.alt = ""; idImagen(icono, material.id);
    const texto = document.createElement("div");
    const fuerte = document.createElement("strong"); fuerte.textContent = item?.es || material.id;
    const pequeño = document.createElement("small"); pequeño.textContent = material.id;
    texto.append(fuerte, pequeño); div.append(icono, texto); celdaNombre.append(div);
    fila.append(celdaNombre);
    const tdCantidad = document.createElement("td");
    const input = document.createElement("input");
    input.type = "number"; input.min = "0.000001"; input.step = "1";
    input.value = String(material.cantidad);
    input.setAttribute("aria-label", `Cantidad de ${item?.es || material.id}`);
    input.addEventListener("input", () => {
      material.cantidad = Number(input.value) || 0;
      if (material === primero) $("unidades-material").value = input.value;
      if (material === artefacto) $("unidades-artefacto").value = input.value;
      const otro = $("otros-materiales").querySelector(`.otro-material[data-id="${material.id}"]`);
      if (otro) otro.querySelector(".otro-cantidad").value = input.value;
      programarCalculo();
    });
    tdCantidad.append(input); fila.append(tdCantidad);
    const tdPrecio = document.createElement("td"); tdPrecio.className = "precio-receta";
    const valor = document.createElement("strong"); valor.className = "valor-precio-guardado";
    valor.textContent = material.precio > 0 ? dinero(material.precio) : "No existe precio";
    const editar = document.createElement("button"); editar.type = "button";
    editar.className = "editar-precio"; editar.textContent = "Editar precio";
    editar.addEventListener("click", () => {
      elegirMaterialParaPrecio(item || {id:material.id, es:material.id});
      $("precios").scrollIntoView({behavior:"smooth", block:"start"});
    });
    tdPrecio.append(valor, editar); fila.append(tdPrecio);
    const tdRetorno = document.createElement("td");
    const check = document.createElement("input"); check.type = "checkbox";
    check.checked = !sinDevolucion(material) && material.retorna;
    check.disabled = sinDevolucion(material);
    if (check.disabled) check.title = "Este material no recibe devolución";
    check.setAttribute("aria-label", `Retorna ${item?.es || material.id}`);
    check.addEventListener("change", () => {material.retorna = check.checked; programarCalculo();});
    tdRetorno.append(check); fila.append(tdRetorno);
    const tdLote = document.createElement("td");tdLote.className = "valor-lote";fila.append(tdLote);
    const tdCoste = document.createElement("td");tdCoste.className = "money valor-neto";fila.append(tdCoste);
    const tdQuitar = document.createElement("td");
    const quitar = document.createElement("button"); quitar.className = "quitar"; quitar.type = "button";
    quitar.textContent = "×"; quitar.title = "Quitar material"; quitar.setAttribute("aria-label", `Quitar ${item?.es || material.id}`);
    quitar.addEventListener("click", () => {estado.materiales.splice(indice, 1); pintarMateriales(); programarCalculo(); actualizarMercado();});
    tdQuitar.append(quitar);fila.append(tdQuitar);
    cuerpo.append(fila);
  });
}

function crearMaterialAdicional(material) {
  const contenedor = document.createElement("div");
  contenedor.className = "otro-material";
  contenedor.dataset.id = material.id;
  const fila = document.createElement("div"); fila.className = "fila-material-principal";
  const imagen = document.createElement("img"); imagen.alt = ""; idImagen(imagen, material.id);
  const rotulo = document.createElement("div");
  const etiqueta = document.createElement("span"); etiqueta.className = "etiqueta"; etiqueta.textContent = "OTRO MATERIAL";
  const nombre = document.createElement("strong"); nombre.textContent = estado.porId.get(material.id)?.es || material.id;
  rotulo.append(etiqueta, nombre); fila.append(imagen, rotulo);
  const controles = document.createElement("div"); controles.className = "par-controles";
  for (const [campo, titulo, clase] of [["cantidad", "Unidades / crafteo", "otro-cantidad"], ["precio", "Precio unitario", "otro-precio"]]) {
    const grupo = document.createElement("div");
    const label = document.createElement("label"); label.className = "etiqueta"; label.textContent = titulo;
    const input = document.createElement("input"); input.type = "number"; input.className = clase;
    input.min = campo === "precio" ? "0" : "0.000001";
    input.step = campo === "precio" ? "0.01" : "1";
    input.value = String(material[campo]);
    if (campo === "precio") input.readOnly = precioEsSoloLectura(material.id);
    input.setAttribute("aria-label", `${titulo} de ${nombre.textContent}`);
    input.addEventListener("input", () => {
      material[campo] = Number(input.value) || 0;
      const indice = estado.materiales.indexOf(material);
      const filaReceta = $("filas-materiales").querySelector(`tr[data-indice="${indice}"]`);
      if (filaReceta) {
        if (campo === "precio") filaReceta.querySelector(".valor-precio-guardado").textContent = dinero(input.value);
        else filaReceta.children[1].querySelector("input").value = input.value;
      }
      programarCalculo();
    });
    if (campo === "precio") input.addEventListener("change", () => guardarPrecioEnBD(material.id, input.value, false));
    grupo.append(label, input); controles.append(grupo);
  }
  contenedor.append(fila, controles);
  return contenedor;
}

function elegirMaterialParaPrecio(item) {
  estado.precioSeleccionado = item.id;
  $("buscar-material").value = item.es;
  $("editor-precio").hidden = false;
  idImagen($("imagen-precio-bd"), item.id);
  $("nombre-precio-bd").textContent = item.es;
  $("id-precio-bd").textContent = item.id;
  $("precio-bd").value = String(estado.precios.get(item.id) ?? 0);
  $("precio-bd").focus();
  $("precio-bd").select();
}

function pintarBasePrecios() {
  const filas = $("filas-precios");
  filas.replaceChildren();
  // La lista de nombres es estable mientras el mapa de precios no cambie.
  // Reutilizar su orden evita ordenar y normalizar miles de nombres por cada
  // tecla al filtrar la base.
  if (estado.mapaOrdenCache !== estado.precios || estado.revisionOrdenCache !== estado.revisionPrecios) {
    estado.idsPreciosOrdenados = [...estado.precios.keys()].filter(id => estado.porId.has(id))
      .sort((a, b) => estado.porId.get(a).es.localeCompare(estado.porId.get(b).es, "es"));
    estado.mapaOrdenCache = estado.precios;
    estado.revisionOrdenCache = estado.revisionPrecios;
  }
  const guardados = estado.idsPreciosOrdenados
    .filter(id => estado.precios.has(id)).map(id => [id, estado.precios.get(id)]);
  $("total-precios").textContent = `${guardados.length} ${guardados.length === 1 ? "precio guardado" : "precios guardados"}`;
  const terminos = sinAcentos($('filtrar-precios')?.value || '').trim().split(/\s+/).filter(Boolean);
  const filtrados = guardados.filter(([id]) => terminos.every(t => (estado.busquedaPorId.get(id) || id.toLowerCase()).includes(t)));
  const limite = estado.limitePrecios || 60;
  $('resumen-filtro-precios').textContent = `${Math.min(limite, filtrados.length)} de ${filtrados.length} resultados`;
  $('ver-mas-precios').hidden = filtrados.length <= limite;
  if (!filtrados.length) {
    const fila = document.createElement("tr");
    const celda = document.createElement("td"); celda.colSpan = 3; celda.className = "tabla-vacia";
    celda.textContent = guardados.length ? 'No hay precios guardados que coincidan con ese filtro.' : "Todavía no hay precios. Busca un material arriba para registrar el primero.";
    fila.append(celda); filas.append(fila); return;
  }
  for (const [id, precio] of filtrados.slice(0, limite)) {
    const item = estado.porId.get(id);
    const fila = document.createElement("tr");
    const tdNombre = document.createElement("td");
    const nombre = document.createElement("div"); nombre.className = "material-celda";
    const imagen = document.createElement("img"); imagen.alt = ""; imagen.loading = 'lazy'; idImagen(imagen, id);
    const rotulo = document.createElement("div");
    const fuerte = document.createElement("strong"); fuerte.textContent = item.es;
    const pequeño = document.createElement("small"); pequeño.textContent = id;
    rotulo.append(fuerte, pequeño); nombre.append(imagen, rotulo); tdNombre.append(nombre);
    const tdPrecio = document.createElement("td");
    const entrada = document.createElement("input"); entrada.type = "number";
    entrada.min = "0"; entrada.step = "0.01"; entrada.value = String(precio);
    entrada.setAttribute("aria-label", `Precio guardado de ${item.es}`);
    tdPrecio.append(entrada);
    const tdAccion = document.createElement("td");
    const guardar = document.createElement("button"); guardar.className = "boton-secundario boton-guardar-fila";
    guardar.type = "button"; guardar.textContent = "Guardar";
    guardar.addEventListener("click", () => guardarPrecioEnBD(id, entrada.value));
    entrada.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); guardar.click(); } });
    const borrar = document.createElement("button"); borrar.className = "boton-borrar-precio";
    borrar.type = "button"; borrar.textContent = "×"; borrar.title = "Eliminar precio guardado";
    borrar.setAttribute("aria-label", `Eliminar precio de ${item.es}`);
    borrar.addEventListener("click", () => eliminarPrecioDeBD(id));
    tdAccion.append(guardar, borrar); fila.append(tdNombre, tdPrecio, tdAccion); filas.append(fila);
  }
}

function sincronizarPrecio(id, precio) {
  for (const material of estado.materiales) {
    if (material.id === id) material.precio = precioMaterial(id);
  }
  if (estado.precioSeleccionado === id && document.activeElement !== $("precio-bd")) {
    $("precio-bd").value = String(precio);
  }
  if (id === estado.id) mercado.ventasManuales.delete(id);
  aplicarPreciosMercado();
  pintarBasePrecios();
  pintarPreciosMercado(idsMercado());
}

async function guardarPrecioEnBD(id, textoPrecio, mostrarAviso = true) {
  const precio = Number(textoPrecio);
  if (String(textoPrecio).trim() === "" || !Number.isFinite(precio) || precio < 0 || precio > 1e15) {
    $("estado-precios").className = "estado-precios error";
    $("estado-precios").textContent = "Escribe un precio válido mayor o igual a cero.";
    return;
  }
  const anterior = estado.guardadosPendientes.get(id) || Promise.resolve();
  const guardado = anterior.catch(() => {}).then(async () => {
    const respuesta = await fetch("/api/precios", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id, precio}),
    });
    const datos = await respuesta.json();
    if (!respuesta.ok) throw new Error(datos.error || "No se pudo guardar el precio");
    return datos;
  });
  estado.guardadosPendientes.set(id, guardado);
  try {
    const datos = await guardado;
    estado.precios.set(id, datos.precio);
    estado.revisionPrecios++;
    sincronizarPrecio(id, datos.precio);
    $("estado-precios").className = "estado-precios";
    $("estado-precios").textContent = mostrarAviso
      ? `Guardado: ${estado.porId.get(id)?.es || id} · ${dinero(datos.precio)} de plata.`
      : `Precio actualizado y guardado: ${estado.porId.get(id)?.es || id}.`;
  } catch (error) {
    $("estado-precios").className = "estado-precios error";
    $("estado-precios").textContent = `No se guardó el precio: ${error.message}`;
  } finally {
    if (estado.guardadosPendientes.get(id) === guardado) estado.guardadosPendientes.delete(id);
  }
}

async function eliminarPrecioDeBD(id) {
  try {
    await estado.guardadosPendientes.get(id)?.catch(() => {});
    const respuesta = await fetch(`/api/precios?item_id=${encodeURIComponent(id)}`, {method: "DELETE"});
    const datos = await respuesta.json();
    if (!respuesta.ok) throw new Error(datos.error || "No se pudo eliminar el precio");
    estado.precios.delete(id);
    estado.revisionPrecios++;
    sincronizarPrecio(id, 0);
    if (estado.precioSeleccionado === id) $("precio-bd").value = "0";
    $("estado-precios").className = "estado-precios";
    $("estado-precios").textContent = `Precio eliminado: ${estado.porId.get(id)?.es || id}.`;
  } catch (error) {
    $("estado-precios").className = "estado-precios error";
    $("estado-precios").textContent = `No se eliminó el precio: ${error.message}`;
  }
}

function pintarResumenMateriales(materiales, calculados = false) {
  const resumen = $("resumen-materiales");
  resumen.replaceChildren();
  $("tarjetas-resultado").classList.toggle("varios-materiales", materiales.length > 1);
  if (!calculados) $("costo-materiales-total").textContent = "—";
  if (!materiales.length) {
    const vacio = document.createElement("p"); vacio.className = "resumen-materiales-vacio";
    vacio.textContent = "Añade materiales a la receta para ver sus cantidades.";
    resumen.append(vacio);
    return;
  }
  for (const material of materiales) {
    const bloque = document.createElement("div"); bloque.className = "detalle-material-resumen";
    bloque.dataset.id = material.id;
    const cabecera = document.createElement("div"); cabecera.className = "nombre-material-resumen";
    const imagen = document.createElement("img"); imagen.alt = ""; idImagen(imagen, material.id);
    const nombre = document.createElement("h4");
    nombre.textContent = estado.porId.get(material.id)?.es || material.nombre || material.id;
    cabecera.append(imagen, nombre);
    const datos = document.createElement("dl");
    for (const [titulo, valor] of [
      ["A comprar (lote)", calculados ? entero(material.a_comprar) : "—"],
      ["Coste neto", calculados ? dinero(material.neto) : "—"],
    ]) {
      const grupo = document.createElement("div");
      const etiqueta = document.createElement("dt"); etiqueta.textContent = titulo;
      const dato = document.createElement("dd");
      const cifra = document.createElement("strong"); cifra.textContent = valor;
      dato.append(cifra); grupo.append(etiqueta, dato); datos.append(grupo);
    }
    bloque.append(cabecera, datos);
    if (esArtefacto(material)) {
      const nota = document.createElement("small"); nota.textContent = "Artefacto · sin devolución";
      bloque.append(nota);
    }
    resumen.append(bloque);
  }
}

function mostrarResultados(respuesta) {
  pintarResumenMateriales(respuesta.detalle, true);
  const preciosIncompletos = (!leerNumero("precio-venta") || estado.materiales.some(m => !m.precio));
  const sinEstacion = respuesta.estacion_calculable === false;
  const sinReceta = respuesta.estado !== "listo" || preciosIncompletos || sinEstacion;
  const sinCostes = respuesta.estado !== 'listo' || estado.materiales.some(m => !m.precio) || sinEstacion;
  const mapa = {
    "costo-materiales-total": respuesta.detalle.length ? dinero(respuesta.materiales_netos) : "—",
    "impuestos-estacion": sinEstacion ? "—" : dinero(respuesta.impuesto_estacion),
    "estacion-unitaria": sinEstacion ? "—" : dinero(respuesta.estacion_por_objeto ?? respuesta.impuesto_estacion / (respuesta.cantidad_producida || respuesta.cantidad || 1)),
    "inversion-total": sinCostes ? "—" : dinero(respuesta.inversion),
    "coste-unitario": sinCostes ? "—" : dinero(respuesta.costo_por_objeto),
    "ganancia-unitaria": sinReceta ? "—" : dinero(respuesta.ganancia_por_objeto),
    "ganancia-lote": sinReceta ? "—" : dinero(respuesta.ganancia),
    "ingreso-bruto": dinero(respuesta.ingreso_bruto),
    "comision-mercado": dinero(respuesta.impuesto_venta),
    "ingreso-neto": dinero(respuesta.ingreso_neto),
    "ahorro-retorno": dinero(respuesta.ahorro_retorno),
    "precio-equilibrio": sinCostes || respuesta.precio_equilibrio == null ? '—' : dinero(respuesta.precio_equilibrio),
    "margen-neto": sinReceta || respuesta.margen_neto == null ? '—' : entero(respuesta.margen_neto) + ' %',
  };
  for (const [id, valor] of Object.entries(mapa)) $(id).textContent = valor;
  $('estado-estacion').textContent = sinEstacion ? 'Introduce el valor del objeto para calcular el cobro de la estación.' : 'Solo estación · no incluye materiales ni comisión de venta.';
  const ganancia = $('ganancia-lote').closest('.ganancia');
  ganancia.dataset.resultado = sinReceta ? 'pendiente' : respuesta.ganancia < 0 ? 'perdida' : 'beneficio';
  $('evaluacion-lote').textContent = sinReceta ? 'Completa los precios para evaluar el lote' : respuesta.ganancia < 0 ? 'Pérdida estimada' : respuesta.ganancia === 0 ? 'Punto de equilibrio' : 'Beneficio estimado';
  for (const fila of document.querySelectorAll("#filas-materiales tr[data-indice]")) {
    const detalle = respuesta.detalle[Number(fila.dataset.indice)];
    if (!detalle) continue;
    fila.querySelector(".valor-lote").textContent = entero(detalle.a_comprar);
    fila.querySelector(".valor-neto").textContent = dinero(detalle.neto);
  }
  const estadoLabel = $("estado");
  if (respuesta.estado === "sin_materiales") {
    estadoLabel.className = "estado alerta";
    estadoLabel.textContent = "Añade materiales y sus precios para calcular la ganancia.";
  } else if (respuesta.estado === "receta_incompleta") {
    estadoLabel.className = "estado alerta";
    estadoLabel.textContent = "Receta parcial: añade los demás materiales y confirma abajo para calcular la ganancia.";
  } else if (sinEstacion) {
    estadoLabel.className = "estado alerta";
    estadoLabel.textContent = "Falta el valor del objeto para calcular el coste de la estación.";
  } else if (!leerNumero("precio-venta") || respuesta.detalle.some(m => !precioMaterial(m.id))) {
    estadoLabel.className = "estado alerta";
    estadoLabel.textContent = mercado.automatico ? "Faltan precios de mercado. Consulta el detalle de precios o cambia al modo manual." : "Cálculo orientativo: guarda los precios que faltan y comprueba los que están en cero.";
  } else {
    estadoLabel.className = "estado bien";
    estadoLabel.textContent = "Cálculo actualizado. Confirma la receta y los precios antes de fabricar.";
  }
}

function limpiarResultados() {
  pintarResumenMateriales(estado.materiales);
  for (const id of ["impuestos-estacion","estacion-unitaria","inversion-total","coste-unitario","ganancia-unitaria","ganancia-lote","ingreso-bruto","comision-mercado","ingreso-neto","ahorro-retorno","precio-equilibrio","margen-neto"]) {
    $(id).textContent = "—";
  }
  $('ganancia-lote').closest('.ganancia').dataset.resultado = 'pendiente';
  $('evaluacion-lote').textContent = 'Completa los datos para evaluar el lote';
  for (const fila of document.querySelectorAll("#filas-materiales tr[data-indice]")) {
    fila.querySelector(".valor-lote").textContent = "—";
    fila.querySelector(".valor-neto").textContent = "—";
  }
}

let temporizador;
function programarCalculo(demora = 130) {
  actualizarProduccion();
  actualizarBotonLote();
  ++estado.tokenCalculo;
  estado.controladorCalculo?.abort();
  clearTimeout(temporizador);
  temporizador = setTimeout(calcular, typeof demora === 'number' ? demora : 130);
}
async function calcular() {
  const token = ++estado.tokenCalculo;
  const controlador = new AbortController(); estado.controladorCalculo = controlador;
  guardarSesion();
  const payload = {
    item_id: estado.id,
    cantidad: leerNumero("cantidad"), precio_venta: leerNumero("precio-venta"),
    retorno: leerNumero("retorno"), tasa_estacion: leerNumero("tasa-estacion"),
    valor_item: leerNumero("valor-item"), comision: leerNumero("comision"),
    otros_costos: leerNumero("otros-costos"), materiales: estado.materiales,
    unidades_por_crafteo: estado.unidadesPorCrafteo,
    receta_completa: estado.completa || $("confirmar-receta").checked,
  };
  try {
    const res = await fetch("/api/calcular", {method:"POST",signal:controlador.signal,headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const datos = await res.json();
    if (token !== estado.tokenCalculo) return;
    if (!res.ok) throw new Error(datos.error || "No se pudo calcular");
    mostrarResultados(datos);
  } catch (error) {
    if (token !== estado.tokenCalculo || error.name === 'AbortError') return;
    limpiarResultados();
    $("estado").className = "estado error";
    $("estado").textContent = error.message;
  }
}

const camposSesion = {'cantidad':[1,1000000], 'retorno':[0,100], 'tasa-estacion':[0,1e12], 'otros-costos':[0,1e12], 'comision':[0,100]};
function guardarSesion() {
  if (!estado.id) return;
  const datos = {id:estado.id};
  for (const [id,[min,max]] of Object.entries(camposSesion)) {
    const valor = Number($(id).value);
    if ($(id).value !== '' && Number.isFinite(valor) && valor >= min && valor <= max && (id !== 'cantidad' || Number.isInteger(valor))) datos[id] = valor;
  }
  try { localStorage.setItem('benjayaye-sesion-v22', JSON.stringify(datos)); } catch (_) {}
}
function restaurarSesion() {
  try {
    const datos = JSON.parse(localStorage.getItem('benjayaye-sesion-v22') || 'null');
    if (!datos || !estado.porId.has(datos.id)) return null;
    for (const [id,[min,max]] of Object.entries(camposSesion)) {
      const v = datos[id];
      if (typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max && (id !== 'cantidad' || Number.isInteger(v))) $(id).value = String(v);
    }
    return datos.id;
  } catch (_) { return null; }
}

async function iniciar() {
  try {
    precargarRecetas();
    const [respuesta, respuestaPrecios] = await Promise.all([fetch("/api/catalogo"), fetch("/api/precios")]);
    if (!respuesta.ok) throw new Error("No se pudo abrir el catálogo local");
    if (!respuestaPrecios.ok) throw new Error("No se pudieron abrir los precios guardados");
    estado.catalogo = await respuesta.json();
    const guardados = await respuestaPrecios.json();
    estado.precios = new Map(Object.entries(guardados.precios));
    mercado.manualesActualizados = Date.now();
    construirFamilias();
    try {
      const r=JSON.parse(localStorage.getItem('benjayaye-recientes-v50') || '[]');
      if(Array.isArray(r))estado.recientes=r.filter(x=>x&&estado.porId.has(x.id)&&Number.isInteger(x.calidad)&&x.calidad>=1&&x.calidad<=5).slice(0,6);
    }catch(_){}
    iniciarPlan();
    iniciarMercado();
    $('calidad-lector').addEventListener('change',registrarReciente);
    pintarBasePrecios();
    $('filtrar-precios').addEventListener('input', () => { estado.limitePrecios = 60; pintarBasePrecios(); });
    $('ver-mas-precios').addEventListener('click', () => { estado.limitePrecios += 60; pintarBasePrecios(); });
    const opcionesProducto = construirOpcionesProducto();
    const opcionesMaterial = estado.catalogo.map(item => ({
      nombre: item.es, pista: item.id, busqueda: sinAcentos(`${item.es} ${item.en} ${item.id}`), valor: item,
    }));
    crearBuscador($("buscar-objeto"), $("sugerencias-objeto"),
      () => opcionesProducto.filter(o => $("tipo-objeto").value !== "comida" || o.tipo === "comida"),
      opcion => seleccionarFamilia(opcion.valor, opcion.tier ?? 8));
    $("tipo-objeto").addEventListener("change", () => {
      $("buscar-objeto").value = "";
      $("buscar-objeto").placeholder = $("tipo-objeto").value === "comida" ? "Ej.: guiso de ternera, tortilla…" : "Escribe el nombre…";
      $("sugerencias-objeto").hidden = true;
      $("buscar-objeto").setAttribute("aria-expanded", "false");
      $("buscar-objeto").focus();
    });
    crearBuscador($("buscar-receta"), $("sugerencias-receta"), () => opcionesMaterial,
      opcion => {
        const item = opcion.valor;
        if (estado.materiales.length >= 20) { $("estado").textContent = "Máximo 20 materiales."; return; }
        if (!estado.materiales.some(m => m.id === item.id)) {
          estado.materiales.push({id:item.id,cantidad:1,precio:precioMaterial(item.id),retorna:!sinDevolucion(item)});
          pintarMateriales(); programarCalculo(); actualizarMercado();
        }
        $("buscar-receta").value = "";
      });
    crearBuscador($("buscar-material"), $("sugerencias-material"), () => opcionesMaterial,
      opcion => elegirMaterialParaPrecio(opcion.valor));
    $("guardar-precio").addEventListener("click", () => {
      if (estado.precioSeleccionado) guardarPrecioEnBD(estado.precioSeleccionado, $("precio-bd").value);
    });
    $("precio-bd").addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); $("guardar-precio").click(); }
    });
    $("tier").addEventListener("change", () => {opcionesEncantamiento(0); seleccionarVariante();});
    $("encantamiento").addEventListener("change", seleccionarVariante);
    $("consultar-receta").addEventListener("click", () => consultarReceta(true));
    $("confirmar-receta").addEventListener("change", programarCalculo);
    for (const id of ["precio-venta","cantidad","retorno","tasa-estacion","valor-item","comision","otros-costos"]) {
      $(id).addEventListener("input", programarCalculo);
    }
    for (const [input,campo] of [["unidades-material","cantidad"],["precio-material","precio"]]) {
      $(input).addEventListener("input", () => {
        const material = estado.materiales.find(m => !esArtefacto(m));
        if (!material) return;
        material[campo] = Number($(input).value) || 0;
        const indice = estado.materiales.indexOf(material);
        const fila = $("filas-materiales").querySelector(`tr[data-indice="${indice}"]`);
        if (fila) {
          if (campo === "precio") fila.querySelector(".valor-precio-guardado").textContent = dinero($(input).value);
          else fila.children[1].querySelector("input").value = $(input).value;
        }
        programarCalculo();
      });
      if (campo === "precio") $(input).addEventListener("change", () => {
        const material = estado.materiales.find(m => !esArtefacto(m));
        if (material) guardarPrecioEnBD(material.id, $(input).value, false);
      });
    }
    for (const [input,campo] of [["unidades-artefacto","cantidad"],["precio-artefacto","precio"]]) {
      $(input).addEventListener("input", () => {
        const artefacto = estado.materiales.find(esArtefacto);
        if (!artefacto) return;
        artefacto[campo] = Number($(input).value) || 0;
        const indice = estado.materiales.indexOf(artefacto);
        const fila = $("filas-materiales").querySelector(`tr[data-indice="${indice}"]`);
        if (fila) {
          if (campo === "precio") fila.querySelector(".valor-precio-guardado").textContent = dinero($(input).value);
          else fila.children[1].querySelector("input").value = $(input).value;
        }
        programarCalculo();
      });
      if (campo === "precio") $(input).addEventListener("change", () => {
        const artefacto = estado.materiales.find(esArtefacto);
        if (artefacto) guardarPrecioEnBD(artefacto.id, $(input).value, false);
      });
    }
    const guardado = restaurarSesion();
    const variante = /^T([1-8])_(.+?)(?:_LEVEL([1-4]))?(?:@([1-4]))?$/.exec(guardado || '');
    const inicial = (variante && estado.familias.get(variante[2])) || estado.familias.get("ARMOR_CLOTH_SET2") || opcionesProducto[0]?.valor;
    if (inicial) seleccionarFamilia(inicial, variante ? Number(variante[1]) : 8, variante ? Number(variante[4] || variante[3] || 0) : 0);
  } catch (error) {
    $("nombre-elegido").textContent = "No se pudo cargar el catálogo";
    $("estado").className = "estado error";
    $("estado").textContent = error.message;
  }
}

iniciar();
