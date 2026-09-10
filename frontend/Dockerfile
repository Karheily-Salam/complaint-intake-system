# Frontend image: build the React/Vite bundle, then serve it with Nginx.
# Nginx also reverse-proxies /api/ to the FastAPI backend (see nginx.conf), so
# the browser only ever talks to a single origin (the VPS on port 80).

FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# `npm run build` = `tsc --noEmit && vite build` (typecheck + production build).
# No VITE_API_BASE_URL is set: the client defaults to the relative "/api/v1",
# which Nginx proxies to the backend - so the same image works behind any host.
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
