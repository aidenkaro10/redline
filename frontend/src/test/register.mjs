// Lets Node's test runner load our .ts/.tsx React components (npm test uses it).
// No extra libraries: TypeScript (already a dev dependency) compiles the JSX.
import { register } from "node:module";

register("./hooks.mjs", import.meta.url);
