// Node module hooks for the tests:
// - "@/..." means src/... (same alias as tsconfig.json)
// - imports without an extension find the .ts / .tsx file (like Next.js does)
// - Next.js-only modules, Leaflet and CSS get small stand-ins from ./stubs
// - .ts / .tsx files are compiled with TypeScript (JSX included)
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const STUB = (name) => pathToFileURL(join(SRC, "test", "stubs", name)).href;
const STUBS = {
  "next/navigation": STUB("next-navigation.mjs"),
  "next/link": STUB("next-link.mjs"),
  "next/dynamic": STUB("next-dynamic.mjs"),
  leaflet: STUB("empty.mjs"),
};

function withExtension(path) {
  for (const ext of ["", ".ts", ".tsx", "/index.ts", "/index.tsx"]) {
    if (existsSync(path + ext) && (ext || /\.[cm]?[jt]sx?$/.test(path))) return path + ext;
  }
  return null;
}

export async function resolve(specifier, context, next) {
  if (STUBS[specifier]) return { url: STUBS[specifier], shortCircuit: true };
  if (specifier.endsWith(".css")) return { url: STUB("empty.mjs"), shortCircuit: true };
  let base = null;
  if (specifier.startsWith("@/")) base = join(SRC, specifier.slice(2));
  else if ((specifier.startsWith("./") || specifier.startsWith("../")) && context.parentURL?.startsWith("file:")) {
    base = join(dirname(fileURLToPath(context.parentURL)), specifier);
  }
  if (base) {
    const found = withExtension(base);
    if (found) return { url: pathToFileURL(found).href, shortCircuit: true };
  }
  return next(specifier, context);
}

export async function load(url, context, next) {
  if (url.startsWith("file:") && /\.tsx?$/.test(url)) {
    const source = readFileSync(fileURLToPath(url), "utf8");
    const out = ts.transpileModule(source, {
      fileName: fileURLToPath(url),
      compilerOptions: {
        module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
        allowImportingTsExtensions: true, rewriteRelativeImportExtensions: false, verbatimModuleSyntax: false,
      },
    });
    return { format: "module", source: out.outputText, shortCircuit: true };
  }
  return next(url, context);
}
