FROM node:20-alpine

WORKDIR /app

COPY package.json /app/package.json
RUN npm install

COPY server.js /app/server.js

EXPOSE 3000

CMD ["node", "/app/server.js"]
