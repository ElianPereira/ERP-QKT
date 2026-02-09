/* Archivo: static/js/tabs_fix.js */

document.addEventListener("DOMContentLoaded", function() {
    console.log("🔧 JS de Pestañas cargado correctamente."); // Verifica si ves esto en la consola (F12)

    // Clave única por URL (para diferenciar Usuario de Cotización)
    const storageKey = 'tab_state_' + window.location.pathname;
    
    // Jazzmin a veces usa .nav-tabs dentro de .card-header
    // Buscamos cualquier enlace dentro de una lista de pestañas
    const tabs = document.querySelectorAll('.nav-tabs .nav-link, .nav-tabs a');

    // 1. RECUPERAR (Al cargar la página)
    const savedTabHref = localStorage.getItem(storageKey);
    
    if (savedTabHref) {
        // Buscamos la pestaña específica por su href (ej: #general)
        // Nota: Jazzmin suele usar IDs como #general, #permisos, o #fieldset-0
        const tabToActivate = document.querySelector(`.nav-tabs a[href="${savedTabHref}"]`) || 
                              document.querySelector(`.nav-tabs .nav-link[href="${savedTabHref}"]`);

        if (tabToActivate) {
            console.log("Restaurando pestaña:", savedTabHref);
            // Jazzmin/Bootstrap 4 requiere activar el Tab (link) y el Pane (contenido)
            
            // A. Simular click (método más seguro para activar eventos de Jazzmin)
            tabToActivate.click(); 

            // B. Refuerzo manual por si el click falla en cargar estilos
            setTimeout(() => {
               if(!tabToActivate.classList.contains('active')) {
                   tabToActivate.classList.add('active');
               }
            }, 50);
        }
    }

    // 2. GUARDAR (Al hacer click)
    tabs.forEach(tab => {
        tab.addEventListener('click', function(e) {
            const href = this.getAttribute('href');
            if (href && href.startsWith('#')) {
                console.log("Guardando pestaña:", href);
                localStorage.setItem(storageKey, href);
            }
        });
    });
});