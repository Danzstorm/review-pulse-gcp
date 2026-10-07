# Flex Template launcher image: runs pipeline.py to build the job graph and submit it.
# Workers do not use this image; Dataflow picks the matching official Beam SDK container.
FROM gcr.io/dataflow-templates-base/python3-template-launcher-base:latest AS template_launcher

# python:3.13-slim + pip, not apache/beam_python3.13_sdk: the Beam SDK image ships zstd
# layers, which the Docker daemon on Cloud Build workers cannot unpack ("invalid tar header").
FROM python:3.13-slim

# The BigQuery Storage Write API sink is a Java cross-language transform:
# building the graph starts a Java expansion service, so the launcher needs a JRE.
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Same pinned Beam version as local tests and the worker containers.
COPY requirements.txt /template/requirements.txt
RUN pip install --no-cache-dir -r /template/requirements.txt

COPY --from=template_launcher /opt/google/dataflow/python_template_launcher /opt/google/dataflow/python_template_launcher

COPY pipeline.py /template/pipeline.py
COPY schemas /template/schemas
ENV FLEX_TEMPLATE_PYTHON_PY_FILE=/template/pipeline.py

ENTRYPOINT ["/opt/google/dataflow/python_template_launcher"]
