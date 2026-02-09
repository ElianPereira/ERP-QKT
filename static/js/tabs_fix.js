/* Ubicación: static/js/tabs_fix.js */

(function($) {
    'use strict';

    $(document).ready(function() {
        // Validación de seguridad
        if (typeof $ === 'undefined') {
            console.error("TabsFix: jQuery no está cargado.");
            return;
        }

        console.log("🚀 Tabs Fix: Modo ACTIVO iniciado.");

        // Clave única basada en la URL
        var storageKey = 'jazzmin_tab_pref_' + window.location.pathname;

        // ===============================================
        // A. FUNCIÓN PARA CAMBIAR PESTAÑA MANUALMENTE
        // ===============================================
        function activarPestana(linkElement) {
            var $link = $(linkElement);
            var targetSelector = $link.attr('href'); // Ej: #general
            
            // Si no es un selector válido, ignorar
            if (!targetSelector || !targetSelector.startsWith('#')) return;

            console.log("⚡ Forzando cambio a:", targetSelector);

            // 1. VISUAL: Pestañas (Nav)
            // Quitar 'active' de todas las pestañas hermanas
            $link.closest('ul').find('a').removeClass('active');
            // Poner 'active' a la actual
            $link.addClass('active');

            // 2. VISUAL: Contenido (Panes)
            // Jazzmin/Bootstrap usan .tab-pane. Ocultamos todos.
            $('.tab-pane').removeClass('active').removeClass('show');
            
            // Buscamos el contenido objetivo.
            // Jazzmin a veces usa ID="general" para el contenido
            var $targetContent = $(targetSelector);
            
            if ($targetContent.length > 0) {
                $targetContent.addClass('active').addClass('show');
            } else {
                // Intento alternativo por si el ID tiene sufijos raros
                console.warn("No se encontró el ID exacto, buscando aproximación...");
                // A veces href="#general" apunta a un div con id="general-tab" o viceversa
            }

            // 3. MEMORIA: Guardar en LocalStorage
            localStorage.setItem(storageKey, targetSelector);
        }

        // ===============================================
        // B. RESTAURAR AL CARGAR (F5)
        // ===============================================
        var savedTab = localStorage.getItem(storageKey);
        if (savedTab) {
            var $savedLink = $('.nav-tabs a[href="' + savedTab + '"]');
            
            // Si no encuentra por href, busca por ID (fix para Jazzmin raros)
            if ($savedLink.length === 0 && savedTab.startsWith('#')) {
                var idSinHash = savedTab.substring(1);
                $savedLink = $('.nav-tabs a[id="' + idSinHash + '"]');
            }

            if ($savedLink.length > 0) {
                console.log("Restaurando historial:", savedTab);
                // Usamos click() para disparar nuestra propia lógica de abajo
                // Pero usamos un timeout pequeño para asegurar que el DOM esté listo
                setTimeout(function(){ 
                    activarPestana($savedLink); 
                }, 100); 
            }
        }

        // ===============================================
        // C. INTERCEPTAR CLICS (La solución al problema)
        // ===============================================
        // Usamos 'document' con delegación para asegurar que funcione 
        // incluso si Jazzmin renderiza cosas tarde.
        $(document).on('click', '.nav-tabs a', function(e) {
            var href = $(this).attr('href');
            
            // Solo intervenimos si es un enlace interno (#algo)
            if (href && href.startsWith('#')) {
                // IMPORTANTE: Prevenir que Jazzmin o Bootstrap bloqueen el evento
                e.preventDefault(); 
                
                // Ejecutar nuestra función de cambio manual
                activarPestana(this);
            }
        });

    });

})(window.jQuery || django.jQuery);