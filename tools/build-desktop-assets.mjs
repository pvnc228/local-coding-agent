import { copyFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outputDirectory = resolve(root, "local_coding_agent", "desktop", "assets");
const lucideSource = resolve(root, "node_modules", "lucide", "dist", "umd", "lucide.min.js");

await mkdir(outputDirectory, { recursive: true });
await copyFile(lucideSource, resolve(outputDirectory, "lucide.min.js"));
