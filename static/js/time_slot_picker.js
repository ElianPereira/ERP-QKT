/* static/js/time_slot_picker.js
 * Selector de hora con lista de franjas buscable (sin dependencias,
 * sin jQuery) para reemplazar el popover "Elija una hora" del admin.
 *
 * Funciona igual con mouse, teclado y touch, y en cualquier navegador
 * moderno (Chrome/Safari/Firefox/Edge, escritorio y móvil) porque solo
 * usa DOM/eventos estándar — nada específico de un sistema operativo.
 */
(function () {
    'use strict';

    var STEP_MINUTES = 15;

    function buildSlots() {
        var slots = [];
        for (var m = 0; m < 24 * 60; m += STEP_MINUTES) {
            var hh = String(Math.floor(m / 60)).padStart(2, '0');
            var mm = String(m % 60).padStart(2, '0');
            slots.push(hh + ':' + mm);
        }
        return slots;
    }

    var ALL_SLOTS = buildSlots();

    function normalize(value) {
        return (value || '').replace(':', '').trim();
    }

    function initField(field) {
        if (field.dataset.timeSlotReady) return;
        field.dataset.timeSlotReady = '1';

        var input = field.querySelector('input');
        var toggle = field.querySelector('.time-slot-toggle');
        var dropdown = field.querySelector('.time-slot-dropdown');
        if (!input || !toggle || !dropdown) return;

        var visibleRows = [];
        var activeIndex = -1;

        function closeDropdown() {
            dropdown.classList.remove('open');
            field.classList.remove('is-upward');
            activeIndex = -1;
        }

        function positionDropdown() {
            var rect = field.getBoundingClientRect();
            var spaceBelow = window.innerHeight - rect.bottom;
            var spaceAbove = rect.top;
            if (spaceBelow < 260 && spaceAbove > spaceBelow) {
                field.classList.add('is-upward');
            } else {
                field.classList.remove('is-upward');
            }
        }

        function highlight(index) {
            visibleRows.forEach(function (row) { row.classList.remove('is-active'); });
            activeIndex = index;
            if (index >= 0 && index < visibleRows.length) {
                visibleRows[index].classList.add('is-active');
                visibleRows[index].scrollIntoView({ block: 'nearest' });
            }
        }

        function selectSlot(slot) {
            input.value = slot;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            closeDropdown();
        }

        function renderRows(filterText) {
            var filter = normalize(filterText);
            dropdown.innerHTML = '';
            visibleRows = [];
            ALL_SLOTS.forEach(function (slot) {
                if (filter && slot.replace(':', '').indexOf(filter) !== 0) return;
                var row = document.createElement('div');
                row.className = 'time-slot-row';
                row.setAttribute('role', 'option');
                row.textContent = slot;
                if (slot === input.value) row.classList.add('is-current');
                row.addEventListener('mousedown', function (evt) {
                    evt.preventDefault();
                    selectSlot(slot);
                });
                dropdown.appendChild(row);
                visibleRows.push(row);
            });
            if (!visibleRows.length) {
                var empty = document.createElement('div');
                empty.className = 'time-slot-empty';
                empty.textContent = 'Sin coincidencias';
                dropdown.appendChild(empty);
            }
        }

        function openDropdown() {
            renderRows(input.value);
            positionDropdown();
            dropdown.classList.add('open');
        }

        input.addEventListener('focus', openDropdown);
        input.addEventListener('click', openDropdown);

        input.addEventListener('input', function () {
            renderRows(input.value);
            dropdown.classList.add('open');
            highlight(visibleRows.length ? 0 : -1);
        });

        input.addEventListener('keydown', function (evt) {
            if (evt.key === 'ArrowDown') {
                evt.preventDefault();
                if (!dropdown.classList.contains('open')) { openDropdown(); return; }
                highlight(Math.min(activeIndex + 1, visibleRows.length - 1));
            } else if (evt.key === 'ArrowUp') {
                evt.preventDefault();
                highlight(Math.max(activeIndex - 1, 0));
            } else if (evt.key === 'Enter') {
                if (dropdown.classList.contains('open') && activeIndex >= 0) {
                    evt.preventDefault();
                    selectSlot(visibleRows[activeIndex].textContent);
                }
            } else if (evt.key === 'Escape') {
                closeDropdown();
            }
        });

        input.addEventListener('blur', function () {
            // se retrasa para permitir que el mousedown de una fila corra primero
            window.setTimeout(closeDropdown, 120);
        });

        toggle.addEventListener('click', function () {
            if (dropdown.classList.contains('open')) {
                closeDropdown();
            } else {
                input.focus();
                openDropdown();
            }
        });

        window.addEventListener('resize', function () {
            if (dropdown.classList.contains('open')) positionDropdown();
        });
    }

    function initAll() {
        document.querySelectorAll('.time-slot-field').forEach(initField);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
    // por si algún formset inline llega a añadir filas dinámicamente
    document.addEventListener('formset:added', initAll);
})();
