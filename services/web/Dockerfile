# ORCAS web client — static files served by nginx.
#   docker build -f services/web/Dockerfile -t orcas-web .
#   docker run --rm -p 8080:80 orcas-web   # then open http://localhost:8080
# The client calls the gateway at the base URL set in its form field (default: same
# origin). In the compose stack, point it at the published gateway (e.g. http://localhost:8000).
FROM nginx:1.29.4-alpine
COPY services/web/static/ /usr/share/nginx/html/
EXPOSE 80
