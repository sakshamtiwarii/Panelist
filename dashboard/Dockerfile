# Production image for the coordinator console. docker-compose overrides the
# command with `npm run dev` for hot reload.
FROM node:22-alpine

WORKDIR /app

# Dependencies first so editing source does not reinstall them. `npm ci`
# installs exactly the lockfile, which keeps a deployed build reproducible.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

# `npm start` runs `next start -p ${PORT:-3000}`, so a platform that assigns
# the port at runtime is honoured and a local run still lands on 3000.
ENV PORT=3000
EXPOSE 3000
CMD ["npm", "start"]
