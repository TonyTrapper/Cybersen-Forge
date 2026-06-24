.PHONY: server-up server-down server-logs agent-linux agent-windows test clean-identity reset-data

server-up:
	docker compose up --build -d

server-down:
	docker compose down

server-logs:
	docker compose logs -f server

agent-linux:
	mkdir -p bin
	cd agent && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o ../bin/cybersen-forge-linux-amd64 ./cmd/agent

agent-windows:
	mkdir -p bin
	cd agent && CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o ../bin/cybersen-forge-windows-amd64.exe ./cmd/agent

test:
	cd agent && go test ./...
	python3 -m py_compile server/app/main.py

clean-identity:
	rm -rf $$HOME/.cybersen-forge

reset-data:
	docker compose down -v
