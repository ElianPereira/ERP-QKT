"""
Importa el historial de cotizaciones, clientes y pagos del sistema anterior al ERP.

Uso:
    python manage.py importar_historico            # ejecuta la importación
    python manage.py importar_historico --dry-run  # previsualiza sin guardar
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from comercial.models import Cliente, Cotizacion, Pago, PortalCliente


# ---------------------------------------------------------------------------
# DATOS DEL SISTEMA ANTERIOR
# ---------------------------------------------------------------------------

CLIENTES = [
    {"clave": 1,  "nombre": "JOSE JAVIER PERAZA GONGORA",        "telefono": "9999105150", "email": "banquetes_rigel@hotmail.com"},
    {"clave": 2,  "nombre": "CARLA GRACIELA CRUZ CETINA",         "telefono": "9991926731", "email": "caarlycruuz@icloud.com"},
    {"clave": 3,  "nombre": "ESMERALDA CANCHE MONTUY",            "telefono": "",           "email": ""},
    {"clave": 4,  "nombre": "ANGI ALBERTOS ESPERON",              "telefono": "9992758440", "email": ""},
    {"clave": 5,  "nombre": "ISABEL DE LA CRUZ PEREIRA CEH",      "telefono": "",           "email": ""},
    {"clave": 6,  "nombre": "MARISELA LOPEZ RAMOS",               "telefono": "9999017703", "email": "marygatomafe18@gmail.com"},
    {"clave": 7,  "nombre": "ALEJANDRA PEREZ BRITO",              "telefono": "9992979773", "email": "alebrito2392@gmail.com"},
    {"clave": 8,  "nombre": "NANCY ANDREA CARDOS CANUL",          "telefono": "9994759103", "email": "Nancykr297@icloud.com"},
    {"clave": 9,  "nombre": "XIMENA RODRIGUEZ MALPICA LINO",      "telefono": "15554588601","email": ""},
    {"clave": 10, "nombre": "MAYRA ROSSANA RIOS CHI",             "telefono": "9992351800", "email": "rossanarios91@gmail.com"},
    {"clave": 11, "nombre": "JULIO ALBERTO PEREIRA CEH",          "telefono": "",           "email": ""},
    {"clave": 12, "nombre": "JOANA BETSABE KU CAAMAL",            "telefono": "9994590886", "email": "kucaamaljoanabetsabe@gmail.com"},
    {"clave": 13, "nombre": "JAVIER PEREZ HERNANDEZ",             "telefono": "9983213206", "email": "javier.perez@fanosa.com"},
    {"clave": 14, "nombre": "MARTHA EUGENIA PENICHE OLAIS",       "telefono": "",           "email": ""},
    {"clave": 15, "nombre": "PUBLICO EN GENERAL",                 "telefono": "",           "email": ""},
    {"clave": 16, "nombre": "ANGELICA ALATRISTE",                 "telefono": "",           "email": ""},
    {"clave": 17, "nombre": "MARIANA BERMEJO",                    "telefono": "",           "email": ""},
    {"clave": 18, "nombre": "MARI PECH",                          "telefono": "",           "email": ""},
    {"clave": 19, "nombre": "INGRID ARCEO",                       "telefono": "",           "email": ""},
    {"clave": 20, "nombre": "YAZMIN GUADALUPE SANCHEZ MARRUFO",   "telefono": "",           "email": ""},
]

# (cot_id, cliente_clave, fecha_evento YYYY-MM-DD, tipo_servicio, total_contratado, estado_final)
COTIZACIONES = [
    (72,  2,  "2024-05-04", "EVENTO",        Decimal("6100.00"),    "CERRADA"),
    (76,  3,  "2024-03-17", "PASADIA",       Decimal("1250.00"),    "CERRADA"),
    (80,  4,  "2024-03-30", "ARRENDAMIENTO", Decimal("450.00"),     "CERRADA"),
    (82,  6,  "2024-03-24", "PASADIA",       Decimal("1100.00"),    "CERRADA"),
    (83,  7,  "2024-07-27", "EVENTO",        Decimal("10950.00"),   "CERRADA"),
    (85,  3,  "2024-09-07", "EVENTO",        Decimal("4500.00"),    "CERRADA"),
    (86,  8,  "2024-06-16", "PASADIA",       Decimal("1700.00"),    "CERRADA"),
    (88,  8,  "2024-06-29", "PASADIA",       Decimal("725.00"),     "CERRADA"),
    (99,  10, "2024-10-06", "EVENTO",        Decimal("10650.00"),   "CERRADA"),
    (100, 11, "2024-10-26", "ARRENDAMIENTO", Decimal("450.00"),     "CERRADA"),
    (103, 13, "2024-12-13", "EVENTO",        Decimal("13578.99"),   "CERRADA"),
    (104, 12, "2024-12-21", "EVENTO",        Decimal("11200.00"),   "CERRADA"),
    (109, 3,  "2024-11-07", "ARRENDAMIENTO", Decimal("150.00"),     "CERRADA"),
    (111, 11, "2024-11-23", "ARRENDAMIENTO", Decimal("400.00"),     "CERRADA"),
    (116, 3,  "2025-04-19", "PASADIA",       Decimal("750.00"),     "CERRADA"),
    (118, 15, "2025-04-29", "ARRENDAMIENTO", Decimal("600.00"),     "CERRADA"),
    (119, 11, "2025-05-25", "ARRENDAMIENTO", Decimal("700.00"),     "CERRADA"),
    (120, 11, "2025-08-03", "ARRENDAMIENTO", Decimal("700.00"),     "CERRADA"),
    (121, 14, "2025-06-07", "ARRENDAMIENTO", Decimal("1450.00"),    "CERRADA"),
    (125, 10, "2025-08-02", "EVENTO",        Decimal("6350.00"),    "CERRADA"),
    (134, 16, "2025-12-05", "EVENTO",        Decimal("17166.84"),   "EJECUTADA"),
    (136, 11, "2025-08-24", "ARRENDAMIENTO", Decimal("700.00"),     "CERRADA"),
    (137, 3,  "2025-09-14", "PASADIA",       Decimal("500.00"),     "CERRADA"),
    (138, 15, "2025-09-18", "ARRENDAMIENTO", Decimal("700.00"),     "CERRADA"),
    (145, 19, "2025-11-04", "ARRENDAMIENTO", Decimal("800.00"),     "CERRADA"),
    (146, 15, "2025-11-08", "ARRENDAMIENTO", Decimal("200.00"),     "CERRADA"),
    (147, 15, "2025-11-15", "ARRENDAMIENTO", Decimal("700.00"),     "CERRADA"),
    (148, 16, "2025-12-05", "ARRENDAMIENTO", Decimal("986.00"),     "EJECUTADA"),
    (149, 20, "2026-01-10", "EVENTO",        Decimal("18328.00"),   "EJECUTADA"),
]

# (cot_id, fecha_pago YYYY-MM-DD, monto, metodo)
PAGOS = [
    (72,  "2024-03-06", Decimal("2700.00"), "EFECTIVO"),
    (72,  "2024-05-01", Decimal("2800.00"), "TRANSFERENCIA"),
    (72,  "2024-05-04", Decimal("600.00"),  "EFECTIVO"),
    (76,  "2024-03-16", Decimal("1250.00"), "TRANSFERENCIA"),
    (80,  "2024-03-17", Decimal("450.00"),  "TRANSFERENCIA"),
    (82,  "2024-03-22", Decimal("1100.00"), "TRANSFERENCIA"),
    (83,  "2024-04-03", Decimal("5000.00"), "DEPOSITO"),
    (83,  "2024-07-24", Decimal("5950.00"), "TRANSFERENCIA"),
    (85,  "2024-09-02", Decimal("4500.00"), "EFECTIVO"),
    (86,  "2024-06-13", Decimal("1700.00"), "TRANSFERENCIA"),
    (88,  "2024-06-29", Decimal("500.00"),  "TRANSFERENCIA"),
    (88,  "2024-06-29", Decimal("227.00"),  "TRANSFERENCIA"),
    (99,  "2024-09-11", Decimal("3700.00"), "TRANSFERENCIA"),
    (99,  "2024-09-28", Decimal("4800.00"), "EFECTIVO"),
    (99,  "2024-10-01", Decimal("2200.00"), "TRANSFERENCIA"),
    (100, "2024-10-27", Decimal("500.00"),  "EFECTIVO"),
    (103, "2024-10-29", Decimal("6539.50"), "TRANSFERENCIA"),
    (103, "2024-11-22", Decimal("7039.50"), "TRANSFERENCIA"),
    (104, "2024-10-12", Decimal("5500.00"), "EFECTIVO"),
    (104, "2024-11-22", Decimal("3000.00"), "TRANSFERENCIA"),
    (104, "2024-12-14", Decimal("2700.00"), "TRANSFERENCIA"),
    (109, "2024-12-08", Decimal("150.00"),  "EFECTIVO"),
    (111, "2024-11-24", Decimal("400.00"),  "EFECTIVO"),
    (116, "2025-04-19", Decimal("800.00"),  "EFECTIVO"),
    (118, "2025-04-29", Decimal("600.00"),  "EFECTIVO"),
    (119, "2025-05-25", Decimal("700.00"),  "EFECTIVO"),
    (120, "2025-08-03", Decimal("700.00"),  "EFECTIVO"),
    (121, "2025-06-07", Decimal("1450.00"), "EFECTIVO"),
    (125, "2025-07-17", Decimal("2000.00"), "TRANSFERENCIA"),
    (125, "2025-07-28", Decimal("4350.00"), "TRANSFERENCIA"),
    (134, "2025-11-04", Decimal("7055.19"), "TARJETA_DEBITO"),
    (134, "2025-11-25", Decimal("8349.10"), "TARJETA_DEBITO"),
    (136, "2025-08-24", Decimal("700.00"),  "EFECTIVO"),
    (137, "2025-09-14", Decimal("500.00"),  "EFECTIVO"),
    (138, "2025-09-10", Decimal("700.00"),  "TRANSFERENCIA"),
    (145, "2025-10-26", Decimal("500.00"),  "EFECTIVO"),
    (145, "2025-11-04", Decimal("300.00"),  "EFECTIVO"),
    (146, "2025-11-07", Decimal("200.00"),  "EFECTIVO"),
    (147, "2025-11-15", Decimal("700.00"),  "EFECTIVO"),
    (148, "2025-12-02", Decimal("975.38"),  "TRANSFERENCIA"),
    (149, "2025-12-08", Decimal("9164.00"), "TRANSFERENCIA"),
]

TIPO_LABELS = {
    "EVENTO": "Evento",
    "PASADIA": "Pasadía",
    "ARRENDAMIENTO": "Arrendamiento",
}


def _nombre_evento(cot_id, tipo, cliente_nombre):
    return f"[HIST-{cot_id:04d}] {TIPO_LABELS[tipo]} - {cliente_nombre.title()}"


class Command(BaseCommand):
    help = "Importa el historial de cotizaciones y pagos del sistema anterior"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra lo que se importaría sin guardar nada",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("── MODO SIMULACIÓN (--dry-run) ──"))

        # Construir índice de clientes por clave
        cliente_map = {c["clave"]: c for c in CLIENTES}

        # Construir índice de pagos por cotizacion_id
        pagos_por_cot = {}
        for row in PAGOS:
            pagos_por_cot.setdefault(row[0], []).append(row)

        with transaction.atomic():
            usuario = self._get_usuario()
            clientes_creados, clientes_existentes = self._importar_clientes(
                dry_run, cliente_map
            )
            cotizaciones_creadas, cotizaciones_omitidas = self._importar_cotizaciones(
                dry_run, cliente_map, pagos_por_cot, usuario
            )

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("─── RESUMEN ───────────────────────────"))
        self.stdout.write(f"  Clientes creados   : {clientes_creados}")
        self.stdout.write(f"  Clientes existentes: {clientes_existentes}")
        self.stdout.write(f"  Cotizaciones creadas  : {cotizaciones_creadas}")
        self.stdout.write(f"  Cotizaciones omitidas : {cotizaciones_omitidas} (ya existían)")
        pagos_totales = sum(len(v) for v in pagos_por_cot.values())
        self.stdout.write(f"  Pagos registrados  : {pagos_totales}")
        if dry_run:
            self.stdout.write(self.style.WARNING("  (simulación — no se guardó nada)"))

    # ------------------------------------------------------------------

    def _get_usuario(self):
        """Devuelve el primer superusuario disponible para asignar como creador."""
        user = User.objects.filter(is_superuser=True).order_by("pk").first()
        if not user:
            user = User.objects.order_by("pk").first()
        return user

    def _importar_clientes(self, dry_run, cliente_map):
        creados = 0
        existentes = 0

        for datos in CLIENTES:
            nombre = datos["nombre"]
            existe = Cliente.objects.filter(nombre__iexact=nombre).exists()
            if existe:
                existentes += 1
                self.stdout.write(f"  [cliente] YA EXISTE  → {nombre}")
            else:
                creados += 1
                self.stdout.write(f"  [cliente] CREAR      → {nombre}")
                if not dry_run:
                    Cliente.objects.create(
                        nombre=nombre,
                        email=datos.get("email", ""),
                        telefono=datos.get("telefono", ""),
                        origen="Otro",
                    )

        return creados, existentes

    def _importar_cotizaciones(self, dry_run, cliente_map, pagos_por_cot, usuario):
        creadas = 0
        omitidas = 0

        for cot_id, cliente_clave, fecha_str, tipo, total, estado_final in COTIZACIONES:
            datos_cliente = cliente_map[cliente_clave]
            nombre_evento = _nombre_evento(cot_id, tipo, datos_cliente["nombre"])

            # Idempotencia: saltar si ya fue importada
            if Cotizacion.objects.filter(nombre_evento=nombre_evento).exists():
                omitidas += 1
                self.stdout.write(f"  [cot {cot_id:04d}] OMITIDA (ya existe)")
                continue

            fecha_evento = datetime.date.fromisoformat(fecha_str)
            pagos_de_esta_cot = pagos_por_cot.get(cot_id, [])
            total_pagado = sum(p[2] for p in pagos_de_esta_cot)

            self.stdout.write(
                f"  [cot {cot_id:04d}] CREAR  {tipo:15s} {fecha_str}  "
                f"total=${total:>10,.2f}  pagado=${total_pagado:>10,.2f}  "
                f"→ {estado_final}  ({len(pagos_de_esta_cot)} pago(s))"
            )

            if dry_run:
                creadas += 1
                continue

            # Buscar el cliente en el ERP (ya fue creado arriba)
            cliente = Cliente.objects.filter(
                nombre__iexact=datos_cliente["nombre"]
            ).first()
            if not cliente:
                self.stdout.write(
                    self.style.ERROR(
                        f"  ¡Cliente no encontrado para cot {cot_id}! Saltando."
                    )
                )
                continue

            # El precio_final que se establece en Cotizacion.save() se
            # sobreescribe inmediatamente después con un update() directo,
            # ya que calcular_totales() toma como base los items (ninguno aquí).
            cot = Cotizacion(
                cliente=cliente,
                tipo_servicio=tipo,
                nombre_evento=nombre_evento,
                fecha_evento=fecha_evento,
                estado="BORRADOR",
                usuario=usuario,
                subtotal=total,
                iva=Decimal("0.00"),
                retencion_isr=Decimal("0.00"),
                retencion_iva=Decimal("0.00"),
                precio_final=total,
                num_personas=1,
            )
            # Usamos save() para respetar el flujo del modelo (portal, etc.)
            cot.save()

            # Corregir precio_final porque calcular_totales() lo pone a 0
            # (no hay items). Usamos update() para no disparar save() de nuevo.
            Cotizacion.objects.filter(pk=cot.pk).update(
                subtotal=total,
                iva=Decimal("0.00"),
                retencion_isr=Decimal("0.00"),
                retencion_iva=Decimal("0.00"),
                precio_final=total,
            )

            # Desactivar el portal que se crea automáticamente
            PortalCliente.objects.filter(cotizacion=cot).update(activo=False)

            # Crear pagos con bulk_create para evitar la validación de saldo
            # (algunos registros históricos tienen ligeros sobrepagos o
            #  pagos tardíos que no deben rechazarse en la importación).
            pago_objs = [
                Pago(
                    cotizacion=cot,
                    tipo="INGRESO",
                    fecha_pago=datetime.date.fromisoformat(fecha_pago),
                    monto=monto,
                    metodo=metodo,
                    solicitar_factura=False,
                    usuario=usuario,
                    notas="Importado desde historial",
                )
                for _, fecha_pago, monto, metodo in pagos_de_esta_cot
            ]
            Pago.objects.bulk_create(pago_objs)

            # Establecer estado final directamente (evita validación del
            # state machine, que requeriría cumplir condiciones de anticipo).
            Cotizacion.objects.filter(pk=cot.pk).update(estado=estado_final)

            creadas += 1

        return creadas, omitidas
