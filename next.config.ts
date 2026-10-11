import type { NextConfig } from "next";

// Vercel's Next.js + FastAPI hybrid. In dev the Python service runs on :8000; in production the
// rewrite lands on api/index.py (a Python serverless function) and FastAPI sees the original path.
const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      {
        source: "/api/py/:path*",
        destination: isDev ? "http://127.0.0.1:8000/api/py/:path*" : "/api/",
      },
    ];
  },
};

export default nextConfig;
