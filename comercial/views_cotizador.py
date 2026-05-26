  """
Cotizador Público — comercial/views_cotizador.py
=================================================
Replica el flujo del webhook de ManyChat:
- Crea Cliente (reutiliza si ya existe por teléfono)
- Crea Cotización BORRADOR con items reales del catálogo
- Crea PortalCliente automáticamente
- Envía notificación WhatsApp al negocio
- Retorna URL del portal para redirigir al cliente

Rutas:
  GET  /cotizar/         → Formulario multi-paso
  POST /cotizar/enviar/  → Procesa y crea en ERP → JSON
  GET  /cotizar/gracias/ → Fallback de confirmación
"""