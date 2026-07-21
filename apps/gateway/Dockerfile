FROM nginx:1.27-alpine

# Full nginx.conf (events{} + http{}), not a conf.d fragment — we own the
# whole config so log format and resolver behavior are explicit.
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 8080
