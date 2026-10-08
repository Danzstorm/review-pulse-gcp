-- One-off: ask Gemini for varied synthetic reviews and print them as rows. The result is
-- committed as generator/review_bank.json, so the generator itself stays deterministic and
-- needs no network. Rebuild only to change the bank:
--   bash scripts/build_bank.sh
WITH pairs AS (
  SELECT * FROM UNNEST([
    STRUCT('bateria' AS topic, 'pos' AS sentiment, 'la duración o la carga rápida de la batería' AS about),
    STRUCT('bateria', 'neg', 'batería que dura poco, se calienta o deja de cargar'),
    STRUCT('conectividad', 'pos', 'emparejamiento Bluetooth rápido y señal estable'),
    STRUCT('conectividad', 'neg', 'desconexiones, cortes de Bluetooth o el teléfono que no lo reconoce'),
    STRUCT('envio', 'pos', 'entrega rápida o paquete bien protegido'),
    STRUCT('envio', 'neg', 'entrega tardía, caja dañada o piezas faltantes'),
    STRUCT('precio', 'pos', 'buena relación calidad-precio u oferta'),
    STRUCT('precio', 'neg', 'producto caro para lo que ofrece'),
    STRUCT('calidad', 'pos', 'buena construcción, materiales y funcionamiento sin fallas'),
    STRUCT('calidad', 'neg', 'se rompe rápido, materiales frágiles o fallas de fabricación')
  ])
),
styles AS (
  SELECT * FROM UNNEST([
    'muy corta, dos frases', 'larga, con detalles de uso', 'tono formal', 'tono coloquial y relajado',
    'menciona una comparación con otra marca', 'menciona cuánto tiempo lleva usándolo',
    'escrita con prisa, sin tildes ni mayúsculas', 'con una queja o elogio concreto y medible',
    'menciona para qué lo usa (gimnasio, oficina, viajes)', 'tono emocionado',
    'tono seco y objetivo', 'menciona a quién se lo recomendaría o no'
  ]) AS style WITH OFFSET AS style_id
),
requests AS (
  SELECT topic, sentiment, style, n,
    CONCAT('Escribe UNA reseña de cliente en español de un producto de audio o accesorios electrónicos. ',
      'Tema: ', about, '. Sentimiento: ', IF(sentiment = 'pos', 'positivo', 'negativo'), '. ',
      'Estilo: ', style, '. Variante ', CAST(n AS STRING), ': que no se parezca a otras. ',
      'No menciones el nombre de ninguna marca ni modelo. ',
      'title: máximo 8 palabras. body: entre 1 y 3 frases.') AS prompt
  FROM pairs CROSS JOIN styles CROSS JOIN UNNEST(GENERATE_ARRAY(1, 3)) AS n
)
SELECT topic, sentiment, title, body
FROM AI.GENERATE_TABLE(
  MODEL `${project}.gold.gemini`,
  TABLE requests,
  STRUCT('title STRING, body STRING' AS output_schema, 1.0 AS temperature, 300 AS max_output_tokens)
)
WHERE status = '' AND title IS NOT NULL AND body IS NOT NULL
ORDER BY topic, sentiment, title;
