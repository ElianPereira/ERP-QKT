/*
 * Auto-cierre de sesión por INACTIVIDAD en el admin/ERP.
 *
 * El control de seguridad real es del backend (SESSION_IDLE_TIMEOUT +
 * SESSION_SAVE_EVERY_REQUEST): tras ese tiempo sin peticiones, la sesión
 * caduca en el servidor. Este script complementa esa protección en el
 * navegador: si la pestaña queda abierta y desatendida, redirige al logout
 * automáticamente (con un aviso previo) en vez de dejar datos financieros
 * a la vista. Debe usar el MISMO tiempo que el backend.
 */
(function () {
    "use strict";

    // Minutos de inactividad. La plantilla puede fijar window.QKT_IDLE_MINUTES
    // desde settings; si no, se usan 30 (igual que el default del backend).
    var IDLE_MIN = parseInt(window.QKT_IDLE_MINUTES, 10);
    if (!IDLE_MIN || IDLE_MIN <= 0) { IDLE_MIN = 30; }

    var IDLE_MS = IDLE_MIN * 60 * 1000;
    var WARN_MS = 60 * 1000;                 // avisar 1 min antes
    var LOGOUT_URL = "/admin/logout/";
    var idleTimer = null;
    var warnTimer = null;
    var aviso = null;

    function crearAviso() {
        if (aviso) { return; }
        aviso = document.createElement("div");
        aviso.setAttribute("role", "alert");
        aviso.style.cssText = [
            "position:fixed", "top:16px", "left:50%", "transform:translateX(-50%)",
            "z-index:99999", "background:#7a1f1f", "color:#fff",
            "padding:12px 18px", "border-radius:8px", "font-family:sans-serif",
            "font-size:14px", "box-shadow:0 4px 16px rgba(0,0,0,.35)", "display:none",
            "max-width:92vw"
        ].join(";");
        aviso.textContent = "Tu sesión se cerrará pronto por inactividad. Mueve el mouse o haz clic para continuar.";
        document.body.appendChild(aviso);
    }

    function mostrarAviso() { if (aviso) { aviso.style.display = "block"; } }
    function ocultarAviso() { if (aviso) { aviso.style.display = "none"; } }

    function cerrarSesion() {
        window.location.href = LOGOUT_URL;
    }

    function reiniciar() {
        if (idleTimer) { clearTimeout(idleTimer); }
        if (warnTimer) { clearTimeout(warnTimer); }
        ocultarAviso();
        warnTimer = setTimeout(mostrarAviso, Math.max(0, IDLE_MS - WARN_MS));
        idleTimer = setTimeout(cerrarSesion, IDLE_MS);
    }

    function init() {
        crearAviso();
        var eventos = ["mousemove", "mousedown", "keydown", "touchstart", "scroll", "click"];
        var pendiente = false;
        function onActividad() {
            // Throttle: no reprogramar timers en cada píxel de movimiento.
            if (pendiente) { return; }
            pendiente = true;
            setTimeout(function () { pendiente = false; }, 1000);
            reiniciar();
        }
        for (var i = 0; i < eventos.length; i++) {
            window.addEventListener(eventos[i], onActividad, { passive: true });
        }
        reiniciar();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
