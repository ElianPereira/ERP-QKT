/* Ubicación: static/js/tabs_fix.js */

document.addEventListener('DOMContentLoaded', function() {
    console.log("🔧 FIX MANUAL: Iniciando script de reparación...");

    // ===============================================
    // 1. FIX MENÚ DE USUARIO (Vanilla JS)
    // ===============================================
    // Buscamos el botón usando selectores estándar de Jazzmin
    var userToggle = document.querySelector('.user-menu .dropdown-toggle');
    var userMenu = document.querySelector('.user-menu .dropdown-menu');
    var userContainer = document.querySelector('.user-menu');

    if (userToggle && userMenu) {
        console.log("✅ Botón de usuario encontrado.");
        
        // Borde ROJO temporal para verificar que el script cargó (Avísame si lo ves)
        userToggle.style.border = "2px solid red"; 

        userToggle.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            
            console.log("🖱️ Click detectado en usuario!");

            // Forzar clases de Bootstrap manualmente
            if (userContainer.classList.contains('show')) {
                userContainer.classList.remove('show');
                userMenu.classList.remove('show');
            } else {
                userContainer.classList.add('show');
                userMenu.classList.add('show');
            }
        });

        // Cerrar si clic fuera
        document.addEventListener('click', function(e) {
            if (!userContainer.contains(e.target)) {
                userContainer.classList.remove('show');
                userMenu.classList.remove('show');
            }
        });
    } else {
        console.error("❌ No se encontró el elemento .user-menu .dropdown-toggle");
    }

    // ===============================================
    // 2. FIX PESTAÑAS (Tu código original simplificado)
    // ===============================================
    // Este bloque usa jQuery solo si está disponible, para no romper nada
    if (typeof jQuery !== 'undefined') {
        (function($) {
            var storageKey = 'jazzmin_tab_pref_' + window.location.pathname;
            
            // Restaurar pestaña
            var savedTab = localStorage.getItem(storageKey);
            if (savedTab) {
                var $link = $('.nav-tabs a[href="' + savedTab + '"]');
                if ($link.length) {
                    setTimeout(function() { 
                        $link.tab('show'); // Intento nativo bootstrap
                        // Fallback manual
                        $('.tab-pane').removeClass('active show');
                        $(savedTab).addClass('active show');
                        $link.closest('ul').find('a').removeClass('active');
                        $link.addClass('active');
                    }, 100);
                }
            }

            // Guardar al hacer click
            $(document).on('click', '.nav-tabs a', function(e) {
                var href = $(this).attr('href');
                if (href && href.startsWith('#')) {
                    localStorage.setItem(storageKey, href);
                }
            });
        })(jQuery);
    }
});