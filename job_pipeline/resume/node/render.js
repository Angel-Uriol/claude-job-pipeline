#!/usr/bin/env node
// Resume renderer. Reads a JSON spec produced by Python and writes a
// .docx via the `docx` library.
//
// Usage:
//   node render.js <spec-json-path> <output-docx-path>
//
// The spec shape is documented in spec_builder.py — see that file for
// the contract. This module loads the right family module from
// `families/<family-name>.js` and asks it to assemble the document.

const fs = require("fs");
const path = require("path");

async function main() {
  const [, , specPath, outputPath] = process.argv;
  if (!specPath || !outputPath) {
    console.error("usage: render.js <spec-path> <output-path>");
    process.exit(2);
  }

  let spec;
  try {
    const raw = fs.readFileSync(specPath, "utf-8");
    spec = JSON.parse(raw);
  } catch (e) {
    console.error(`Failed to read spec at ${specPath}: ${e.message}`);
    process.exit(2);
  }

  const familyName = spec.family;
  const modulePath = path.join(__dirname, "families", `${familyName}.js`);
  if (!fs.existsSync(modulePath)) {
    console.error(`Unknown family: ${familyName} (expected ${modulePath})`);
    process.exit(2);
  }

  let familyModule;
  try {
    familyModule = require(modulePath);
  } catch (e) {
    console.error(`Failed to load family module ${familyName}: ${e.message}`);
    process.exit(2);
  }

  if (typeof familyModule.render !== "function") {
    console.error(`Family ${familyName} does not export a render() function`);
    process.exit(2);
  }

  try {
    await familyModule.render(spec, outputPath);
    console.log(`Wrote resume to ${outputPath}`);
  } catch (e) {
    console.error(`Render failed: ${e.message}`);
    console.error(e.stack);
    process.exit(1);
  }
}

main();
