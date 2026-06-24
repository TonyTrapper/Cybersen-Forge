FROM golang:1.24-alpine AS agent-builder
WORKDIR /src
COPY agent/go.mod ./agent/go.mod
RUN cd agent && go mod download
COPY agent ./agent
RUN mkdir -p /artifacts \
    && cd agent \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /artifacts/cybersen-forge-linux-amd64 ./cmd/agent \
    && CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /artifacts/cybersen-forge-windows-amd64.exe ./cmd/agent \
    && sed -n 's/^const agentVersion = "\([^"]*\)"/\1/p' cmd/agent/main.go > /artifacts/AGENT_VERSION

FROM python:3.12-slim
WORKDIR /app
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY server/app ./app
COPY --from=agent-builder /artifacts /artifacts
RUN mkdir -p /data
ENV ARTIFACT_DIR=/artifacts
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
