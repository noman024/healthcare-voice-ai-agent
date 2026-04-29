/** @type {import('next').NextConfig} */
const nextConfig = {
  /**
   * Do not customize webpack cache in dev: forcing `memory` was linked to missing
   * `./948.js` chunk errors (broken server bundle graph until `.next` is wiped).
   * If webpack cache ENOENT persists: run `npm run clean` then `npm run dev`.
   */
};

export default nextConfig;
