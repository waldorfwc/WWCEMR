# ---------- build stage ----------
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---------- runtime stage ----------
FROM nginx:1-alpine

# Built SPA + nginx template (nginx-alpine entrypoint runs envsubst on
# everything under /etc/nginx/templates/ at container start, filling in
# ${PORT} and ${BACKEND_URL} from the environment).
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Cloud Run convention
ENV PORT=8080
EXPOSE 8080
