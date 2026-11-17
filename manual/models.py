"""Bitácora de descargas del manual (Issue #507): quién tiene qué versión."""
from django.conf import settings
from django.db import models


class DescargaManual(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                related_name='+', verbose_name='Usuario')
    rol_exportado = models.CharField(max_length=12, verbose_name='Manual descargado')
    version = models.CharField(max_length=40, blank=True, verbose_name='Versión')
    secciones = models.PositiveSmallIntegerField(default=0, verbose_name='Secciones')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de descarga')

    class Meta:
        verbose_name = 'Descarga del manual'
        verbose_name_plural = 'Descargas'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.rol_exportado} · {self.created_at:%d/%m/%Y %H:%M}'
