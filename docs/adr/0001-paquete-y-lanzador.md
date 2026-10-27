# ADR-0001: Paquete en GitHub + notebook lanzador

**Estado:** Aceptada
**Fecha:** 2026-10-27
**Decide:** Adriel

## Contexto

El estudio vivía entero dentro de un notebook de Colab de 9 celdas. La librería compartida (~1.200 líneas) y
el pipeline (~1.700 líneas) iban como texto dentro de las celdas 1 y 7, que las escribían a disco al
ejecutarse. Editar ese código en Colab era incómodo, no había historial de cambios, las pruebas vivían fuera
del proyecto y cada arreglo obligaba a descargar y reemplazar el notebook completo.

## Decisión

El código pasa a un paquete Python (`studio/`) en un repositorio público de GitHub. El notebook queda como
lanzador de 8 celdas: la Celda 1 clona o actualiza el repositorio y las demás solo importan y llaman una función.

## Opciones consideradas

### A: Notebook autocontenido (como antes)

| Dimensión | Evaluación |
|---|---|
| Complejidad | Baja para usar, alta para mantener |
| Actualizar | Reemplazar el `.ipynb` completo |
| Pruebas | Fuera del proyecto |
| Dependencia externa | Ninguna |

**A favor:** un solo archivo; funciona sin internet a GitHub.
**En contra:** código como texto, sin historial, cambios difíciles de revisar.

### B: Repositorio solo con las celdas 7 y 8

**En contra:** dependen de la librería de la Celda 1; el repositorio quedaría incompleto.

### C: Paquete completo + lanzador (elegida)

| Dimensión | Evaluación |
|---|---|
| Complejidad | Media: un paquete, un notebook corto |
| Actualizar | Volver a ejecutar la Celda 1 |
| Pruebas | En el repositorio, con CI en cada cambio |
| Dependencia externa | GitHub (con copia de respaldo en Drive) |

**A favor:** historial y versiones (`VERSION = "v2.1.0"`), pruebas automáticas, celdas legibles con sus
opciones a la vista.
**En contra:** depende de GitHub al iniciar; al ser público, el código es visible para cualquiera.

## Análisis

La dependencia de GitHub se mitiga con una copia del código en `Drive/YT-Channel-Studio/code`, que la Celda 1
usa si GitHub no responde. Que el repositorio sea público no expone datos: las claves viven solo en
`api_keys.toml` dentro del Drive del usuario y el código no contiene ninguna.

## Consecuencias

- **Más fácil:** revisar cambios, volver a una versión anterior, recibir arreglos sin reemplazar el notebook,
  probar cada cambio.
- **Más difícil:** las rutas ya no pueden estar fijas en `/content`; ahora salen de `STUDIO_CONTENT_DIR` y
  `STUDIO_BASE_DIR`.
- **Por revisar:** si un `git pull` cambia dependencias de una herramienta, hoy hay que volver a ejecutar su
  celda; podría detectarse solo guardando una versión de dependencias en cada venv.
