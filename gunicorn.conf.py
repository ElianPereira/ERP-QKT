import os

# Si existe la variable PORT (en la nube), úsala. Si no, usa la 8000 (local).
port_id = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port_id}"
timeout = 120
workers = 2