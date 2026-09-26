/* static/js/qkt_ui.js — comportamiento de los componentes del admin (Issue #322).
   Se carga en todas las páginas del admin (templates/admin/base.html). */
(function () {
    'use strict';

    // Botones con confirmación (antes: onclick="return confirm(...)" en línea)
    document.addEventListener('click', function (e) {
        var el = e.target.closest('[data-qkt-confirmar]');
        if (el && !window.confirm(el.getAttribute('data-qkt-confirmar'))) {
            e.preventDefault();
            e.stopImmediatePropagation();
        }
    }, true);

    // Copiar al portapapeles (enlace del portal, etc.)
    document.addEventListener('click', function (e) {
        var el = e.target.closest('[data-qkt-copiar]');
        if (!el) return;
        e.preventDefault();
        var texto = el.getAttribute('data-qkt-copiar');
        var original = el.innerHTML;
        var avisar = function (msg) {
            el.textContent = msg;
            setTimeout(function () { el.innerHTML = original; }, 1500);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(texto).then(
                function () { avisar('Copiado'); },
                function () { window.prompt('Copia el enlace:', texto); }
            );
        } else {
            window.prompt('Copia el enlace:', texto);
        }
    });

    // Menús "más acciones": uno abierto a la vez, se cierran al hacer clic fuera
    document.addEventListener('click', function (e) {
        document.querySelectorAll('details.qkt-menu[open]').forEach(function (d) {
            if (!d.contains(e.target)) d.removeAttribute('open');
        });
    });
    // La tabla tiene overflow-x: auto, que recortaría un panel absoluto: el
    // panel se posiciona fijo respecto a la ventana, pegado a su botón, y se
    // abre hacia arriba si no cabe abajo.
    document.addEventListener('toggle', function (e) {
        var d = e.target;
        if (!d.matches || !d.matches('details.qkt-menu') || !d.open) return;
        var panel = d.querySelector('.qkt-menu__panel');
        var r = d.querySelector('summary').getBoundingClientRect();
        panel.style.position = 'fixed';
        panel.style.right = 'auto';
        panel.style.left = Math.max(8, r.right - panel.offsetWidth) + 'px';
        var abajo = r.bottom + 4;
        panel.style.top = (abajo + panel.offsetHeight > window.innerHeight - 8
            ? Math.max(8, r.top - 4 - panel.offsetHeight) : abajo) + 'px';
    }, true);
    window.addEventListener('scroll', function () {
        document.querySelectorAll('details.qkt-menu[open]').forEach(function (d) { d.removeAttribute('open'); });
    }, true);
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('details.qkt-menu[open]').forEach(function (d) { d.removeAttribute('open'); });
    });

    // Zona de carga de archivos: cuenta los elegidos y acepta soltarlos encima
    function contarArchivos(input) {
        var zona = input.closest('.qkt-zona-carga');
        var salida = zona && zona.querySelector('.qkt-zona-carga__conteo');
        if (!salida) return;
        var n = input.files.length;
        salida.textContent = n === 0 ? '' : (n === 1 ? '1 archivo seleccionado' : n + ' archivos seleccionados');
    }
    document.addEventListener('change', function (e) {
        if (e.target.matches('.qkt-zona-carga input[type="file"]')) contarArchivos(e.target);
    });
    ['dragenter', 'dragover'].forEach(function (tipo) {
        document.addEventListener(tipo, function (e) {
            var zona = e.target.closest && e.target.closest('.qkt-zona-carga');
            if (!zona) return;
            e.preventDefault();
            zona.classList.add('is-arrastrando');
        });
    });
    ['dragleave', 'drop'].forEach(function (tipo) {
        document.addEventListener(tipo, function (e) {
            var zona = e.target.closest && e.target.closest('.qkt-zona-carga');
            if (!zona) return;
            zona.classList.remove('is-arrastrando');
            if (tipo !== 'drop') return;
            e.preventDefault();
            var input = zona.querySelector('input[type="file"]');
            if (input && e.dataTransfer && e.dataTransfer.files.length) {
                input.files = e.dataTransfer.files;
                contarArchivos(input);
            }
        });
    });

    function iniciarLista() {
        var tabla = document.getElementById('result_list');
        if (tabla) {
            // Encabezados de columnas numéricas a la derecha, como sus celdas
            var filas = tabla.querySelectorAll('tbody tr');
            tabla.querySelectorAll('thead th').forEach(function (th, i) {
                for (var r = 0; r < filas.length; r++) {
                    var celda = filas[r].children[i];
                    if (celda && celda.querySelector(':scope > .qkt-num')) {
                        th.classList.add('qkt-col-num');
                        return;
                    }
                }
            });
            // Columnas de texto libre que pueden partirse en dos líneas
            var contenedor = document.querySelector('[data-qkt-texto]');
            var texto = contenedor && contenedor.getAttribute('data-qkt-texto');
            if (texto) {
                texto.split(',').filter(Boolean).forEach(function (campo) {
                    tabla.querySelectorAll('td.field-' + campo + ', th.field-' + campo).forEach(function (td) {
                        td.classList.add('qkt-col-texto');
                    });
                });
            }
        }

        // Acciones masivas: visibles solo con registros seleccionados
        var acciones = document.querySelector('.change-list-actions .actions');
        if (acciones) {
            var sincronizar = function () {
                var n = document.querySelectorAll('input.action-select:checked').length;
                acciones.classList.toggle('is-visible', n > 0);
            };
            document.addEventListener('change', function (e) {
                if (e.target.matches('input.action-select, #action-toggle')) sincronizar();
            });
            sincronizar();
        }

        // "Más filtros"
        var boton = document.querySelector('.qkt-mas-filtros');
        var extra = document.getElementById('qkt-filtros-extra');
        if (boton && extra) {
            boton.addEventListener('click', function () {
                extra.hidden = !extra.hidden;
                boton.setAttribute('aria-expanded', String(!extra.hidden));
            });
        }

        // Filtro activo en verde + recarga al elegir un valor (antes había que
        // pulsar "Buscar" después de cada filtro)
        var $ = window.jQuery || (window.django && window.django.jQuery);
        document.querySelectorAll('.qkt-filtro select.search-filter').forEach(function (sel) {
            var marcar = function () {
                var op = sel.options[sel.selectedIndex];
                sel.closest('.qkt-filtro').classList.toggle('is-activo', !!(op && op.getAttribute('data-name')));
            };
            marcar();
            var enviar = function () {
                marcar();
                // Jazzmin asigna el `name` del select en su propio handler de
                // change; se envía en el siguiente ciclo para correr después.
                setTimeout(function () { sel.form && sel.form.submit(); }, 0);
            };
            // Solo eventos de select2 (los dispara el usuario): Jazzmin lanza un
            // `change` sintético al iniciar, que aquí provocaría un bucle de envíos.
            if ($ && $.fn.select2) { $(sel).on('select2:select', enviar); } else { sel.addEventListener('change', enviar); }
        });
        var ocultosActivos = document.querySelectorAll('#qkt-filtros-extra .qkt-filtro.is-activo').length;
        var contador = document.querySelector('.qkt-mas-filtros__n');
        if (contador && ocultosActivos) {
            contador.textContent = ocultosActivos;
            contador.hidden = false;
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciarLista);
    } else {
        iniciarLista();
    }
})();
