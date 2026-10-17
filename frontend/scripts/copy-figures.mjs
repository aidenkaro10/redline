// Copies the result figures (docs/figures/*.png, made by the ML session) into
// public/figures so the /results page can show them. Runs before `npm run dev`
// and `npm run build`. Does nothing if docs/figures doesn't exist yet.
import { copyFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";

const src = join(import.meta.dirname, "..", "..", "docs", "figures");
const dst = join(import.meta.dirname, "..", "public", "figures");
if (!existsSync(src)) {
  console.log("figures: docs/figures not found yet, skipping");
} else {
  mkdirSync(dst, { recursive: true });
  const files = readdirSync(src).filter((f) => f.endsWith(".png"));
  for (const f of files) copyFileSync(join(src, f), join(dst, f));
  console.log(`figures: copied ${files.length} from docs/figures`);
}
