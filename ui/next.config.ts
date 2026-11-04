import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Old page names keep working after the plain-language rename.
  async redirects() {
    return [
      { source: "/track", destination: "/applications", permanent: false },
      { source: "/discover", destination: "/jobs", permanent: false },
    ];
  },
};

export default nextConfig;
