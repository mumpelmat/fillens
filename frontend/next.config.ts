import { createHash } from "node:crypto";
import type { NextConfig } from "next";
import packageJson from "./package.json";

const basePath = process.env.APP_BASE_PATH || "";
if (basePath && !/^\/(?:[a-zA-Z0-9_-]+\/)*[a-zA-Z0-9_-]+$/.test(basePath))
  throw new Error("Ungültiger APP_BASE_PATH");
const config: NextConfig = {
  output: "standalone",
  basePath,
  env: {
    NEXT_PUBLIC_APP_BASE_PATH: basePath,
    NEXT_PUBLIC_APP_VERSION: packageJson.version,
    NEXT_PUBLIC_COOKIE_SUFFIX: createHash("sha256")
      .update(basePath)
      .digest("hex")
      .slice(0, 8),
  },
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
          {
            key: "Content-Security-Policy",
            value:
              "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
          },
        ],
      },
    ];
  },
};
export default config;
