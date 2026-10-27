// Shared docx building blocks. Family modules import these helpers
// and compose them — each family controls which sections render and
// in what order, but the visual style of each block is consistent.
//
// Hard rules (master profile §9, locked):
//   - US Letter (12240 × 15840 DXA), 1-inch margins (1440 DXA each side).
//   - Default font: Arial.
//   - Centered name in DARK BLUE.
//   - Section headers in DARK BLUE with a DARK BLUE bottom border.
//   - Skill rows: BOLD label, then `: `, then comma-separated items.
//   - Never unicode bullets — use LevelFormat.BULLET via numbering config.
//   - ShadingType.CLEAR only (no SOLID).
//   - One-page output (density is controlled by the spec, not here).
//   - Tables (none currently) would need both columnWidths AND per-cell
//     width in DXA — never WidthType.PERCENTAGE.

const {
  AlignmentType,
  BorderStyle,
  Document,
  ExternalHyperlink,
  LevelFormat,
  Packer,
  Paragraph,
  TabStopPosition,
  TabStopType,
  TextRun,
} = require("docx");

// --- typography defaults --------------------------------------------

const FONT = "Arial";
const FONT_SIZE = 22;     // half-points → 11pt body text
const HEADING_SIZE = 26;  // half-points → 13pt section heading
const NAME_SIZE = 36;     // half-points → 18pt name at the very top

// Master profile §9 specifies "dark blue" for the name and section
// headings — using Word's standard dark-blue (Accent 1, Darker 25%)
// which renders consistently across Office versions.
const DARK_BLUE = "1F3864";
const HYPERLINK_BLUE = "0563C1";

// --- bullet list config ---------------------------------------------

// One global numbering reference used by every bulleted list in the
// document. LevelFormat.BULLET is the docx-library-native bullet
// implementation; do NOT switch this to unicode "•" — Word rendering
// across versions is inconsistent if you do.
const BULLET_NUMBERING = {
  config: [
    {
      reference: "resume-bullets",
      levels: [
        {
          level: 0,
          format: LevelFormat.BULLET,
          text: "•",
          alignment: AlignmentType.LEFT,
          style: {
            paragraph: { indent: { left: 360, hanging: 240 } },
          },
        },
      ],
    },
  ],
};

// --- paragraph builders ---------------------------------------------

function nameLine(header) {
  // Master §9: centered, bold, dark blue.
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 60 },
    children: [
      new TextRun({
        text: header.name,
        bold: true,
        font: FONT,
        size: NAME_SIZE,
        color: DARK_BLUE,
      }),
    ],
  });
}

function contactLine(header) {
  // Master §9: single contact line beneath the name —
  //   `City, State · email · LinkedIn URL`
  // No GitHub. No phone unless explicitly set in header. Order: Location,
  // Email, LinkedIn.
  const parts = [];
  if (header.location) parts.push(makeRun(header.location));
  if (header.email) parts.push(makeRun(header.email));
  if (header.linkedin)
    parts.push(
      new ExternalHyperlink({
        link: header.linkedin,
        children: [makeRun(header.linkedin.replace(/^https?:\/\//, ""), {
          color: HYPERLINK_BLUE,
          underline: {},
        })],
      })
    );
  if (header.phone) parts.push(makeRun(header.phone));

  const children = [];
  parts.forEach((part, i) => {
    if (i > 0) children.push(makeRun("  ·  "));
    children.push(part);
  });

  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 120 },
    children,
  });
}

function sectionHeading(text) {
  // Master §9: section headers all caps in dark blue with a dark blue
  // bottom border. The single-pixel border under each section is the
  // visual anchor of the resume.
  return new Paragraph({
    spacing: { before: 200, after: 60 },
    border: {
      bottom: { color: DARK_BLUE, space: 1, style: BorderStyle.SINGLE, size: 6 },
    },
    children: [
      new TextRun({
        text: text.toUpperCase(),
        bold: true,
        font: FONT,
        size: HEADING_SIZE,
        color: DARK_BLUE,
      }),
    ],
  });
}

function summaryParagraph(text) {
  return new Paragraph({
    spacing: { after: 100 },
    children: [makeRun(text)],
  });
}

function experienceHeader(exp) {
  // Title · company on the left; dates right-tabbed to the page edge.
  return new Paragraph({
    spacing: { before: 80, after: 20 },
    tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
    children: [
      new TextRun({ text: exp.title, bold: true, font: FONT, size: FONT_SIZE }),
      makeRun(`  ·  ${exp.company}`),
      makeRun(`\t${exp.start} – ${exp.end}`, { italics: true }),
    ],
  });
}

function experienceSubheader(exp) {
  if (!exp.location) return null;
  return new Paragraph({
    spacing: { after: 60 },
    children: [makeRun(exp.location, { italics: true })],
  });
}

function bulletParagraph(text) {
  return new Paragraph({
    numbering: { reference: "resume-bullets", level: 0 },
    spacing: { after: 40 },
    children: [makeRun(text)],
  });
}

function projectHeader(project) {
  return new Paragraph({
    spacing: { before: 80, after: 20 },
    children: [
      new TextRun({ text: project.title, bold: true, font: FONT, size: FONT_SIZE }),
      makeRun(project.stack ? `  ·  ${project.stack}` : "", { italics: true }),
    ],
  });
}

function educationLine(edu) {
  // School (bold) · degree, right-tabbed dates.
  return new Paragraph({
    spacing: { after: 30 },
    tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
    children: [
      new TextRun({ text: edu.school, bold: true, font: FONT, size: FONT_SIZE }),
      makeRun(edu.degree ? `  ·  ${edu.degree}` : ""),
      makeRun(edu.end ? `\t${edu.start ? edu.start + " – " : ""}${edu.end}` : "", {
        italics: true,
      }),
    ],
  });
}

function educationHonors(edu) {
  // Awards / honors / coursework fold under each education line so we
  // never need a separate "Awards" section (master §9: "sometimes folded
  // into Education line").
  if (!edu.honors) return null;
  return new Paragraph({
    spacing: { after: 30 },
    children: [makeRun(edu.honors, { italics: true })],
  });
}

function skillsLine(group) {
  // Master §9: BOLD label, then `: `, then items joined by `, `.
  return new Paragraph({
    spacing: { after: 30 },
    children: [
      new TextRun({
        text: `${group.group}: `,
        bold: true,
        font: FONT,
        size: FONT_SIZE,
      }),
      makeRun(group.items.join(", ")),
    ],
  });
}

// --- run helper -----------------------------------------------------

function makeRun(text, opts = {}) {
  return new TextRun({
    text,
    font: FONT,
    size: FONT_SIZE,
    ...opts,
  });
}

// --- document assembly ----------------------------------------------

function buildDocument(children) {
  return new Document({
    creator: "job-pipeline-v2",
    title: "Resume",
    numbering: BULLET_NUMBERING,
    styles: {
      default: {
        document: { run: { font: FONT, size: FONT_SIZE } },
      },
    },
    sections: [
      {
        properties: {
          page: {
            size: { width: 12240, height: 15840 }, // US Letter, DXA
            margin: {
              top: 1080,
              right: 1080,
              bottom: 1080,
              left: 1080, // 0.75-inch margins — extra vertical room for one-page
            },
          },
        },
        children,
      },
    ],
  });
}

async function writeDocument(doc, outputPath) {
  const fs = require("fs");
  const buffer = await Packer.toBuffer(doc);
  fs.writeFileSync(outputPath, buffer);
}

// --- section builders ----------------------------------------------
//
// Each section name maps to a function that returns an array of
// Paragraphs (filtered for nulls). The family module's section_order
// in the spec drives which sections appear and in what order.

const SECTION_BUILDERS = {
  summary: (spec) => {
    if (!spec.summary) return [];
    return [sectionHeading("Professional Summary"), summaryParagraph(spec.summary)];
  },
  skills: (spec) => {
    if (!spec.skills || spec.skills.length === 0) return [];
    const out = [sectionHeading("Technical Skills")];
    for (const group of spec.skills) out.push(skillsLine(group));
    return out;
  },
  experience: (spec) => {
    const out = [sectionHeading("Experience")];
    for (const exp of spec.experiences || []) {
      out.push(experienceHeader(exp));
      const sub = experienceSubheader(exp);
      if (sub) out.push(sub);
      for (const b of exp.bullets || []) out.push(bulletParagraph(b.text));
    }
    return out;
  },
  education: (spec) => {
    if (!spec.education || spec.education.length === 0) return [];
    const out = [sectionHeading("Education")];
    for (const e of spec.education) {
      out.push(educationLine(e));
      const honors = educationHonors(e);
      if (honors) out.push(honors);
    }
    return out;
  },
  projects: (spec) => {
    if (!spec.projects || spec.projects.length === 0) return [];
    const out = [sectionHeading("Projects")];
    for (const p of spec.projects) {
      out.push(projectHeader(p));
      for (const b of p.bullets || []) out.push(bulletParagraph(b.text));
    }
    return out;
  },
  additional: (spec) => {
    // Short final block — Languages / Certifications / Academic Background.
    // Each is rendered as a bold-label line, matching the resumes' style.
    const add = spec.additional || {};
    const rows = [
      ["Languages", add.languages],
      ["Certifications", add.certifications],
      ["Academic Background", add.academic_background],
    ].filter(([, v]) => v && v.trim());
    if (rows.length === 0) return [];
    const out = [sectionHeading("Additional")];
    for (const [label, value] of rows) {
      out.push(skillsLine({ group: label, items: [value] }));
    }
    return out;
  },
};

// --- top-level assembler -------------------------------------------

async function assembleResume(spec, outputPath, sectionOrder) {
  // sectionOrder is taken from spec.section_order by default, but each
  // family module can override (legacy path; new code passes spec.section_order).
  const order = sectionOrder || spec.section_order || [
    "summary", "skills", "experience", "education",
  ];

  // Always start with the header (name + contact). Then walk the
  // section order, filtering empty section outputs.
  const children = [nameLine(spec.header), contactLine(spec.header)];

  for (const section of order) {
    const builder = SECTION_BUILDERS[section];
    if (!builder) throw new Error(`Unknown section: ${section}`);
    const block = builder(spec);
    for (const p of block) {
      if (p) children.push(p);
    }
  }

  const doc = buildDocument(children);
  await writeDocument(doc, outputPath);
}

module.exports = {
  // builders
  nameLine,
  contactLine,
  sectionHeading,
  summaryParagraph,
  experienceHeader,
  experienceSubheader,
  bulletParagraph,
  projectHeader,
  educationLine,
  educationHonors,
  skillsLine,
  // assembly
  buildDocument,
  writeDocument,
  assembleResume,
};
