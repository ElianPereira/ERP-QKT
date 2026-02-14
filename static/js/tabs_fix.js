/* Ubicación: static/js/tabs_fix.js */

(function($) {
    'use strict';

    $(document).ready(function() {
        // Validación de seguridad
        if (typeof $ === 'undefined') {
            console.error("TabsFix: jQuery no está cargado.");
            return;
        }

        console.log("🚀 Fix Global (Pestañas + Menú Usuario): ACTIVO.");

        // ===============================================
        // 1. FIX MENÚ DE USUARIO (DROPDOWN)
        // ===============================================
        // Este bloque fuerza al menú de usuario a abrirse manualmente
        $(document).on('click', '.user-menu .dropdown-toggle', function(e) {
            e.preventDefault();
            e.stopPropagation(); // Evita conflictos con otros scripts

            var $parent = $(this).parent();
            var $menu = $(this).next('.dropdown-menu');

            // Alternar estado (Abrir/Cerrar)
            $parent.toggleClass('show');
            $menu.toggleClass('show');
        });

        // Cerrar el menú si hacemos clic fuera de él
        $(document).on('click', function(e) {
            if (!$(e.target).closest('.user-menu').length) {
                $('.user-menu').removeClass('show');
                $('.user-menu .dropdown-menu').removeClass('show');
            }
        });

        // ===============================================
        // 2. FIX PESTAÑAS (TABS) - Tu código original
        // ===============================================
        var storageKey = 'jazzmin_tab_pref_' + window.location.pathname;

        function activarPestana(linkElement) {
            var $link = $(linkElement);
            var targetSelector = $link.attr('href');
            
            if (!targetSelector || !targetSelector.startsWith('#')) return;

            // Visual: Nav
            $link.closest('ul').find('a').removeClass('active');
            $link.addClass('active');

            // Visual: Content
            $('.tab-pane').removeClass('active').removeClass('show');
            var $targetContent = $(targetSelector);
            if ($targetContent.length > 0) {
                $targetContent.addClass('active').addClass('show');
            }

            // Memoria
            localStorage.setItem(storageKey, targetSelector);
        }

        // Restaurar al cargar
        var savedTab = localStorage.getItem(storageKey);
        if (savedTab) {
            var $savedLink = $('.nav-tabs a[href="' + savedTab + '"]');
            if ($savedLink.length > 0) {
                setTimeout(function(){ activarPestana($savedLink); }, 100);
            }
        }

        // Interceptar clics en pestañas
        $(document).on('click', '.nav-tabs a', function(e) {
            var href = $(this).attr('href');
            if (href && href.startsWith('#')) {
                e.preventDefault(); 
                activarPestana(this);
            }
        });

    });

})(window.jQuery || django.jQuery || window.$ || {});