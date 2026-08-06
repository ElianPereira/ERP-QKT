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
   FIX CALENDARIO/RELOJ DEL ADMIN — se abre fuera de la pantalla

   Django siempre despliega el calendarbox/clockbox hacia abajo del
   campo sin comprobar si hay espacio. En formularios donde el campo
   de fecha/hora queda cerca del final de la página (ej. Estados de
   cuenta bancarios: "Fecha de corte real"), la caja se corta contra
   el borde de la ventana y no hay manera de alcanzar el resto de los
   días/horas ni los botones de abajo (Hoy/Cancelar).

   Tras el clic que la abre (DateTimeShortcuts.js de Django, sin
   tocar ese archivo) se reposiciona la caja hacia arriba lo justo
   para que quede completa dentro de la ventana visible.

   El propio DateTimeShortcuts.js hace e.stopPropagation() al abrir
   (para no disparar su listener de "clic afuera cierra"), así que
   este listener se registra en fase de CAPTURA (tercer argumento
   true) — se ejecuta antes de que Django corte la propagación.
   ============================================================ */
document.addEventListener('click', function (e) {
    var link = e.target.closest && e.target.closest('a[id^="calendarlink"], a[id^="clocklink"]');
    if (!link) return;
    setTimeout(function () {
        var num = link.id.replace(/^\D+/, '');
        var boxId = (link.id.indexOf('calendarlink') === 0 ? 'calendarbox' : 'clockbox') + num;
        var box = document.getElementById(boxId);
        if (!box) return;
        var viewportMargin = 10;
        var availableHeight = window.innerHeight - (viewportMargin * 2);
        if (box.getBoundingClientRect().height > availableHeight) {
            box.style.maxHeight = availableHeight + 'px';
            box.style.overflowY = 'auto';
        }
        var overflow = box.getBoundingClientRect().bottom - window.innerHeight;
        if (overflow > 0) {
            var actual = parseInt(box.style.top, 10) || 0;
            box.style.top = Math.max(viewportMargin, actual - overflow - viewportMargin) + 'px';
        }
    }, 0);
}, true);
