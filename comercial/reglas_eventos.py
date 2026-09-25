"""Reglas de negocio del cotizador de Eventos, en un solo sitio.

Las comparte el backend y el JS de `cotizador/index.html`: si el aforo máximo
viviera duplicado en los dos lados, el frontend podría ofrecer una
combinación que el servidor luego rechaza.
"""

# Tope duro de aforo en Evento — la Quinta no opera eventos de más de 150
# personas, no hay ruta alterna ni excepción por autorización.
MAX_PERSONAS_EVENTO = 150

# Pasadía: 20 personas incluidas y hasta 10 adicionales con cargo, para un
# máximo inexcedible de 30 (Reglamento Interno v1.2, sección 4).
MAX_PERSONAS_PASADIA = 30

# Piso de personas para "Arma tu propio evento" (catálogo abierto por
# categoría). Por debajo, solo se ofrece un paquete de precio fijo —
# decisión del propietario, 2026-09-20.
MIN_PERSONAS_PERSONALIZADO_EVENTO = 50

# Hospedaje: la comodidad y el espacio se garantizan hasta la capacidad base
# de cada habitación (`Producto.capacidad_base_hospedaje`, 4 en Ka'an y
# Otoch). Por encima se admiten personas extra con recargo
# (PERSONA_EXTRA_HOSPEDAJE), pero como máximo estas por habitación — 4 + 6 =
# 10 por habitación, decisión del propietario, 2026-09-23.
MAX_PERSONAS_EXTRA_POR_HABITACION = 6
