/* Archivo: static/js/tabs_fix.js */

(function($) {
    'use strict';
    
    $(document).ready(function() {
        console.log("🚀 Tabs Fix: Iniciado globalmente.");

        // 1. Definir clave única basada en la URL (para que Usuario no choque con Cotización)
        var storageKey = 'tab_preference_' + window.location.pathname;

        // 2. RECUPERAR: Al cargar la página, ver si hay memoria
        var activeTab = localStorage.getItem(storageKey);
        
        if (activeTab) {
            // Buscamos el enlace que tenga ese href
            var $tabLink = $('.nav-tabs a[href="' + activeTab + '"]');
            
            if ($tabLink.length > 0) {
                console.log("Restaurando pestaña:", activeTab);
                
                // Opción A: Trigger nativo de Bootstrap (La forma elegante)
                $tabLink.tab('show'); 
                
                // Opción B: Forzado bruto (si Jazzmin se pone rebelde)
                // Esto quita la clase active de todos y se la pone al correcto
                $('.nav-tabs a').removeClass('active');
                $('.tab-pane').removeClass('active show');
                
                $tabLink.addClass('active');
                $(activeTab).addClass('active show');
            }
        }

        // 3. GUARDAR: Escuchar el evento oficial de cambio de pestaña de Bootstrap
        // Jazzmin usa Bootstrap 4, así que el evento es 'shown.bs.tab'
        $('a[data-toggle="tab"], a[data-toggle="pill"]').on('shown.bs.tab', function (e) {
            var target = $(e.target).attr("href"); // La pestaña que se acaba de activar (ej: #general)
            if(target && target.startsWith('#')) {
                console.log("Guardando preferencia:", target);
                localStorage.setItem(storageKey, target);
            }
        });
        
        // Soporte extra para clicks directos si el evento de BS falla
        $('.nav-tabs a').on('click', function() {
            var href = $(this).attr('href');
            if(href && href.startsWith('#')) {
                 localStorage.setItem(storageKey, href);
            }
        });
    });

})(django.jQuery || jQuery); // Usamos el jQuery de Django o el global