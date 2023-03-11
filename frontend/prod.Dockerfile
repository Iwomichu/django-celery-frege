FROM node:current-alpine as build
WORKDIR /app
COPY package.json .
COPY package-lock.json .
RUN npm ci
COPY src /app/src
COPY public /app/public
COPY tsconfig.json .
RUN npm run build

FROM nginx:1.23.1-alpine
COPY ./default.conf.template /etc/nginx/conf.d/default.conf.template
COPY --from=build /app/build /usr/share/nginx/html