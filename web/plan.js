"use strict";

const cantidadesPlan = new Intl.NumberFormat("es-ES", {maximumFractionDigits: 6});
const cantidadPlan = valor => cantidadesPlan.format(valor);
const comprasEnCola = new Map();
let guardandoCompras = false;

function nodoPlan(etiqueta, texto, clase = "") {
  const nodo = document.createElement(etiqueta);
  if (texto !== undefined) nodo.textContent = texto;
  if (clase) nodo.className = clase;
  return nodo;
}

function fichaPlan(id, nombre, calidad = 1) {
  const ficha = nodoPlan("div", undefined, "material-celda");
  const imagen = document.createElement("img"); imagen.alt = ""; imagen.loading = "lazy";
  idImagen(imagen, id, calidad);
  const texto = document.createElement("div");
  const tier = /^T(\d)/.exec(id)?.[1];
  const nivel = /@(\d)$/.exec(id)?.[1] || "0";
  texto.append(nodoPlan("strong", nombre), nodoPlan("small", tier ? `T${tier}.${nivel} · ${id}` : id));
  ficha.append(imagen, texto);
  return ficha;
}

function actualizarBotonLote() {
  const boton = $("guardar-lote");
  boton.disabled = !estado.id || !estado.planCargado || estado.planOcupado || $("consultar-receta").disabled;
}

function mensajeGuardado(texto, error = false) {
  $("estado-guardado").textContent = texto;
  $("estado-guardado").classList.toggle("error", error);
}

function pintarPlan(plan) {
  estado.ultimoPlan = plan;
  $('completar-plan').disabled = estado.planOcupado || !plan.lotes.length;
  $('borrar-todos-lotes').disabled = estado.planOcupado || !plan.lotes.length;
  $("total-lotes-plan").textContent = cantidadPlan(plan.lotes.length);
  $("total-items-plan").textContent = cantidadPlan(plan.total_items);
  $("total-materiales-plan").textContent = cantidadPlan(plan.materiales.length);
  $("plan-vacio").hidden = plan.lotes.length > 0;
  $("plan-contenido").hidden = plan.lotes.length === 0;
  $("objetos-plan").replaceChildren();
  for (const objeto of plan.objetos) {
    const tarjeta = nodoPlan("div", undefined, "objeto-plan");
    tarjeta.append(fichaPlan(objeto.id, objeto.nombre), nodoPlan("strong", `${cantidadPlan(objeto.cantidad)} ítems`, "cantidad-objeto-plan"));
    $("objetos-plan").append(tarjeta);
  }
  $("materiales-plan").replaceChildren();
  for (const material of plan.materiales) {
    const fila = document.createElement("tr"); fila.dataset.materialId = material.id;
    const nombre = document.createElement("td"); nombre.append(fichaPlan(material.id, material.nombre));
    fila.dataset.comprado=String(material.comprado===true);
    const total = nodoPlan('td',undefined,'money');
    const compra=nodoPlan('div',undefined,'cantidad-compra-material');
    const contador=nodoPlan('div',undefined,'contador-compra-material');
    const entrada=document.createElement('input');entrada.type='number';entrada.className='cantidad-comprada-material';
    entrada.min='0';entrada.max=String(Number.MAX_SAFE_INTEGER);entrada.step='1';entrada.inputMode='numeric';
    entrada.value=String(material.cantidad_comprada||0);entrada.autocomplete='off';
    entrada.setAttribute('aria-label',`Cantidad comprada: ${material.nombre} · ${material.id}`);
    entrada.setAttribute('aria-describedby',`compra-nota-${material.id}`);
    entrada.title='Unidades que ya compraste. Escribe la cantidad y pulsa Enter o sal del campo para guardarla.';
    entrada.addEventListener('change',()=>guardarNumeroCompra(material.id,entrada));
    entrada.addEventListener('keydown',evento=>{
      if(evento.key==='Enter'){evento.preventDefault();guardarNumeroCompra(material.id,entrada);}
    });
    entrada.disabled=estado.planOcupado&&!guardandoCompras;
    const barra=nodoPlan('span','/','barra-compra-material');barra.setAttribute('aria-hidden','true');
    const necesario=nodoPlan('strong',cantidadPlan(material.a_comprar),'total-acumulado');necesario.title='Cantidad total necesaria, con la devolución aplicada';
    contador.append(entrada,barra,necesario);compra.append(contador);
    const etiqueta=nodoPlan('label',undefined,'marcar-compra-material');
    const check=document.createElement('input');check.type='checkbox';check.className='check-compra-material';
    check.checked=material.comprado===true;check.dataset.sinCompra=String(material.a_comprar<=0);
    check.disabled=estado.planOcupado||material.a_comprar<=0;
    check.setAttribute('aria-label',`Comprado: ${cantidadPlan(material.a_comprar)} ${material.nombre} · ${material.id}`);
    check.addEventListener('change',()=>guardarCompraMaterial(material,check));
    etiqueta.title='Marca para completar la cantidad necesaria; desmarca para volver a 0.';
    etiqueta.append(check,nodoPlan('span','Comprado'));compra.append(etiqueta);total.append(compra);
    const nota=nodoPlan('small',notaCompraMaterial(material),'nota-compra-material');nota.id=`compra-nota-${material.id}`;total.append(nota);
    const destino = document.createElement("td");
    const lista = nodoPlan("ul", undefined, "destinos-plan");
    for (const uso of material.destinos) {
      const entrada = document.createElement("li");
      entrada.append(nodoPlan("strong", `≈ ${cantidadPlan(uso.a_comprar)} unidades → ${cantidadPlan(uso.cantidad_items)} × ${uso.nombre}`));
      entrada.append(nodoPlan("small", uso.item_id));
      lista.append(entrada);
    }
    destino.append(lista); fila.append(nombre, total, destino); $("materiales-plan").append(fila);
  }
  actualizarEstadoCompras(plan);
  pintarLotesPlan(plan);
  $("estado-plan").textContent = plan.lotes.some(lote => !lote.retorno_guardado)
    ? "Hay lotes antiguos sin devolución guardada: se mantienen al 0 %. Usa Editar lote para aplicarles el porcentaje que necesites."
    : "Lista guardada en este equipo. Cada lote conserva su porcentaje de devolución.";
  $("estado-plan").classList.remove("error");
  const f = plan.finanzas;
  $('ganancia-total-plan').textContent = f?.ganancia_total != null ? dinero(f.ganancia_total) : 'Pendiente';
  $('ganancia-parcial-plan').textContent = f ? dinero(f.ganancia) : '—';
  for (const [id,clave] of [['gasto-total-plan','gasto_total'],['ventas-total-plan','ingreso_bruto'],
      ['materiales-total-plan','materiales_netos'],['estacion-total-plan','impuesto_estacion'],
      ['otros-total-plan','otros_costos'],['comision-total-plan','impuesto_venta'],
      ['inversion-total-plan','inversion'],['ingreso-total-plan','ingreso_neto']]) {
    const total=f?.totales?.[clave], conocidos=f?.cobertura?.[clave] || 0;
    $(id).textContent = total != null ? dinero(total) : conocidos ? dinero(f.disponibles[clave])+' (parcial)' : 'Faltan datos';
    $(id).title = total != null ? 'Incluye todos los lotes' : `${conocidos} de ${plan.lotes.length} lotes con este dato. Revisa los campos que faltan en cada fila.`;
  }
  $('parcial-finanzas-plan').hidden = !f?.lotes_pendientes;
  $('finanzas-plan').dataset.resultado = f?.ganancia_total == null ? 'pendiente' : f.ganancia_total < 0 ? 'perdida' : 'beneficio';
  $('estado-finanzas-plan').textContent = f?.lotes_pendientes
    ? `${f.lotes_completos} de ${plan.lotes.length} lotes completos · ${f.lotes_pendientes} pendientes. Cada fila indica lo que falta. Pulsa «Completar precios faltantes» o «Editar lote». Las cifras parciales no incluyen todos los lotes.`
    : `Incluye los ${plan.lotes.length} lotes y ${cantidadPlan(plan.total_items)} ítems. Ventas − gasto total = ganancia. La comisión ya está descontada una sola vez.`;
}


function pintarLotesPlan(plan) {
  if(!estado.limiteLotes)estado.limiteLotes=50;
  const consulta=sinAcentos($('filtrar-lotes').value).trim().split(/\s+/).filter(Boolean);
  const filtro=$('estado-lotes').value,orden=$('orden-lotes').value;
  const visibles=[...plan.lotes].reverse().filter(lote=>{
    const r=lote.resumen_economico;
    const tipo=!r?.completa ? 'pendiente' : r.ganancia<0 ? 'perdida' : 'beneficio';
    const tier=/^T([1-8])_/.exec(lote.item_id)?.[1],encanto=/@([1-4])$/.exec(lote.item_id)?.[1] || '0';
    const texto=sinAcentos(lote.nombre+' '+lote.item_id+' '+(nombresCalidad[lote.economia?.calidad] || '')+' '+(tier ? `T${tier}.${encanto}` : ''));
    return (filtro==='todos'||filtro===tipo)&&consulta.every(t=>texto.includes(t));
  });
  if(orden==='recientes')visibles.sort((a,b)=>Date.parse(b.creado)-Date.parse(a.creado));
  else if(orden==='nombre')visibles.sort((a,b)=>a.nombre.localeCompare(b.nombre,'es')||a.item_id.localeCompare(b.item_id));
  else if(orden!=='recientes')visibles.sort((a,b)=>{
    const x=a.resumen_economico?.ganancia,y=b.resumen_economico?.ganancia;
    if(x==null||y==null)return x==null ? y==null ? 0 : 1 : -1;
    return orden==='menor-ganancia' ? x-y : y-x;
  });
  $("lotes-plan").replaceChildren();
  for (const lote of visibles.slice(0,estado.limiteLotes)) {
    const fila = document.createElement("tr"); fila.dataset.loteId = lote.id;
    const nombre = document.createElement("td"); nombre.append(fichaPlan(lote.item_id, lote.nombre, lote.economia?.calidad || 1));
    if (lote.economia) nombre.append(nodoPlan('small', nombresCalidad[lote.economia.calidad], 'devolucion-lote'));
    nombre.append(nodoPlan("small", lote.retorno_guardado
      ? `Devolución guardada: ${cantidadPlan(lote.retorno)} %`
      : "Sin devolución guardada: se aplica 0 %", "devolucion-lote"));
    const total = nodoPlan("td", cantidadPlan(lote.cantidad), "money");
    if (lote.unidades_por_crafteo > 1) total.append(nodoPlan("small", `${cantidadPlan(lote.crafteos)} crafteos × ${cantidadPlan(lote.unidades_por_crafteo)}`));
    const resumen = lote.resumen_economico;
    const venta = nodoPlan('td', resumen?.precio_venta > 0 ? dinero(resumen.precio_venta) : 'Sin precio', 'money venta-lote');
    venta.append(nodoPlan('small', 'plata por ítem'));
    const estacion = nodoPlan('td', resumen?.impuesto_estacion != null ? dinero(resumen.impuesto_estacion) : 'Pendiente', 'money estacion-lote');
    if (resumen?.estacion_por_objeto != null) estacion.append(nodoPlan('small', dinero(resumen.estacion_por_objeto)+' por ítem'));
    if (resumen?.valor_item_automatico) estacion.append(nodoPlan('small', 'Valor del objeto automático'));
    const gasto = nodoPlan('td', resumen?.gasto_total != null ? dinero(resumen.gasto_total) : 'Faltan datos', 'money gasto-lote');
    gasto.append(nodoPlan('small', 'Incluye comisión de venta'));
    const ganancia = nodoPlan('td', resumen?.completa ? dinero(resumen.ganancia) : 'Completar precios', 'money ganancia-lote-guardado');
    ganancia.dataset.resultado = !resumen?.completa ? 'pendiente' : resumen.ganancia < 0 ? 'perdida' : 'beneficio';
    if (!resumen?.completa) {
      ganancia.title = (resumen?.faltantes || ['Precios y costes no guardados']).join('; ');
      ganancia.append(nodoPlan('small', 'Falta: '+ganancia.title, 'faltantes-lote'));
    }
    const materiales = document.createElement("td");
    const lista = nodoPlan("ul", undefined, "destinos-plan");
    for (const material of lote.materiales) {
      const entrada = document.createElement("li");
      entrada.append(nodoPlan("strong", `${cantidadPlan(material.a_comprar)} × ${material.nombre}`));
      entrada.append(nodoPlan("small", `A comprar, redondeado · ${material.id}`));
      lista.append(entrada);
    }
    materiales.append(lista);
    const acciones = document.createElement("td");
    const editar = nodoPlan('button', lote.economia ? 'Editar lote' : 'Completar lote', 'boton-secundario boton-editar-lote'); editar.type='button';
    editar.setAttribute('aria-label',`Editar lote de ${lote.nombre}`);
    editar.addEventListener('click',()=>abrirEditorLote(lote));
    const quitar = nodoPlan("button", "Quitar lote", "boton-quitar-lote"); quitar.type = "button";
    quitar.setAttribute("aria-label", `Quitar lote de ${cantidadPlan(lote.cantidad)} ${lote.nombre}`);
    quitar.addEventListener("click", () => quitarLote(lote.id));
    acciones.append(editar, quitar); fila.append(nombre, total, venta, estacion, gasto, ganancia, materiales, acciones); $("lotes-plan").append(fila);
  }

  $('resumen-filtro-lotes').textContent=`Mostrando ${Math.min(estado.limiteLotes,visibles.length)} de ${visibles.length} coincidencias · ${plan.lotes.length} lotes guardados`;
  $('sin-resultados-lotes').hidden=visibles.length>0;
  $('ver-mas-lotes').hidden=visibles.length<=estado.limiteLotes;
}

function bloquearPlan(ocupado) {
  estado.planOcupado = ocupado;
  $("recargar-plan").disabled = ocupado;
  $('completar-plan').disabled = ocupado || !estado.ultimoPlan?.lotes.length;
  $('borrar-todos-lotes').disabled = ocupado || !estado.ultimoPlan?.lotes.length;
  $('deshacer-lote').disabled=ocupado;
  $('restaurar-respaldo').disabled=ocupado||!$('archivo-respaldo').files?.length;
  $('desmarcar-compras').disabled=ocupado||!estado.ultimoPlan?.materiales.some(m=>m.cantidad_marcada!=null);
  document.querySelectorAll('.check-compra-material').forEach(check=>{check.disabled=ocupado||check.dataset.sinCompra==='true';});
  document.querySelectorAll('.cantidad-comprada-material').forEach(entrada=>{entrada.disabled=ocupado&&!guardandoCompras;});
  document.querySelectorAll(".boton-quitar-lote,.boton-editar-lote").forEach(boton => { boton.disabled = ocupado; });
  actualizarBotonLote();
}

async function cargarPlan() {
  if (estado.planOcupado) return;
  bloquearPlan(true);
  try {
    const respuesta = await fetch("/api/plan");
    const datos = await respuesta.json();
    if (!respuesta.ok) throw new Error(datos.error || "No se pudo abrir la lista");
    pintarPlan(datos); estado.planCargado = true;
  } catch (error) {
    $("estado-plan").textContent = `No se pudo cargar la lista: ${error.message}. Pulsa Actualizar lista para reintentar.`;
    $("estado-plan").classList.add("error");
  } finally { bloquearPlan(false); }
}

async function guardarLoteActual() {
  if (estado.planOcupado || !estado.planCargado || $("consultar-receta").disabled) return;
  const cantidad = Number($("cantidad").value);
  if (!estado.id || !Number.isInteger(cantidad) || cantidad < 1 || cantidad > 1000000) {
    mensajeGuardado("Introduce una cantidad entera entre 1 y 1.000.000 de objetos.", true); return;
  }
  if (!estado.materiales.length || !(estado.completa || $("confirmar-receta").checked)) {
    mensajeGuardado("Añade todos los materiales y confirma la receta abajo antes de guardar.", true); return;
  }
  if (estado.materiales.some(m => !Number.isFinite(m.cantidad) || m.cantidad <= 0)) {
    mensajeGuardado("Cada material necesita una cantidad válida mayor que cero.", true); return;
  }
  const retorno = Number($("retorno").value);
  if (!Number.isFinite(retorno) || retorno < 0 || retorno > 100) {
    mensajeGuardado("La devolución debe estar entre 0 y 100 %.", true); return;
  }
  const lote = {item_id: estado.id, cantidad, retorno, receta_completa: true, unidades_por_crafteo: estado.unidadesPorCrafteo,
    materiales: estado.materiales.map(m => ({id: m.id, cantidad: m.cantidad, retorna: m.retorna})),
    economia: {precio_venta:leerNumero('precio-venta'),tasa_estacion:leerNumero('tasa-estacion'),valor_item:leerNumero('valor-item'),
      comision:leerNumero('comision'),otros_costos:leerNumero('otros-costos'),calidad:Number($('calidad-lector').value),
      precios_materiales:Object.fromEntries(estado.materiales.map(m=>[m.id,m.precio])),
      modo:mercado.modo,ciudad_materiales:mercado.ciudad,ciudad_objeto:$('ciudad-objeto-lector').value,tipo:$('tipo-lector').value}};
  const huella = JSON.stringify(lote);
  // Reutilizar la clave solo al reintentar el mismo guardado fallido evita duplicados.
  if (!estado.guardadoIntento || estado.guardadoIntento.huella !== huella) {
    estado.guardadoIntento = {huella, id: crypto.randomUUID()};
  }
  lote.solicitud_id = estado.guardadoIntento.id;
  bloquearPlan(true);
  $("guardar-lote").textContent = "Guardando…";
  try {
    const respuesta = await fetch("/api/plan", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(lote)});
    const datos = await respuesta.json();
    if (!respuesta.ok) throw new Error(datos.error || "No se pudo guardar el lote");
    pintarPlan(datos); estado.guardadoIntento = null;
    mensajeGuardado(`Guardado: ${cantidadPlan(cantidad * lote.unidades_por_crafteo)} × ${estado.porId.get(lote.item_id)?.es || lote.item_id}. Consulta el acumulado al final de la página.`);
  } catch (error) {
    mensajeGuardado(`No se pudo confirmar el guardado: ${error.message}. Puedes reintentar.`, true);
  } finally {
    $("guardar-lote").textContent = "＋ Guardar crafteo";
    bloquearPlan(false);
  }
}

async function quitarLote(id) {
  if (estado.planOcupado) return;
  bloquearPlan(true);
  try {
    const respuesta = await fetch(`/api/plan?lote_id=${encodeURIComponent(id)}`, {method: "DELETE"});
    const datos = await respuesta.json();
    if (!respuesta.ok) throw new Error(datos.error || "No se pudo quitar el lote");
    pintarPlan(datos);
    if(datos.eliminado){estado.loteEliminado=datos.eliminado;$('deshacer-lote').hidden=false;}
    mensajeGuardado("Lote eliminado. Puedes deshacer el último borrado desde el acumulado.");
  } catch (error) {
    $("estado-plan").textContent = error.message; $("estado-plan").classList.add("error");
  } finally { bloquearPlan(false); }
}

async function borrarTodosLotes() {
  if (estado.planOcupado || !estado.ultimoPlan?.lotes.length) return;
  if (!window.confirm("¿Borrar todos los lotes del plan, incluso los ocultos por filtros?\n\nLos precios guardados y las compras marcadas se conservan. Este borrado no se puede deshacer desde el plan.")) return;
  bloquearPlan(true);
  const boton = $('borrar-todos-lotes');
  boton.textContent = "Borrando lotes…";
  try {
    const respuesta = await fetch("/api/plan/lotes", {method: "DELETE"});
    const datos = await respuesta.json();
    if (!respuesta.ok) throw new Error(datos.error || "No se pudieron borrar los lotes");
    estado.loteEliminado = null;
    estado.guardadoIntento = null;
    $('deshacer-lote').hidden = true;
    pintarPlan(datos);
    const cantidad = datos.lotes_borrados;
    const mensaje = cantidad === 1 ? "Se borró 1 lote." : cantidad > 1
      ? `Se borraron ${cantidadPlan(cantidad)} lotes.` : "No había lotes para borrar.";
    $('estado-plan').textContent = mensaje;
    mensajeGuardado(mensaje);
  } catch (error) {
    $('estado-plan').textContent = `No se pudo confirmar el borrado: ${error.message}. Actualiza la lista para comprobarlo.`;
    $('estado-plan').classList.add("error");
  } finally {
    boton.textContent = "Borrar todos los lotes";
    bloquearPlan(false);
  }
}

function iniciarPlan() {
  $("guardar-lote").addEventListener("click", guardarLoteActual);
  $("recargar-plan").addEventListener("click", cargarPlan);
  $('borrar-todos-lotes').addEventListener('click', borrarTodosLotes);
  $('completar-plan').addEventListener('click',completarDatosPlan);
  $('desmarcar-compras').addEventListener('click',reiniciarCompras);
  $('buscar-materiales-plan').addEventListener('input',filtrarComprasPlan);
  const filtroCompras=localStorage.getItem('benjayaye-filtro-compras');
  if(['todos','pendientes','completados'].includes(filtroCompras))$('filtro-compras-plan').value=filtroCompras;
  $('filtro-compras-plan').addEventListener('change',()=>{
    localStorage.setItem('benjayaye-filtro-compras',$('filtro-compras-plan').value);filtrarComprasPlan();
  });
  estado.limiteLotes=50;
  for(const id of ['filtrar-lotes','estado-lotes','orden-lotes'])$(id).addEventListener(id==='filtrar-lotes'?'input':'change',()=>{
    estado.limiteLotes=50;if(estado.ultimoPlan)pintarLotesPlan(estado.ultimoPlan);
  });
  $('ver-mas-lotes').addEventListener('click',()=>{estado.limiteLotes+=50;if(estado.ultimoPlan)pintarLotesPlan(estado.ultimoPlan);});
  $('deshacer-lote').addEventListener('click',deshacerLote);
  $('archivo-respaldo').addEventListener('change',()=>{
    $('restaurar-respaldo').disabled=estado.planOcupado||!$('archivo-respaldo').files?.length;
    $('estado-respaldo').textContent='El respaldo añadirá solo los lotes y precios que no existen en este equipo.';
  });
  $('restaurar-respaldo').addEventListener('click',restaurarRespaldo);
  $('editor-lote-form').addEventListener('submit',guardarEdicionLote);
  $('editor-lote-cancelar').addEventListener('click',cerrarEditorLote);
  $('editor-lote-actualizar').addEventListener('click',cargarPreciosEditorLote);
  $('editor-lote-calidad').addEventListener('change',cargarPreciosEditorLote);
  $('editar-lote-dialog').addEventListener('close',()=>{editorLote.controlador?.abort();editorLote.token++;});
  cargarPlan();
}

function notaCompraMaterial(material) {
  if(material.sobran>0)return `${cantidadPlan(material.sobran)} de sobra.`;
  if(material.a_comprar<=0)return 'No necesitas comprar este material.';
  return material.comprado ? 'Compra completa.' : `Faltan ${cantidadPlan(material.faltan??material.a_comprar)} por comprar.`;
}

function actualizarEstadoCompras(plan) {
  const necesarios=plan.materiales.filter(m=>m.a_comprar>0);
  const parciales=necesarios.filter(m=>!m.comprado&&m.cantidad_comprada>0).length;
  $('estado-compras-plan').textContent=`${necesarios.filter(m=>m.comprado).length} de ${necesarios.length} materiales completados.`+
    (parciales?` ${parciales} con compra parcial.`:'');
  $('desmarcar-compras').disabled=estado.planOcupado||!plan.materiales.some(m=>m.cantidad_marcada!=null);
  $('avance-compras-plan').max=Math.max(1,necesarios.length);
  $('avance-compras-plan').value=necesarios.filter(m=>m.comprado).length;
  filtrarComprasPlan();
}

function filtrarComprasPlan() {
  const filtro=$('filtro-compras-plan').value;let visibles=0;
  const palabras=sinAcentos($('buscar-materiales-plan').value).trim().split(/\s+/).filter(Boolean);
  $('exportar-compras-plan').href='/api/plan/compras.csv?estado='+encodeURIComponent(filtro)+'&buscar='+encodeURIComponent($('buscar-materiales-plan').value);
  for(const fila of $('materiales-plan').querySelectorAll('tr')){
    const material=compraActual(fila.dataset.materialId),completo=material?.comprado||material?.a_comprar===0;
    const tier=/^T(\d)/.exec(material?.id||'')?.[1],encanto=/@(\d)$/.exec(material?.id||'')?.[1]||'0';
    const texto=sinAcentos(`${material?.nombre||''} ${material?.id||''} ${tier?'T'+tier+'.'+encanto:''}`);
    fila.hidden=(filtro==='pendientes'&&completo)||(filtro==='completados'&&!completo)||!palabras.every(p=>texto.includes(p));
    if(!fila.hidden)visibles++;
  }
  $('compras-sin-resultados').hidden=visibles>0||!estado.ultimoPlan?.materiales.length;
}

function compraActual(id) {return estado.ultimoPlan?.materiales.find(m=>m.id===id);}

function refrescarCompra(material,valorEnviado) {
  const fila=document.querySelector(`#materiales-plan tr[data-material-id="${material.id}"]`);if(!fila)return;
  fila.dataset.comprado=String(material.comprado===true);
  fila.querySelector('.total-acumulado').textContent=cantidadPlan(material.a_comprar);
  const entrada=fila.querySelector('.cantidad-comprada-material');
  // Conserva el campo y el foco: una respuesta no debe borrar lo que sigues escribiendo.
  if(!comprasEnCola.has(material.id)&&(document.activeElement!==entrada||entrada.value===String(valorEnviado))){
    entrada.value=String(material.cantidad_comprada||0);entrada.removeAttribute('aria-invalid');
  }
  const check=fila.querySelector('.check-compra-material');check.checked=material.comprado===true;
  check.dataset.sinCompra=String(material.a_comprar<=0);
  check.setAttribute('aria-label',`Comprado: ${cantidadPlan(material.a_comprar)} ${material.nombre} · ${material.id}`);
  fila.querySelector('.nota-compra-material').textContent=notaCompraMaterial(material);
}

function guardarCompraMaterial(material,check) {
  material=compraActual(material.id);if(!material)return;
  if(estado.planOcupado){check.checked=material.comprado===true;return;}
  encolarCompra(material,check.checked?material.a_comprar:0);
}

function guardarNumeroCompra(id,entrada) {
  const texto=entrada.value.trim(),cantidad=Number(texto),material=compraActual(id);if(!material)return;
  if(!/^\d+$/.test(texto)||!Number.isSafeInteger(cantidad)||cantidad<0){
    entrada.setAttribute('aria-invalid','true');
    $('estado-compras-plan').textContent='Escribe una cantidad entera, mayor o igual a 0. La compra todavía no se ha guardado.';return;
  }
  entrada.removeAttribute('aria-invalid');
  if(estado.planOcupado&&!guardandoCompras){entrada.value=String(material.cantidad_comprada||0);return;}
  encolarCompra(material,cantidad);
}

function encolarCompra(material,cantidad) {
  if(!guardandoCompras&&cantidad===(material.cantidad_comprada||0)){actualizarEstadoCompras(estado.ultimoPlan);return;}
  comprasEnCola.set(material.id,{id:material.id,cantidad:material.a_comprar,cantidad_comprada:cantidad});
  procesarCompras();
}

async function procesarCompras() {
  if(guardandoCompras)return;guardandoCompras=true;bloquearPlan(true);const erroresGuardado=new Map();
  try {
    while(comprasEnCola.size){
      const [id,compra]=comprasEnCola.entries().next().value;comprasEnCola.delete(id);
      if(compraActual(id)?.cantidad_comprada===compra.cantidad_comprada)continue;
      $('estado-compras-plan').textContent='Guardando la cantidad comprada…';
      const controlador=new AbortController();const limite=setTimeout(()=>controlador.abort(),10000);
      try {
        const r=await fetch('/api/plan/compras',{method:'PATCH',headers:{'Content-Type':'application/json'},signal:controlador.signal,
          body:JSON.stringify({item_id:id,cantidad:compra.cantidad,cantidad_comprada:compra.cantidad_comprada,respuesta:'material'})});
        const datos=await r.json();if(!r.ok)throw new Error(datos.error||'No se pudo guardar la compra');
        // La compra no cambia las cuentas ni los lotes: actualiza solo esta fila.
        const material=datos.material||datos.materiales?.find(m=>m.id===id),actual=compraActual(id);
        if(!material||material.id!==id||material.cantidad_comprada!==compra.cantidad_comprada)
          throw new Error('El servidor no confirmó la cantidad enviada');
        if(material&&actual){Object.assign(actual,material);refrescarCompra(actual,compra.cantidad_comprada);}
        erroresGuardado.delete(id);
      } catch(error) {
        const actual=compraActual(id);if(actual)refrescarCompra(actual,compra.cantidad_comprada);
        erroresGuardado.set(id,'No se guardó la compra: '+(error.name==='AbortError'?'el servidor no respondió a tiempo':error.message)+'. Pulsa Actualizar lista para comprobarla.');
      } finally {clearTimeout(limite);}
    }
  } finally {
    guardandoCompras=false;bloquearPlan(false);actualizarEstadoCompras(estado.ultimoPlan);
    if(erroresGuardado.size)$('estado-compras-plan').textContent=erroresGuardado.values().next().value;
  }
}

async function reiniciarCompras() {
  if(estado.planOcupado)return;bloquearPlan(true);
  try {
    const r=await fetch('/api/plan/compras',{method:'DELETE'});const datos=await r.json();
    if(!r.ok)throw new Error(datos.error||'No se pudieron desmarcar las compras');
    pintarPlan(datos);
  } catch(error) {$('estado-compras-plan').textContent=error.message;}
  finally {bloquearPlan(false);}
}

async function completarDatosPlan() {
  if (estado.planOcupado || !estado.ultimoPlan?.lotes.length) return;
  bloquearPlan(true);$('completar-plan').textContent='Completando…';
  const contexto={modo:mercado.modo,ciudad_materiales:mercado.ciudad,ciudad_objeto:$('ciudad-objeto-lector').value,
    tipo:$('tipo-lector').value,calidad:Number($('calidad-lector').value),
    tasa_estacion:leerNumero('tasa-estacion'),comision:leerNumero('comision')};
  try {
    const respuesta=await fetch('/api/plan/completar',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(contexto)});
    const datos=await respuesta.json();if(!respuesta.ok)throw new Error(datos.error || 'No se pudieron completar los datos');
    pintarPlan(datos);
    mensajeGuardado(datos.finanzas.lotes_pendientes ? 'Datos completados donde había precios disponibles. Revisa los campos pendientes en cada lote.' : 'Todos los lotes tienen sus datos: gasto, ventas y ganancia total actualizados.');
  } catch(error) {$('estado-plan').textContent=error.message;$('estado-plan').classList.add('error');}
  finally {$('completar-plan').textContent='Completar precios faltantes';bloquearPlan(false);}
}

const editorLote = {lote:null,precios:new Map(),token:0,controlador:null,contexto:{}};
function cerrarEditorLote() {
  editorLote.controlador?.abort();editorLote.token++;
  const d=$('editar-lote-dialog');if(typeof d.close==='function')d.close();else d.removeAttribute('open');
}
function abrirEditorLote(lote) {
  if(estado.planOcupado)return;
  editorLote.controlador?.abort();editorLote.token++;editorLote.lote=lote;editorLote.precios.clear();
  const e=lote.economia;
  editorLote.contexto=e ? {...e} : {modo:mercado.modo,ciudad_materiales:mercado.ciudad,ciudad_objeto:$('ciudad-objeto-lector').value,tipo:$('tipo-lector').value};
  $('editor-lote-cantidad').value=String(lote.crafteos);
  $('editor-lote-retorno').value=String(lote.retorno);
  $('editor-lote-produccion').textContent=lote.unidades_por_crafteo>1 ? `${lote.unidades_por_crafteo} ítems por crafteo. La cantidad indica crafteos.` : 'La cantidad indica ítems a fabricar.';
  $('editor-lote-objeto').textContent=`${lote.nombre} · ${cantidadPlan(lote.cantidad)} ítems · devolución ${cantidadPlan(lote.retorno)} %`;
  for(const [campo,clave,base] of [['venta','precio_venta',0],['tasa','tasa_estacion',leerNumero('tasa-estacion')],
      ['valor','valor_item',lote.resumen_economico?.valor_item || 0],['comision','comision',leerNumero('comision')],['otros','otros_costos',0],['calidad','calidad',Number($('calidad-lector').value)]]) {
    $('editor-lote-'+campo).value=String(campo==='valor' ? e?.valor_item > 0 ? e.valor_item : base : e?.[clave] ?? base);
  }
  const zona=$('editor-lote-materiales');zona.replaceChildren();
  for(const m of lote.materiales) {
    const label=nodoPlan('label',m.nombre);const input=document.createElement('input');input.type='number';input.min='0';input.step='0.01';input.required=true;
    input.value=String(e?.precios_materiales?.[m.id] ?? estado.precios.get(m.id) ?? 0);
    input.setAttribute('aria-label','Precio de '+m.nombre);label.append(input);zona.append(label);editorLote.precios.set(m.id,input);
  }
  $('editor-lote-estado').textContent=e ? 'Se muestran los precios y costes que conserva este lote.' : 'El lote conserva sus materiales. Completa sus precios y el cobro de estación antes de evaluar la ganancia.';
  $('editor-lote-guardar').disabled=false;
  $('editor-lote-actualizar').disabled=false;$('editor-lote-venta').disabled=false;
  const d=$('editar-lote-dialog');if(typeof d.showModal==='function')d.showModal();else d.setAttribute('open','');
  if(!e)cargarPreciosEditorLote();
}
async function cargarPreciosEditorLote() {
  const lote=editorLote.lote;if(!lote)return;
  editorLote.controlador?.abort();const controlador=new AbortController();editorLote.controlador=controlador;
  const token=++editorLote.token;
  const ids=[...new Set([lote.item_id,...lote.materiales.map(m=>m.id)])];
  const contexto={modo:mercado.modo,ciudad_materiales:mercado.ciudad,ciudad_objeto:$('ciudad-objeto-lector').value,tipo:$('tipo-lector').value};
  const calidad=Number($('editor-lote-calidad').value);
  $('editor-lote-actualizar').disabled=true;$('editor-lote-guardar').disabled=true;
  const entradas=[$('editor-lote-venta'),...editorLote.precios.values()];entradas.forEach(e=>{e.disabled=true;});
  $('editor-lote-estado').textContent='Consultando los precios de este lote…';
  try {
    const parametros=new URLSearchParams({ids:ids.join(','),objeto:lote.item_id,ciudad:contexto.ciudad_materiales,ciudad_objeto:contexto.ciudad_objeto,calidad:String(calidad),tipo:contexto.tipo});
    const leidos=contexto.modo==='manual' ? null : await pedirJSON('/api/lector/precios?'+parametros,controlador.signal);
    if(token!==editorLote.token)return;
    const lecturas=new Map((leidos?.precios||[]).map(p=>[p.id,p.precio]));
    const precio=id=>contexto.modo!=='manual'&&lecturas.get(id)>0 ? lecturas.get(id) : contexto.modo==='lector' ? 0 : estado.precios.get(id)||0;
    $('editor-lote-venta').value=String(precio(lote.item_id));
    for(const [id,input] of editorLote.precios)input.value=String(precio(id));
    editorLote.contexto=contexto;
    $('editor-lote-estado').textContent=`Precios para ${nombreCiudad(contexto.ciudad_objeto)} · ${nombresCalidad[calidad]}. Los materiales usan ${nombreCiudad(contexto.ciudad_materiales)}. Revisa los ceros y el valor del objeto antes de guardar.`;
  } catch(error) {
    if(error.name!=='AbortError'&&token===editorLote.token)$('editor-lote-estado').textContent='No se pudieron cargar los precios: '+error.message+'. Puedes escribirlos manualmente.';
  } finally {
    if(token===editorLote.token){$('editor-lote-actualizar').disabled=false;$('editor-lote-guardar').disabled=false;entradas.forEach(e=>{e.disabled=false;});}
  }
}
async function guardarEdicionLote(evento) {
  evento.preventDefault();if(!editorLote.lote||estado.planOcupado)return;
  const economia={...editorLote.contexto,precios_materiales:Object.fromEntries([...editorLote.precios].map(([id,input])=>[id,Number(input.value)]))};
  for(const [campo,clave] of [['venta','precio_venta'],['tasa','tasa_estacion'],['valor','valor_item'],['comision','comision'],['otros','otros_costos'],['calidad','calidad']])economia[clave]=Number($('editor-lote-'+campo).value);
  bloquearPlan(true);$('editor-lote-guardar').disabled=true;
  try {
    const respuesta=await fetch('/api/plan',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({lote_id:editorLote.lote.id,revision:editorLote.lote.revision,economia,cantidad:Number($('editor-lote-cantidad').value),retorno:Number($('editor-lote-retorno').value)})});
    const datos=await respuesta.json();if(!respuesta.ok)throw new Error(datos.error||'No se pudieron guardar los precios');
    pintarPlan(datos);cerrarEditorLote();mensajeGuardado('Lote actualizado: cantidades, materiales a comprar, gastos y ganancia recalculados.');
  } catch(error){$('editor-lote-estado').textContent=error.message;}
  finally {bloquearPlan(false);$('editor-lote-guardar').disabled=false;}
}

async function enviarRestauracion(copia) {
  const r=await fetch('/api/respaldo/importar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(copia)});
  const datos=await r.json();if(!r.ok)throw new Error(datos.error||'No se pudieron recuperar los datos');
  pintarPlan(datos);return datos;
}
async function deshacerLote() {
  if(estado.planOcupado||!estado.loteEliminado)return;
  bloquearPlan(true);
  try {
    await enviarRestauracion({formato:'benjayaye-craft-calculadora',version:1,precios:{},lotes:[estado.loteEliminado]});
    estado.loteEliminado=null;$('deshacer-lote').hidden=true;mensajeGuardado('Lote recuperado con sus cantidades, precios y fecha.');
  } catch(error){$('estado-plan').textContent=error.message;$('estado-plan').classList.add('error');}
  finally{bloquearPlan(false);}
}
async function restaurarRespaldo() {
  if(estado.planOcupado)return;
  const archivo=$('archivo-respaldo').files?.[0];if(!archivo)return;
  if(archivo.size>16*1024*1024){$('estado-respaldo').textContent='El respaldo debe ocupar menos de 16 MB.';return;}
  bloquearPlan(true);$('estado-respaldo').textContent='Comprobando y combinando el respaldo…';
  try {
    const copia=JSON.parse(await archivo.text());
    const datos=await enviarRestauracion(copia);
    $('estado-respaldo').textContent=`Recuperados ${datos.importados.lotes} lotes y ${datos.importados.precios} precios. Los datos que ya existían se conservaron.`;
    mercado.manualesActualizados=0;await actualizarMercado({forzar:true});
  } catch(error){$('estado-respaldo').textContent='No se pudo restaurar: '+error.message;}
  finally{bloquearPlan(false);}
}
