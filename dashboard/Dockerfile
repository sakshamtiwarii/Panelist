# Production image for the coordinator console.
#
# It used to install dependencies and run `next dev`, which was fine while the
# only consumer was docker-compose (which overrides the command with its own
# `npm run dev` for hot reload). A hosted deployment builds this same file and
# got the dev server on a hardcoded port 3000 — so it ignored the platform's
# assigned $PORT and every request returned 502 with a healthy-looking build.
FROM node:22-alpine

WORKDIR /app

# Dependencies first so editing source does not reinstall them. `npm ci`
# rather than `npm install`: it installs exactly the lockfile, which is what
# makes a deployed build reproducible.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

# `npm start` runs `next start -p ${PORT:-3000}`, so a platform that assigns
# the port at runtime is honoured and a local run still lands on 3000.
ENV PORT=3000
EXPOSE 3000
CMD ["npm", "start"]
