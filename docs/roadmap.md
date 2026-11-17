# Roadmap de ejecución

Plan de trabajo del sistema de decisión crediticia end-to-end (scorecard → API de scoring → motor de reglas → monitoreo) sobre datos públicos de Lending Club.

Supuestos: arranque el 23-nov-2026, 6–8 h por semana, unas 15 semanas. Cada fase se cierra con un tag (`v0.0` … `v0.5`) y un post.

| Fase | Semanas | Fechas | Tag | Entregable |
| --- | --- | --- | --- | --- |
| 0. Cimientos | 1 | 23-nov → 29-nov | `v0.0` | Repo público con esqueleto, `make data` y CI en verde |
| 1. Score | 4 | 30-nov → 27-dic | `v0.1` | Scorecard calibrado + challenger + validación OOT en MLflow |
| 2. API de scoring | 2 | 28-dic → 10-ene | `v0.2` | Imagen Docker con `/score`, `/health`, `/model-info` |
| 3. Política y reglas | 4 | 11-ene → 7-feb | `v0.3` | `decision_service` + `docs/policy.md` |
| 4. Monitoreo | 2 | 8-feb → 21-feb | `v0.4` | Simulador + tablero Streamlit de 3 pestañas |
| 5. MLOps y cierre | 2 | 22-feb → 7-mar | `v0.5` | CI end-to-end con compose, README final |

Si falta tiempo se recorta, en este orden: nube opcional, workflow de KNIME, challenger LightGBM. La Fase 3 no se recorta.

## Decisiones a cerrar antes de escribir código

| # | Decisión | Recomendación | Por qué |
| --- | --- | --- | --- |
| D1 | Fuente de los datos | Mirror de Kaggle vía API (`kaggle datasets download`), con hash verificado | Requiere token de Kaggle propio; el CI no descarga datos, usa una muestra sintética |
| D2 | Períodos del split | Desarrollo 2013–2015, OOT 2016, producción simulada 2017 | 2018 no madura (ver R1 y ADR-002) |
| D3 | Definición del target | *Bad* = Charged Off / Default con último pago hasta el mes 8 (90+ días hacia el mes 12) | El archivo es una foto final, no trae historia mensual (ver R2) |
| D4 | Base de logs | PostgreSQL | Dos servicios y el simulador escriben a la vez; DuckDB es de un solo escritor. DuckDB queda para el análisis offline sobre parquet |
| D5 | Versión de Python | 3.12, fijada en `.python-version` | Todas las librerías del stack la soportan hoy |
| D6 | Gestor de dependencias | uv, con workspace (un `pyproject.toml` por servicio) | Lockfile único, imágenes livianas por servicio |
| D7 | Carga del modelo en la API | Artefacto fijo exportado desde MLflow (`models/<versión>/`) | La API no depende de un servidor MLflow en runtime |
| D8 | Idioma | README y código en inglés; `docs/` en inglés con resumen en español | Alcance remoto |
| D9 | Nombre del repo | `credit-decisioning-e2e` | Renombrar el repo actual antes de publicar |

## Riesgos detectados

**R1. Los préstamos de 2018 no tienen maduración.** El mirror llega hasta fines de 2018, así que una originación de 2018 tiene entre 0 y 12 meses de historia: no hay resultado que "revelar" en la simulación. Alternativa probable: desarrollo 2013–2015, OOT 2016, producción simulada 2017. Se confirma en Fase 0 midiendo meses observados por cohorte.

**R2. No hay performance mensual.** El archivo de aceptados trae el estado final y la fecha del último pago, no el atraso mes a mes. "90+ días dentro de los 12 meses" no se puede calcular literal: hay que aproximarlo con estado final + `last_pymnt_d`, y documentar el sesgo en `docs/target.md`. Lo mismo limita la vista de vintages (se arma por mes de default aproximado).

**R3. El archivo de rechazados es pobre.** Trae pocas columnas (monto, fecha, score de riesgo, DTI, antigüedad laboral, estado). La inferencia de rechazados necesita un modelo reducido solo con esas variables comunes; no se puede aplicar el scorecard completo a los rechazados.

**R4. Leakage.** Buena parte de las ~150 columnas se conoce después de la originación. Se trabaja con una lista blanca explícita de variables de solicitud y buró, no con una lista negra.

**R5. `grade` y `int_rate` son salida del modelo de Lending Club.** Usarlos como variable da un AUC inflado y un modelo que no es propio. Quedan fuera del score; `grade` se usa solo como política "vieja" en el swap-set e `int_rate` solo en la rentabilidad.

**R6. Sesgo de selección.** El modelo se entrena solo con aprobados por Lending Club. Se declara en el model card y es la motivación de la inferencia de rechazados.

**R7. Licencia y disponibilidad del mirror.** Verificar la licencia antes de publicar cualquier derivado (incluida la muestra para CI: por eso es sintética).

**R8. Tamaño.** El archivo de aceptados pesa más de 1 GB en CSV. Se convierte una sola vez a parquet con tipos fijos y todo lo demás lee parquet.

## Fase 0: cimientos (`v0.0`)

- [x] Cerrar D1–D9. Split y target en `docs/adr/002-target-and-split.md`.
- [x] Esqueleto del monorepo: `src/credit_score/`, `tests/`, `docs/`. Las demás carpetas se crean en la fase que les da contenido.
- [x] `pyproject.toml` + `uv.lock`, ruff, pytest, `.gitignore` (con `data/` y `mlruns/`).
- [x] `make data`: descarga, verificación de SHA-256, conversión a parquet.
- [x] Auditoría de datos reproducible (`make audit` → `docs/data_audit.md`): cohortes, maduración, estados y nulos.
- [x] Lista blanca de variables con su rol (`src/credit_score/data/schema.py`).
- [x] Generador de muestra sintética con el mismo esquema, para tests y CI.
- [x] CI mínimo: ruff + pytest. Badge en el README.
- [x] README inicial: una frase, diagrama Mermaid, estado por fase.
- [x] ADR-001 (dataset).
- [x] ADR-002 (target y split temporal).

Hecho cuando: un clon limpio corre `make data && make test` sin intervención y el CI está verde.

## Fase 1: score (`v0.1`)

| Semana | Trabajo | Salida |
| --- | --- | --- |
| 1 ✅ | Contrato de datos con pandera; target, exclusiones; split temporal | ADR-002, `make dataset` → `modeling.parquet` |
| 2 ✅ | Binning monotónico con optbinning, IV, correlación, PSI por variable entre períodos; 17 variables seleccionadas | `make binning` → `docs/binning.md` |
| 3 ✅ | Logística sobre WoE, escalado a puntos (600 = 50:1, PDO 20), calibración a PD, bandas de riesgo | Tabla de puntos exportada, artefacto del modelo |
| 4 ✅ | Challenger LightGBM + SHAP; validación OOT (KS, AUC/Gini, lift, PSI, tasa de malos por banda); model card | Reporte de validación, `docs/model_card.md` |

- El experimento se preregistra antes de entrenar (`docs/experiments/001-scorecard.md`), siguiendo data-science-harness.
- Todo el entrenamiento es un comando (`make train`), con semilla fija y hash de datos registrados en MLflow.
- Signos de los coeficientes verificados (todos del mismo signo sobre WoE) como test automático.
- Extra recortable: replicar binning y scorecard en KNIME y comparar puntos.

Hecho cuando: `make train` reproduce las mismas métricas dos veces seguidas y el reporte OOT está en `docs/`.

## Fase 2: API de scoring (`v0.2`)

- [x] Esquema pydantic generado desde `credit_score/bounds.py`, la misma tabla que genera el contrato pandera del entrenamiento.
- [x] Cálculo de puntos en Python puro (`credit_score/scoring.py`), sin librerías de entrenamiento en runtime.
- [x] Reason codes: diferencia contra el máximo de puntos de cada variable, hasta 4.
- [x] `POST /score`, `GET /health`, `GET /model-info`; `model_version` y `request_id` en cada respuesta.
- [x] Logging de entrada y salida a PostgreSQL (y a stdout como JSON).
- [x] Dockerfile multi-stage, usuario no root, healthcheck. Imagen de 210 MB.
- [x] Tests: paridad con el entrenamiento, contrato (422 fuera de rango), smoke test del stack y carga con locust.

Verificado el 17-nov-2026 sobre el contenedor: 3.000 préstamos reales con score, PD y banda idénticos al entrenamiento; p95 de 15 ms con 20 usuarios concurrentes (unas 500 solicitudes por segundo, 12.973 sin fallas).

Hecho cuando: paridad exacta entre el score offline y el de la API sobre 1.000 casos, y p95 < 50 ms en local.

## Fase 3: política y motor de reglas (`v0.3`)

| Semana | Trabajo | Salida |
| --- | --- | --- |
| 1 ✅ | Curva de corte: aprobación, tasa de malos y rentabilidad esperada por punto de corte; LGD supuesto | Notebook + ADR del LGD |
| 2 ✅ | Swap-set contra política por `grade`; inferencia de rechazados (parceling / fuzzy augmentation) con modelo reducido | Notebooks con efecto neto en mora y margen |
| 3 ✅ | Matriz de límites (banda × DTI/ingreso); grafo de decisión en GoRules JDM: knockouts → corte por banda → límite | `policy/*.json` + tests por regla |
| 4 ✅ | `decision_service`: llama al score, evalúa el grafo, champion–challenger por hash del ID, traza completa en logs | Servicio en compose, `docs/policy.md` |

- El análisis es un reporte generado (`make policy-analysis` → `docs/policy_analysis.md`) en lugar de notebooks, como el resto del repo. La política propuesta está en `docs/policy.md` y los supuestos en el ADR-004.
- Cada regla tiene dueño, razón, impacto estimado y un test con casos borde.
- La política lleva versión propia, independiente de la del modelo; ambas quedan en cada decisión registrada.
- Script de diff de impacto: dadas dos versiones de política y una muestra, cuántas decisiones cambian y hacia dónde.

Verificado el 17-nov-2026: 3.000 solicitudes reales de 2017 pasaron por el stack completo con decisiones idénticas a la política calculada offline; p95 de 9,4 ms por decisión; challenger en 10,5% de los casos.

Hecho cuando: una solicitud devuelve APROBAR / REVISAR / RECHAZAR con límite, motivos y traza, y cada corte del documento de política cita un número de los notebooks.

## Fase 4: monitoreo (`v0.4`)

- [x] Simulador (`make simulate`): envía mes a mes las solicitudes de 2017 al `decision_service` y registra el resultado real de cada préstamo con la fecha desde la que se conoce (14 meses después de la emisión).
- [x] Drift inyectado (`DRIFT=broker`): desde julio de 2017, un canal con solicitantes más riesgosos suma 40% al volumen.
- [x] Job de monitoreo (`make monitor`): PSI del score (contra desarrollo y contra los 3 meses previos) y por variable, KS/AUC y observado contra esperado sobre préstamos aprobados de cohortes maduras.
- [x] Tablero Streamlit con selector de mes: Estabilidad, Performance, Política y operación, Vintages, Alertas.
- [x] Alertas con umbrales escritos en `docs/retraining_criteria.md`.
- [x] Reportes mensuales de Evidently en HTML (`make evidently`), en un entorno propio porque Evidently 0.7 no corre con pandas 3.
- [x] Vista de vintages de los aprobados.
- [x] `docs/retraining_criteria.md`: qué combinación de alertas dispara recalibrar y cuál reentrenar.

Verificado el 17-nov-2026 con el 10% de 2017 (40.116 solicitudes; 48.522 con drift): recomendación "recalibrar" en ambos casos. Sin drift, ninguna alerta de política. Con drift, julio dispara la caída de aprobación (−9,8 pp), la regla K2 (+58%) y el PSI contra los meses previos (0,077), mientras el PSI contra desarrollo baja.

Hecho cuando: `make simulate && make dashboard` muestra los 12 meses, con performance visible solo para los meses maduros.

## Fase 5: MLOps y cierre (`v0.5`)

- [x] CI completo: ruff + pytest → pipeline sobre datos sintéticos → build de las 3 imágenes → `compose up` → 40 solicitudes de prueba → simulación y job de monitoreo → verificación de logs y tablero.
- [x] Job de política: un cambio en `policy/` corre los tests de reglas y publica el diff de impacto en el resumen del PR.
- [x] Ciclo de vida del modelo (`docs/model_lifecycle.md`): `models/registry.json` define el champion; `make recalibrate` y `make promote` comparan candidato contra vigente sobre los mismos préstamos y promueven solo si se cumplen los umbrales escritos; registro de versiones y alias `champion` en MLflow.
- [x] Primera promoción real: `scorecard-v1.1` (experimento 002), recalibración que pidió el monitoreo. Cumplió los 6 criterios; con el modelo nuevo, el monitoreo pasa de "recalibrar" a "monitorear".
- [x] README final: frase, GIF de 22 s, resultados con contexto de negocio, diagrama, quickstart y links a `docs/`.
- [x] ADRs: scorecard y no solo GBM (experimento 001 y ADR-003), GoRules (ADR-005), PostgreSQL (D4 y ADR-005), monitoreo (ADR-006).
- [ ] Tags `v0.0` … `v0.5`: pendientes de confirmación.
- [ ] Opcional: despliegue de la API en capa gratuita. No se hizo.

Hecho cuando: un tercero clona, corre `make data && docker compose up` y ve una decisión en el tablero sin leer más que el README.

## Verificado al 17-nov-2026

- Mirror de Kaggle `wordsforthewise/lending-club`: disponible, licencia CC0 (dominio público). Hashes SHA-256 fijados en `data_manifest.json`.
- Aprobados: 2.260.668 préstamos emitidos entre 2007-06 y 2018-12. Rechazados: 27.648.741 solicitudes. Foto de los datos: marzo de 2019.
- **R1 confirmado**: 2018 no madura (tasa de malos a 12 meses truncada desde las emisiones de marzo de 2018). 2017 completo sí madura para una ventana de 12 meses.
- **R2 confirmado**: solo hay estado final y mes del último pago. El proxy de "malo a 12 meses" da entre 2,6% y 4,0% por año de emisión (ver `docs/data_audit.md`).
- **R3 confirmado**: el archivo de rechazados tiene 8 columnas útiles y el score de riesgo viene nulo en el 67% de los casos.
- Versiones en PyPI con soporte declarado para Python 3.12: optbinning 1.0.0, zen-engine 2.1.7, evidently 0.7.23, lightgbm 4.7.0, mlflow 3.18.0, pandera 0.36.0, shap 0.54.1. Todavía sin instalar.
