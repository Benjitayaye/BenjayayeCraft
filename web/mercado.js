"use strict";

const mercado = {modo: 'ambos', proximaConsulta: 0, cargando: false, automatico: true, ciudad: 'Martlock', precios: new Map(),
  ventasManuales: new Map(), token: 0, controlador: null, temporizador: null,
  eventos: null, conectado: false, cambioPendiente: false, firmaFormulario: null, manualesActualizados: 0};
const nombresCalidad = ['Sin reconocer', 'Normal', 'Bueno', 'Notable', 'Sobresaliente', 'Obra maestra'];
const nombreCiudad = ciudad => ciudad === 'Black Market' ? 'Mercado Negro (Caerleon)' : ciudad;
const idsMercado = () => [...new Set([estado.id, ...estado.materiales.map(m => m.id)].filter(Boolean))];
const camposPrecioApi = ['sell_price_min', 'sell_price_max', 'buy_price_min', 'buy_price_max'];

function datoPrecio(id) {
  const lectura = mercado.precios.get(id);
  if (mercado.modo !== 'manual' && lectura?.precio > 0) return lectura;
  if (mercado.modo === 'ambos' && lectura?.publico?.vigente && lectura.publico.precio > 0) {
    return {...lectura.publico, api: true};
  }
  if (mercado.modo !== 'lector') {
    const precio = id === estado.id && mercado.ventasManuales.has(id)
      ? mercado.ventasManuales.get(id) : estado.precios.get(id);
    if (precio > 0) return {id, precio, fuente: 'Guardado manualmente', manual: true};
  }
  return {id, precio: 0, fuente: 'Sin precio'};
}
function precioMaterial(id) { return datoPrecio(id).precio || 0; }
function actualizarAvisoPrecioVenta() {
  const aviso = $('estado-precio-venta');
  if (!aviso) return;
  const hayPrecio = Number($('precio-venta').value) > 0;
  aviso.hidden = !estado.id || hayPrecio;
  if (!aviso.hidden) {
    aviso.textContent = 'No existe precio registrado. Se coloca 0 automáticamente; completa el precio para evaluar la ganancia.';
  }
}
function precioEsSoloLectura(id) {
  const dato = datoPrecio(id);
  return mercado.modo === 'lector' || (mercado.modo === 'ambos' && dato.precio > 0 && !dato.manual);
}
function hayEdicionPrecio() {
  const activo = document.activeElement;
  return (activo?.tagName === 'INPUT' && activo.type === 'number') || estado.guardadosPendientes?.size > 0;
}
function aplicarPreciosMercado(forzar = true, demora = 130) {
  const firma = JSON.stringify([mercado.modo, estado.id, idsMercado().map(id => [id, precioMaterial(id), precioEsSoloLectura(id)])]);
  if (!forzar && firma === mercado.firmaFormulario) return;
  mercado.firmaFormulario = firma;
  for (const material of estado.materiales) material.precio = precioMaterial(material.id);
  $('precio-venta').readOnly = precioEsSoloLectura(estado.id);
  $('precio-venta').value = String(precioMaterial(estado.id));
  actualizarAvisoPrecioVenta();
  pintarMateriales();
  programarCalculo(demora);
}
function invalidarPreciosMercado() {
  if(typeof limpiarHistorialMercado==='function')limpiarHistorialMercado();
  ++mercado.token;
  mercado.controlador?.abort();
  mercado.cargando = false;
  mercado.proximaConsulta = 0;
  mercado.precios.clear();
  $('precios-mercado').replaceChildren();
  $('actualizar-mercado').disabled = false;
  $('precio-venta').value = String(precioMaterial(estado.id));
  actualizarAvisoPrecioVenta();
  $('estado-mercado').textContent = 'Preparando los precios del objeto y sus materiales…';
}
function pintarPreciosMercado(ids) {
  const cuerpo = $('precios-mercado');
  cuerpo.replaceChildren();
  for (const id of ids) {
    const dato = datoPrecio(id), lectura = mercado.precios.get(id);
    const fila = document.createElement('tr');
    const nombre = document.createElement('td');
    nombre.textContent = `${estado.porId.get(id)?.es || id} · ${id}`;
    const precio = document.createElement('td');
    precio.textContent = dato.precio > 0 ? dinero(dato.precio) : 'No existe precio';
    if (dato.precio <= 0) precio.className = 'precio-ausente';
    const detalle = document.createElement('td');
    if (dato.manual) detalle.textContent = 'Guardado manualmente · precio personal sin ciudad';
    else if (dato.precio > 0) {
      detalle.textContent = `${dato.api ? 'API · Albion Online Data Project' : 'Lector'} · ${nombreCiudad(dato.ciudad)} · ${nombresCalidad[dato.calidad]} · ${dato.tipo === 'compra' ? 'orden de compra' : 'oferta de venta'}`;
      if (dato.fecha) {
        detalle.textContent += ' · ' + new Date(dato.fecha).toLocaleString('es-CL');
        if (Date.now() - Date.parse(dato.fecha) > 86400000) {
          detalle.textContent += ' · Más de 24 h'; detalle.className = 'precio-antiguo';
        }
      }
    } else if (mercado.modo === 'ambos' && lectura?.publico && !lectura.publico.vigente) {
      detalle.textContent = 'La API tiene un precio de hace más de 24 h. Puedes verlo en las cotizaciones sincronizadas.';
    } else if (mercado.modo !== 'manual' && lectura?.disponibles?.length) {
      const lado = lectura.tipo === 'compra' ? 'compra' : 'venta';
      const exacta = lectura.disponibles.find(p => p.ciudad === lectura.ciudad && p.calidad === lectura.calidad);
      if (exacta && exacta[lado] == null) {
        detalle.textContent = `No existe precio de ${lado} registrado para ${nombreCiudad(lectura.ciudad)} · ${nombresCalidad[lectura.calidad]}. La calculadora coloca 0.`;
      } else {
        detalle.textContent = 'Hay lecturas, pero no coinciden con lo seleccionado: ' + lectura.disponibles.map(p =>
          `${nombreCiudad(p.ciudad)}, ${nombresCalidad[p.calidad]}, ${p.venta ? 'venta ' + dinero(p.venta) : 'sin venta'} / ${p.compra ? 'compra ' + dinero(p.compra) : 'sin compra'}`).join('; ')
          + '. Se coloca 0 hasta registrar el precio correspondiente.';
      }
    } else detalle.textContent = mercado.modo === 'manual'
      ? 'No existe precio guardado. El campo de la calculadora queda en 0; guarda un precio en tu base.'
      : 'No existe precio para esta ciudad y calidad. Se coloca 0 hasta que haya un dato que coincida.';
    fila.append(nombre, precio, detalle); cuerpo.append(fila);
  }
}
function mostrarConexionLector(datos) {
  const panel = $('conexion-lector');
  panel.hidden = mercado.modo === 'manual';
  if (panel.hidden) return;
  const lectura = datos?.estado_lector || datos?.ultima_guardada;
  if (!lectura) {
    panel.textContent = mercado.modo === 'ambos'
      ? 'Todavía no hay lecturas de pantalla. En Ambos puedes usar las cotizaciones sincronizadas desde el Analizador.'
      : 'Todavía no hay lecturas de pantalla. Confirma una lectura o inicia el guardado automático.';
    return;
  }
  const id = lectura.item_id || lectura.base_id;
  const nombre = estado.porId.get(id)?.es || id || 'objeto sin reconocer';
  panel.textContent = `${lectura.estado === 'pendiente' ? 'Lectura recibida, pendiente de confirmar' : 'Última lectura guardada'}: ${nombre} · ${id || '?'} · ${nombreCiudad(lectura.ciudad)} · ${nombresCalidad[lectura.calidad] || 'calidad sin reconocer'}`;
  if (lectura.fecha) panel.textContent += ' · ' + new Date(lectura.fecha).toLocaleString('es-CL');
  if (lectura.estado === 'pendiente') panel.textContent += '. El guardado automático espera dos capturas fiables iguales. Si falta un campo, revisa su zona en el lector.';
  else panel.textContent += '. Se aplica al seleccionar el mismo objeto, tier, encantamiento, mercado y calidad.';
}
async function pedirJSON(url, signal) {
  const respuesta = await fetch(url, {signal, cache: 'no-store'});
  const datos = await respuesta.json();
  if (!respuesta.ok) throw new Error(datos.error || 'No se pudieron consultar los precios');
  return datos;
}
function iniciarConsultaApi() {
  const seccion = $('detalle-api-publica'), formulario = $('formulario-api-publica');
  if (!seccion || !formulario) return;
  const entrada = $('objeto-api-publica'), cuerpo = $('filas-api-publica'), estadoApi = $('estado-api-publica');
  let consulta;
  const actualizar = async () => {
    const id = entrada.value.trim().toUpperCase();
    consulta?.abort();
    cuerpo.replaceChildren();
    if (!id) { estadoApi.textContent = 'Escribe el ID de un objeto. Puedes copiarlo del Analizador.'; return; }
    const actual = new AbortController(); consulta = actual;
    estadoApi.textContent = 'Buscando cotizaciones sincronizadas…';
    try {
      const datos = await pedirJSON('/api/mercado/publicos?' + new URLSearchParams({item_id:id}), actual.signal);
      if (consulta !== actual || actual.signal.aborted) return;
      for (const dato of datos.cotizaciones) {
        const fila = document.createElement('tr');
        const celdas = [nombreCiudad(dato.ciudad), nombresCalidad[dato.calidad],
          ...camposPrecioApi.map(k => {
            const p = dato[k];
            return p ? `${dinero(p.precio)} · ${new Date(p.fecha).toLocaleString('es-CL')}` : '—';
          })];
        for (const valor of celdas) {
          const td = document.createElement('td'); td.textContent = valor; fila.append(td);
        }
        cuerpo.append(fila);
      }
      estadoApi.textContent = datos.cotizaciones.length
        ? `${datos.cotizaciones.length} combinación(es) de ciudad y calidad para ${id} · ${datos.fuente}.`
        : `Todavía no hay cotizaciones sincronizadas para ${id}.`;
    } catch (error) {
      if (consulta === actual && error.name !== 'AbortError') estadoApi.textContent = error.message;
    }
  };
  formulario.addEventListener('submit', e => { e.preventDefault(); actualizar(); });
  seccion.addEventListener('toggle', () => {
    if (seccion.open) {
      if (!entrada.value) entrada.value = estado.id || '';
      actualizar();
    } else consulta?.abort();
  });
}
async function actualizarMercado(opciones = {}) {
  if (!estado.id || $('consultar-receta').disabled) return;
  if(typeof actualizarHistorialMercado==='function')actualizarHistorialMercado();
  mercado.controlador?.abort();
  const controlador = new AbortController(); mercado.controlador = controlador;
  const token = ++mercado.token, receta = estado.tokenReceta, modo = mercado.modo;
  mercado.cargando = true; mercado.cambioPendiente = false;
  mercado.proximaConsulta = Date.now() + (mercado.conectado ? 15000 : 2000);
  const ids = idsMercado();
  $('actualizar-mercado').disabled = true;
  try {
    const parametros = new URLSearchParams({ciudad: mercado.ciudad, ids: ids.join(','), objeto: estado.id,
      ciudad_objeto: $('ciudad-objeto-lector').value, calidad: $('calidad-lector').value, tipo: $('tipo-lector').value});
    if (modo === 'ambos') parametros.set('publicos', '1');
    const refrescarManuales = opciones?.forzar === true || !mercado.manualesActualizados || Date.now()-mercado.manualesActualizados>=2000;
    const resultados = await Promise.allSettled([
      refrescarManuales ? pedirJSON('/api/precios', controlador.signal) : Promise.resolve(null),
      modo !== 'manual' ? pedirJSON('/api/lector/precios?' + parametros, controlador.signal) : Promise.resolve(null)
    ]);
    if (token !== mercado.token || receta !== estado.tokenReceta) return;
    if (hayEdicionPrecio()) { mercado.cambioPendiente = true; return; }
    const [manuales, lector] = resultados;
    if (manuales.status === 'fulfilled' && manuales.value) {
      mercado.manualesActualizados = Date.now();
      const nuevos = new Map(Object.entries(manuales.value.precios));
      const cambiaron = nuevos.size !== estado.precios.size || [...nuevos].some(([id, precio]) => estado.precios.get(id) !== precio);
      if (cambiaron) { estado.precios = nuevos; estado.revisionPrecios++; pintarBasePrecios(); }
    }
    if (lector.status === 'fulfilled' && lector.value) {
      mercado.precios = new Map(lector.value.precios.map(p => [p.id, p]));
      mostrarConexionLector(lector.value);
    }
    aplicarPreciosMercado(false, 0); pintarPreciosMercado(ids);
    const fallos = resultados.filter(r => r.status === 'rejected' && r.reason?.name !== 'AbortError');
    const usados = ids.map(datoPrecio), faltan = usados.filter(p => !p.precio).length;
    const lectores = usados.filter(p => p.precio && !p.manual && !p.api).length;
    const publicos = usados.filter(p => p.api).length, manual = usados.filter(p => p.manual).length;
    $('estado-mercado').textContent = `${lectores} del lector · ${publicos} de la API · ${manual} manual(es). `
      + (faltan ? `${faltan} sin precio; se usa 0 y se indica en el detalle de abajo. ` : 'Precios aplicados a la calculadora. ')
      + (fallos.length ? 'Error al actualizar: ' + fallos.map(r => r.reason.message).join('; ') + '. Se reintentará. ' : '')
      + (modo !== 'manual' ? (mercado.conectado ? 'Conexión en vivo con los precios guardados.' : 'Actualización de respaldo cada 2 segundos.') : 'Puedes editar y guardar tus precios.');
  } catch (error) {
    if (error.name !== 'AbortError' && token === mercado.token) $('estado-mercado').textContent = error.message + '. Se reintentará la actualización.';
  } finally {
    if (token === mercado.token) { $('actualizar-mercado').disabled = false; mercado.cargando = false; }
  }
}
function guardarOpcionesMercado() {
  try {
    localStorage.setItem('benjayaye-ciudad', mercado.ciudad);
    localStorage.setItem('benjayaye-modo-precios', mercado.modo);
    for (const id of ['ciudad-objeto-lector','calidad-lector','tipo-lector']) localStorage.setItem('benjayaye-' + id, $(id).value);
  } catch (_) {}
}
function iniciarMercado() {
  try {
    const guardada = localStorage.getItem('benjayaye-ciudad');
    if ([...$('ciudad-mercado').options].some(o => o.value === guardada)) mercado.ciudad = guardada;
    const modo = localStorage.getItem('benjayaye-modo-precios');
    mercado.modo = ['manual','lector','ambos'].includes(modo) ? modo : 'ambos';
    for (const id of ['ciudad-objeto-lector','calidad-lector','tipo-lector']) {
      const valor = localStorage.getItem('benjayaye-' + id);
      if ([...$(id).options].some(o => o.value === valor)) $(id).value = valor;
    }
  } catch (_) {}
  mercado.automatico = mercado.modo !== 'manual';
  $('ciudad-mercado').value = mercado.ciudad; $('modo-mercado').value = mercado.modo;
  mostrarOpcionesLector(); guardarOpcionesMercado();
  const cambiar = () => {
    mercado.ciudad = $('ciudad-mercado').value; mercado.modo = $('modo-mercado').value;
    mercado.automatico = mercado.modo !== 'manual';
    mostrarOpcionesLector(); guardarOpcionesMercado(); invalidarPreciosMercado(); aplicarPreciosMercado(); actualizarMercado();
  };
  for (const id of ['ciudad-mercado','modo-mercado','ciudad-objeto-lector','calidad-lector','tipo-lector']) $(id).addEventListener('change', () => {
    if (id==='calidad-lector') actualizarImagenesObjeto();
    cambiar();
  });
  actualizarImagenesObjeto();
  iniciarConsultaApi();
  $('actualizar-mercado').addEventListener('click', () => actualizarMercado({forzar:true}));
  $('precio-venta').addEventListener('input', () => {
    if (!precioEsSoloLectura(estado.id) && estado.id) mercado.ventasManuales.set(estado.id, leerNumero('precio-venta'));
    actualizarAvisoPrecioVenta();
  });
  $('precio-venta').addEventListener('change', () => {
    if (!precioEsSoloLectura(estado.id) && estado.id) guardarPrecioEnBD(estado.id, $('precio-venta').value, false);
    actualizarAvisoPrecioVenta();
  });
  if (typeof EventSource !== 'undefined') {
    mercado.eventos = new EventSource('/api/lector/eventos');
    mercado.eventos.onopen = () => { mercado.conectado = true; mostrarCanalLocal(); };
    mercado.eventos.onerror = () => { mercado.conectado = false; mercado.proximaConsulta = 0; mostrarCanalLocal(); };
    mercado.eventos.addEventListener('precios', () => { mercado.cambioPendiente = true; mercado.manualesActualizados = 0; });
  }
  mercado.temporizador = setInterval(() => {
    if (!document.hidden && !mercado.cargando && !hayEdicionPrecio() && (mercado.cambioPendiente || Date.now() >= mercado.proximaConsulta)) actualizarMercado();
  }, 250);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !hayEdicionPrecio()) actualizarMercado();
  });
}
function mostrarCanalLocal() {
  const indicador = $('canal-local');
  if (!indicador) return;
  indicador.textContent = mercado.conectado ? 'Sincronización de precios activa' : 'Reconectando · consulta de respaldo';
  indicador.dataset.estado = mercado.conectado ? 'activo' : 'espera';
}
function mostrarOpcionesLector() {
  $('opciones-lector').hidden = mercado.modo === 'manual';
  $('conexion-lector').hidden = mercado.modo === 'manual';
  $('detalle-fuente-mercado').textContent = mercado.modo === 'lector'
    ? 'Solo lecturas de pantalla confirmadas para la ciudad y calidad elegidas'
    : mercado.modo === 'ambos' ? 'Primero el lector; después, la API sincronizada o tus precios manuales'
    : 'Tus precios guardados manualmente · sin distinguir ciudad';
}
