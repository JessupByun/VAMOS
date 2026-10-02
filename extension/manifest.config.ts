import { defineManifest } from "@crxjs/vite-plugin";

// The public key pins the extension ID (hflgneiimkcoebdkfiblggbfklelebdn) for unpacked
// installs, which the native messaging host manifest must name in allowed_origins.
const KEY =
  "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAoGTr5Zdz+uLIkgLjWjn8G+40EID4he/+qypDL9ce9s7b+YfK5c4CsazGxwmdF/irJVxWL7bdsihhzpykDz5GhrZlPpfAZrkvYDoUrqSFx+F/yw/VmKaN4tndwoEbKC7Iu5pKrK/FJ0qAB4YdSv6Fz3X9EbUb3ryuZih1wpaXea/T2OUigBTxcJtl45gAFWMHr14+Ij/XM0X8i0eXsV2JTVd+nv7cw1Dbgwb9PdUdF/aoiPPmxM9CZNh14RdwNVY1KNpEGGiQVYQNcTnq7cKi73TH/UkFYp+W7eGdhnU40/hdX/cJLa9Am4qjHj9JkDXCL3gUpuNp16prmT3WVvG86QIDAQAB";

const POKERNOW = ["https://*.pokernow.com/*", "https://*.pokernow.club/*"];

export default defineManifest({
  manifest_version: 3,
  name: "VAMOS",
  description: "Real-time poker assistant for PokerNow",
  version: "0.1.0",
  key: KEY,
  permissions: ["nativeMessaging", "sidePanel"],
  host_permissions: POKERNOW,
  background: { service_worker: "src/background/sw.ts", type: "module" },
  content_scripts: [
    {
      matches: POKERNOW,
      js: ["src/content/index.tsx"],
      run_at: "document_idle",
    },
  ],
  side_panel: { default_path: "src/sidepanel/index.html" },
  action: { default_title: "VAMOS" },
});
