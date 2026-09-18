"""Reglas de negocio del cotizador de Eventos, en un solo sitio.

Las comparte el backend y el JS de `cotizador/index.html`: si el aforo máximo
viviera duplicado en los dos lados, el frontend podría ofrecer una
combinación que el servidor luego rechaza.
"""

# Tope duro de aforo en Evento — la Quinta no opera eventos de más de 150
# personas, no hay ruta alterna ni excepción por autorización.
MAX_PERSONAS_EVENTO = 150
