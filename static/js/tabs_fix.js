/* Archivo: static/js/tabs_fix.js */

document.addEventListener("DOMContentLoaded", function() {
    
    // 1. CLAVE ÚNICA DINÁMICA
    // Esto asegura que la pestaña de 'Cotización 1' no se mezcle con 'Producto 1'
    const storageKey = 'tab_state_' + window.location.pathname;

    // Selector amplio para agarrar pestañas de Jazzmin (Fieldsets) y Bootstrap normales
    const tabs = document.querySelectorAll('.change-form .nav-tabs a, .nav-tabs-custom .nav-tabs a');
    
    // A. Lógica al hacer Clic (Guardar)
    tabs.forEach(tab => {
        tab.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href');
            
            // Validamos que sea un ID interno
            if (!targetId || !targetId.startsWith('#')) return;

            // Guardamos en LocalStorage
            localStorage.setItem(storageKey, targetId);

            // --- FORZADO VISUAL (Para corregir fallos de Jazzmin/Bootstrap) ---
            const targetContent = document.querySelector(targetId);
            
            // Buscamos contenedores tanto de fieldsets como de pestañas normales
            const allContents = document.querySelectorAll('.tab-pane, .tab-content > div');
            const allTabs = document.querySelectorAll('.nav-tabs li a');

            if (targetContent) {
                // 1. Ocultar todo
                allContents.forEach(content => {
                    content.classList.remove('active', 'show');
                    content.style.display = 'none'; 
                });
                allTabs.forEach(t => t.classList.remove('active'));

                // 2. Mostrar el seleccionado
                targetContent.classList.add('active', 'show');
                targetContent.style.display = 'block'; 
                this.classList.add('active');
            }
        });
    });
    
    // B. Lógica al Cargar la página (Recuperar)
    const savedTab = localStorage.getItem(storageKey);
    
    if (savedTab) {
        // Buscamos la pestaña guardada
        const activeTab = document.querySelector(`.nav-tabs a[href="${savedTab}"]`);
        if (activeTab) {
            // Simulamos clic para activar toda la lógica visual
            activeTab.click();
        }
    } else {
        // Si no hay nada guardado, activar la primera por defecto
        if(tabs.length > 0) {
            tabs[0].click();
        }
    }
});