---
titulo: Qué hacer si el estado de cuenta del mes no llega
capitulo: Contabilidad
orden: 1200
roles: [CONTADOR, DIRECCION]
---
El banco manda el estado de cuenta por correo los **días 1 y 2** de cada mes. Un filtro lo recibe y el sistema lo lee solo, sin que nadie suba nada. Esta sección es para cuando ese camino falla.

:::nota
Desde el **día 7** del mes, si no hay estado de cuenta del mes anterior, el sistema crea un aviso urgente para la Dirección. Si ya llegaste a ese punto, sigue los pasos de abajo.
:::

### Paso 1. Revisa si el sistema ya lo tiene

1. En el menú lateral entra a **Contabilidad → Estados**.
2. Busca el renglón del mes y la cuenta. Según su situación:

    | Situación | Qué significa | Qué hacer |
    |---|---|---|
    | Procesado | El sistema ya leyó los movimientos | Nada; sigue con la conciliación |
    | Subido, sin procesar | El archivo está, pero falta leerlo | Ve al paso 3 |
    | Error al procesar | El sistema no pudo leer el archivo | Anota el mensaje de error y avisa a la Dirección |
    | No hay renglón | Nunca llegó | Ve al paso 2 |

    ![Lista de estados de cuenta con la acción Procesar](img/estado-lista.webp)

### Paso 2. Si no hay renglón: súbelo a mano

1. Descarga el estado de cuenta del mes en PDF desde el portal del banco.
2. En **Contabilidad → Estados** toca **Agregar Estado de cuenta bancario**.
3. Elige la cuenta bancaria, el mes y el año, y adjunta el archivo. Guarda.

[CONFIRMAR: ¿Quién descarga el estado de cuenta del portal del banco cuando no llega por correo: el contador o la Dirección? ¿Y qué hace el contador si el mes ya debe cerrarse y el estado todavía no está: espera, lo pide al banco o cierra con el saldo según libros?]

### Paso 3. Procesarlo

1. En la lista de **Estados**, marca el renglón del mes.
2. En el menú de acciones elige **Procesar (extraer movimientos y emparejar)** y toca **Ejecutar**.

:::correcto
El estado queda en **Procesado** y cada movimiento muestra su asiento. Los que el sistema no pudo emparejar quedan marcados para que los revises.
:::

:::nohagas
Cargar el mismo mes dos veces. Si ya existe un estado de ese periodo, el sistema conserva el primero y los demás se ignoran.
:::

:::rol DIRECCION
### Para la Dirección: dónde se atora el correo

Cada correo del banco queda registrado en **Contabilidad → Correos**, incluso si no se pudo leer.

![Bitácora de correos del banco](img/estado-correos.webp)

| Resultado | Qué significa | Qué hacer |
|---|---|---|
| Procesado | Todo bien | Nada |
| Con error o Por procesar | El sistema lo reintenta solo una vez al día | Espera al día siguiente; si sigue igual, súbelo a mano |
| Ignorado | No pasó la revisión de seguridad del remitente, o ya había un estado de ese mes | Lee el detalle del registro |
| No aparece | Nunca llegó al sistema | Revisa el filtro de Gmail y el correo de reenvío |

Si el error dice que no se pudo abrir el archivo, probablemente el banco cambió la contraseña del PDF: se actualiza en la cuenta bancaria (solo la Dirección puede verla o cambiarla).
:::
