#!/usr/bin/env node
// Cover-letter renderer, same look as the resume (base.js): name + contact
// line at the top, then date, greeting, body paragraphs, sign-off and name.
//
// Usage: node cover_letter.js <spec-json-path> <output-docx-path>
// Spec: {header, date, company, title, greeting, paragraphs[], closing}

const fs = require("fs");
const { Paragraph, TextRun } = require("docx");
const { nameLine, contactLine, buildDocument, writeDocument } = require("./base");

const FONT = "Arial";
const SIZE = 22; // 11pt

function para(text, after = 200) {
  return new Paragraph({
    spacing: { after, line: 276 },
    children: [new TextRun({ text, font: FONT, size: SIZE })],
  });
}

async function main() {
  const [, , specPath, outputPath] = process.argv;
  if (!specPath || !outputPath) {
    console.error("usage: cover_letter.js <spec-path> <output-path>");
    process.exit(2);
  }
  const spec = JSON.parse(fs.readFileSync(specPath, "utf-8"));
  const children = [
    nameLine(spec.header),
    contactLine(spec.header),
    para(spec.date, 240),
    para(`Re: ${spec.title}`, 240),
    para(spec.greeting),
    ...spec.paragraphs.map((p) => para(p)),
    para(spec.closing, 360),
    para(spec.header.name, 0),
  ];
  const doc = buildDocument(children);
  await writeDocument(doc, outputPath);
  console.log(`Wrote cover letter to ${outputPath}`);
}

main().catch((e) => {
  console.error(`Render failed: ${e.message}`);
  process.exit(1);
});
