"""
Management command: enviar_recordatorios_pagos
Uso: python manage.py enviar_recordatorios_pagos
     python manage.py enviar_recordatorios_pagos --dias-anticipacion 1
     python manage.py enviar_recordatorios_pagos --dry-run

Se ejecuta diariamente vía Railway Cron Job.
Envía recordatorios de pago por WhatsApp a clientes con parcialidades
pendientes que vencen hoy (o en N días de anticipación configurables).
"""

import requests
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from decouple import config

from comercial.models import ParcialidadPago, RecordatorioPago, ConstanteSistema

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Envía recordatorios de pago por WhatsApp a clientes con parcialidades pendientes"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias-anticipacion',
            type=int,
            default=0,
            help='Enviar recordatorios N días ANTES del vencimiento (0 = el mismo día)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simula el envío sin hacer llamadas a la API ni guardar registros',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        dias_anticipacion = options['dias_anticipacion']

        # Leer configuración desde ConstanteSistema o .env
        wa_token = config('WA_CLOUD_API_TOKEN', default='')
        wa_phone_id = config('WA_PHONE_NUMBER_ID', default='')

        if not wa_token or not wa_phone_id:
            self.stderr.write(self.style.ERROR(
                "Variables WA_CLOUD_API_TOKEN y WA_PHONE_NUMBER_ID no configuradas en .env"
            ))
            return

        # Fecha objetivo: hoy + anticipación
        fecha_objetivo = date.today() + timedelta(days=dias_anticipacion)

        self.stdout.write(
            f"{'[DRY RUN] ' if dry_run else ''}"
            f"Buscando parcialidades pendientes para: {fecha_objetivo.strftime('%d/%m/%Y')}"
        )

        # Obtener parcialidades pendientes que vencen en fecha_objetivo
        # Excluye cotizaciones canceladas
        parcialidades = ParcialidadPago.objects.filter(
            fecha_limite=fecha_objetivo,
            pagada=False,
            plan__activo=True,
            plan__cotizacion__estado__in=['CONFIRMADA', 'EJECUTADA'],
        ).select_related(
            'plan__cotizacion__cliente',
        ).order_by('plan__cotizacion__fecha_evento')

        total = parcialidades.count()
        self.stdout.write(f"Parcialidades encontradas: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Sin recordatorios que enviar hoy."))
            return

        enviados = 0
        fallidos = 0
        omitidos = 0

        for parcialidad in parcialidades:
            cotizacion = parcialidad.plan.cotizacion
            cliente = cotizacion.cliente

            # Verificar que no se haya enviado ya HOY
            ya_enviado = RecordatorioPago.objects.filter(
                parcialidad=parcialidad,
                fecha_envio__date=date.today(),
                estado='ENVIADO',
            ).exists()

            if ya_enviado:
                self.stdout.write(f"  OMITIDO (ya enviado hoy): {cliente.nombre} — {parcialidad.concepto}")
                continue

            # Validar teléfono
            telefono = _limpiar_telefono(cliente.telefono)
            if not telefono:
                self.stdout.write(
                    self.style.WARNING(f"  SIN TELÉFONO: {cliente.nombre} — COT-{cotizacion.id:03d}")
                )
                if not dry_run:
                    RecordatorioPago.objects.create(
                        parcialidad=parcialidad,
                        estado='OMITIDO',
                        mensaje_enviado='',
                        error_detalle='Cliente sin teléfono registrado',
                    )
                omitidos += 1
                continue

            # Construir mensaje
            mensaje = _construir_mensaje(parcialidad, cotizacion, cliente)

            self.stdout.write(
                f"  {'[DRY] ' if dry_run else ''}Enviando a {cliente.nombre} "
                f"({telefono}) — {parcialidad.concepto} ${parcialidad.monto:,.2f}"
            )

            if dry_run:
                self.stdout.write(f"    Mensaje preview:\n{mensaje}\n")
                enviados += 1
                continue

            # Enviar via WhatsApp Cloud API
            ok, respuesta = _enviar_whatsapp(wa_token, wa_phone_id, telefono, mensaje)

            RecordatorioPago.objects.create(
                parcialidad=parcialidad,
                estado='ENVIADO' if ok else 'FALLIDO',
                mensaje_enviado=mensaje,
                respuesta_api=respuesta[:1000] if respuesta else '',
                error_detalle='' if ok else respuesta[:500],
            )

            if ok:
                enviados += 1
                self.stdout.write(self.style.SUCCESS(f"    ✓ Enviado"))
            else:
                fallidos += 1
                self.stdout.write(self.style.ERROR(f"    ✗ Falló: {respuesta[:100]}"))

        # Resumen
        self.stdout.write("\n" + "="*50)
        self.stdout.write(self.style.SUCCESS(
            f"Resumen: {enviados} enviados | {fallidos} fallidos | {omitidos} sin teléfono"
        ))


# ==========================================
# HELPERS
# ==========================================

def _limpiar_telefono(telefono: str) -> str:
    """
    Limpia y formatea teléfono para WhatsApp Cloud API.
    Retorna formato internacional sin '+': 521XXXXXXXXXX
    Retorna '' si no es válido.
    """
    if not telefono:
        return ''
    
    # Quitar todo excepto dígitos
    digitos = ''.join(filter(str.isdigit, str(telefono)))
    
    if not digitos:
        return ''
    
    # Ya tiene código de país México (521 o 52)
    if digitos.startswith('521') and len(digitos) == 13:
        return digitos
    if digitos.startswith('52') and len(digitos) == 12:
        return '521' + digitos[2:]  # Añadir 1 para celular mexicano
    
    # Número local de 10 dígitos
    if len(digitos) == 10:
        return '521' + digitos
    
    # Número de 8 dígitos (Mérida antiguo — poco probable)
    if len(digitos) == 8:
        return '5219999' + digitos  # fallback poco probable
    
    return ''


def _construir_mensaje(parcialidad, cotizacion, cliente) -> str:
    """
    Construye el mensaje de recordatorio de pago.
    Personalizado con datos del evento y la parcialidad.
    """
    # Datos del plan
    plan = parcialidad.plan
    pendientes = plan.parcialidades.filter(pagada=False).count()
    total_pendiente = plan.parcialidades.filter(pagada=False).aggregate(
        total=__import__('django.db.models', fromlist=['Sum']).Sum('monto')
    )['total'] or Decimal('0.00')

    # Datos bancarios desde ConstanteSistema
    banco = _get_constante_texto('WA_BANCO', 'BBVA')
    clabe = _get_constante_texto('WA_CLABE', 'CLABE no configurada')
    titular = _get_constante_texto('WA_TITULAR', 'Quinta Ko\'ox Tanil')
    numero_wa = _get_constante_texto('WA_NUMERO_CONTACTO', '9991234567')

    dias_evento = (cotizacion.fecha_evento - date.today()).days

    mensaje = (
        f"Hola {cliente.nombre.split()[0].title()} 👋\n\n"
        f"Te recordamos que hoy vence tu pago correspondiente a:\n\n"
        f"📋 *{parcialidad.concepto}*\n"
        f"💰 *${parcialidad.monto:,.2f} MXN*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 *Tu evento:* {cotizacion.nombre_evento}\n"
        f"📅 *Fecha:* {cotizacion.fecha_evento.strftime('%d de %B de %Y')}\n"
        f"⏳ *Faltan:* {dias_evento} días\n"
        f"📊 *Saldo total pendiente:* ${total_pendiente:,.2f} MXN "
        f"({pendientes} pago{'s' if pendientes > 1 else ''} restante{'s' if pendientes > 1 else ''})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💳 *Formas de pago:*\n\n"
        f"🏦 *Transferencia / SPEI:*\n"
        f"   Banco: {banco}\n"
        f"   CLABE: `{clabe}`\n"
        f"   Titular: {titular}\n\n"
        f"💵 *Efectivo o tarjeta:* Coordina con nosotros.\n\n"
        f"Una vez realizado tu pago, envíanos tu comprobante por este medio.\n\n"
        f"¿Tienes alguna duda? Estamos para apoyarte 😊\n"
        f"*Quinta Ko'ox Tanil*"
    )

    return mensaje


def _get_constante_texto(clave: str, default: str) -> str:
    """
    Lee una constante de texto desde ConstanteSistema.
    NOTA: ConstanteSistema tiene campo 'valor' Decimal — 
    para texto usamos la descripción como valor de texto.
    """
    try:
        obj = ConstanteSistema.objects.get(clave=clave)
        # Si la descripción tiene contenido, úsala como valor de texto
        return obj.descripcion if obj.descripcion else default
    except ConstanteSistema.DoesNotExist:
        return default


def _enviar_whatsapp(token: str, phone_number_id: str, telefono: str, mensaje: str):
    """
    Envía un mensaje de texto via WhatsApp Cloud API (Meta).
    Retorna (True, respuesta) o (False, error_string).
    """
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "to": telefono,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": mensaje,
        },
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        
        if response.status_code == 200:
            return True, response.text
        else:
            logger.error(
                f"WhatsApp API error {response.status_code}: {response.text[:300]}"
            )
            return False, f"HTTP {response.status_code}: {response.text[:300]}"
    
    except requests.exceptions.Timeout:
        return False, "Timeout al conectar con WhatsApp API"
    except requests.exceptions.ConnectionError as e:
        return False, f"Error de conexión: {str(e)[:200]}"
    except Exception as e:
        return False, f"Error inesperado: {str(e)[:200]}"