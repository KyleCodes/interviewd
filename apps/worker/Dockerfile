# Multi-stage: resolve deps + build in golang image, ship a static binary on alpine.
FROM golang:1.23 AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /worker .

FROM alpine:3.20
COPY --from=builder /worker /worker
EXPOSE 9100
ENTRYPOINT ["/worker"]
