// app.js's api() wrapper: a rejected js_api Promise must become {error}.
//
// pywebview rejects a Promise when the Python side raises. A bare .then()
// would run nothing and the window would appear to freeze, so api() turns
// every rejection into the {error: "..."} shape the call sites check for.
//
// Run with: node tests/shell/test_api_wrapper.mjs
import { readFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "..", "if_player", "shell", "app.js"), "utf8");

// Pull the two functions out of the IIFE for isolated testing.
const grab = (name) => {
  const i = src.indexOf(`  function ${name}(`);
  if (i < 0) throw new Error(`${name} not found`);
  let depth = 0, j = src.indexOf("{", i);
  for (let k = j; k < src.length; k++) {
    if (src[k] === "{") depth++;
    else if (src[k] === "}") { depth--; if (depth === 0) return src.slice(i, k + 1); }
  }
};
const code = grab("api") + "\n" + grab("describeError");
const factory = new Function("window", code + "; return {api, describeError};");

let passed = 0, failed = 0;
const check = (name, cond) => { cond ? passed++ : failed++; console.log(`  ${cond ? "PASS" : "FAIL"}  ${name}`); };

// A fake pywebview whose methods reject the way the real bridge does.
const pyError = Object.assign(new Error("EXTERNAL 'place_value_now' has no bound Python callable"), { name: "UnboundExternalError" });
const fakeWindow = { pywebview: { api: {
  boom: () => Promise.reject(pyError),
  fine: () => Promise.resolve({ text: "hello" }),
  plain: () => Promise.reject("just a string"),
  empty: () => Promise.reject(undefined),
  notAFunction: 42,
}}};
const { api } = factory(fakeWindow);

const results = await Promise.all([
  api().boom(), api().fine(), api().plain(), api().empty(),
]);
check("a rejection resolves to an {error} object", typeof results[0].error === "string");
check("the error names the Python exception type", results[0].error.includes("UnboundExternalError"));
check("the error carries the message", results[0].error.includes("place_value_now"));
check("a success is passed through untouched", results[1].text === "hello");
check("a non-Error rejection still yields {error}", typeof results[2].error === "string");
check("an empty rejection still yields {error}", typeof results[3].error === "string");
check("non-function properties are not wrapped", api().notAFunction === undefined);

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
