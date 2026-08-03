"""
Catálogo de cadenas de la red Paynet.

Fuente única: lo consumen el portal (bloque de referencia de efectivo) y la
ficha PDF. Antes vivía suelto en el JavaScript de la plantilla, así que la
ficha no podía reutilizarlo sin copiarlo.

Los datos salen del kit oficial de marca de Paynet. El listado que había antes
era de memoria: nombraba OXXO y Farmacias Benavides, que no están afiliadas, y
omitía la mitad de las que sí lo están. No agregar una cadena sin su logotipo
en `static/img/pagos/paynet/`.
"""

# (slug del logotipo, nombre visible)
TIENDAS_PAYNET = [
    ('walmart', 'Walmart'),
    ('walmart-express', 'Walmart Express'),
    ('bodega-aurrera', 'Bodega Aurrera'),
    ('mas-bodega', 'Mi Bodega Aurrera'),
    ('sams-club', "Sam's Club"),
    ('soriana', 'Soriana'),
    ('7-eleven', '7-Eleven'),
    ('circle-k', 'Circle K'),
    ('extra', 'Extra'),
    ('kiosko', 'Kiosko'),
    ('farmacia-guadalajara', 'Farmacias Guadalajara'),
    ('farmacia-ahorro', 'Farmacias del Ahorro'),
    ('waldos', "Waldo's"),
    ('tiendas-k', 'Tiendas K'),
    ('mercadia', 'Mercadía'),
    ('systienda', 'Systienda'),
    ('pagaqui', 'PagaQui'),
    ('eleczion', 'Eleczion'),
    ('via-servicios', 'VIA Servicios'),
]
