import { mkdir, rm, rename } from "node:fs/promises";
await rm("dist/web", { recursive: true, force: true });
await mkdir("dist/web", { recursive: true });
const result = await Bun.build({
  entrypoints: ["web/index.html"],
  outdir: "dist/web",
  target: "browser",
  minify: true,
  publicPath: "/assets/",
  naming: { entry: "[name]-[hash].[ext]", asset: "[name]-[hash].[ext]" },
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
});
if (!result.success)
  throw new AggregateError(result.logs, "Browser build failed");
const html = result.outputs.find((output) => output.path.endsWith(".html"));
if (!html) throw new Error("Browser build did not emit HTML");
await rename(html.path, "dist/web/index.html");
console.log(`Built ${result.outputs.length} browser assets`);
