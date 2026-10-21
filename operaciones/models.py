"""
Módulo de organización de actividades (Issue #257).

Todo el flujo hacia los colaboradores es informativo, unidireccional, por
WhatsApp — sin checklist interactiva ni confirmación digital de su parte
(decisión explícita del propietario). El registro que queda aquí es para
supervisión posterior desde el admin, no para que el colaborador interactúe
con él.
"""
from datetime import datetime, timedelta

from django.db import models

from .constantes import HORAS_ANTES_ENVIO_OPERATIVO


class PlantillaChecklist(models.Model):
    TIPO_CHOICES = [
        ('TURNOVER_EVENTO', 'Preparación — Evento'),
        ('TURNOVER_PASADIA', 'Preparación — Pasadía'),
        ('TURNOVER_HOSPEDAJE', 'Preparación — Hospedaje'),
        ('TURNOVER_ARRENDAMIENTO', 'Preparación — Arrendamiento'),
        ('MANTENIMIENTO_RECURRENTE', 'Mantenimiento recurrente'),
    ]
    CADENCIA_CHOICES = [
        ('DIARIA', 'Diaria'),
        ('SEMANAL', 'Semanal'),
        ('MENSUAL', 'Mensual'),
    ]
    DIAS_SEMANA = [
        (0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'),
        (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo'),
    ]

    # Tipos de turnover: uno por tipo de servicio, mismo criterio que
    # comercial.Cotizacion.TIPO_SERVICIO_CHOICES. Mantenimiento recurrente sí
    # puede tener varias plantillas activas (ej. "semanal" y "mensual").
    TIPOS_TURNOVER = (
        'TURNOVER_EVENTO', 'TURNOVER_PASADIA',
        'TURNOVER_HOSPEDAJE', 'TURNOVER_ARRENDAMIENTO',
    )
    # tipo_servicio de Cotizacion -> tipo de PlantillaChecklist de turnover.
    TIPO_POR_SERVICIO = {
        'EVENTO': 'TURNOVER_EVENTO',
        'PASADIA': 'TURNOVER_PASADIA',
        'HOSPEDAJE': 'TURNOVER_HOSPEDAJE',
        'ARRENDAMIENTO': 'TURNOVER_ARRENDAMIENTO',
    }

    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    encabezado = models.CharField(
        max_length=100, blank=True,
        help_text="Título del mensaje, ej. 'Preparación — Hospedaje'. Vacío usa el nombre.",
    )
    responsable_default = models.ForeignKey(
        'nomina.Empleado', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='plantillas_checklist',
    )
    duracion_estimada_horas = models.DecimalField(
        max_digits=4, decimal_places=1, default=2.0,
        help_text="Solo turnover: cuánto antes de la hora límite debe entrar el "
                   "colaborador a preparar.",
                   verbose_name='Duración estimada (horas)',
    )
    hora_limite_default = models.TimeField(
        null=True, blank=True,
        help_text="Solo mantenimiento recurrente: hora límite del día. Turnover usa "
                   "la hora de inicio real del servicio.",
                   verbose_name='Hora límite por defecto',
    )
    cadencia = models.CharField(
        max_length=10, choices=CADENCIA_CHOICES, blank=True,
        help_text="Solo mantenimiento recurrente.",
    )
    dia_semana = models.IntegerField(
        null=True, blank=True, choices=DIAS_SEMANA, help_text="Solo cadencia semanal.",
        verbose_name='Día de la semana',
    )
    dia_mes = models.IntegerField(
        null=True, blank=True,
        help_text="Solo cadencia mensual (1-28, para evitar meses cortos).",
        verbose_name='Día del mes',
    )
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Plantilla de checklist"
        verbose_name_plural = "Plantillas"
        ordering = ['tipo', 'nombre']

    def __str__(self):
        return self.nombre

    def es_turnover(self):
        return self.tipo in self.TIPOS_TURNOVER

    def titulo_mensaje(self):
        return self.encabezado or self.nombre


class ItemChecklist(models.Model):
    plantilla = models.ForeignKey(PlantillaChecklist, on_delete=models.CASCADE, related_name='items')
    orden = models.PositiveIntegerField(
        default=1, help_text="Orden de ejecución física, no alfabético.",
    )
    texto = models.CharField(
        max_length=300,
        help_text="Verbo + objeto + criterio de terminado. Ej. 'Tallar y trapear "
                   "los 2 baños — sin manchas ni cabello en el piso'.",
    )

    class Meta:
        verbose_name = "Tarea del checklist"
        verbose_name_plural = "Tareas del checklist"
        ordering = ['plantilla', 'orden']

    def __str__(self):
        return f"{self.orden}. {self.texto}"


class TareaProgramada(models.Model):
    ESTADO_ENVIO = [
        ('NO_APLICA', 'No aplica'),
        ('PENDIENTE', 'Pendiente'),
        ('ENVIADO', 'Enviado'),
        ('FALLIDO', 'Fallido'),
    ]

    plantilla = models.ForeignKey(PlantillaChecklist, on_delete=models.PROTECT, related_name='tareas')
    cotizacion = models.ForeignKey(
        'comercial.Cotizacion', on_delete=models.CASCADE, null=True, blank=True,
        related_name='tareas_operativas',
        help_text="Solo turnover: la cotización cuyo servicio se está preparando.",
        verbose_name='Cotización',
    )
    responsable = models.ForeignKey(
        'nomina.Empleado', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tareas_programadas',
    )
    fecha = models.DateField(help_text="Día en que se ejecuta la tarea.")
    hora_entrada = models.TimeField(help_text="Hora de entrada del colaborador ese día.")
    hora_limite = models.TimeField(help_text="Hora límite para tener todo listo.", verbose_name='Hora límite')
    requiere_tiempo_extra = models.BooleanField(default=False)

    # Tres envíos independientes por tarea: el aviso de horario especial (solo
    # si requiere_tiempo_extra), el checklist operativo al responsable, y el
    # resumen anticipado al propietario. Cada uno se reintenta por su cuenta.
    estado_aviso_horario = models.CharField(max_length=10, choices=ESTADO_ENVIO, default='NO_APLICA')
    estado_operativo = models.CharField(max_length=10, choices=ESTADO_ENVIO, default='PENDIENTE')
    estado_resumen_propietario = models.CharField(max_length=10, choices=ESTADO_ENVIO, default='PENDIENTE')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado el')

    class Meta:
        verbose_name = "Tarea programada"
        verbose_name_plural = "Tareas"
        ordering = ['-fecha', 'hora_entrada']
        unique_together = [('plantilla', 'cotizacion', 'fecha')]
        indexes = [
            models.Index(fields=['fecha', 'responsable']),
            models.Index(fields=['estado_operativo']),
            models.Index(fields=['estado_resumen_propietario']),
            models.Index(fields=['estado_aviso_horario']),
        ]

    def __str__(self):
        quien = self.responsable.nombre if self.responsable else 'sin asignar'
        return f"{self.plantilla.titulo_mensaje()} — {self.fecha} ({quien})"

    def hora_envio_operativo(self) -> datetime:
        """Momento en que debe salir el checklist: HORAS_ANTES_ENVIO_OPERATIVO antes de la entrada."""
        return datetime.combine(self.fecha, self.hora_entrada) - timedelta(hours=HORAS_ANTES_ENVIO_OPERATIVO)


class EjecucionCron(models.Model):
    """Una corrida de una tarea del programador único (Issue #423).

    La ranura (`programada_para`) es el momento del horario que la corrida
    cubre, no cuándo arrancó: el `UniqueConstraint` sobre (tarea, ranura) es lo
    que impide que dos procesos corran la misma. Sin borrado: es la bitácora.
    """
    ESTADO_CHOICES = [
        ('EN_CURSO', 'En curso'),
        ('EXITO', 'Éxito'),
        ('ERROR', 'Error'),
    ]

    tarea = models.CharField(max_length=60, verbose_name='Tarea', db_index=True)
    programada_para = models.DateTimeField(verbose_name='Ranura programada')
    inicio = models.DateTimeField(verbose_name='Inicio')
    fin = models.DateTimeField(null=True, blank=True, verbose_name='Fin')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='EN_CURSO', verbose_name='Estado')
    salida = models.TextField(blank=True, default='', verbose_name='Salida')
    error = models.TextField(blank=True, default='', verbose_name='Error')

    class Meta:
        verbose_name = 'Ejecución'
        verbose_name_plural = 'Ejecuciones'
        ordering = ['-inicio']
        constraints = [
            models.UniqueConstraint(fields=['tarea', 'programada_para'], name='uniq_ejecucioncron_tarea_ranura'),
        ]
        indexes = [models.Index(fields=['tarea', '-programada_para'])]

    def __str__(self):
        return f'{self.tarea} · {self.programada_para:%Y-%m-%d %H:%M}'

    @property
    def duracion(self):
        return (self.fin - self.inicio) if self.fin else None
