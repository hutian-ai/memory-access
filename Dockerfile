FROM python:3.11-slim
WORKDIR /app/memory-access
COPY pyproject.toml ./
RUN python -c 'import tomllib, subprocess, sys; p=tomllib.load(open("pyproject.toml", "rb"))["project"]; subprocess.run([sys.executable,"-m","pip","install","--no-cache-dir",*p["dependencies"],*p["optional-dependencies"]["mem0"]],check=True)'
COPY README.md LICENSE ./
COPY hutian_memory_access ./hutian_memory_access
RUN pip install --no-cache-dir --no-deps .
ENV MEM0_TELEMETRY=false
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["hutian_memory_access.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
