# ============================================================
# GUÍA DE INTEGRACIÓN — Módulo de Reportes
# ============================================================
# Cambios necesarios en archivos existentes del proyecto.
# Cada sección indica archivo, ubicación y cambio exacto.
# ============================================================


# ──────────────────────────────────────────────────────────────
# 1. core_erp/settings.py — INSTALLED_APPS
# ──────────────────────────────────────────────────────────────
# Agregar 'reportes' después de 'contabilidad':
#
#     'airbnb',
#     'contabilidad',
#     'reportes',          # <-- AGREGAR
# ]


# ──────────────────────────────────────────────────────────────
# 2. core_erp/settings.py — JAZZMIN_SETTINGS icons
# ──────────────────────────────────────────────────────────────
# Agregar dentro del dict "icons":
#
#     "reportes":                          "fas fa-chart-bar",
#     "reportes.reportegenerado":          "fas fa-history",


# ──────────────────────────────────────────────────────────────
# 3. core_erp/settings.py — JAZZMIN_SETTINGS order_with_respect_to
# ──────────────────────────────────────────────────────────────
# Agregar antes de "auth":
#
#     # === REPORTES ===
#     "reportes",
#     "reportes.reportegenerado",


# ──────────────────────────────────────────────────────────────
# 4. core_erp/settings.py — JAZZMIN_SETTINGS topmenu_links
# ──────────────────────────────────────────────────────────────
# Agregar un link al top menu (antes de "Cerrar sesión"):
#
#     {"name": "Reportes", "url": "reportes:selector"},


# ──────────────────────────────────────────────────────────────
# 5. core_erp/urls.py — Imports
# ──────────────────────────────────────────────────────────────
# No necesita imports nuevos de views. Solo include.


# ──────────────────────────────────────────────────────────────
# 6. core_erp/urls.py — urlpatterns
# ──────────────────────────────────────────────────────────────
# Agregar ANTES de la línea: path('admin/', admin.site.urls),
#
#     # --- MÓDULO REPORTES ---
#     path('admin/reportes/', include('reportes.urls')),


# ──────────────────────────────────────────────────────────────
# 7. core_erp/urls.py — Import include
# ──────────────────────────────────────────────────────────────
# Asegurar que 'include' está importado:
# from django.urls import path, include


# ──────────────────────────────────────────────────────────────
# 8. Migraciones
# ──────────────────────────────────────────────────────────────
# python manage.py makemigrations reportes
# python manage.py migrate


# ──────────────────────────────────────────────────────────────
# 9. Git
# ──────────────────────────────────────────────────────────────
# git add .
# git commit -m "feat(reportes): módulo centralizado con 10 reportes PDF"
# git push origin main
