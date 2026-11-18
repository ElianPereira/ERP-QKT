---
titulo: Administrar el sistema: usuarios, responsables y procesos automáticos
capitulo: Administración del sistema
orden: 1800
roles: [DIRECCION]
---
### Usuarios y permisos

1. **Nuevo usuario**: en **Autenticación y Usuarios → Usuarios**, crea el usuario con contraseña y marca **Acceso al sitio de administración**. Agrégalo al **grupo** que corresponda; el grupo decide qué ve.

| Grupo | Qué recibe |
|---|---|
| Vendedor | Ventas: cotizar, cobrar, visitas. Sin costos ni cancelaciones |
| Ventas | El encargado: operación diaria |
| Contabilidad | El contador |
| Nómina | Nómina |

2. **Baja de alguien**: desactiva su usuario; **no lo borres**.
3. El usuario de **Dirección** es superusuario y pide código de verificación al entrar.
4. Si cambias los permisos de los grupos, el equipo técnico corre `python manage.py crear_grupos_permisos`; **no corre solo con el deploy.**

### A quién le llegan los avisos

En **Comunicación → Responsables** se define quién recibe los avisos de cada rol (Encargado, Dirección, Contador, Compras, Ventas):

1. Captura su teléfono de WhatsApp.
2. Al guardar un número nuevo, queda **pendiente** y el sistema le manda un mensaje: debe contestar **ACEPTO** desde ese teléfono. Hasta entonces, el número anterior sigue activo.
3. Puedes definir **suplentes** con rango de fechas.
4. Dirección puede recibir copia en vivo de los avisos del encargado.

### Procesos automáticos (el Programador)

Un solo servicio de Railway corre `programador` cada 5 minutos y ejecuta las tareas del día: cierre de cotizaciones, recordatorios, guías, seguimientos, descarga del SAT, avisos de vencimientos, resúmenes y más. Cada corrida queda en **Operaciones → Ejecuciones**.

- Si la última corrida tiene más de 30 minutos, aparece un aviso rojo arriba en todas las páginas.
- Una tarea en error o que no corrió abre un pendiente urgente para la Dirección.
- Una ranura que se pasa de su tolerancia **no se recupera sola**: se avisa.

### Variables de entorno

Las llaves y configuraciones viven solo en Railway; el catálogo con el nombre, para qué sirve y dónde se rota cada una está en `docs/variables_entorno.md`. Nunca se pegan valores en chats ni en Issues. Las variables de los demás servicios apuntan a las de `web`, no llevan valores copiados.

### Las tres rutinas automáticas

Tres rutinas programadas revisan el repositorio y entregan reportes por Pull Request: **Técnica**, **Operativa/Contable** y **Seguridad**. Nunca hacen merge ni migraciones. Sus reportes quedan en `docs/`.

### Lo que no se hace

:::nohagas
Correr migraciones o escribir directo en la base de producción a mano. Los cambios de esquema salen solo al unir cambios revisados: el despliegue corre las migraciones pendientes.
:::
