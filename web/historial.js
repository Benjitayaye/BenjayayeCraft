"use strict";
const historialMercado={token:0,controlador:null};
const enteroHistorial=n=>n==null?'Sin reconocer':Number(n).toLocaleString('es-CL',{maximumFractionDigits:0});
function parametrosHistorial(){
  return new URLSearchParams({item_id:estado.id||'',ciudad:$('ciudad-objeto-lector').value,
    calidad:$('calidad-lector').value,periodo:$('historial-periodo').value});
}
function limpiarHistorialMercado(){
  ++historialMercado.token;historialMercado.controlador?.abort();
  $('historial-datos').hidden=true;$('historial-referencias').hidden=true;
  $('historial-filas').replaceChildren();$('historial-media').textContent='—';
  $('historial-estado').textContent='Consultando el historial de este objeto…';
  $('historial-contexto').textContent='';
  pintarPuntosHistorial(null);
}
function pintarPuntosHistorial(historia){
  const puntos=historia?.puntos||[];const filas=$('historial-puntos-filas');filas.replaceChildren();
  $('historial-puntos').hidden=!puntos.length;
  $('historial-puntos-resumen').textContent=`${puntos.length} punto(s) recorridos automáticamente`;
  for(const p of puntos){
    const fila=document.createElement('tr');
    for(const v of [p.posicion,enteroHistorial(p.precio),enteroHistorial(p.vendido)]){
      const td=document.createElement('td');td.textContent=String(v);fila.append(td);
    }
    filas.append(fila);
  }
}
function pintarFilasHistorial(datos){
  const zona=$('historial-filas');zona.replaceChildren();
  const referencias=datos.referencias||{};
  $('historial-referencias').hidden=!Object.keys(referencias).length;
  for(const lado of ['venta','compra']){
    if(!referencias[lado]?.length)continue;
    const titulo=document.createElement('p');titulo.textContent=lado==='venta'?'Ofertas de venta':'Órdenes de compra';zona.append(titulo);
    const tabla=document.createElement('table');const cabecera=document.createElement('tr');
    for(const t of ['Fila','Precio','Cant.','Uso']){const th=document.createElement('th');th.textContent=t;cabecera.append(th);}
    tabla.append(cabecera);
    zona.append(tabla);
    for(const f of referencias[lado]){
      const uso=f.uso || (f.fila===1?'precio':'referencia');
      const fila=document.createElement('tr');if(uso==='precio')fila.className='primera-fila';
      for(const v of [f.fila,enteroHistorial(f.precio),enteroHistorial(f.cantidad),uso==='revisar'?'Revisar zona':uso==='precio'?'Coincide con precio':'Referencia']){
        const td=document.createElement('td');td.textContent=String(v);fila.append(td);
      }
      tabla.append(fila);
    }
  }
  if(Object.keys(referencias).length){
    const nota=document.createElement('p');nota.className='historial-ayuda';
    nota.textContent='El precio procede del recorte de primera fila. Las referencias marcadas para revisar no lo sustituyen.';
    if(datos.referencia_fecha)nota.textContent+=' Leídas: '+new Date(datos.referencia_fecha).toLocaleString('es-CL');
    zona.append(nota);
  }
}
async function actualizarHistorialMercado(){
  if(!estado.id||!$('historial-periodo'))return;
  historialMercado.controlador?.abort();const controlador=new AbortController();historialMercado.controlador=controlador;
  const token=++historialMercado.token;const parametros=parametrosHistorial();const clave=parametros.toString();
  try{
    const respuesta=await fetch('/api/lector/historial?'+clave,{signal:controlador.signal,cache:'no-store'});
    const datos=await respuesta.json();if(!respuesta.ok)throw new Error(datos.error||'No se pudo leer el historial');
    if(token!==historialMercado.token||clave!==parametrosHistorial().toString())return;
    $('historial-contexto').textContent=`${nombreCiudad(datos.ciudad)} · ${nombresCalidad[datos.calidad]} · ${datos.item_id}`;
    pintarFilasHistorial(datos);
    const h=datos.historial;$('historial-datos').hidden=!h;pintarPuntosHistorial(h);
    if(!h){
      $('historial-estado').textContent='Aún no has escaneado este historial. En el lector, selecciona su zona y el mismo periodo.';
      const nombres={"24h":"24 horas","7d":"7 días","4w":"4 semanas"};
      if(datos.periodos_disponibles?.length)$('historial-estado').textContent+=' Hay capturas de '+datos.periodos_disponibles.map(p=>nombres[p]).join(', ')+'.';
      return;
    }
    $('historial-estado').textContent='Gráfico leído del juego · referencia histórica';
    $('historial-media').textContent=(h.aproximado?'≈ ':'')+enteroHistorial(h.precio_medio);
    const url='/api/lector/historial/imagen?'+clave+'&v='+encodeURIComponent(h.firma);
    if($('historial-grafico').getAttribute('src')!==url)$('historial-grafico').setAttribute('src',url);
    $('historial-ampliar').setAttribute('href',url);
    $('historial-punto').textContent=h.punto_precio!=null||h.punto_vendido!=null
      ?`Punto señalado: ${enteroHistorial(h.punto_precio)} de plata · ${enteroHistorial(h.punto_vendido)} vendido(s) en ese punto.`
      :'Pasa el ratón sobre un punto del gráfico en Albion y vuelve a leer para ver su precio y cantidad.';
    $('historial-fecha').textContent='Capturado: '+new Date(h.fecha).toLocaleString('es-CL');
  }catch(error){
    if(error.name!=='AbortError'&&token===historialMercado.token){$('historial-datos').hidden=true;$('historial-estado').textContent=error.message;}
  }
}
$('historial-periodo').addEventListener('change',()=>{limpiarHistorialMercado();actualizarHistorialMercado();});
