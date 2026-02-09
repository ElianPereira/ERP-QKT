/* Ubicación: static/js/tabs_fix.js */

(function($) {
    'use strict';

    $(document).ready(function() {
        // Validación de seguridad por si jQuery no cargó
        if (typeof $ === 'undefined') {
            console.error("TabsFix: jQuery no está cargado.");
            return;
        }

        console.log("🚀 Tabs Fix: Iniciado correctamente en Jazzmin.");

        // 1. Crear una clave única para esta URL específica
        // Esto evita que la pestaña de 'Usuario Juan' afecte a 'Cotización #5'
        var storageKey = 'jazzmin_tab_pref_' + window.location.pathname;

        // 2. RECUPERAR: Restaurar pestaña al cargar la página
        var savedTab = localStorage.getItem(storageKey);

        if (savedTab) {
            // Buscamos el enlace de la pestaña (el <a> dentro de .nav-tabs)
            // Jazzmin a veces usa ID="#tab" y otras HREF="#tab"
            var $tabLink = $('.nav-tabs a[href="' + savedTab + '"]');

            // Si no lo encuentra por href, busca por ID (algunas versiones de Jazzmin hacen esto)
            if ($tabLink.length === 0 && savedTab.startsWith('#')) {
                var idSinHash = savedTab.substring(1); // quitar el #
                $tabLink = $('.nav-tabs a[id="' + idSinHash + '"]');
            }

            // Si encontramos la pestaña, la activamos
            if ($tabLink.length > 0) {
                console.log("Restaurando pestaña:", savedTab);
                $tabLink.tab('show'); // Función nativa de Bootstrap
            }
        }

        // 3. GUARDAR: Escuchar el evento de cambio de pestaña
        // 'shown.bs.tab' es el evento estándar de Bootstrap 4
        $(document).on('shown.bs.tab', 'a[data-toggle="tab"]', function (e) {
            var $target = $(e.target); // La pestaña que se acaba de activar
            var href = $target.attr('href');
            var id = $target.attr('id');

            // Preferimos guardar el HREF (ej: #general), si no hay, el ID
            var valToSave = href && href.startsWith('#') ? href : ('#' + id);

            if (valToSave) {
                console.log("Guardando pestaña:", valToSave);
                localStorage.setItem(storageKey, valToSave);
            }
        });
    });

})(window.jQuery || django.jQuery); // Usar jQuery global o el de Django