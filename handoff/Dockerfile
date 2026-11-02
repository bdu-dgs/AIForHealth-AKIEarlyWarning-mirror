# Python and Jupyter only: code and data are mounted at run time (see docker-compose.yml), never baked in.
FROM python:3.13.5-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-handoff.txt /tmp/requirements-handoff.txt
RUN pip install --no-cache-dir -r /tmp/requirements-handoff.txt

WORKDIR /work/AIForHealth-AKIEarlyWarning
EXPOSE 8888
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", \
     "--ServerApp.token=", "--ServerApp.password="]
