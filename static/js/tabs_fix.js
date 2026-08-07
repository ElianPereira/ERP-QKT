/* Ubicación: static/js/tabs_fix.js */

(function($) {
    'use strict';
    
    $(document).ready(function() {
        console.log(" Tabs Fix V2: Cargado y blindado.");

        var storageKey = 'tab_pref_' + window.location.pathname;

        // 1. Restaurar pestaña al recargar la página
        var savedTab = localStorage.getItem(storageKey);
        if (savedTab) {
            setTimeout(function() {
                var $link = $('.nav-tabs a[href="' + savedTab + '"]');
                if ($link.length > 0) {
                    $link.trigger('click');
                    $('.tab-pane').removeClass('active show');
                    $(savedTab).addClass('active show');
                    $link.closest('ul').find('a').removeClass('active');
                    $link.addClass('active');
                }
            }, 100);
        }

        // 2. Guardar preferencia al hacer clic y FORZAR cambio
        $(document).on('click', '.nav-tabs a', function(e) {
            e.preventDefault(); 
            var href = $(this).attr('href');
            
            if (href && href.startsWith('#')) {
                localStorage.setItem(storageKey, href);
                // Forzar visualmente el cambio al instante
                $('.nav-tabs a').removeClass('active');
                $('.tab-pane').removeClass('active show');
                $(this).addClass('active');
                $(href).addClass('active show');
            }
        });
    });

})(window.jQuery || django.jQuery || window.$ || {});


/* ============================================================
   FIX BFCACHE — Botones que dejan de funcionar al regresar
   con el botón "atrás" del navegador móvil.

   Causa: el browser restaura la página desde caché congelada
   (bfcache). Los enlaces quedan en estado muerto.
   Solución: detectar restauración desde bfcache con el evento
   'pageshow' y forzar recarga completa de la página.
   ============================================================ */
window.addEventListener('pageshow', function(event) {
    // event.persisted = true significa que viene del bfcache
    if (event.persisted) {
        window.location.reload();
    }
});


/* ============================================================
   FIX CALENDARIO/RELOJ DEL ADMIN — se abre descolocado o cortado

   Django (DateTimeShortcuts.js) coloca el calendarbox/clockbox con
   findPosX/findPosY, que suman offsetLeft/offsetTop recorriendo la
   cadena de padres. Ese cálculo:

     - ignora los `transform` CSS (AdminLTE/Jazzmin desplaza así el
       contenido al colapsar la barra lateral),
     - se descuadra cuando hay scroll horizontal o zoom del navegador,
     - y nunca comprueba si la caja cabe en la ventana.

   Resultado reportado en Contabilidad > Estados de cuenta bancarios
   ("Fecha de corte real"): el calendario aparece lejos del campo y
   cortado contra el borde, sin poder elegir la mayoría de los días
   ni alcanzar Hoy/Cancelar.

   En vez de corregir la aritmética de Django, se reposiciona la caja
   con getBoundingClientRect() del propio icono —que sí refleja la
   posición real en pantalla, con transform, scroll y zoom incluidos—
   y se pasa a `position: fixed` para anclarla a la ventana visible.
   No se toca DateTimeShortcuts.js.
   ============================================================ */
(function () {
    var MARGEN = 8;   // separación mínima respecto al borde de la ventana
    var HUECO = 6;    // separación entre el icono y la caja

    function idIcono(box) {
        var num = box.id.replace(/^\D+/, '');
        return (box.id.indexOf('calendarbox') === 0 ? 'calendarlink' : 'clocklink') + num;
    }

    // Ojo: NO usar offsetParent aquí. Una vez que la caja pasa a
    // position:fixed su offsetParent es null, así que ese chequeo la
    // daría por oculta para siempre y no se volvería a reposicionar.
    function visible(box) {
        return box && box.isConnected && getComputedStyle(box).display !== 'none';
    }

    function reposicionar(box) {
        var icono = document.getElementById(idIcono(box));
        if (!icono) return;

        box.style.position = 'fixed';

        // Si la caja no cabe a lo alto en la ventana, se limita su altura
        // y se le permite scroll interno (aportación del PR #151), para no
        // perder los últimos días ni el pie Hoy/Cancelar. Se resetea antes
        // de medir para que al agrandar la ventana recupere su tamaño.
        var disponible = window.innerHeight - MARGEN * 2;
        box.style.maxHeight = '';
        box.style.overflowY = '';
        if (box.offsetHeight > disponible) {
            box.style.maxHeight = disponible + 'px';
            box.style.overflowY = 'auto';
        }

        var r = icono.getBoundingClientRect();
        var ancho = box.offsetWidth;
        var alto = box.offsetHeight;
        var maxX = Math.max(MARGEN, window.innerWidth - ancho - MARGEN);
        var maxY = Math.max(MARGEN, window.innerHeight - alto - MARGEN);

        // Horizontal: a la derecha del icono; si no cabe, a su izquierda.
        var x = r.right + HUECO;
        if (x > maxX) {
            x = r.left - ancho - HUECO;
        }

        // Vertical: alineada con el icono; si se sale por abajo, se sube.
        var y = r.top - 4;

        box.style.left = Math.min(Math.max(MARGEN, x), maxX) + 'px';
        box.style.top = Math.min(Math.max(MARGEN, y), maxY) + 'px';
    }

    function reposicionarAbiertos() {
        var cajas = document.querySelectorAll('.calendarbox, .clockbox');
        for (var i = 0; i < cajas.length; i++) {
            if (visible(cajas[i])) {
                reposicionar(cajas[i]);
            }
        }
    }

    // DateTimeShortcuts.js hace e.stopPropagation() al abrir (para no
    // disparar su propio listener de "clic afuera cierra"), así que hay
    // que escuchar en fase de CAPTURA para llegar antes que ese corte.
    document.addEventListener('click', function (e) {
        var icono = e.target.closest && e.target.closest('a[id^="calendarlink"], a[id^="clocklink"]');
        if (!icono) return;
        // setTimeout(0): deja que Django cree/posicione la caja primero.
        setTimeout(function () {
            reposicionarAbiertos();
            // Pasar de absolute a fixed puede encoger el documento y
            // desplazar el scroll; se recalcula ya con el layout estable.
            requestAnimationFrame(reposicionarAbiertos);
        }, 0);
    }, true);

    // Al estar anclada a la ventana, hay que reseguir el campo cuando
    // la página se desplaza o cambia de tamaño.
    window.addEventListener('scroll', reposicionarAbiertos, true);
    window.addEventListener('resize', reposicionarAbiertos);
})();