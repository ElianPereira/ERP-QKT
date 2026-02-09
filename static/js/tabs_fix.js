document.addEventListener("DOMContentLoaded", function() {
    // Detectar clics en las pestañas del admin (Jazzmin / Bootstrap)
    const tabs = document.querySelectorAll('.change-form .nav-tabs a');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', function(e) {
            // Obtener el ID del objetivo (ej: #informacion-del-evento)
            const targetId = this.getAttribute('href');
            
            // Si es un enlace real a otra página, no hacer nada
            if (!targetId || !targetId.startsWith('#')) return;

            // Prevenir el comportamiento por defecto si es necesario, 
            // pero dejar que el hash cambie.
            // e.preventDefault(); 
            
            // Forzar la visualización del contenido
            const targetContent = document.querySelector(targetId);
            const allContents = document.querySelectorAll('.tab-pane, .tab-content > div');
            const allTabs = document.querySelectorAll('.nav-tabs li a');

            if (targetContent) {
                // 1. Ocultar todos los contenidos
                allContents.forEach(content => {
                    content.classList.remove('active', 'show');
                    content.style.display = 'none'; // Forzar ocultado CSS
                });

                // 2. Desactivar todas las pestañas visualmente
                allTabs.forEach(t => t.classList.remove('active'));

                // 3. Activar el contenido seleccionado
                targetContent.classList.add('active', 'show');
                targetContent.style.display = 'block'; // Forzar mostrado CSS
                
                // 4. Activar la pestaña clicada
                this.classList.add('active');
            }
        });
    });
    
    // Ejecutar al cargar por si hay un hash en la URL (ej: /change/#finanzas)
    if(window.location.hash) {
        const activeTab = document.querySelector(`.nav-tabs a[href="${window.location.hash}"]`);
        if(activeTab) {
            activeTab.click();
        }
    }
});