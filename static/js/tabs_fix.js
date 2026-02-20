/* Ubicación: static/js/tabs_fix.js */

(function($) {
    'use strict';
    
    $(document).ready(function() {
        console.log("✅ Tabs Fix V2: Cargado y blindado.");

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